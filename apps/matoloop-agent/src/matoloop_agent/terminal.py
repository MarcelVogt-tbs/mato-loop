from __future__ import annotations

import asyncio
import base64
import codecs
import errno
import os
import signal
import struct
import time
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from uuid import uuid4

try:
    import fcntl
    import pty
    import pwd
    import termios
except ImportError:  # pragma: no cover - exercised only on non-POSIX hosts
    fcntl = pty = pwd = termios = None

from .config import AgentConfig
from .errors import AgentError
from .process import EXECUTION_PATH, ProcessRunner

PTY_AVAILABLE = (
    os.name == "posix"
    and fcntl is not None
    and pty is not None
    and pwd is not None
    and termios is not None
    and hasattr(os, "killpg")
)
MAX_TERMINALS = 8
MAX_BUFFER_BYTES = 1024 * 1024
StreamCallback = Callable[[str, str], Awaitable[None]]

if TYPE_CHECKING:
    from .shared_terminal import SharedTerminal


@dataclass
class _Terminal:
    terminal_id: str
    pid: int
    fd: int
    buffer: bytearray = field(default_factory=bytearray)
    condition: asyncio.Condition = field(default_factory=asyncio.Condition)
    write_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    eof: bool = False
    exit_code: int | None = None
    reader_task: asyncio.Task | None = None
    wait_task: asyncio.Task | None = None
    owner_task: asyncio.Task | None = None
    read_waiter: asyncio.Future | None = None
    shared: SharedTerminal | None = None


class TerminalManager:
    def __init__(self, config: AgentConfig, process: ProcessRunner) -> None:
        self.home = os.fspath(config.home_directory)
        self.shell = process.shell
        self.process = process
        self.max_output = config.max_output_bytes
        self._terminals: dict[str, _Terminal] = {}
        self._runs: dict[str, _Terminal] = {}

    @property
    def available(self) -> bool:
        return PTY_AVAILABLE and self.shell is not None

    def _personal_environment(self) -> dict[str, str]:
        username = pwd.getpwuid(os.getuid()).pw_name
        return {
            "HOME": self.home,
            "SHELL": os.fspath(self.shell),
            "TERM": os.environ.get("TERM", "xterm-256color"),
            "COLORTERM": os.environ.get("COLORTERM", "truecolor"),
            "LANG": os.environ.get("LANG", "C.UTF-8"),
            "PATH": EXECUTION_PATH,
            "USER": username,
            "LOGNAME": username,
        }

    def _spawn(
        self,
        terminal_id: str,
        argv: list[str],
        current_directory: str,
        rows: int,
        columns: int,
        environment: dict[str, str],
        pass_fds: tuple[int, ...] = (),
    ) -> _Terminal:
        if not PTY_AVAILABLE:
            raise AgentError("operation.unsupported", "POSIX terminal support is unavailable")
        _cwd, cwd_fd = self.process._cwd(current_directory)
        try:
            self.process.validate_executable(argv[0])
            pid, master_fd = pty.fork()
            if pid == 0:  # pragma: no branch - child replaces itself
                try:
                    os.fchdir(cwd_fd)
                    os.close(cwd_fd)
                    signal.signal(signal.SIGPIPE, signal.SIG_DFL)
                    start = 3
                    for fd in sorted(pass_fds):
                        os.closerange(start, fd)
                        os.set_inheritable(fd, True)
                        start = fd + 1
                    os.closerange(start, os.sysconf("SC_OPEN_MAX"))
                    os.execve(argv[0], argv, environment)
                except BaseException:
                    os._exit(127)
        except OSError as exc:
            raise AgentError(
                "process.startFailed", "Terminal process could not be started"
            ) from exc
        finally:
            os.close(cwd_fd)
        try:
            flags = fcntl.fcntl(master_fd, fcntl.F_GETFL)
            fcntl.fcntl(master_fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)
            fcntl.ioctl(master_fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, columns, 0, 0))
        except OSError as exc:
            os.close(master_fd)
            self._signal(pid, signal.SIGKILL)
            with suppress(ChildProcessError):
                os.waitpid(pid, 0)
            raise AgentError("process.startFailed", "Terminal could not be configured") from exc
        entry = _Terminal(terminal_id, pid, master_fd)
        entry.wait_task = asyncio.create_task(self._wait(entry))
        return entry

    async def _wait(self, entry: _Terminal) -> None:
        try:
            _pid, status = await asyncio.to_thread(os.waitpid, entry.pid, 0)
            entry.exit_code = os.waitstatus_to_exitcode(status)
        except ChildProcessError:
            entry.exit_code = -signal.SIGKILL
        async with entry.condition:
            entry.condition.notify_all()

    @staticmethod
    async def _ready(entry: _Terminal, *, write: bool = False) -> None:
        loop = asyncio.get_running_loop()
        fd = entry.fd
        ready = loop.create_future()
        add = loop.add_writer if write else loop.add_reader
        remove = loop.remove_writer if write else loop.remove_reader

        def wake() -> None:
            if not ready.done():
                ready.set_result(None)

        if not write:
            entry.read_waiter = ready
        add(fd, wake)
        try:
            await ready
        finally:
            remove(fd)
            if entry.read_waiter is ready:
                entry.read_waiter = None

    @classmethod
    async def _read_fd(cls, entry: _Terminal, size: int = 16 * 1024) -> bytes:
        while entry.fd >= 0:
            await cls._ready(entry)
            try:
                return os.read(entry.fd, size)
            except BlockingIOError:
                continue
            except OSError as exc:
                if exc.errno in {errno.EIO, errno.EBADF}:
                    return b""
                raise
        return b""

    async def _buffer_output(self, entry: _Terminal) -> None:
        try:
            while True:
                async with entry.condition:
                    if entry.shared is None:
                        await entry.condition.wait_for(
                            lambda: len(entry.buffer) < MAX_BUFFER_BYTES or entry.eof
                        )
                    if entry.eof:
                        return
                    available = (
                        MAX_BUFFER_BYTES if entry.shared else MAX_BUFFER_BYTES - len(entry.buffer)
                    )
                chunk = await self._read_fd(entry, min(16 * 1024, available))
                async with entry.condition:
                    if not chunk:
                        entry.eof = True
                    else:
                        entry.buffer.extend(chunk)
                        if entry.shared is not None:
                            del entry.buffer[:-MAX_BUFFER_BYTES]
                            entry.shared.output(chunk)
                    entry.condition.notify_all()
                if not chunk:
                    return
        finally:
            if entry.eof:
                self._close_fd(entry)

    @staticmethod
    def _close_fd(entry: _Terminal) -> None:
        if entry.fd < 0:
            return
        fd = entry.fd
        entry.fd = -1
        if entry.read_waiter is not None and not entry.read_waiter.done():
            entry.read_waiter.set_result(None)
        loop = asyncio.get_running_loop()
        loop.remove_reader(fd)
        loop.remove_writer(fd)
        with suppress(OSError):
            os.close(fd)

    async def _terminate(self, entry: _Terminal, *, close_fd: bool = True) -> None:
        if entry.shared is not None:
            await entry.shared.shutdown()
        self._signal(entry.pid, signal.SIGTERM)
        if entry.wait_task is not None and not entry.wait_task.done():
            try:
                await asyncio.wait_for(asyncio.shield(entry.wait_task), 2)
            except TimeoutError:
                pass
        # The leader may exit on SIGTERM while descendants ignore it.
        self._signal(entry.pid, signal.SIGKILL)
        if entry.wait_task is not None:
            await entry.wait_task
        if close_fd:
            entry.eof = True
            self._close_fd(entry)
            async with entry.condition:
                entry.condition.notify_all()
            if entry.reader_task is not None and entry.reader_task is not asyncio.current_task():
                entry.reader_task.cancel()
                await asyncio.gather(entry.reader_task, return_exceptions=True)

    @staticmethod
    def _signal(pid: int, sig: signal.Signals) -> None:
        try:
            os.killpg(pid, sig)
        except ProcessLookupError:
            with suppress(ProcessLookupError):
                os.kill(pid, sig)

    def _get(self, terminal_id: str) -> _Terminal:
        entry = self._runs.get(terminal_id)
        if entry is None:
            entry = self._terminals.get(terminal_id)
        if entry is None:
            raise AgentError("terminal.notFound", "Terminal is unavailable")
        return entry

    async def open(self, rows: int, columns: int) -> dict:
        if not self.available:
            raise AgentError("operation.unsupported", "Personal terminal shell is unavailable")
        if len(self._terminals) + len(self._runs) >= MAX_TERMINALS:
            raise AgentError("terminal.limitReached", "Too many terminals are open")
        terminal_id = str(uuid4())
        while terminal_id in self._terminals or terminal_id in self._runs:
            terminal_id = str(uuid4())
        entry = self._spawn(
            terminal_id,
            [os.fspath(self.shell), "-i"],
            self.home,
            rows,
            columns,
            self._personal_environment(),
        )
        self._terminals[terminal_id] = entry
        entry.reader_task = asyncio.create_task(self._buffer_output(entry))
        return {"terminalId": terminal_id}

    async def read(self, terminal_id: str, max_bytes: int, wait_seconds: float) -> dict:
        try:
            entry = self._terminals[terminal_id]
        except KeyError as exc:
            raise AgentError("terminal.notFound", "Terminal is unavailable") from exc
        async with entry.condition:
            if not entry.buffer and not entry.eof and wait_seconds:
                try:
                    async with asyncio.timeout(wait_seconds):
                        await entry.condition.wait_for(lambda: bool(entry.buffer) or entry.eof)
                except TimeoutError:
                    pass
            data = bytes(entry.buffer[:max_bytes])
            del entry.buffer[: len(data)]
            entry.condition.notify_all()
            eof = entry.eof and not entry.buffer
        if eof and entry.wait_task is not None:
            await entry.wait_task
        return {
            "data": base64.b64encode(data).decode("ascii"),
            "eof": eof,
            "exitCode": entry.exit_code,
        }

    async def write(self, terminal_id: str, encoded: str) -> dict:
        entry = self._get(terminal_id)
        data = base64.b64decode(encoded, validate=True)
        if len(data) > 65_536:
            raise AgentError("request.invalid", "Terminal input is too large")
        if entry.shared is not None:
            async with entry.shared.lock:
                return await self._write(entry, data)
        return await self._write(entry, data)

    async def _write(self, entry: _Terminal, data: bytes) -> dict:
        written = 0
        async with entry.write_lock:
            while written < len(data):
                if entry.fd < 0 or (entry.wait_task is not None and entry.wait_task.done()):
                    raise AgentError("terminal.closed", "Terminal is closed")
                await self._ready(entry, write=True)
                try:
                    written += os.write(entry.fd, data[written:])
                except BlockingIOError:
                    continue
                except OSError as exc:
                    raise AgentError("terminal.closed", "Terminal is closed") from exc
        return {}

    async def resize(self, terminal_id: str, rows: int, columns: int) -> dict:
        self._resize(self._get(terminal_id), rows, columns)
        return {}

    def _resize(self, entry: _Terminal, rows: int, columns: int) -> None:
        if entry.fd < 0 or (entry.wait_task is not None and entry.wait_task.done()):
            raise AgentError("terminal.closed", "Terminal is closed")
        try:
            fcntl.ioctl(entry.fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, columns, 0, 0))
            self._signal(entry.pid, signal.SIGWINCH)
        except OSError as exc:
            raise AgentError("terminal.closed", "Terminal is closed") from exc

    async def close(self, terminal_id: str) -> dict:
        entry = self._runs.pop(terminal_id, None)
        if entry is None:
            entry = self._terminals.pop(terminal_id, None)
        if entry is None:
            raise AgentError("terminal.notFound", "Terminal is unavailable")
        self._cancel_owner(entry)
        await self._terminate(entry)
        return {}

    async def close_all(self) -> None:
        entries = [*self._runs.values(), *self._terminals.values()]
        self._runs.clear()
        self._terminals.clear()
        for entry in entries:
            self._cancel_owner(entry)
        await asyncio.gather(*(self._terminate(entry) for entry in entries), return_exceptions=True)

    @staticmethod
    def _cancel_owner(entry: _Terminal) -> None:
        if (
            entry.owner_task is not None
            and entry.owner_task is not asyncio.current_task()
            and not entry.owner_task.done()
        ):
            entry.owner_task.cancel()

    async def run(
        self,
        *,
        terminal_id: str,
        current_directory: str,
        rows: int,
        columns: int,
        command: str,
        timeout: float | None,
        expected_inspection_revision: str | None,
        stream: StreamCallback,
    ) -> dict:
        timeout = self.process.execution_timeout(timeout)
        if terminal_id in self._runs or terminal_id in self._terminals:
            raise AgentError("terminal.exists", "Terminal identifier is already in use")
        if len(self._terminals) + len(self._runs) >= MAX_TERMINALS:
            raise AgentError("terminal.limitReached", "Too many terminals are open")
        argv, inspection = self.process.execution_argv(
            current_directory=current_directory,
            command=command,
            expected_inspection_revision=expected_inspection_revision,
        )
        entry = self._spawn(
            terminal_id,
            argv,
            current_directory,
            rows,
            columns,
            self.process.execution_environment(current_directory, inspection),
        )
        entry.owner_task = asyncio.current_task()
        self._runs[terminal_id] = entry
        started = time.monotonic()
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        sent = 0
        truncated = False

        async def drain() -> None:
            nonlocal sent, truncated
            while chunk := await self._read_fd(entry):
                remaining = self.max_output - sent
                if remaining <= 0:
                    truncated = True
                    continue
                text = decoder.decode(chunk, final=False)
                encoded = text.encode("utf-8")
                if len(encoded) > remaining:
                    text = encoded[:remaining].decode("utf-8", errors="ignore")
                    encoded = text.encode("utf-8")
                    truncated = True
                sent += len(encoded)
                if text:
                    await stream("stdout", text)

        async def finish_decoder() -> None:
            nonlocal sent, truncated
            final = decoder.decode(b"", final=True)
            if not final:
                return
            remaining = self.max_output - sent
            encoded = final.encode("utf-8")
            if len(encoded) > remaining:
                final = encoded[:remaining].decode("utf-8", errors="ignore")
                encoded = final.encode("utf-8")
                truncated = True
            sent += len(encoded)
            if final:
                await stream("stdout", final)

        timed_out = False
        try:
            async with asyncio.timeout(timeout):
                await drain()
                if entry.wait_task is not None:
                    await asyncio.shield(entry.wait_task)
            await finish_decoder()
        except TimeoutError:
            timed_out = True
            await self._terminate(entry, close_fd=False)
            with suppress(TimeoutError):
                await asyncio.wait_for(drain(), 1)
            await finish_decoder()
        except asyncio.CancelledError:
            await self._terminate(entry)
            raise
        except BaseException:
            await self._terminate(entry)
            raise
        finally:
            if self._runs.get(terminal_id) is entry:
                self._runs.pop(terminal_id, None)
            entry.owner_task = None
            self._close_fd(entry)
        return {
            "exitCode": entry.exit_code,
            "timedOut": timed_out,
            "durationMs": round((time.monotonic() - started) * 1000),
            "stdoutBytes": sent,
            "stderrBytes": 0,
            "stdoutTruncated": truncated,
            "stderrTruncated": False,
        }
