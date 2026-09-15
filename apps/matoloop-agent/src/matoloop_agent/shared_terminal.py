from __future__ import annotations

import array
import asyncio
import codecs
import hashlib
import json
import os
import shlex
import shutil
import signal
import socket
import struct
import tempfile
import time
from contextlib import suppress
from pathlib import Path
from uuid import uuid4

from . import terminal as terminal_support
from .errors import AgentError
from .process import EXECUTION_PATH, _parse_shell
from .terminal import MAX_TERMINALS, PTY_AVAILABLE, TerminalManager

if PTY_AVAILABLE:
    import fcntl
    import termios

SHARED_OPERATIONS = {
    "terminal.shared.open",
    "terminal.shared.status",
    "terminal.shared.stage",
    "terminal.shared.inspect",
    "terminal.shared.execute",
    "terminal.shared.cancel",
}
MAX_FRAME = 512 * 1024


def native_library() -> Path | None:
    for path in (
        Path(__file__).with_name("native") / "shared.so",
        Path("/usr/local/lib/matoloop-agent/shared.so"),
    ):
        if path.is_file():
            return path
    return None


class SharedTerminals:
    def __init__(self, terminal: TerminalManager):
        self.terminal = terminal
        self.bash = (
            shutil.which("bash", path=EXECUTION_PATH) if terminal_support.PTY_AVAILABLE else None
        )
        self.library = native_library()

    @property
    def available(self) -> bool:
        return self.bash is not None and self.library is not None

    def get(self, terminal_id: str) -> SharedTerminal:
        shared = self.terminal._get(terminal_id).shared
        if shared is None:
            raise AgentError("operation.unsupported", "Terminal is not a managed Bash session")
        return shared

    async def open(self, rows: int, columns: int, *, terminal_id: str | None = None) -> dict:
        if not self.available:
            raise AgentError("operation.unsupported", "Managed Bash integration is unavailable")
        manager = self.terminal
        terminal_id = terminal_id or str(uuid4())
        existing = manager._terminals.get(terminal_id) or manager._runs.get(terminal_id)
        if existing is not None:
            if existing.shared is None or existing.shared.open_dimensions != (rows, columns):
                raise AgentError("terminal.exists", "Terminal identifier is already in use")
            await existing.shared.ready.wait()
            if (
                manager._terminals.get(terminal_id) is not existing
                or existing.shared.state["state"] == "closed"
            ):
                raise AgentError("terminal.closed", "Shared terminal is closed")
            return {"terminalId": terminal_id}
        if len(manager._terminals) + len(manager._runs) >= MAX_TERMINALS:
            raise AgentError("terminal.limitReached", "Too many terminals are open")
        parent, child = socket.socketpair()
        parent.setblocking(False)
        entry = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", prefix="matoloop-bash-") as rc:
                rc.write(
                    f"enable -f {shlex.quote(str(self.library))} ml_shared || exit 125\n"
                    f"builtin ml_shared init {child.fileno()} || exit 125\n"
                    "set -o emacs; set +H\n"
                    "PS1='\\u@\\h:\\w\\$ '; PS2='> '\n"
                    "__ml_prompt() { local result=$?; set +H; "
                    'builtin ml_shared prompt "$result"; }\n'
                    "readonly -f __ml_prompt\n"
                    "PROMPT_COMMAND=__ml_prompt; readonly PROMPT_COMMAND\n"
                )
                rc.flush()
                environment = {
                    "HOME": manager.home,
                    "SHELL": self.bash,
                    "PATH": EXECUTION_PATH,
                    "TERM": "xterm-256color",
                    "LANG": "C.UTF-8",
                    "INPUTRC": "/dev/null",
                }
                entry = manager._spawn(
                    terminal_id,
                    [self.bash, "--noprofile", "--rcfile", rc.name, "-i"],
                    manager.home,
                    rows,
                    columns,
                    environment,
                    (child.fileno(),),
                )
                child.close()
                shared = SharedTerminal(manager, entry, parent, (rows, columns))
                entry.shared = shared
                manager._terminals[entry.terminal_id] = entry
                entry.reader_task = asyncio.create_task(manager._buffer_output(entry))
                try:
                    async with asyncio.timeout(3):
                        await shared.ready.wait()
                except TimeoutError as exc:
                    raise AgentError(
                        "operation.unsupported", "Managed Bash did not initialize"
                    ) from exc
                if manager._terminals.get(terminal_id) is not entry:
                    raise AgentError("terminal.closed", "Shared terminal was closed while opening")
                if shared.state["state"] == "closed":
                    raise AgentError("operation.unsupported", "Managed Bash could not be loaded")
        except BaseException:
            if entry is not None and manager._terminals.get(terminal_id) is entry:
                await manager.close(entry.terminal_id)
            else:
                parent.close()
            raise
        finally:
            child.close()
        return {"terminalId": entry.terminal_id}


class SharedTerminal:
    def __init__(self, manager, entry, control, open_dimensions):
        self.manager, self.entry, self.control = manager, entry, control
        self.open_dimensions = open_dimensions
        self.lock = asyncio.Lock()
        self.ready = asyncio.Event()
        self.state = {
            "terminalId": entry.terminal_id,
            "currentDirectory": manager.home,
            "state": "busy",
            "contextRevision": 0,
            "commandId": None,
            "command": None,
            "revision": 0,
            "submitted": False,
            "cancelled": False,
        }
        self.waiters = {}
        self.reader = asyncio.create_task(self._read_control())
        self.inspected = None
        self.capture = None
        self.capture_start = 0
        self.capture_end = None
        self.received = 0
        self.truncated = False
        self.completed = asyncio.Event()
        self.exit_code = None
        self.executing = False

    def _boundary(self):
        pending = array.array("i", [0])
        if self.entry.fd >= 0:
            fcntl.ioctl(self.entry.fd, termios.FIONREAD, pending, True)
        return self.received + pending[0]

    def output(self, chunk):
        if self.capture is not None:
            start = max(0, self.capture_start - self.received)
            end = (
                len(chunk) if self.capture_end is None else max(0, self.capture_end - self.received)
            )
            captured = chunk[start:end]
            remaining = self.manager.max_output - len(self.capture)
            self.capture.extend(captured[:remaining])
            self.truncated |= len(captured) > remaining
        self.received += len(chunk)
        if self.capture_end is not None and self.received >= self.capture_end:
            self.completed.set()

    async def _read_control(self):
        buffer = bytearray()
        try:
            while chunk := await asyncio.get_running_loop().sock_recv(self.control, 65536):
                buffer.extend(chunk)
                while len(buffer) >= 4:
                    size = struct.unpack("!I", buffer[:4])[0]
                    if size > MAX_FRAME:
                        raise ValueError("Control frame exceeds limit")
                    if len(buffer) < size + 4:
                        break
                    fields = bytes(buffer[4 : 4 + size]).decode().split("\0")
                    del buffer[: 4 + size]
                    if len(fields) != 12 or fields[-1]:
                        raise ValueError("Invalid control frame")
                    (
                        request,
                        error,
                        state,
                        cwd,
                        context,
                        cid,
                        command,
                        revision,
                        submitted,
                        cancelled,
                        exit_code,
                        _,
                    ) = fields
                    self.state = {
                        "terminalId": self.entry.terminal_id,
                        "currentDirectory": cwd,
                        "state": state,
                        "contextRevision": int(context),
                        "commandId": cid or None,
                        "command": command if cid else None,
                        "revision": int(revision),
                        "submitted": submitted == "1",
                        "cancelled": cancelled == "1",
                    }
                    if state == "idle":
                        self.ready.set()
                    if self.executing and self.capture_end is None and exit_code:
                        self.exit_code = int(exit_code)
                        self.capture_end = self._boundary()
                        if self.received >= self.capture_end:
                            self.completed.set()
                    waiter = self.waiters.get(request)
                    if waiter is not None and not waiter.done():
                        if error:
                            waiter.set_exception(
                                AgentError(error, "Shared terminal request rejected")
                            )
                        else:
                            waiter.set_result(dict(self.state))
        except (OSError, ValueError):
            pass
        finally:
            self.control.close()
            self.state = {**self.state, "state": "closed"}
            self.ready.set()
            if self.executing and self.capture_end is None:
                self.capture_end = self._boundary()
            if self.capture_end is None or self.received >= self.capture_end:
                self.completed.set()
            for waiter in self.waiters.values():
                if not waiter.done():
                    waiter.set_exception(
                        AgentError("terminal.closed", "Bash control channel closed")
                    )

    async def _rpc(self, operation, command_id="", command="", expected=None):
        if self.state["state"] == "closed":
            raise AgentError("terminal.closed", "Shared terminal is closed")
        request_id = str(uuid4())
        expected = expected or self.state
        fields = [
            request_id,
            operation,
            command_id,
            command,
            str(expected["revision"]),
            str(expected["contextRevision"]),
            str(time.monotonic() + 0.8),
        ]
        if any("\0" in value for value in fields):
            raise AgentError("request.invalid", "Shared command contains a NUL byte")
        packet = ("\0".join(fields) + "\0").encode()
        if len(packet) > MAX_FRAME - 4:
            raise AgentError("request.invalid", "Shared command exceeds control limit")
        waiter = asyncio.get_running_loop().create_future()
        self.waiters[request_id] = waiter
        try:
            async with asyncio.timeout(1):
                await asyncio.get_running_loop().sock_sendall(
                    self.control, struct.pack("!I", len(packet)) + packet
                )
                return await waiter
        except TimeoutError as exc:
            # Expired frames are rejected in Bash, even if it returns to Readline later.
            if operation == "execute":
                raise
            raise AgentError("terminal.busy", "Bash is not servicing primary Readline") from exc
        except OSError as exc:
            raise AgentError("terminal.closed", "Shared terminal is closed") from exc
        finally:
            self.waiters.pop(request_id, None)

    async def status(self, *, redisplay: bool = False):
        async with self.lock:
            if self.state["state"] == "closed":
                return dict(self.state)
            try:
                return await self._rpc("redisplay" if redisplay else "status")
            except AgentError as exc:
                if exc.code != "terminal.busy":
                    raise
                # No Readline callback exists in a foreground program. Never turn
                # a missing response into idle readiness or inject terminal input.
                result = {**self.state, "state": "running" if self.executing else "busy"}
                with suppress(OSError):
                    result["currentDirectory"] = os.readlink(f"/proc/{self.entry.pid}/cwd")
                return result

    async def stage(self, command_id, command):
        if not command or len(command.encode()) > 256 * 1024 or "\0" in command:
            raise AgentError("request.invalid", "Invalid shared command")
        _parse_shell(command)
        async with self.lock:
            if self.executing:
                raise AgentError("terminal.busy", "A shared command is still executing")
            status = await self._rpc("status")
            self.manager.process.filesystem.resolve_directory(status["currentDirectory"])
            result = await self._rpc("stage", command_id, command, status)
            self.inspected = None
            return result

    def _inspection(self, snapshot):
        inspection = self.manager.process.inspect_shell(
            snapshot["command"], snapshot["currentDirectory"]
        )
        binding = {
            key: snapshot[key]
            for key in (
                "terminalId",
                "commandId",
                "command",
                "currentDirectory",
                "revision",
                "contextRevision",
            )
        }
        inspection["inspectionRevision"] = hashlib.sha256(
            json.dumps({**binding, "inspection": inspection}, sort_keys=True).encode()
        ).hexdigest()
        return inspection

    async def inspect(self, command_id, *, freeze: bool = True):
        async with self.lock:
            snapshot = await self._rpc("inspect" if freeze else "snapshot", command_id)
        inspection = await asyncio.to_thread(self._inspection, snapshot)
        async with self.lock:
            current = await self._rpc("status")
            if any(
                current[key] != snapshot[key]
                for key in (
                    "commandId",
                    "command",
                    "revision",
                    "contextRevision",
                    "state",
                    "cancelled",
                )
            ):
                raise AgentError("terminal.stale", "Proposal changed during inspection")
            self.inspected = {**snapshot, "inspection": inspection}
            return {
                key: self.inspected[key]
                for key in (
                    "command",
                    "currentDirectory",
                    "revision",
                    "contextRevision",
                    "inspection",
                )
            }

    def interrupt(self):
        if self.entry.fd >= 0:
            with suppress(ProcessLookupError, OSError):
                os.killpg(os.tcgetpgrp(self.entry.fd), signal.SIGINT)

    async def _recover(self):
        if not self.completed.is_set():
            if self.capture_end is None:
                self.interrupt()
            with suppress(TimeoutError):
                await asyncio.wait_for(self.completed.wait(), 1)
            if not self.completed.is_set() and self.capture_end is None:
                await self.manager._terminate(self.entry)

    async def cancel(self, command_id):
        async with self.lock:
            if self.state["commandId"] != command_id:
                raise AgentError("terminal.stale", "Proposal is no longer current")
            if self.executing:
                if self.capture_end is None:
                    self.interrupt()
            elif not self.state["cancelled"]:
                await self._rpc("cancel", command_id)
            return {}

    async def execute(
        self,
        command_id,
        command,
        expected_revision,
        expected_context_revision,
        expected_inspection_revision,
        timeout,
        stream,
    ):
        timeout = self.manager.process.execution_timeout(timeout)
        started = time.monotonic()
        async with self.lock:
            expected = self.inspected
            if (
                self.executing
                or expected is None
                or expected["commandId"] != command_id
                or command != expected["command"]
                or expected_revision != expected["revision"]
                or expected_context_revision != expected["contextRevision"]
                or expected_inspection_revision != expected["inspection"]["inspectionRevision"]
            ):
                raise AgentError("terminal.stale", "Approval does not match inspected proposal")
        fresh = await asyncio.to_thread(self._inspection, expected)
        if fresh["inspectionRevision"] != expected_inspection_revision:
            raise AgentError("terminal.stale", "Inspection targets changed")
        async with self.lock:
            if self.executing or self.inspected is not expected:
                raise AgentError("terminal.stale", "Another proposal replaced the approval")
            self.capture = bytearray()
            self.capture_start, self.capture_end = self._boundary(), None
            self.truncated = False
            self.exit_code = None
            self.completed.clear()
            self.executing = True
            try:
                await self._rpc("execute", command_id, command, expected)
            except TimeoutError:
                # Acceptance may have occurred but its acknowledgement was lost.
                # Close the shell and report an unknown outcome, never a retryable busy.
                await self.manager._terminate(self.entry)
                self.exit_code = None
            except AgentError as exc:
                if exc.code == "terminal.closed":
                    await self.manager._terminate(self.entry)
                    self.exit_code = None
                else:
                    self.executing = False
                    self.capture = None
                    raise
            except BaseException:
                await self.manager._terminate(self.entry)
                self.executing = False
                self.capture = None
                raise
        sent = 0
        emitted = 0
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        timed_out = False

        async def emit(text):
            nonlocal emitted
            encoded = text.encode("utf-8")
            remaining = self.manager.max_output - emitted
            if len(encoded) > remaining:
                text = encoded[:remaining].decode("utf-8", errors="ignore")
                encoded = text.encode("utf-8")
                self.truncated = True
            emitted += len(encoded)
            if text and stream:
                await stream("stdout", text)

        try:
            try:
                async with asyncio.timeout(timeout):
                    while not self.completed.is_set():
                        if len(self.capture) > sent:
                            text = decoder.decode(bytes(self.capture[sent : sent + 16384]))
                            sent += min(16384, len(self.capture) - sent)
                            await emit(text)
                        await asyncio.sleep(0.01)
            except TimeoutError:
                timed_out = True
                await self._recover()
            except BaseException:
                await self._recover()
                raise
            while sent < len(self.capture):
                text = decoder.decode(bytes(self.capture[sent : sent + 16384]))
                sent += min(16384, len(self.capture) - sent)
                await emit(text)
            text = decoder.decode(b"", final=True)
            await emit(text)
        finally:
            self.executing = False
            self.capture = None
        return {
            "exitCode": self.exit_code,
            "timedOut": timed_out,
            "durationMs": round((time.monotonic() - started) * 1000),
            "stdoutBytes": emitted,
            "stderrBytes": 0,
            "stdoutTruncated": self.truncated,
            "stderrTruncated": False,
        }

    async def shutdown(self):
        self.control.close()
        self.reader.cancel()
        await asyncio.gather(self.reader, return_exceptions=True)
        self.completed.set()
        if self.entry.fd >= 0:
            with suppress(OSError):
                group = os.tcgetpgrp(self.entry.fd)
                if group != self.entry.pid:
                    os.killpg(group, signal.SIGKILL)
