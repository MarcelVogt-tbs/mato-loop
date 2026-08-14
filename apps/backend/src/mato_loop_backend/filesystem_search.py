from __future__ import annotations

import asyncio
import json
import os
import shutil
from collections.abc import Callable
from pathlib import PurePosixPath

from mato_loop_backend.workspace import FileRoot, RootFileError, RootFileService

MAX_SEARCH_RESULTS = 100
MAX_MATCH_TEXT = 2_000
_MAX_RECORD_BYTES = 256 * 1024
_MAX_STDERR_BYTES = 64 * 1024
_MAX_OUTPUT_BYTES = 8 * 1024 * 1024
_SEARCH_TIMEOUT_SECONDS = 10


class RootFileSearch:
    def __init__(self, root_files: RootFileService) -> None:
        self.root_files = root_files
        self.executable = shutil.which("rg")

    @staticmethod
    def _path(base: tuple[str, ...], value: str) -> str | None:
        while value.startswith("./"):
            value = value[2:]
        path = PurePosixPath(value)
        if (
            not value
            or path.is_absolute()
            or any(part in {"", ".", "..", ".git"} for part in path.parts)
        ):
            return None
        combined = (*base, *path.parts)
        result = "/".join(combined)
        return result if len(result) <= 1_000 else None

    async def glob(self, root: FileRoot | str, path: str, pattern: str) -> dict:
        base = self.root_files.path_parts(path)
        paths: list[str] = []

        def consume(record: bytes) -> bool:
            try:
                result = self._path(base, record.decode("utf-8"))
            except UnicodeDecodeError:
                return False
            if result is None or any(
                part.startswith(".") for part in PurePosixPath(result).parts[len(base) :]
            ):
                return False
            paths.append(result)
            return len(paths) == MAX_SEARCH_RESULTS

        return_code, truncated = await self._run(
            root,
            path,
            [
                "--no-config",
                "--files",
                "--null",
                "--no-require-git",
                "--no-follow",
                f"--glob={pattern}",
                "--glob=!.git",
                "--glob=!.git/**",
                ".",
            ],
            b"\0",
            consume,
        )
        if return_code != 0 and not truncated:
            raise RootFileError("tool.executionFailed", "Glob search failed")
        paths.sort()
        return {"paths": paths, "truncated": truncated}

    async def grep(
        self,
        root: FileRoot | str,
        path: str,
        pattern: str,
        include: str | None,
    ) -> dict:
        base = self.root_files.path_parts(path)
        matches: list[dict[str, str | int]] = []

        def consume(record: bytes) -> bool:
            try:
                message = json.loads(record)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise RootFileError("tool.executionFailed", "Invalid ripgrep output") from exc
            if message.get("type") != "match":
                return False
            data = message.get("data", {})
            result = self._path(base, data.get("path", {}).get("text", ""))
            text = data.get("lines", {}).get("text")
            line = data.get("line_number")
            if result is None or not isinstance(text, str) or not isinstance(line, int) or line < 1:
                return False
            matches.append(
                {"path": result, "line": line, "text": text.rstrip("\r\n")[:MAX_MATCH_TEXT]}
            )
            return len(matches) == MAX_SEARCH_RESULTS

        arguments = [
            "--no-config",
            "--json",
            "--hidden",
            "--no-require-git",
            "--no-follow",
            "--no-binary",
            "--glob=!.git",
            "--glob=!.git/**",
        ]
        if include is not None:
            arguments.append(f"--glob={include}")
        arguments += ["--", pattern, "."]
        return_code, truncated = await self._run(root, path, arguments, b"\n", consume)
        if return_code == 2 and not truncated:
            raise RootFileError("tool.invalidArguments", "Invalid grep pattern")
        if return_code not in {0, 1} and not truncated:
            raise RootFileError("tool.executionFailed", "Grep search failed")
        matches.sort(key=lambda match: (match["path"], match["line"]))
        return {"matches": matches, "truncated": truncated}

    async def _run(
        self,
        root: FileRoot | str,
        path: str,
        arguments: list[str],
        delimiter: bytes,
        consume: Callable[[bytes], bool],
    ) -> tuple[int, bool]:
        if self.executable is None:
            raise RootFileError("tool.unavailable", "Ripgrep is unavailable")
        with self.root_files.open_directory_descriptor(root, path) as directory_fd:
            try:
                process = await asyncio.create_subprocess_exec(
                    self.executable,
                    *arguments,
                    cwd=f"/proc/self/fd/{directory_fd}",
                    env={"LANG": os.environ.get("LANG", "C.UTF-8")},
                    stdin=asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    start_new_session=True,
                    pass_fds=(directory_fd,),
                )
            except OSError as exc:
                raise RootFileError("tool.executionFailed", "Ripgrep could not be started") from exc

        async def drain_stderr() -> None:
            total = 0
            while chunk := await process.stderr.read(16 * 1024):
                total += len(chunk)
                if total > _MAX_STDERR_BYTES:
                    raise RootFileError("file.operationTooLarge", "Search error output is too large")

        async def read_stdout() -> bool:
            total = 0
            buffer = bytearray()
            while chunk := await process.stdout.read(16 * 1024):
                total += len(chunk)
                if total > _MAX_OUTPUT_BYTES:
                    raise RootFileError("file.operationTooLarge", "Search output is too large")
                buffer.extend(chunk)
                while (index := buffer.find(delimiter)) >= 0:
                    record = bytes(buffer[:index])
                    del buffer[: index + len(delimiter)]
                    if len(record) > _MAX_RECORD_BYTES:
                        raise RootFileError("file.operationTooLarge", "Search record is too large")
                    if record and consume(record):
                        return True
                if len(buffer) > _MAX_RECORD_BYTES:
                    raise RootFileError("file.operationTooLarge", "Search record is too large")
            return bool(buffer and consume(bytes(buffer)))

        stderr_task = asyncio.create_task(drain_stderr())
        try:
            async with asyncio.timeout(_SEARCH_TIMEOUT_SECONDS):
                truncated = await read_stdout()
                if truncated:
                    process.terminate()
                await process.wait()
                await stderr_task
        except TimeoutError as exc:
            process.kill()
            await process.wait()
            await asyncio.gather(stderr_task, return_exceptions=True)
            raise RootFileError("tool.executionFailed", "Search timed out") from exc
        except asyncio.CancelledError:
            process.kill()
            await process.wait()
            await asyncio.gather(stderr_task, return_exceptions=True)
            raise
        except BaseException:
            if process.returncode is None:
                process.kill()
                await process.wait()
            await asyncio.gather(stderr_task, return_exceptions=True)
            raise
        return process.returncode, truncated
