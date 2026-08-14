from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shlex
import signal
import stat
import time
from collections.abc import Awaitable, Callable
from pathlib import Path

from .config import AgentConfig
from .workspace import AgentError, Workspace, _parts

StreamCallback = Callable[[str, str], Awaitable[None]]


class ProcessRunner:
    def __init__(self, config: AgentConfig, workspace: Workspace) -> None:
        try:
            self.executables = {
                alias: self._resolve_executable(path)
                for alias, path in config.process_executables.items()
            }
            self.shell = (
                self._resolve_executable(config.shell_executable)
                if config.shell_executable is not None
                else None
            )
        except AgentError as exc:
            raise ValueError("configured executable is unavailable") from exc
        self.default_timeout = config.default_timeout_seconds
        self.max_output = config.max_output_bytes
        self.workspace = workspace

    @staticmethod
    def _resolve_executable(path: Path) -> Path:
        try:
            canonical = path.resolve(strict=True)
            metadata = canonical.stat(follow_symlinks=False)
        except OSError as exc:
            raise AgentError("process.unavailable", "Configured executable is unavailable") from exc
        if not stat.S_ISREG(metadata.st_mode) or not os.access(canonical, os.X_OK):
            raise AgentError("process.unavailable", "Configured executable is unavailable")
        return canonical

    def _cwd(self, root_id: str, revision: str, working_directory: str) -> tuple[str, int]:
        resolved = self.workspace.resolve_directory(
            working_directory, root_id=root_id, revision=revision
        )
        parts = _parts(resolved["relativePath"])
        with self.workspace._root_fd(root_id, revision) as root_fd:
            directory_fd = self.workspace._open_dir(root_fd, parts)
            try:
                # Linux procfs gives subprocess a stable path without following user symlinks.
                return f"/proc/self/fd/{directory_fd}", directory_fd
            except BaseException:
                os.close(directory_fd)
                raise

    def inspect_shell(
        self, command: str, current_directory: str, root_id: str, revision: str
    ) -> dict:
        current = self.workspace.resolve_directory(
            current_directory, root_id=root_id, revision=revision
        )["path"]
        if any(
            character in command
            for character in ("$", "`", "\n", "\r", "*", "?", "[", "{", "}", "~")
        ):
            return {"targets": [], "analysisComplete": False}
        lexer = shlex.shlex(command, posix=True, punctuation_chars="();<>|&")
        lexer.whitespace_split = True
        try:
            tokens = list(lexer)
        except ValueError:
            return {"targets": [], "analysisComplete": False}
        if not tokens or any(token in {";", "|", "||", "&", "&&", "(", ")"} for token in tokens):
            return {"targets": [], "analysisComplete": False}

        targets = []

        def add(path: str, kind: str, *, must_exist: bool) -> bool:
            try:
                target = self.workspace.resolve_path_target(path, current, must_exist=must_exist)
            except AgentError as exc:
                if exc.code == "path.outsideRoots":
                    raise
                return False
            target["kind"] = "directory" if target.pop("targetKind", None) == "directory" else kind
            targets.append(target)
            return True

        executable = tokens[0]
        if executable in {".", "source"}:
            if len(tokens) < 2 or not add(tokens[1], "source", must_exist=True):
                return {"targets": targets, "analysisComplete": False}
            index = 2
        else:
            resolved_executable = self._trusted_executable(executable, current)
            if resolved_executable is None:
                return {"targets": [], "analysisComplete": False}
            if "/" in executable and self._is_within_roots(resolved_executable):
                if not add(executable, "executable", must_exist=True):
                    return {"targets": targets, "analysisComplete": False}
            index = 1
            executable_name = resolved_executable.name
            if executable_name in {
                "bash",
                "dash",
                "node",
                "perl",
                "python",
                "python3",
                "ruby",
                "sh",
            } or executable_name.startswith("python"):
                while index < len(tokens) and tokens[index].startswith("-"):
                    if tokens[index] in {"-c", "--command"}:
                        return {"targets": targets, "analysisComplete": False}
                    index += 1
                if index < len(tokens) and tokens[index] not in {"<", ">", ">>"}:
                    if not add(tokens[index], "script", must_exist=True):
                        return {"targets": targets, "analysisComplete": False}
                    index += 1

        while index < len(tokens):
            token = tokens[index]
            if (
                token.isdigit()
                and index + 1 < len(tokens)
                and tokens[index + 1] in {"<", ">", ">>"}
            ):
                index += 1
                token = tokens[index]
            if token in {"<", ">", ">>"}:
                if index + 1 >= len(tokens) or not add(
                    tokens[index + 1],
                    "input" if token == "<" else "output",
                    must_exist=token == "<",
                ):
                    return {"targets": targets, "analysisComplete": False}
                index += 2
                continue
            if token in {"<<", "<<<", "<>", ">&", "<&"}:
                return {"targets": targets, "analysisComplete": False}
            if "/" in token and not add(token, "path", must_exist=True):
                return {"targets": targets, "analysisComplete": False}
            index += 1
        result = {"targets": targets, "analysisComplete": True}
        result["inspectionRevision"] = self._inspection_revision(result)
        return result

    @staticmethod
    def _inspection_revision(result: dict) -> str:
        return hashlib.sha256(
            json.dumps(result, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

    def _trusted_executable(self, executable: str, current_directory: str) -> Path | None:
        if executable in self.executables:
            return self.executables[executable]
        if "/" in executable:
            candidates = [Path(current_directory) / executable]
        else:
            candidates = [
                Path(directory) / executable for directory in ("/usr/local/bin", "/usr/bin", "/bin")
            ]
        for candidate in candidates:
            try:
                resolved = self._resolve_executable(candidate)
            except AgentError:
                continue
            if "/" not in executable or self._is_within_roots(resolved):
                return resolved
        return None

    def _is_within_roots(self, path: Path) -> bool:
        return any(
            path == root.config.path or root.config.path in path.parents
            for root in self.workspace.roots.values()
        )

    async def exec(
        self,
        *,
        root_id: str,
        revision: str,
        working_directory: str,
        program: str | None,
        args: list[str],
        command: str | None,
        timeout: float | None,
        stream: StreamCallback,
        expected_inspection_revision: str | None = None,
    ) -> dict:
        if command is None:
            executable_path = self.executables.get(program or "")
            if executable_path is None:
                raise AgentError("process.notAllowed", "Program alias is not configured")
            argv = [os.fspath(executable_path), *args]
        else:
            if self.shell is None:
                raise AgentError("process.notAllowed", "Shell execution is disabled")
            if expected_inspection_revision is not None:
                inspection = self.inspect_shell(command, working_directory, root_id, revision)
                actual_revision = inspection.get("inspectionRevision") or self._inspection_revision(
                    inspection
                )
                if actual_revision != expected_inspection_revision:
                    raise AgentError("process.inspectionChanged", "Shell inspection changed")
            argv = [os.fspath(self.shell), "-c", command]
        cwd, cwd_fd = self._cwd(root_id, revision, working_directory)
        environment = {
            "HOME": cwd,
            "LANG": os.environ.get("LANG", "C.UTF-8"),
            "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        }
        started = time.monotonic()
        try:
            process = await asyncio.create_subprocess_exec(
                *argv,
                cwd=cwd,
                env=environment,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
                pass_fds=(cwd_fd,),
            )
        except OSError as exc:
            os.close(cwd_fd)
            raise AgentError("process.startFailed", "Process could not be started") from exc
        os.close(cwd_fd)
        sent = {"stdout": 0, "stderr": 0}
        truncated = {"stdout": False, "stderr": False}
        total_sent = 0

        async def drain(channel: str, reader: asyncio.StreamReader) -> None:
            nonlocal total_sent
            while chunk := await reader.read(16 * 1024):
                remaining = self.max_output - total_sent
                if remaining <= 0:
                    truncated[channel] = True
                    continue
                text = chunk.decode("utf-8", errors="replace")
                encoded = text.encode("utf-8")
                if len(encoded) > remaining:
                    text = encoded[:remaining].decode("utf-8", errors="ignore")
                    encoded = text.encode("utf-8")
                    truncated[channel] = True
                sent[channel] += len(encoded)
                total_sent += len(encoded)
                await stream(channel, text)

        drain_tasks = [
            asyncio.create_task(drain("stdout", process.stdout)),
            asyncio.create_task(drain("stderr", process.stderr)),
        ]
        timed_out = False
        try:
            async with asyncio.timeout(timeout or self.default_timeout):
                await process.wait()
                await asyncio.gather(*drain_tasks)
        except TimeoutError:
            timed_out = True
            await self._terminate(process)
            await self._finish_drains(process, drain_tasks)
        except asyncio.CancelledError:
            await self._terminate(process)
            await self._finish_drains(process, drain_tasks)
            raise
        except BaseException:
            await self._terminate(process)
            await self._finish_drains(process, drain_tasks)
            raise
        return {
            "exitCode": process.returncode,
            "timedOut": timed_out,
            "durationMs": round((time.monotonic() - started) * 1000),
            "stdoutBytes": sent["stdout"],
            "stderrBytes": sent["stderr"],
            "stdoutTruncated": truncated["stdout"],
            "stderrTruncated": truncated["stderr"],
        }

    @staticmethod
    async def _terminate(process: asyncio.subprocess.Process) -> None:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            if process.returncode is None:
                await process.wait()
            return
        if process.returncode is None:
            try:
                await asyncio.wait_for(process.wait(), 2)
            except TimeoutError:
                pass
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        if process.returncode is None:
            await process.wait()

    @staticmethod
    async def _finish_drains(
        process: asyncio.subprocess.Process, tasks: list[asyncio.Task]
    ) -> None:
        done, pending = await asyncio.wait(tasks, timeout=1)
        if pending:
            transport = getattr(process, "_transport", None)
            if transport is not None:
                for descriptor in (1, 2):
                    pipe = transport.get_pipe_transport(descriptor)
                    if pipe is not None:
                        pipe.close()
            for task in pending:
                task.cancel()
        await asyncio.gather(*done, *pending, return_exceptions=True)
