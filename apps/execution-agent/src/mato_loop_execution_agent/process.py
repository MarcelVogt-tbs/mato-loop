from __future__ import annotations

import asyncio
import os
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
        self.workspace._root(root_id, revision)
        parts = _parts(working_directory)
        with self.workspace._root_fd(root_id, revision) as root_fd:
            directory_fd = self.workspace._open_dir(root_fd, parts)
            try:
                # Linux procfs gives subprocess a stable path without following user symlinks.
                return f"/proc/self/fd/{directory_fd}", directory_fd
            except BaseException:
                os.close(directory_fd)
                raise

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
    ) -> dict:
        if command is None:
            executable_path = self.executables.get(program or "")
            if executable_path is None:
                raise AgentError("process.notAllowed", "Program alias is not configured")
            argv = [os.fspath(executable_path), *args]
        else:
            if self.shell is None:
                raise AgentError("process.notAllowed", "Shell execution is disabled")
            argv = [os.fspath(self.shell), "-c", command]
        cwd, cwd_fd = self._cwd(root_id, revision, working_directory)
        environment = {
            "HOME": cwd,
            "LANG": os.environ.get("LANG", "C.UTF-8"),
            "PATH": "/usr/local/bin:/usr/bin:/bin",
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
