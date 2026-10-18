"""Bounded, read-only Git working-tree inspection for the Control Center."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
from pathlib import PurePosixPath

from .errors import AgentError
from .platform import FilesystemAdapter
from .protocol import _relative_path

MAX_TEXT_BYTES = 64 * 1024
MAX_OUTPUT_BYTES = 512 * 1024
MAX_FILES = 500


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
) -> tuple[int, bytes, bytes]:
    executable = shutil.which("git")
    if not executable:
        raise AgentError("operation.unsupported", "Git is not installed")
    environment = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    environment.update(
        GIT_OPTIONAL_LOCKS="0",
        GIT_TERMINAL_PROMPT="0",
        LC_ALL="C",
        GIT_ALLOW_PROTOCOL="",
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
        *arguments,
        cwd=directory,
        env=environment,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    readers = [
        asyncio.create_task(_read(process.stdout, limit)),
        asyncio.create_task(_read(process.stderr, 8 * 1024)),
    ]
    try:
        async with asyncio.timeout(15):
            output, error = await asyncio.gather(*readers)
            return await process.wait(), output, error
    except TimeoutError as exc:
        raise AgentError("path.unavailable", "Git inspection timed out") from exc
    finally:
        if process.returncode is None:
            process.kill()
        for reader in readers:
            reader.cancel()
        await asyncio.gather(*readers, return_exceptions=True)
        await process.communicate()


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
        r"^filter\..*\.(clean|process|required)$",
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
        for option, value in (("clean", ""), ("process", ""), ("required", "false"))
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
