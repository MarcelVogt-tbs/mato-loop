from __future__ import annotations

import array
import asyncio
import base64
import codecs
import hashlib
import json
import os
import shlex
import shutil
import signal
import socket
import tempfile
import time
from contextlib import suppress
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
    "terminal.shared.acquire",
    "terminal.shared.release",
    "terminal.shared.prepare",
    "terminal.shared.execute",
    "terminal.shared.cancel",
}
TRIGGER = b"\x1b[777;777~"
MAX_COMMAND_BYTES = 256 * 1024


class SharedTerminals:
    def __init__(self, terminal: TerminalManager):
        self.terminal = terminal
        self.bash = (
            shutil.which("bash", path=EXECUTION_PATH) if terminal_support.PTY_AVAILABLE else None
        )

    @property
    def available(self) -> bool:
        return self.bash is not None

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
            if existing.shared.state == "closed":
                raise AgentError("terminal.closed", "Shared terminal is closed")
            return {"terminalId": terminal_id}
        if len(manager._terminals) + len(manager._runs) >= MAX_TERMINALS:
            raise AgentError("terminal.limitReached", "Too many terminals are open")

        parent, child = socket.socketpair()
        parent.setblocking(False)
        entry = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", prefix="matoloop-bash-") as rc:
                fd = child.fileno()
                rc.write(
                    f"readonly __ml_fd={fd}\n"
                    "__ml_prompt() { builtin printf 'P\\tprimary\\n' >&$__ml_fd; }\n"
                    "__ml_secondary() { builtin printf 'P\\tsecondary\\n' >&$__ml_fd; }\n"
                    "__ml_control() {\n"
                    "  local __ml_req __ml_op __ml_arg __ml_expiry __ml_now __ml_code __ml_cwd\n"
                    "  local __ml_trap\n"
                    "  while IFS=$'\\t' read -r __ml_req __ml_op "
                    "__ml_expiry __ml_arg <&$__ml_fd; do\n"
                    "    __ml_now=${EPOCHSECONDS:-0}\n"
                    "    if [[ $__ml_op == acquire && $__ml_expiry -ge $__ml_now ]]; "
                    "then break; fi\n"
                    "    builtin printf '%s\\tbusy\\n' \"$__ml_req\" >&$__ml_fd\n"
                    "  done\n"
                    "  [[ $__ml_op == acquire ]] || return\n"
                    "  if [[ -n $READLINE_LINE ]]; then\n"
                    "    builtin printf '%s\\tbusy\\n' \"$__ml_req\" >&$__ml_fd; return\n"
                    "  fi\n"
                    "  __ml_cwd=$(builtin pwd -P | base64 -w0)\n"
                    '  builtin printf \'%s\\tok\\t%s\\n\' "$__ml_req" "$__ml_cwd" >&$__ml_fd\n'
                    "  while IFS=$'\\t' read -r __ml_req __ml_op __ml_arg <&$__ml_fd; do\n"
                    "    case $__ml_op in\n"
                    "      execute)\n"
                    "        __ml_cwd=$(builtin pwd -P | base64 -w0)\n"
                    "        builtin printf '%s\\tstarted\\t%s\\n' "
                    '"$__ml_req" "$__ml_cwd" >&$__ml_fd\n'
                    "        __ml_trap=$(trap -p INT); trap ':' INT\n"
                    '        eval -- "$(builtin printf %s "$__ml_arg" | base64 -d)"\n'
                    "        __ml_code=$?\n"
                    '        if [[ $__ml_trap ]]; then eval "$__ml_trap"; else trap - INT; fi\n'
                    "        __ml_cwd=$(builtin pwd -P | base64 -w0)\n"
                    "        builtin printf '%s\\tdone\\t%s\\t%s\\n' "
                    '"$__ml_req" "$__ml_code" "$__ml_cwd" >&$__ml_fd\n'
                    "        ;;\n"
                    "      release)\n"
                    "        builtin printf '%s\\tok\\n' \"$__ml_req\" >&$__ml_fd\n"
                    "        READLINE_LINE=; READLINE_POINT=0; return;;\n"
                    "      *) builtin printf '%s\\tinvalid\\n' \"$__ml_req\" >&$__ml_fd;;\n"
                    "    esac\n"
                    "  done\n"
                    "}\n"
                    "readonly -f __ml_prompt __ml_secondary __ml_control\n"
                    "bind -x '\"\\e[777;777~\":__ml_control'\n"
                    "set +H\n"
                    "PS1='\\u@\\h:\\w\\$ '; PS2='$(__ml_secondary)> '\n"
                    "PROMPT_COMMAND=__ml_prompt\n"
                )
                rc.flush()
                environment = {
                    "HOME": manager.home,
                    "SHELL": self.bash,
                    "PATH": EXECUTION_PATH,
                    "TERM": os.environ.get("TERM", "xterm-256color"),
                    "COLORTERM": os.environ.get("COLORTERM", "truecolor"),
                    "LANG": os.environ.get("LANG", "C.UTF-8"),
                    "USER": os.environ.get("USER", "matoloop"),
                    "LOGNAME": os.environ.get("LOGNAME", "matoloop"),
                }
                entry = manager._spawn(
                    terminal_id,
                    [self.bash, "--noprofile", "--rcfile", rc.name, "-i"],
                    manager.home,
                    rows,
                    columns,
                    environment,
                    (fd,),
                )
                child.close()
                shared = SharedTerminal(manager, entry, parent, (rows, columns))
                entry.shared = shared
                manager._terminals[terminal_id] = entry
                entry.reader_task = asyncio.create_task(manager._buffer_output(entry))
                try:
                    async with asyncio.timeout(3):
                        await shared.ready.wait()
                except TimeoutError as exc:
                    raise AgentError(
                        "operation.unsupported", "Managed Bash did not initialize"
                    ) from exc
                if shared.state == "closed":
                    raise AgentError("operation.unsupported", "Managed Bash could not initialize")
        except BaseException:
            if entry is not None and manager._terminals.get(terminal_id) is entry:
                await manager.close(terminal_id)
            else:
                parent.close()
            raise
        finally:
            child.close()
        return {"terminalId": terminal_id}


class SharedTerminal:
    def __init__(self, manager, entry, control, open_dimensions):
        self.manager, self.entry, self.control = manager, entry, control
        self.open_dimensions = open_dimensions
        self.lock = asyncio.Lock()
        self.ready = asyncio.Event()
        self.state = "human"
        self.prompt = None
        self.command_id = None
        self.command = None
        self.inspection = None
        self.waiters = {}
        self.reader = asyncio.create_task(self._read_control())
        self.capture = None
        self.capture_start = 0
        self.capture_end = None
        self.received = 0
        self.truncated = False
        self.completed = asyncio.Event()
        self.exit_code = None
        self.cancelled = False

    def _cwd(self):
        try:
            return os.readlink(f"/proc/{self.entry.pid}/cwd")
        except OSError:
            return self.manager.home

    def _status(self):
        return {
            "terminalId": self.entry.terminal_id,
            "currentDirectory": self._cwd(),
            "state": self.state,
            "commandId": self.command_id,
        }

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
                while b"\n" in buffer:
                    raw, _, rest = buffer.partition(b"\n")
                    buffer[:] = rest
                    fields = raw.decode("ascii").split("\t")
                    if fields[:1] == ["P"] and len(fields) == 2:
                        self.prompt = fields[1]
                        self.ready.set()
                        continue
                    if len(fields) < 2:
                        raise ValueError("Invalid Bash control response")
                    request, result = fields[:2]
                    if result == "done":
                        self.exit_code = 130 if self.cancelled else int(fields[2])
                        self.capture_end = self._boundary()
                        self.state = "agent"
                        if self.received >= self.capture_end:
                            self.completed.set()
                        continue
                    waiter = self.waiters.get(request)
                    if waiter is not None and not waiter.done():
                        if result in {"busy", "invalid"}:
                            waiter.set_exception(
                                AgentError("terminal.busy", "Bash control request was rejected")
                            )
                        else:
                            waiter.set_result(fields[2:])
        except (OSError, UnicodeError, ValueError):
            pass
        finally:
            self.control.close()
            self.state = "closed"
            self.ready.set()
            if self.capture_end is None:
                self.capture_end = self._boundary()
            self.completed.set()
            for waiter in self.waiters.values():
                if not waiter.done():
                    waiter.set_exception(
                        AgentError("terminal.closed", "Bash control channel closed")
                    )

    async def _send(self, operation, argument="", *, trigger=False):
        if self.state == "closed":
            raise AgentError("terminal.closed", "Shared terminal is closed")
        request = str(uuid4())
        expiry = str(int(time.time()) + 2) if operation == "acquire" else ""
        line = f"{request}\t{operation}\t{expiry}\t{argument}\n".encode("ascii")
        waiter = asyncio.get_running_loop().create_future()
        self.waiters[request] = waiter
        try:
            await asyncio.get_running_loop().sock_sendall(self.control, line)
            if trigger:
                await self.manager._write(self.entry, TRIGGER)
            async with asyncio.timeout(1):
                return request, await waiter
        except TimeoutError as exc:
            raise AgentError("terminal.busy", "Bash did not accept control") from exc
        except OSError as exc:
            raise AgentError("terminal.closed", "Shared terminal is closed") from exc
        finally:
            self.waiters.pop(request, None)

    async def status(self):
        return self._status()

    async def acquire(self):
        async with self.lock:
            if self.state != "human":
                raise AgentError("terminal.busy", "Terminal is not under human control")
            if self.prompt != "primary" or os.tcgetpgrp(self.entry.fd) != self.entry.pid:
                raise AgentError("terminal.busy", "Bash is not at its primary prompt")
            await self._send("acquire", trigger=True)
            self.state = "agent"
            self.prompt = None
            return self._status()

    async def release(self):
        async with self.lock:
            if self.state != "agent":
                raise AgentError("terminal.busy", "Terminal is not owned by the agent")
            await self._send("release")
            self.command_id = self.command = self.inspection = None
            self.state = "human"
            self.prompt = "primary"
            return self._status()

    def _inspect(self, command, cwd):
        inspection = self.manager.process.inspect_shell(command, cwd)
        inspection["inspectionRevision"] = hashlib.sha256(
            json.dumps(
                {
                    "terminalId": self.entry.terminal_id,
                    "commandId": self.command_id,
                    "command": command,
                    "currentDirectory": cwd,
                    "inspection": inspection,
                },
                sort_keys=True,
            ).encode()
        ).hexdigest()
        return inspection

    async def prepare(self, command_id, command):
        if not command or len(command.encode()) > MAX_COMMAND_BYTES or "\0" in command:
            raise AgentError("request.invalid", "Invalid shared command")
        _parse_shell(command)
        async with self.lock:
            if self.state != "agent":
                raise AgentError("terminal.busy", "Terminal is not owned by the agent")
            if self.command_id is not None and self.command_id != command_id:
                raise AgentError("terminal.busy", "Another command is already prepared")
            if self.command_id == command_id and self.command != command:
                raise AgentError("terminal.stale", "Prepared command is immutable")
            cwd = self._cwd()
            self.manager.process.filesystem.resolve_directory(cwd)
            self.command_id, self.command = command_id, command
        inspection = await asyncio.to_thread(self._inspect, command, cwd)
        async with self.lock:
            if self.command_id != command_id or self.command != command or self.state != "agent":
                raise AgentError("terminal.stale", "Prepared command changed during inspection")
            self.inspection = inspection
            return {"command": command, "currentDirectory": cwd, "inspection": inspection}

    def interrupt(self):
        if self.entry.fd >= 0:
            with suppress(ProcessLookupError, OSError):
                group = os.tcgetpgrp(self.entry.fd)
                os.killpg(
                    group,
                    signal.SIGINT if group == self.entry.pid else signal.SIGTERM,
                )

    async def _interrupt_until_complete(self):
        self.cancelled = True
        deadline = time.monotonic() + 1
        while not self.completed.is_set() and time.monotonic() < deadline:
            self.interrupt()
            await asyncio.sleep(0.01)
        if not self.completed.is_set():
            await self.manager._terminate(self.entry)

    async def cancel(self, command_id):
        async with self.lock:
            if self.command_id != command_id:
                raise AgentError("terminal.stale", "Command is no longer current")
            if self.state == "running":
                self.cancelled = True
                self.interrupt()
            else:
                self.command_id = self.command = self.inspection = None
            return {}

    async def execute(
        self,
        command_id,
        expected_inspection_revision,
        timeout,
        stream,
        environment=None,
    ):
        timeout = self.manager.process.execution_timeout(timeout)
        started = time.monotonic()
        async with self.lock:
            if (
                self.state != "agent"
                or self.command_id != command_id
                or self.command is None
                or self.inspection is None
                or self.inspection["inspectionRevision"] != expected_inspection_revision
            ):
                raise AgentError("terminal.stale", "Execution does not match prepared command")
            fresh = await asyncio.to_thread(self._inspect, self.command, self._cwd())
            if fresh["inspectionRevision"] != expected_inspection_revision:
                raise AgentError("terminal.stale", "Inspection targets changed")
            self.capture = bytearray()
            self.capture_start, self.capture_end = self._boundary(), None
            self.truncated = False
            self.exit_code = None
            self.cancelled = False
            self.completed.clear()
            command = self.command
            if environment:
                helper = f"__ml_env_{uuid4().hex}"
                code = f"{helper}_code"
                assignments = "\n".join(
                    f"  local {name}={shlex.quote(value)}\n  export {name}"
                    for name, value in environment.items()
                )
                encoded_command = base64.b64encode(command.encode()).decode("ascii")
                command = (
                    f"{helper}() {{\n{assignments}\n"
                    f'  eval -- "$(builtin printf %s {encoded_command} | base64 -d)"\n'
                    f'  local {code}=$?\n  unset -f {helper}\n  return "${code}"\n}}\n'
                    f"{helper}"
                )
            encoded = base64.b64encode(command.encode()).decode("ascii")
            self.state = "running"
            try:
                await self._send("execute", encoded)
            except AgentError:
                await self.manager._terminate(self.entry)
                self.exit_code = None
                self.completed.set()

        sent = emitted = 0
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        timed_out = False

        async def emit(text):
            nonlocal emitted
            encoded_text = text.encode()
            remaining = self.manager.max_output - emitted
            if len(encoded_text) > remaining:
                text = encoded_text[:remaining].decode(errors="ignore")
                encoded_text = text.encode()
                self.truncated = True
            emitted += len(encoded_text)
            if text and stream:
                await stream("stdout", text)

        try:
            try:
                async with asyncio.timeout(timeout):
                    while not self.completed.is_set():
                        if self.cancelled:
                            self.interrupt()
                        if len(self.capture) > sent:
                            chunk = bytes(self.capture[sent : sent + 16384])
                            sent += len(chunk)
                            await emit(decoder.decode(chunk))
                        await asyncio.sleep(0.01)
            except TimeoutError:
                timed_out = True
                await self._interrupt_until_complete()
            except BaseException:
                await self._interrupt_until_complete()
                raise
            while self.capture is not None and sent < len(self.capture):
                chunk = bytes(self.capture[sent : sent + 16384])
                sent += len(chunk)
                await emit(decoder.decode(chunk))
            await emit(decoder.decode(b"", final=True))
        finally:
            self.capture = None
            if self.state == "agent":
                self.command_id = self.command = self.inspection = None
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
