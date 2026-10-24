"""Bounded Git inspection and owner-bound session worktree preparation."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import signal
import stat
import subprocess
from concurrent.futures import Executor, ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit
from uuid import UUID

from .config import AgentConfig
from .errors import AgentError
from .platform import FilesystemAdapter
from .protocol import GitWorktreeArguments, GitWorktreeRemoveArguments, _relative_path

MAX_TEXT_BYTES = 64 * 1024
MAX_OUTPUT_BYTES = 512 * 1024
MAX_FILES = 500
_worktree_lock = asyncio.Lock()
_preparation_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="git-preparation")


async def _read(reader: asyncio.StreamReader, limit: int) -> bytes:
    output = bytearray()
    while chunk := await reader.read(min(64 * 1024, limit + 1 - len(output))):
        output.extend(chunk)
        if len(output) > limit:
            raise AgentError("operation.tooLarge", "Git output exceeds the preview limit")
    return bytes(output)


async def _git(
    directory: str,
    *arguments: str,
    limit: int = MAX_OUTPUT_BYTES,
    config: dict[str, str] | None = None,
    network: bool = False,
    timeout_seconds: float = 15,
) -> tuple[int, bytes, bytes]:
    executable = shutil.which("git")
    if not executable:
        raise AgentError("operation.unsupported", "Git is not installed")
    environment = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    environment.update(
        GIT_OPTIONAL_LOCKS="0",
        GIT_TERMINAL_PROMPT="0",
        LC_ALL="C",
        GIT_ALLOW_PROTOCOL="https:http:ssh:git" if network else "",
        GIT_NO_LAZY_FETCH="1",
    )
    if config:
        environment["GIT_CONFIG_COUNT"] = str(len(config))
        for index, (key, value) in enumerate(config.items()):
            environment[f"GIT_CONFIG_KEY_{index}"] = key
            environment[f"GIT_CONFIG_VALUE_{index}"] = value
    process = await asyncio.create_subprocess_exec(
        executable,
        "--no-optional-locks",
        "--literal-pathspecs",
        "-c",
        "core.fsmonitor=false",
        "-c",
        f"core.hooksPath={os.devnull}",
        *arguments,
        cwd=directory,
        env=environment,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        **(
            {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
            if os.name == "nt"
            else {"start_new_session": True}
        ),
    )
    readers = [
        asyncio.create_task(_read(process.stdout, limit)),
        asyncio.create_task(_read(process.stderr, 8 * 1024)),
    ]
    completed = False
    try:
        async with asyncio.timeout(120 if network else timeout_seconds):
            output, error = await asyncio.gather(*readers)
            code = await process.wait()
            completed = True
            return code, output, error
    except TimeoutError as exc:
        raise AgentError("path.unavailable", "Git inspection timed out") from exc
    finally:
        for reader in readers:
            reader.cancel()
        if not completed:
            termination = asyncio.create_task(_terminate_git(process))
            try:
                await asyncio.shield(termination)
            except asyncio.CancelledError:
                await termination
                raise
        await asyncio.gather(*readers, return_exceptions=True)


async def _terminate_git(process: asyncio.subprocess.Process) -> None:
    if os.name == "nt":
        killer = await asyncio.create_subprocess_exec(
            "taskkill",
            "/PID",
            str(process.pid),
            "/T",
            "/F",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            await asyncio.wait_for(killer.wait(), 2)
        except TimeoutError:
            killer.kill()
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    if process.returncode is None:
        try:
            process.kill()
        except ProcessLookupError:
            pass
    # Detached children must not keep cleanup or the preparation lock alive through pipes.
    transport = getattr(process, "_transport", None)
    if transport is not None:
        for descriptor in (0, 1, 2):
            pipe = transport.get_pipe_transport(descriptor)
            if pipe is not None:
                pipe.close()
    try:
        await asyncio.wait_for(process.wait(), 2)
    except TimeoutError:
        if transport is not None:
            transport.close()


async def _checked(directory: str, *arguments: str, config: dict[str, str] | None = None) -> bytes:
    code, output, _ = await _git(directory, *arguments, config=config)
    if code:
        raise AgentError("path.unavailable", "Git inspection failed")
    return output


async def _disabled_filters(directory: str) -> dict[str, str]:
    code, output, _ = await _git(
        directory,
        "config",
        "--null",
        "--name-only",
        "--get-regexp",
        r"^filter\..*\.(clean|smudge|process|required)$",
    )
    if code not in {0, 1}:
        raise AgentError("path.unavailable", "Git filter inspection failed")
    try:
        drivers = {key.decode("utf-8").rsplit(".", 1)[0] for key in output.split(b"\0") if key}
    except UnicodeDecodeError as exc:
        raise AgentError("path.unsupportedType", "Unsupported Git filter name") from exc
    return {
        f"{driver}.{option}": value
        for driver in drivers
        for option, value in (("clean", ""), ("smudge", ""), ("process", ""), ("required", "false"))
    }


async def _diff(
    filesystem: FilesystemAdapter, root: str, reference: str, paths: tuple[str, ...]
) -> tuple[bytes, bytes]:
    # Keep file-tool writes from introducing a new filter between inspection and comparison.
    while not filesystem._lock.acquire(blocking=False):
        await asyncio.sleep(0.01)
    try:
        config = await _disabled_filters(root)
        flags = ("--no-ext-diff", "--no-textconv", "--no-renames", "--ignore-submodules=all")
        names = await _checked(
            root, "diff", *flags, "--name-status", "-z", reference, *paths, config=config
        )
        stats = await _checked(
            root, "diff", *flags, "--numstat", "-z", reference, *paths, config=config
        )
        return names, stats
    finally:
        filesystem._lock.release()


def _path(value: bytes) -> str:
    try:
        path = _relative_path(value.decode("utf-8"))
        if ".git" in PurePosixPath(path).parts:
            raise ValueError("Git metadata is not a file preview")
        return path
    except (UnicodeDecodeError, ValueError) as exc:
        raise AgentError("path.unsupportedType", "Unsupported Git filename") from exc


def _text(data: bytes) -> str:
    if b"\0" in data:
        raise AgentError("file.invalidEncoding", "Binary file cannot be previewed as text")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise AgentError("file.invalidEncoding", "File is not UTF-8 text") from exc


async def _working(filesystem: FilesystemAdapter, root: str, path: str) -> bytes:
    return await asyncio.to_thread(
        filesystem._read_bytes, f"{root.rstrip('/')}/{path}", MAX_TEXT_BYTES
    )


async def changes(filesystem: FilesystemAdapter, directory: str, file: str | None = None) -> dict:
    directory = (await asyncio.to_thread(filesystem.resolve_directory, directory))["path"]
    code, output, error = await _git(directory, "rev-parse", "--show-toplevel")
    if code:
        if error.startswith(b"fatal: not a git repository"):
            return {"repository": None, "files": [], "truncated": False}
        code = "path.accessDenied" if b"dubious ownership" in error else "path.unavailable"
        raise AgentError(code, "Git working tree inspection failed")
    root = output.decode("utf-8").rstrip("\r\n").replace("\\", "/")
    await asyncio.to_thread(filesystem.resolve_directory, root)
    head_code, head, _ = await _git(root, "rev-parse", "--verify", "--quiet", "HEAD")
    if head_code not in {0, 1}:
        raise AgentError("path.unavailable", "Git HEAD inspection failed")
    reference = head.decode("ascii").strip()
    paths = ("--", file) if file is not None else ("--",)
    files = {}
    if not head_code:
        names, stats = await _diff(filesystem, root, reference, paths)
        names = names.split(b"\0")
        for index in range(0, len(names) - 1, 2):
            path = _path(names[index + 1])
            files[path] = {
                "path": path,
                "status": {b"A": "added", b"D": "deleted"}.get(names[index], "modified"),
                "additions": None,
                "deletions": None,
            }
        for record in stats.split(b"\0"):
            if not record:
                continue
            added, removed, name = record.split(b"\t", 2)
            item = files.get(_path(name))
            if item is not None and added != b"-":
                item.update(additions=int(added), deletions=int(removed))
        untracked = await _checked(root, "ls-files", "--others", "--exclude-standard", "-z", *paths)
    else:
        untracked = await _checked(
            root, "ls-files", "--cached", "--others", "--exclude-standard", "-z", *paths
        )
    extra = {
        _path(name) for name in untracked.split(b"\0") if name and not name.endswith(b"/")
    }.difference(files)
    for path in extra:
        files[path] = {"path": path, "status": "added", "additions": None, "deletions": None}
    if file is not None:
        if file not in files:
            raise AgentError("file.changed", "File is no longer changed")
        item = files[file]
        result = {"repository": root, "file": item, "before": "", "after": "", "reason": None}
        try:
            if not head_code and item["status"] != "added":
                code, before, _ = await _git(
                    root, "cat-file", "blob", f"{reference}:{file}", limit=MAX_TEXT_BYTES
                )
                if code:
                    raise AgentError("path.unsupportedType", "Git object is not a regular file")
                result["before"] = _text(before)
            if item["status"] != "deleted":
                result["after"] = _text(await _working(filesystem, root, file))
        except AgentError as exc:
            reasons = {
                "file.tooLarge": "tooLarge",
                "operation.tooLarge": "tooLarge",
                "file.invalidEncoding": "binary",
                "path.notFile": "unsupported",
                "path.unsupportedType": "unsupported",
            }
            if exc.code not in reasons:
                raise
            result.update(before="", after="", reason=reasons[exc.code])
        return result
    selected = []
    budget = 0
    for path, item in sorted(files.items()):
        size = len(json.dumps(item).encode("utf-8"))
        if len(selected) >= MAX_FILES or budget + size > 256 * 1024:
            break
        if path in extra:
            try:
                text = _text(await _working(filesystem, root, path))
                item.update(
                    additions=text.count("\n") + int(bool(text) and not text.endswith("\n")),
                    deletions=0,
                )
            except AgentError as exc:
                if exc.code not in {
                    "file.tooLarge",
                    "file.invalidEncoding",
                    "path.notFile",
                    "path.unsupportedType",
                    "path.notFound",
                }:
                    raise
        selected.append(item)
        budget += size
    return {"repository": root, "files": selected, "truncated": len(selected) < len(files)}


async def repository(
    filesystem: FilesystemAdapter,
    directory: str,
    *,
    executor: Executor | None = None,
    target_branch: str | None = None,
) -> dict:
    loop = asyncio.get_running_loop()
    directory = (await loop.run_in_executor(executor, filesystem.resolve_directory, directory))[
        "path"
    ]
    code, output, error = await _git(directory, "rev-parse", "--show-toplevel")
    if code:
        if error.startswith(b"fatal: not a git repository"):
            return {
                "repository": None,
                "commonDirectory": None,
                "remotes": [],
                "defaultRemote": None,
                "defaultBranch": None,
                "branch": None,
                "localBranches": [],
                "localBranchesTruncated": False,
            }
        raise AgentError("path.unavailable", "Git repository inspection failed")
    root = output.decode("utf-8").rstrip("\r\n").replace("\\", "/")
    await loop.run_in_executor(executor, filesystem.resolve_directory, root)
    common = (
        (await _checked(root, "rev-parse", "--path-format=absolute", "--git-common-dir"))
        .decode("utf-8")
        .rstrip("\r\n")
        .replace("\\", "/")
    )
    await loop.run_in_executor(executor, filesystem.resolve_directory, common)
    remotes = (await _checked(root, "remote")).decode("utf-8").splitlines()
    if len(remotes) > 100:
        raise AgentError("operation.tooLarge", "Too many Git remotes")
    identities = {}
    urls = {}
    for name in remotes:
        fetch_urls = await _checked(root, "remote", "get-url", "--all", "--", name)
        push_urls = await _checked(root, "remote", "get-url", "--push", "--all", "--", name)
        identities[name] = hashlib.sha256(fetch_urls + b"\0" + push_urls).hexdigest()
        value = fetch_urls.decode("utf-8").splitlines()[0].strip()
        try:
            parsed = urlsplit(value)
            hidden = (
                parsed.password is not None
                or parsed.query
                or parsed.fragment
                or (parsed.scheme in {"http", "https"} and parsed.username is not None)
            )
            urls[name] = (
                None
                if hidden or len(value) > 4096 or any(ord(char) < 32 for char in value)
                else value
            )
        except ValueError:
            urls[name] = None
    remote = "origin" if "origin" in remotes else remotes[0] if len(remotes) == 1 else None
    code, branch, _ = await _git(root, "symbolic-ref", "--quiet", "HEAD")
    branch = branch.decode("utf-8").strip().removeprefix("refs/heads/") if code == 0 else None
    local_branches = (
        (
            await _checked(
                root, "for-each-ref", "--format=%(refname:lstrip=2)", "--count=1001", "refs/heads/"
            )
        )
        .decode("utf-8")
        .splitlines()
    )
    default_branch = None
    if remote:
        code, reference, _ = await _git(
            root, "symbolic-ref", "--quiet", f"refs/remotes/{remote}/HEAD"
        )
        prefix = f"refs/remotes/{remote}/"
        value = reference.decode("utf-8").strip()
        if code == 0 and value.startswith(prefix):
            default_branch = value[len(prefix) :]
    target = target_branch or default_branch or branch
    target_has_commit = None
    if target:
        code, _, _ = await _git(
            root, "rev-parse", "--verify", "--quiet", f"refs/heads/{target}^{{commit}}"
        )
        target_has_commit = code == 0
    return {
        "repository": root,
        "commonDirectory": common,
        "remotes": remotes,
        "defaultRemote": remote,
        "defaultBranch": default_branch or branch,
        "branch": branch,
        "localBranches": local_branches[:1000],
        "localBranchesTruncated": len(local_branches) > 1000,
        "remoteIdentities": identities,
        "remoteUrls": urls,
        "targetHasCommit": target_has_commit,
        "gitDirectory": (await _checked(root, "rev-parse", "--absolute-git-dir"))
        .decode("utf-8")
        .strip()
        .replace("\\", "/"),
    }


def _check_metadata(common: str) -> None:
    pending = [Path(common)]
    count = 0
    while pending:
        with os.scandir(pending.pop()) as entries:
            for entry in entries:
                count += 1
                if count > 100_000:
                    raise AgentError(
                        "operation.tooLarge", "Git metadata exceeds the inspection limit"
                    )
                metadata = entry.stat(follow_symlinks=False)
                if (
                    stat.S_ISLNK(metadata.st_mode)
                    or getattr(metadata, "st_file_attributes", 0) & 0x400
                ):
                    raise AgentError(
                        "git.worktreeFailed",
                        "Linked Git metadata is not supported for managed preparation",
                    )
                if stat.S_ISDIR(metadata.st_mode):
                    pending.append(Path(entry.path))
    alternates = Path(common) / "objects" / "info" / "alternates"
    if alternates.exists() and alternates.stat().st_size:
        raise AgentError(
            "git.worktreeFailed",
            "Alternate object directories are not supported for managed preparation",
        )


@asynccontextmanager
async def _preparation_lock(config: AgentConfig, filesystem: FilesystemAdapter):
    directory = config.data_dir / "worktree-state"
    filesystem.resolve_directory(config.data_dir.as_posix())
    lock = config.data_dir / "worktree-preparation.lock"
    if lock.is_symlink() or (
        lock.exists() and getattr(lock.lstat(), "st_file_attributes", 0) & 0x400
    ):
        raise AgentError("git.worktreeFailed", "Preparation lock must not be a link")
    descriptor = os.open(lock, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise AgentError("git.worktreeFailed", "Invalid preparation lock")
        while True:
            try:
                if os.name == "nt":
                    import msvcrt

                    os.lseek(descriptor, 0, os.SEEK_SET)
                    msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                await asyncio.sleep(0.05)
            except OSError as exc:
                if os.name != "nt" or exc.errno not in {13, 36}:
                    raise
                await asyncio.sleep(0.05)
        filesystem.mkdir(directory.as_posix(), True)
        yield directory
    finally:
        os.close(descriptor)


@asynccontextmanager
async def _locked_worktrees(config: AgentConfig, filesystem: FilesystemAdapter):
    async with _worktree_lock:
        while not filesystem._lock.acquire(blocking=False):
            await asyncio.sleep(0.01)
        try:
            async with _preparation_lock(config, filesystem) as state_directory:
                try:
                    yield state_directory
                except AgentError as exc:
                    if exc.code == "path.unavailable":
                        raise AgentError(
                            "operation.cancelled", "Git preparation outcome is unknown"
                        ) from exc
                    raise
        finally:
            filesystem._lock.release()


async def prepare_worktree(
    config: AgentConfig, filesystem: FilesystemAdapter, arguments: GitWorktreeArguments
) -> dict:
    async with _locked_worktrees(config, filesystem) as state_directory:
        return await _prepare_worktree(config, filesystem, arguments, state_directory)


def _check_worktree_session(filesystem, git_directory: str, owner: str, session: str) -> None:
    try:
        identity = filesystem._read_bytes(f"{git_directory}/matoloop-session", 128)
    except AgentError as exc:
        if exc.code != "path.notFound":
            raise
        raise AgentError("git.worktreeChanged", "The worktree session identity is missing") from exc
    if identity != f"{owner}/{session}".encode():
        raise AgentError("git.worktreeChanged", "The worktree session identity changed")


async def remove_worktree(config, filesystem, arguments: GitWorktreeRemoveArguments) -> dict:
    async with _locked_worktrees(config, filesystem) as state_directory:
        owner, project = str(UUID(arguments.owner_id)), str(UUID(arguments.project_id))
        session = str(UUID(arguments.session_id))
        record = state_directory / f"{owner}-{session}.json"
        try:
            state = json.loads(filesystem._read_bytes(record.as_posix(), 256 * 1024))
        except (ValueError, UnicodeDecodeError) as exc:
            raise AgentError("git.worktreeChanged", "Invalid preparation record") from exc
        path = state.get("path") if isinstance(state, dict) else None
        worktrees = config.data_dir / "worktrees"
        if (
            not isinstance(state, dict)
            or not state.get("ready")
            or state.get("projectId") != project
            or path
            not in (
                (worktrees / session[:8]).as_posix(),
                (worktrees / project / session).as_posix(),
            )
            or arguments.path != path
            or state.get("branch") not in (f"matoloop/{owner}/{session}", f"worktree-{session[:8]}")
            or state.get("commonDirectory") != arguments.common_directory
        ):
            raise AgentError("git.worktreeChanged", "The worktree binding changed")
        info = await repository(filesystem, state["repository"], executor=_preparation_executor)
        if info["commonDirectory"] != state["commonDirectory"]:
            raise AgentError("git.worktreeChanged", "The source repository changed")
        await asyncio.get_running_loop().run_in_executor(
            _preparation_executor, _check_metadata, state["commonDirectory"]
        )
        registered = (
            await _checked(state["repository"], "worktree", "list", "--porcelain", "-z")
        ).split(b"\0")
        if f"worktree {path}".encode() in registered:
            current = await repository(filesystem, path, executor=_preparation_executor)
            if (
                current["repository"] != path
                or current["branch"] != state["branch"]
                or current["commonDirectory"] != state["commonDirectory"]
                or current["gitDirectory"] != state.get("gitDirectory")
            ):
                raise AgentError("git.worktreeChanged", "The worktree binding changed")
            if path == (worktrees / session[:8]).as_posix():
                _check_worktree_session(filesystem, current["gitDirectory"], owner, session)
            code, _, _ = await _git(
                state["repository"],
                "worktree",
                "remove",
                "--force",
                "--",
                path,
                timeout_seconds=120,
            )
            if code:
                raise AgentError("git.worktreeRemoveFailed", "The worktree could not be removed")
        elif os.path.lexists(path):
            raise AgentError(
                "git.worktreeChanged", "An unregistered directory occupies the worktree path"
            )
        # Retain the receipt for retries after a lost response or a failed database commit.
        return {"removed": True}


async def _prepare_worktree(config, filesystem, arguments, state_directory) -> dict:
    loop = asyncio.get_running_loop()
    info = await repository(filesystem, arguments.path, executor=_preparation_executor)
    root = info["repository"]
    if root is None:
        raise AgentError("git.notRepository", "The selected directory is not a Git repository")
    selected = (
        await loop.run_in_executor(
            _preparation_executor, filesystem.resolve_directory, arguments.path
        )
    )["path"]
    if selected != root:
        raise AgentError("git.repositoryRootRequired", "Select the repository root")
    if info["commonDirectory"] != arguments.common_directory:
        raise AgentError("git.worktreeChanged", "The repository metadata location changed")
    remote_identity = None
    if arguments.remote is not None:
        if arguments.remote not in info["remotes"]:
            raise AgentError("git.remoteRequired", "The selected Git remote is unavailable")
        remote_url = await _checked(root, "remote", "get-url", "--", arguments.remote)
        remote_identity = info["remoteIdentities"][arguments.remote]
        if remote_identity != arguments.remote_identity:
            raise AgentError("git.worktreeChanged", "The remote URL changed")
        url = remote_url.decode("utf-8").strip()
        scheme = urlsplit(url).scheme
        scp_remote = (
            scheme != "file"
            and ":" in url
            and "://" not in url
            and len(url.split(":", 1)[0]) > 1
            and "/" not in url.split(":", 1)[0]
            and "\\" not in url.split(":", 1)[0]
            and not url.startswith(".")
        )
        if scheme not in {"https", "http", "ssh", "git"} and not scp_remote:
            raise AgentError(
                "git.localRemoteUnsupported", "Remote preparation requires a network remote"
            )
    await loop.run_in_executor(_preparation_executor, _check_metadata, info["commonDirectory"])
    target = f"refs/heads/{arguments.target_branch}"
    code, _, _ = await _git(root, "check-ref-format", target)
    if code:
        raise AgentError("git.invalidBranch", "Invalid target branch")
    owner = str(UUID(arguments.owner_id))
    project = str(UUID(arguments.project_id))
    session = str(UUID(arguments.session_id))
    worktrees = config.data_dir / "worktrees"
    path = (worktrees / session[:8]).as_posix()
    branch = f"worktree-{session[:8]}"
    state_path = (state_directory / f"{owner}-{session}.json").as_posix()
    try:
        state = json.loads(filesystem._read_bytes(state_path, 256 * 1024))
    except AgentError as exc:
        if exc.code != "path.notFound":
            raise
        state = None
    except (ValueError, UnicodeDecodeError) as exc:
        raise AgentError("git.worktreeChanged", "Invalid preparation record") from exc
    if state is not None:
        if (
            not isinstance(state, dict)
            or state.get("path") not in (path, (worktrees / project / session).as_posix())
            or state.get("branch") not in (branch, f"matoloop/{owner}/{session}")
        ):
            raise AgentError("git.worktreeChanged", "Invalid preparation binding")
        path, branch = state["path"], state["branch"]
    # Older Backends authorize only the original project/session destination.
    destinations = arguments.destinations or [(worktrees / project / session).as_posix()]
    if path not in destinations:
        raise AgentError("git.worktreeChanged", "The expected worktree path changed")
    if path == root or path.startswith(root.rstrip("/") + "/"):
        raise AgentError(
            "git.worktreeLocation", "Managed worktrees must be outside the source repository"
        )
    base_ref = f"refs/matoloop/bases/{owner}/{session}"
    binding = {
        "repository": root,
        "commonDirectory": info["commonDirectory"],
        "path": path,
        "branch": branch,
        "remote": arguments.remote,
        "remoteIdentity": remote_identity,
        "targetBranch": arguments.target_branch,
        "projectId": project,
    }
    if state is not None and (
        not isinstance(state, dict)
        or any(state.get(key) != value for key, value in binding.items())
    ):
        raise AgentError("git.worktreeChanged", "Preparation inputs changed")
    parent = path.rsplit("/", 1)[0]
    filesystem.mkdir(parent, True)
    # Recover only the exact registered worktree; never adopt or reset an unrelated directory.
    registered = (await _checked(root, "worktree", "list", "--porcelain", "-z")).split(b"\0")
    exists = f"worktree {path}".encode() in registered
    if state is None:
        if exists or os.path.lexists(path):
            raise AgentError(
                "git.worktreeChanged", "The destination has no matching preparation record"
            )
        state = {**binding, "ready": False, "baseCommit": None}
        filesystem.upsert(state_path, json.dumps(state))
    if exists:
        current = await repository(filesystem, path, executor=_preparation_executor)
        git_dir = (
            (await _checked(path, "rev-parse", "--absolute-git-dir"))
            .decode()
            .strip()
            .replace("\\", "/")
        )
        if (
            not state.get("ready")
            or current["repository"] != path
            or current["branch"] != branch
            or current["commonDirectory"] != info["commonDirectory"]
            or git_dir != state.get("gitDirectory")
        ):
            raise AgentError("git.worktreeChanged", "The session worktree binding changed")
        if path == (worktrees / session[:8]).as_posix():
            _check_worktree_session(filesystem, git_dir, owner, session)
    elif state.get("ready"):
        raise AgentError("git.worktreeChanged", "The completed worktree is no longer registered")
    if state.get("baseCommit") is None:
        if arguments.remote is None:
            code, commit, _ = await _git(root, "rev-parse", "--verify", f"{target}^{{commit}}")
            if code:
                raise AgentError(
                    "git.localBranchRequired", "The local target branch must contain a commit"
                )
            await _checked(root, "update-ref", base_ref, commit.decode().strip())
        else:
            code, _, _ = await _git(
                root,
                "fetch",
                "--no-tags",
                "--no-write-fetch-head",
                "--",
                arguments.remote,
                f"{target}:{base_ref}",
                network=True,
            )
            if code:
                raise AgentError("git.fetchFailed", "The target branch could not be fetched")
    base = (
        (await _checked(root, "rev-parse", "--verify", f"{base_ref}^{{commit}}")).decode().strip()
    )
    if state.get("baseCommit") not in {None, base}:
        raise AgentError("git.worktreeChanged", "The preparation base changed")
    state["baseCommit"] = base
    if not exists:
        code, previous, _ = await _git(
            root, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"
        )
        if code == 0 and (
            branch == f"worktree-{session[:8]}"
            or previous.decode().strip() != base
            or f"branch refs/heads/{branch}".encode() in registered
        ):
            raise AgentError("git.worktreeChanged", "The preparation branch changed or is in use")
        if code not in {0, 1}:
            raise AgentError("git.worktreeFailed", "The preparation branch could not be inspected")
        if os.path.lexists(path):
            filesystem.resolve_directory(path)
            if any(Path(path).iterdir()):
                raise AgentError("git.worktreeChanged", "The incomplete destination contains files")
        else:
            # Git checks out as the Agent process, not the data directory's owner.
            filesystem.mkdir(path, False, inherit_ownership=False)
        filesystem.resolve_directory(path)
        filesystem.upsert(state_path, json.dumps(state))
        code, _, _ = await _git(
            root,
            "worktree",
            "add",
            "--no-checkout",
            *(("-b", branch) if code == 1 else ()),
            "--",
            path,
            base if code == 1 else branch,
            timeout_seconds=120,
        )
        if not code:
            code, _, _ = await _git(
                path,
                "read-tree",
                "--reset",
                "-u",
                "HEAD",
                config=await _disabled_filters(path),
                timeout_seconds=120,
            )
        if code:
            raise AgentError("git.worktreeFailed", "The worktree could not be prepared")
        state["gitDirectory"] = (
            (await _checked(path, "rev-parse", "--absolute-git-dir"))
            .decode()
            .strip()
            .replace("\\", "/")
        )
        filesystem.upsert(f"{state['gitDirectory']}/matoloop-session", f"{owner}/{session}")
        state["ready"] = True
        filesystem.upsert(state_path, json.dumps(state))
    await loop.run_in_executor(_preparation_executor, filesystem.resolve_directory, path)
    return {
        "repository": root,
        "path": path,
        "branch": branch,
        "baseCommit": base,
        "remote": arguments.remote,
        "targetBranch": arguments.target_branch,
        "remoteIdentity": remote_identity,
        "commonDirectory": info["commonDirectory"],
        "gitDirectory": state["gitDirectory"],
    }
