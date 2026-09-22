from __future__ import annotations

import asyncio
import json
import os
import shutil
import stat
import sys
from collections.abc import Callable
from pathlib import Path, PurePosixPath

from .errors import AgentError
from .filesystem import Filesystem, _parts, _path
from .process import ProcessRunner

MAX_RESULTS = 100
MAX_MATCH_TEXT = 2000
MAX_RECORD_BYTES = 256 * 1024
MAX_STDERR_BYTES = 64 * 1024
MAX_OUTPUT_BYTES = 8 * 1024 * 1024
SEARCH_TIMEOUT_SECONDS = 10


class FilesystemSearch:
    def __init__(self, filesystem: Filesystem) -> None:
        self.filesystem = filesystem
        self.executable = self._find_ripgrep()

    @staticmethod
    def _find_ripgrep() -> Path | None:
        sidecar = Path(sys.executable).absolute().parent / "rg"
        system = shutil.which("rg")
        for candidate in (sidecar, Path(system) if system else None):
            if candidate is None:
                continue
            try:
                resolved = candidate.resolve(strict=True)
                metadata = resolved.stat(follow_symlinks=False)
            except OSError:
                continue
            if stat.S_ISREG(metadata.st_mode) and os.access(resolved, os.X_OK):
                return resolved
        return None

    def _cwd(self, path: str) -> tuple[str, int]:
        parts = _parts(path)
        with self.filesystem._root_fd() as root_fd:
            directory_fd = self.filesystem._open_dir(root_fd, parts)
        return f"/proc/self/fd/{directory_fd}", directory_fd

    @staticmethod
    def _path(value: str) -> str | None:
        while value.startswith("./"):
            value = value[2:]
        path = PurePosixPath(value)
        if (
            not value
            or len(value) > 1000
            or path.is_absolute()
            or any(part in {"", ".", "..", ".git"} for part in path.parts)
        ):
            return None
        return path.as_posix()

    @staticmethod
    def _result_path(base: tuple[str, ...], path: str) -> str | None:
        relative = PurePosixPath(path)
        if relative.is_absolute() or any(part in {"", ".", ".."} for part in path.split("/")):
            return None
        result = _path((*base, *relative.parts))
        return result if len(result) <= 4096 else None

    async def glob(self, path: str, pattern: str) -> dict:
        base = _parts(path)
        if ".git" in base:
            return {"paths": [], "truncated": False}
        paths: list[str] = []
        response_bytes = 32

        def consume(record: bytes) -> bool:
            nonlocal response_bytes
            try:
                path = self._path(record.decode("utf-8"))
            except UnicodeDecodeError:
                return False
            if path is None or any(part.startswith(".") for part in PurePosixPath(path).parts):
                return False
            result = self._result_path(base, path)
            if result is None:
                return False
            if len(paths) >= MAX_RESULTS:
                return True
            size = len(json.dumps(result, ensure_ascii=False).encode("utf-8")) + 1
            if response_bytes + size > self.filesystem.max_response_bytes:
                return True
            paths.append(result)
            response_bytes += size
            return False

        return_code, truncated = await self._run(
            path,
            [
                "--no-config",
                "--files",
                "--null",
                "--no-require-git",
                "--no-follow",
                "--type-clear",
                "matoloop",
                "--type-add",
                f"matoloop:{pattern}",
                "--type",
                "matoloop",
                "--glob",
                "!.git",
                "--glob",
                "!.git/**",
            ],
            b"\0",
            consume,
        )
        if return_code not in {0, 1} and not truncated:
            raise AgentError("operation.failed", "Glob search failed")
        paths.sort()
        return {"paths": paths, "truncated": truncated}

    async def grep(self, path: str, pattern: str, include: str | None) -> dict:
        base = _parts(path)
        if ".git" in base:
            return {"matches": [], "truncated": False}
        kind = self.filesystem.stat(path)["kind"]
        if kind not in {"file", "directory"}:
            raise AgentError("path.unsupportedType", "Search requires a regular file or directory")
        is_file = kind == "file"
        matches: list[dict[str, str | int]] = []
        response_bytes = 32
        text_clipped = False
        file_truncated = False
        binary = False

        def consume(record: bytes) -> bool:
            nonlocal response_bytes, text_clipped, file_truncated, binary
            try:
                message = json.loads(record)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise AgentError("operation.failed", "Invalid ripgrep output") from exc
            data = message.get("data", {})
            if is_file and message.get("type") == "end":
                binary |= data.get("binary_offset") is not None
            if message.get("type") != "match":
                return False
            path_data = data.get("path", {})
            lines_data = data.get("lines", {})
            result_path = self._path(path_data.get("text", ""))
            text = lines_data.get("text")
            line = data.get("line_number")
            if not isinstance(text, str) or not isinstance(line, int) or line < 1:
                return False
            result = (
                path if is_file else self._result_path(base, result_path) if result_path else None
            )
            if result is None:
                return False
            if "\0" in text:
                if is_file:
                    binary = True
                return False
            if len(matches) >= MAX_RESULTS:
                file_truncated = True
                return not is_file
            text = text.rstrip("\r\n")
            match = {
                "path": result,
                "line": line,
                "text": text[:MAX_MATCH_TEXT],
            }
            size = len(json.dumps(match, ensure_ascii=False).encode("utf-8")) + 1
            if response_bytes + size > self.filesystem.max_response_bytes:
                file_truncated = True
                return not is_file
            matches.append(match)
            response_bytes += size
            text_clipped |= len(text) > MAX_MATCH_TEXT
            return False

        arguments = [
            "--no-config",
            "--json",
            "--no-require-git",
            "--no-follow",
            "--binary" if is_file else "--no-binary",
            "--glob",
            "!.*",
            "--glob",
            "!**/.*",
            "--glob",
            "!**/.*/**",
        ]
        if include is not None and not is_file:
            arguments += [
                "--type-clear",
                "matoloop",
                "--type-add",
                f"matoloop:{include}",
                "--type",
                "matoloop",
            ]
        arguments += [
            "--glob",
            "!.git",
            "--glob",
            "!.git/**",
            "--regexp",
            pattern,
            "-" if is_file else ".",
        ]
        if is_file:
            arguments = [f"--max-count={MAX_RESULTS + 1}", *arguments]
            # Open once with no-follow and regular-file validation; never reopen the filename in rg.
            with self.filesystem._parent(path) as (parent_fd, name, _parts_value):
                fd, _metadata = self.filesystem._open_regular(parent_fd, name)
            try:
                return_code, truncated = await self._run(
                    _path(base[:-1]), arguments, b"\n", consume, input_fd=fd
                )
            finally:
                os.close(fd)
        else:
            return_code, truncated = await self._run(path, arguments, b"\n", consume)
        if return_code == 2 and not truncated:
            raise AgentError("request.invalid", "Invalid grep pattern")
        if return_code not in {0, 1} and not truncated:
            raise AgentError("operation.failed", "Grep search failed")
        if binary:
            return {"matches": [], "truncated": False}
        matches.sort(key=lambda match: (match["path"], match["line"]))
        return {"matches": matches, "truncated": truncated or text_clipped or file_truncated}

    async def _run(
        self,
        path: str,
        arguments: list[str],
        delimiter: bytes,
        consume: Callable[[bytes], bool],
        *,
        input_fd: int | None = None,
    ) -> tuple[int, bool]:
        if self.executable is None:
            raise AgentError("operation.unsupported", "Ripgrep is unavailable")
        cwd, cwd_fd = self._cwd(path)
        try:
            process = await asyncio.create_subprocess_exec(
                os.fspath(self.executable),
                *arguments,
                cwd=cwd,
                env={"LANG": os.environ.get("LANG", "C.UTF-8")},
                stdin=input_fd if input_fd is not None else asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
                pass_fds=(cwd_fd,),
            )
        except OSError as exc:
            raise AgentError("operation.failed", "Ripgrep could not be started") from exc
        finally:
            os.close(cwd_fd)

        async def drain_stderr() -> None:
            total = 0
            while chunk := await process.stderr.read(16 * 1024):
                total += len(chunk)
                if total > MAX_STDERR_BYTES:
                    raise AgentError("operation.tooLarge", "Search error output is too large")

        async def read_stdout() -> bool:
            total = 0
            buffer = bytearray()
            while chunk := await process.stdout.read(16 * 1024):
                total += len(chunk)
                if total > MAX_OUTPUT_BYTES:
                    raise AgentError("operation.tooLarge", "Search output is too large")
                buffer.extend(chunk)
                while (index := buffer.find(delimiter)) >= 0:
                    record = bytes(buffer[:index])
                    del buffer[: index + len(delimiter)]
                    if len(record) > MAX_RECORD_BYTES:
                        raise AgentError("operation.tooLarge", "Search record is too large")
                    if record and consume(record):
                        return True
                if len(buffer) > MAX_RECORD_BYTES:
                    raise AgentError("operation.tooLarge", "Search record is too large")
            return bool(buffer and consume(bytes(buffer)))

        stderr_task = asyncio.create_task(drain_stderr())
        try:
            async with asyncio.timeout(SEARCH_TIMEOUT_SECONDS):
                truncated = await read_stdout()
                if truncated:
                    await ProcessRunner._terminate(process)
                else:
                    await process.wait()
                await stderr_task
        except TimeoutError as exc:
            await ProcessRunner._terminate(process)
            await asyncio.gather(stderr_task, return_exceptions=True)
            raise AgentError("operation.timedOut", "Search timed out") from exc
        except asyncio.CancelledError:
            await ProcessRunner._terminate(process)
            await asyncio.gather(stderr_task, return_exceptions=True)
            raise
        except BaseException:
            await ProcessRunner._terminate(process)
            await asyncio.gather(stderr_task, return_exceptions=True)
            raise
        return process.returncode, truncated
