from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import os
import posixpath
import shutil
import socket
import stat
import tempfile
from urllib.parse import unquote, urlsplit

from .config import AgentConfig
from .filesystem import AgentError, Filesystem, _parts, _relative_parts, _rename_no_replace
from .process import ProcessRunner

GIT_OPERATIONS = {
    "integration.git.clone",
    "integration.git.status",
    "integration.git.diff",
    "integration.git.log",
    "integration.git.add",
    "integration.git.commit",
}


class GitRunner:
    def __init__(self, config: AgentConfig, filesystem: Filesystem) -> None:
        self.filesystem = filesystem
        self.allow_network = config.allow_network
        self.max_output = min(config.max_output_bytes, 512 * 1024)
        self.timeout = config.default_timeout_seconds
        self._index_lock = asyncio.Lock()
        self.home = config.data_dir / "git-home"
        self.home.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.home.chmod(0o700)
        self.executable = None
        if config.git_executable is not None:
            try:
                self.executable = ProcessRunner._resolve_executable(config.git_executable)
            except AgentError as exc:
                raise ValueError("configured Git executable is unavailable") from exc

    def _directory_fd(self, current_directory: str) -> int:
        self.filesystem.resolve_directory(current_directory)
        with self.filesystem._root_fd() as root_fd:
            return self.filesystem._open_dir(root_fd, _parts(current_directory))

    @staticmethod
    def _repository_fd(cwd_fd: int) -> int:
        try:
            git_fd = os.open(
                ".git", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=cwd_fd
            )
            if not stat.S_ISDIR(os.fstat(git_fd).st_mode):
                raise OSError
            return git_fd
        except OSError as exc:
            raise AgentError(
                "git.notRepository", "Current directory is not a repository root"
            ) from exc

    @staticmethod
    def _validate_repository_metadata(git_fd: int) -> None:
        try:
            for _root, directories, files, directory_fd in os.fwalk(
                ".", topdown=True, follow_symlinks=False, dir_fd=git_fd
            ):
                for name in (*directories, *files):
                    if stat.S_ISLNK(
                        os.stat(name, dir_fd=directory_fd, follow_symlinks=False).st_mode
                    ):
                        raise AgentError(
                            "git.unsafeRepository", "Repository metadata contains a symlink"
                        )
            for path in ("commondir", "objects/info/alternates", "objects/info/http-alternates"):
                try:
                    os.stat(path, dir_fd=git_fd, follow_symlinks=False)
                except FileNotFoundError:
                    continue
                raise AgentError(
                    "git.unsafeRepository", "Repository metadata redirects outside the repository"
                )
        except AgentError:
            raise
        except OSError as exc:
            raise AgentError(
                "git.unsafeRepository", "Repository metadata could not be validated"
            ) from exc

    @staticmethod
    def _paths(paths: list[str]) -> list[str]:
        for path in paths:
            _relative_parts(path)
        return paths

    def _environment(self, cwd_fd: int, git_fd: int | None, protocol: str) -> dict[str, str]:
        environment = {
            "HOME": os.fspath(self.home),
            "LANG": "C.UTF-8",
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_TERMINAL_PROMPT": "0",
            "GCM_INTERACTIVE": "Never",
            "GIT_PAGER": "cat",
            "PAGER": "cat",
            "GIT_EDITOR": "true",
            "GIT_SEQUENCE_EDITOR": "true",
            "GIT_ALLOW_PROTOCOL": protocol,
            "GIT_OPTIONAL_LOCKS": "0",
        }
        if git_fd is not None:
            environment["GIT_DIR"] = f"/proc/self/fd/{git_fd}"
            environment["GIT_WORK_TREE"] = f"/proc/self/fd/{cwd_fd}"
        return environment

    async def _run(
        self,
        argv: list[str],
        cwd_fd: int,
        *,
        git_fd: int | None = None,
        protocol: str = "file",
        error_code: str = "git.failed",
        extra_fds: tuple[int, ...] = (),
        allowed_returncodes: tuple[int, ...] = (0,),
        mutation: bool = False,
    ) -> tuple[str, bool]:
        if self.executable is None:
            raise AgentError("operation.unsupported", "Git is not configured")
        command = [
            os.fspath(self.executable),
            "-c",
            "core.hooksPath=/dev/null",
            "-c",
            "core.fsmonitor=false",
            "-c",
            "core.attributesFile=/dev/null",
            "-c",
            "core.excludesFile=/dev/null",
            "-c",
            "commit.gpgSign=false",
            "-c",
            "tag.gpgSign=false",
            "-c",
            "core.pager=cat",
            "-c",
            "interactive.diffFilter=",
            "-c",
            "submodule.recurse=false",
            "-c",
            "http.followRedirects=false",
            "-c",
            "http.proxy=",
            "-c",
            "credential.helper=",
            "-c",
            "log.mailmap=false",
            "--literal-pathspecs",
            *argv,
        ]
        pass_fds = (cwd_fd, *extra_fds) if git_fd is None else (cwd_fd, git_fd, *extra_fds)
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                cwd=f"/proc/self/fd/{cwd_fd}",
                env=self._environment(cwd_fd, git_fd, protocol),
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
                pass_fds=pass_fds,
            )
        except OSError as exc:
            raise AgentError("git.startFailed", "Git could not be started") from exc

        output = bytearray()
        total = 0
        truncated = False

        async def drain(reader: asyncio.StreamReader, *, include: bool) -> None:
            nonlocal total, truncated
            while chunk := await reader.read(16 * 1024):
                remaining = self.max_output - total
                if remaining > 0:
                    accepted = chunk[:remaining]
                    total += len(accepted)
                    if include:
                        output.extend(accepted)
                if len(chunk) > remaining:
                    truncated = True

        drain_tasks = [
            asyncio.create_task(drain(process.stdout, include=True)),
            asyncio.create_task(drain(process.stderr, include=False)),
        ]
        try:
            async with asyncio.timeout(self.timeout):
                await process.wait()
                await asyncio.gather(*drain_tasks)
        except TimeoutError as exc:
            await ProcessRunner._terminate(process)
            await ProcessRunner._finish_drains(process, drain_tasks)
            raise AgentError(
                "git.outcomeUnknown" if mutation else "git.timedOut",
                "Git operation timed out",
            ) from exc
        except asyncio.CancelledError:
            await ProcessRunner._terminate(process)
            await ProcessRunner._finish_drains(process, drain_tasks)
            raise
        except BaseException:
            await ProcessRunner._terminate(process)
            await ProcessRunner._finish_drains(process, drain_tasks)
            raise
        if process.returncode not in allowed_returncodes:
            raise AgentError(
                "git.outcomeUnknown" if mutation else error_code, "Git operation failed"
            )
        return output.decode("utf-8", errors="replace"), truncated

    async def _repository_run(
        self,
        current_directory: str,
        argv: list[str],
        *,
        error_code: str = "git.failed",
        allowed_returncodes: tuple[int, ...] = (0,),
        mutation: bool = False,
    ) -> tuple[str, bool]:
        cwd_fd = self._directory_fd(current_directory)
        try:
            git_fd = self._repository_fd(cwd_fd)
            try:
                self._validate_repository_metadata(git_fd)
                return await self._run(
                    argv,
                    cwd_fd,
                    git_fd=git_fd,
                    error_code=error_code,
                    allowed_returncodes=allowed_returncodes,
                    mutation=mutation,
                )
            finally:
                os.close(git_fd)
        finally:
            os.close(cwd_fd)

    def _index_revision(self, current_directory: str) -> str:
        cwd_fd = self._directory_fd(current_directory)
        try:
            git_fd = self._repository_fd(cwd_fd)
            try:
                self._validate_repository_metadata(git_fd)
                try:
                    index_fd = os.open(
                        "index", os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=git_fd
                    )
                except FileNotFoundError:
                    return hashlib.sha256(b"").hexdigest()
                try:
                    before = os.fstat(index_fd)
                    digest = hashlib.sha256()
                    while chunk := os.read(index_fd, 1024 * 1024):
                        digest.update(chunk)
                    after = os.fstat(index_fd)
                    current = os.stat("index", dir_fd=git_fd, follow_symlinks=False)
                    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
                        after.st_dev,
                        after.st_ino,
                        after.st_size,
                        after.st_mtime_ns,
                    ) or (before.st_dev, before.st_ino) != (current.st_dev, current.st_ino):
                        raise AgentError("git.indexChanged", "Git index changed while reading")
                    return digest.hexdigest()
                finally:
                    os.close(index_fd)
            finally:
                os.close(git_fd)
        finally:
            os.close(cwd_fd)

    async def clone(
        self,
        current_directory: str,
        source: str,
        destination: str,
        allow_network: bool,
    ) -> dict:
        destination_parts = _relative_parts(destination)
        if not destination_parts:
            raise AgentError("git.invalidDestination", "Clone destination must be absent")
        cwd_fd = self._directory_fd(current_directory)
        source_fd = None
        source_git_fd = None
        parent_fd = None
        temporary = None
        try:
            parent_fd = self.filesystem._open_dir(cwd_fd, destination_parts[:-1])
            parsed = urlsplit(source)
            if parsed.scheme == "https":
                if not self.allow_network or not allow_network:
                    raise AgentError("git.networkDenied", "Network cloning is disabled")
                try:
                    async with asyncio.timeout(self.timeout):
                        clone_source, curl_resolve = await asyncio.to_thread(
                            self._https_source, source
                        )
                except TimeoutError as exc:
                    raise AgentError("git.timedOut", "Git source resolution timed out") from exc
                protocol = "https"
            else:
                if parsed.scheme and parsed.scheme != "file":
                    raise AgentError("git.invalidSource", "Unsupported clone source")
                local_source = self._local_source(source, current_directory)
                source_fd = self._directory_fd(local_source["path"])
                source_git_fd = self._repository_fd(source_fd)
                self._validate_repository_metadata(source_git_fd)
                clone_source = f"/proc/self/fd/{source_git_fd}"
                protocol = "file"

            temporary_path = tempfile.mkdtemp(prefix=".mato-git-", dir=f"/proc/self/fd/{parent_fd}")
            temporary = os.path.basename(temporary_path)
            target = f"/proc/self/fd/{parent_fd}/{temporary}"
            argv = ["clone"]
            if parsed.scheme == "https":
                argv[:0] = ["-c", f"http.curloptResolve={curl_resolve}"]
            output, truncated = await self._run(
                [
                    *argv,
                    "--no-recurse-submodules",
                    "--no-hardlinks",
                    "--template=",
                    "--config",
                    "submodule.recurse=false",
                    "--",
                    clone_source,
                    target,
                ],
                cwd_fd,
                protocol=protocol,
                error_code="git.cloneFailed",
                extra_fds=(parent_fd,) if source_git_fd is None else (parent_fd, source_git_fd),
            )
            target_fd = os.open(
                temporary,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                dir_fd=parent_fd,
            )
            try:
                target_git_fd = self._repository_fd(target_fd)
                try:
                    self._validate_repository_metadata(target_git_fd)
                finally:
                    os.close(target_git_fd)
            finally:
                os.close(target_fd)
            _rename_no_replace(parent_fd, temporary, parent_fd, destination_parts[-1])
            temporary = None
            return {"output": output, "truncated": truncated}
        finally:
            if source_git_fd is not None:
                os.close(source_git_fd)
            if source_fd is not None:
                os.close(source_fd)
            if parent_fd is not None:
                if temporary is not None:
                    shutil.rmtree(temporary, dir_fd=parent_fd, ignore_errors=True)
                os.close(parent_fd)
            os.close(cwd_fd)

    def _local_source(self, source: str, current_directory: str) -> dict:
        parsed = urlsplit(source)
        if parsed.scheme == "file":
            if parsed.netloc not in {"", "localhost"} or parsed.query or parsed.fragment:
                raise AgentError("git.invalidSource", "Invalid local clone source")
            source = unquote(parsed.path)
            if not source.startswith("/"):
                raise AgentError("git.invalidSource", "File clone source must be absolute")
        try:
            absolute = posixpath.normpath(
                source if source.startswith("/") else f"{current_directory}/{source}"
            )
            return self.filesystem.resolve_directory(absolute)
        except AgentError as exc:
            raise AgentError("git.invalidSource", "Local clone source is unavailable") from exc

    @staticmethod
    def _https_source(source: str) -> tuple[str, str]:
        try:
            parsed = urlsplit(source)
            port = parsed.port or 443
        except ValueError as exc:
            raise AgentError("git.invalidSource", "Invalid HTTPS clone source") from exc
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or parsed.path in {"", "/"}
        ):
            raise AgentError("git.invalidSource", "Invalid HTTPS clone source")
        try:
            addresses = {
                ipaddress.ip_address(item[4][0])
                for item in socket.getaddrinfo(parsed.hostname, port, type=socket.SOCK_STREAM)
            }
        except (OSError, ValueError) as exc:
            raise AgentError(
                "git.networkAddressDenied", "Clone host could not be validated"
            ) from exc
        if not addresses or any(not address.is_global for address in addresses):
            raise AgentError("git.networkAddressDenied", "Clone host address is not allowed")
        address = sorted(addresses, key=lambda value: (value.version, value.packed))[0]
        rendered_address = f"[{address}]" if address.version == 6 else str(address)
        return source, f"{parsed.hostname}:{port}:{rendered_address}"

    async def status(self, current_directory: str) -> dict:
        async with self._index_lock:
            output, truncated = await self._repository_run(
                current_directory,
                ["status", "--porcelain=v1", "--branch", "--untracked-files=normal"],
                error_code="git.statusFailed",
            )
            return {
                "output": output,
                "truncated": truncated,
                "indexTree": self._index_revision(current_directory),
            }

    async def diff(self, current_directory: str, staged: bool, paths: list[str]) -> dict:
        argv = ["diff", "--no-ext-diff", "--no-textconv", "--no-renames"]
        if staged:
            argv.append("--cached")
        if paths:
            argv.extend(["--", *self._paths(paths)])
        output, truncated = await self._repository_run(
            current_directory, argv, error_code="git.diffFailed"
        )
        return {"output": output, "truncated": truncated}

    async def log(self, current_directory: str, limit: int, paths: list[str]) -> dict:
        argv = [
            "log",
            "--no-decorate",
            "--date=iso-strict",
            "--pretty=format:%H%x09%aI%x09%an%x09%s",
            f"--max-count={limit}",
        ]
        if paths:
            argv.extend(["--", *self._paths(paths)])
        output, truncated = await self._repository_run(
            current_directory, argv, error_code="git.logFailed"
        )
        return {"output": output, "truncated": truncated}

    async def add(self, current_directory: str, paths: list[str]) -> dict:
        async with self._index_lock:
            unsafe, _ = await self._repository_run(
                current_directory,
                [
                    "config",
                    "--no-includes",
                    "--name-only",
                    "--get-regexp",
                    r"^(filter\..*\.(clean|process|required)|include(if)?\..*)$",
                ],
                allowed_returncodes=(0, 1),
            )
            if unsafe:
                raise AgentError(
                    "git.unsafeRepository", "Repository config can execute staging filters"
                )
            await self._repository_run(
                current_directory,
                ["add", "--", *self._paths(paths)],
                error_code="git.addFailed",
                mutation=True,
            )
            return {"indexTree": self._index_revision(current_directory)}

    async def commit(
        self,
        current_directory: str,
        message: str,
        expected_index_tree: str,
    ) -> dict:
        async with self._index_lock:
            if self._index_revision(current_directory) != expected_index_tree:
                raise AgentError("git.indexChanged", "Git index changed before commit")
            await self._repository_run(
                current_directory,
                ["commit", "--no-verify", "--no-gpg-sign", "--cleanup=verbatim", "-m", message],
                error_code="git.commitFailed",
                mutation=True,
            )
            try:
                commit, _ = await self._repository_run(
                    current_directory, ["rev-parse", "--verify", "HEAD^{commit}"]
                )
                tree, _ = await self._repository_run(
                    current_directory, ["rev-parse", "--verify", "HEAD^{tree}"]
                )
            except AgentError as exc:
                raise AgentError(
                    "git.outcomeUnknown", "Commit succeeded but could not be verified"
                ) from exc
            return {"commit": commit.strip(), "tree": tree.strip()}
