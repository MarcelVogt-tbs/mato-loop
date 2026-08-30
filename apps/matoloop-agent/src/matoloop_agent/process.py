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
from .filesystem import AgentError, Filesystem, _parts

StreamCallback = Callable[[str, str], Awaitable[None]]

_READ_ONLY_EXECUTABLES = {
    "cat",
    "cmp",
    "head",
    "ls",
    "pwd",
    "readlink",
    "realpath",
    "sha256sum",
    "stat",
    "tail",
    "wc",
}
_READ_ONLY_PATH_OPTION_EXECUTABLES = {"realpath"}
_READ_ONLY_OPTION_VALUES = {
    "cmp": {"-i", "--ignore-initial", "-n", "--bytes"},
    "head": {"-c", "--bytes", "-n", "--lines"},
    "ls": {"-I", "--ignore", "-w", "--width"},
    "realpath": {"--relative-base", "--relative-to"},
    "stat": {"-c", "--format", "--printf"},
    "tail": {
        "-c",
        "--bytes",
        "-n",
        "--lines",
        "--max-unchanged-stats",
        "--pid",
        "-s",
        "--sleep-interval",
    },
}
_READ_ONLY_PATH_OPTIONS = {
    "realpath": {"--relative-base", "--relative-to"},
}
_INTERPRETERS = {
    "bash",
    "dash",
    "node",
    "perl",
    "python",
    "python3",
    "ruby",
    "sh",
}
_PREFIX_ARITY = {
    ("aws",): 3,
    ("az",): 3,
    ("bun",): 2,
    ("bun", "run"): 3,
    ("cargo",): 2,
    ("docker",): 2,
    ("docker", "compose"): 3,
    ("docker", "container"): 3,
    ("docker", "image"): 3,
    ("docker", "network"): 3,
    ("docker", "volume"): 3,
    ("gcloud",): 3,
    ("gh",): 3,
    ("git",): 2,
    ("git", "config"): 3,
    ("git", "remote"): 3,
    ("git", "stash"): 3,
    ("go",): 2,
    ("kubectl",): 2,
    ("npm",): 2,
    ("npm", "exec"): 3,
    ("npm", "init"): 3,
    ("npm", "run"): 3,
    ("pip",): 2,
    ("pnpm",): 2,
    ("pnpm", "exec"): 3,
    ("pnpm", "run"): 3,
    ("python",): 2,
    ("terraform",): 2,
    ("terraform", "workspace"): 3,
    ("yarn",): 2,
    ("yarn", "run"): 3,
}


def _command_prefix(tokens: list[str]) -> list[str] | None:
    if "/" in tokens[0]:
        return None
    executable = tokens[0]
    arguments = tokens[1:]
    if executable in {"eval", "exec"} or (
        (executable in _INTERPRETERS or executable.startswith("python"))
        and any(option in arguments for option in {"-c", "--command", "-e", "--eval"})
    ):
        return None
    if executable == "git":
        while arguments and arguments[0].startswith("-"):
            option = arguments[0]
            if option == "-C":
                if len(arguments) < 2:
                    return None
                arguments = arguments[2:]
            elif option in {"--no-pager", "--literal-pathspecs"}:
                arguments = arguments[1:]
            else:
                return None
    elif executable.startswith("python") and arguments[:1] == ["-m"]:
        arguments = arguments[1:]
    elif arguments and arguments[0].startswith("-") and (executable,) in _PREFIX_ARITY:
        return [executable]

    semantic = [executable, *(token for token in arguments if not token.startswith("-"))]
    for length in range(len(semantic), 0, -1):
        arity = _PREFIX_ARITY.get(tuple(semantic[:length]))
        if arity is not None:
            return semantic[:arity]
    if executable.startswith("python"):
        return semantic[:2]
    return semantic[:1]


class ProcessRunner:
    def __init__(self, config: AgentConfig, filesystem: Filesystem) -> None:
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
        self.filesystem = filesystem

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

    def _cwd(self, current_directory: str) -> tuple[str, int]:
        self.filesystem.resolve_directory(current_directory)
        with self.filesystem._root_fd() as root_fd:
            directory_fd = self.filesystem._open_dir(root_fd, _parts(current_directory))
            try:
                # Linux procfs gives subprocess a stable path without following user symlinks.
                return f"/proc/self/fd/{directory_fd}", directory_fd
            except BaseException:
                os.close(directory_fd)
                raise

    def inspect_shell(self, command: str, current_directory: str) -> dict:
        def result(
            *,
            targets: list[dict] | None = None,
            commands: list[dict] | None = None,
            analysis_complete: bool = False,
        ) -> dict:
            command_units = commands or []
            classification = (
                "read"
                if analysis_complete
                and command_units
                and all(unit["classification"] == "read" for unit in command_units)
                else "write"
            )
            result = {
                "targets": targets or [],
                "analysisComplete": analysis_complete,
                "readOnly": classification == "read",
                "classification": classification,
                "commands": command_units,
            }
            result["inspectionRevision"] = self._inspection_revision(result)
            return result

        current = self.filesystem.resolve_directory(current_directory)["path"]
        if any(
            character in command
            for character in ("$", "`", "\n", "\r", "*", "?", "[", "{", "}", "~")
        ):
            return result()
        lexer = shlex.shlex(command, posix=True, punctuation_chars="();<>|&")
        lexer.whitespace_split = True
        try:
            tokens = list(lexer)
        except ValueError:
            return result()
        if not tokens:
            return result()

        units: list[list[str]] = [[]]
        for token in tokens:
            if token in {";", "|", "||", "&&"}:
                if not units[-1]:
                    return result()
                units.append([])
                continue
            if (
                token
                and all(character in "();<>|&" for character in token)
                and token
                not in {
                    "<",
                    ">",
                    ">>",
                }
            ):
                return result()
            units[-1].append(token)
        if not units[-1]:
            if tokens[-1] != ";":
                return result()
            units.pop()

        targets = []

        def add(path: str, kind: str, *, must_exist: bool) -> bool:
            try:
                target = self.filesystem.resolve_path_target(path, current, must_exist=must_exist)
            except AgentError:
                return False
            target["kind"] = "directory" if target.pop("targetKind", None) == "directory" else kind
            targets.append(target)
            return True

        parsed: list[tuple[list[str], list[tuple[str, str, bool]]]] = []
        for unit in units:
            arguments: list[str] = []
            redirections: list[tuple[str, str, bool]] = []
            index = 0
            while index < len(unit):
                token = unit[index]
                descriptor = bool(
                    token.isdigit()
                    and index + 1 < len(unit)
                    and unit[index + 1] in {"<", ">", ">>"}
                )
                if descriptor:
                    index += 1
                    token = unit[index]
                if token in {"<", ">", ">>"}:
                    if index + 1 >= len(unit) or unit[index + 1] in {"<", ">", ">>"}:
                        return result()
                    redirections.append((token, unit[index + 1], descriptor))
                    index += 2
                    continue
                arguments.append(token)
                index += 1
            if not arguments:
                return result()
            parsed.append((arguments, redirections))

        reserved = {
            "case",
            "coproc",
            "do",
            "done",
            "elif",
            "else",
            "esac",
            "fi",
            "for",
            "function",
            "if",
            "in",
            "select",
            "then",
            "time",
            "until",
            "while",
        }
        if any(
            arguments[0] in reserved
            or (
                "=" in arguments[0]
                and arguments[0].split("=", 1)[0].replace("_", "a").isalnum()
                and not arguments[0][0].isdigit()
            )
            for arguments, _redirections in parsed
        ):
            return result()

        commands = []
        for arguments, _redirections in parsed:
            command_unit = {"tokens": arguments, "classification": "write"}
            prefix = _command_prefix(arguments)
            if prefix is not None:
                command_unit["prefix"] = prefix
            commands.append(command_unit)
        complete: list[bool] = []
        for unit_index, (arguments, redirections) in enumerate(parsed):
            executable = arguments[0]
            executable_name = ""
            read_only_executable = False
            unit_complete = False
            argument_index = 1
            if executable in {".", "source"}:
                if len(arguments) < 2 or not add(arguments[1], "source", must_exist=True):
                    return result(targets=targets, commands=commands)
                argument_index = 2
            else:
                resolved_executable = self._trusted_executable(executable, current)
                if resolved_executable is not None:
                    if "/" in executable and not add(executable, "executable", must_exist=True):
                        return result(targets=targets, commands=commands)
                    executable_name = resolved_executable.name
                    read_only_executable = (
                        executable_name in _READ_ONLY_EXECUTABLES
                        and resolved_executable.parent
                        in {Path("/bin").resolve(), Path("/usr/bin").resolve()}
                    )
                    unit_complete = read_only_executable
                    if executable_name in _INTERPRETERS or executable_name.startswith("python"):
                        while argument_index < len(arguments) and arguments[
                            argument_index
                        ].startswith("-"):
                            argument_index += 1
                        if argument_index < len(arguments):
                            if not add(arguments[argument_index], "script", must_exist=True):
                                return result(targets=targets, commands=commands)
                            argument_index += 1

            output_redirection = False
            descriptor_redirection = False
            for operator, target, descriptor in redirections:
                output_redirection |= operator != "<"
                descriptor_redirection |= descriptor
                if not add(
                    target,
                    "input" if operator == "<" else "output",
                    must_exist=operator == "<",
                ):
                    return result(targets=targets, commands=commands)

            if executable_name == "git":
                index = 1
                while index < len(arguments):
                    if arguments[index] == "-C":
                        if index + 1 >= len(arguments) or not add(
                            arguments[index + 1], "path", must_exist=True
                        ):
                            return result(targets=targets, commands=commands)
                        index += 2
                    else:
                        index += 1

            if not read_only_executable:
                for token in arguments[argument_index:]:
                    if "/" in token and not add(token, "path", must_exist=True):
                        return result(targets=targets, commands=commands)

            read_only = read_only_executable and executable not in {".", "source"}
            if read_only:
                operands = []
                after_options = False
                index = 1
                while index < len(arguments):
                    token = arguments[index]
                    if token == "--":
                        after_options = True
                        index += 1
                        continue
                    if not after_options and token.startswith("-"):
                        if (
                            (
                                executable_name == "ls"
                                and (
                                    (not token.startswith("--") and "L" in token[1:])
                                    or token.startswith("--dere")
                                )
                            )
                            or (
                                executable_name == "tail"
                                and (
                                    (not token.startswith("--") and {"f", "F"} & set(token[1:]))
                                    or token.startswith(("--f", "--retry"))
                                )
                            )
                            or (executable_name == "wc" and token.startswith("--f"))
                            or (
                                executable_name == "sha256sum"
                                and (
                                    (not token.startswith("--") and "c" in token[1:])
                                    or token.startswith("--c")
                                )
                            )
                        ):
                            read_only = False
                            unit_complete = False
                            index += 1
                            continue
                        option = token.split("=", 1)[0]
                        if (
                            option in _READ_ONLY_OPTION_VALUES.get(executable_name, set())
                            and "=" not in token
                        ):
                            if index + 1 >= len(arguments):
                                read_only = False
                                unit_complete = False
                                break
                            value = arguments[index + 1]
                            if option in _READ_ONLY_PATH_OPTIONS.get(
                                executable_name, set()
                            ) and not add(value, "path", must_exist=True):
                                read_only = False
                                unit_complete = False
                                break
                            index += 2
                            continue
                        if executable_name in _READ_ONLY_PATH_OPTION_EXECUTABLES and "=" in token:
                            if not add(token.split("=", 1)[1], "path", must_exist=True):
                                read_only = False
                                unit_complete = False
                                break
                        index += 1
                        continue
                    operands.append(token)
                    index += 1
                if executable_name == "pwd" and operands:
                    read_only = False
                else:
                    for operand in operands:
                        if not add(operand, "path", must_exist=True):
                            read_only = False
                            unit_complete = False
                            break

            if output_redirection or descriptor_redirection:
                read_only = False
            commands[unit_index]["classification"] = "read" if read_only else "write"
            complete.append(unit_complete)

        return result(
            targets=targets,
            commands=commands,
            analysis_complete=all(complete),
        )

    @staticmethod
    def _inspection_revision(result: dict) -> str:
        return hashlib.sha256(
            json.dumps(result, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

    def _trusted_executable(self, executable: str, current_directory: str) -> Path | None:
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
            return resolved
        return None

    def execution_argv(
        self,
        *,
        current_directory: str,
        program: str | None,
        args: list[str],
        command: str | None,
        expected_inspection_revision: str | None = None,
    ) -> tuple[list[str], dict | None]:
        inspection = None
        if command is None:
            executable_path = self.executables.get(program or "")
            if executable_path is None:
                raise AgentError("process.notAllowed", "Program alias is not configured")
            return [os.fspath(executable_path), *args], inspection
        if self.shell is None:
            raise AgentError("process.notAllowed", "Shell execution is disabled")
        if expected_inspection_revision is not None:
            inspection = self.inspect_shell(command, current_directory)
            actual_revision = inspection.get("inspectionRevision") or self._inspection_revision(
                inspection
            )
            if actual_revision != expected_inspection_revision:
                raise AgentError("process.inspectionChanged", "Shell inspection changed")
        if inspection is not None and inspection.get("readOnly") is True:
            commands = inspection.get("commands", [])
            simple = not any(character in command for character in "();<>|&")
            tokens = shlex.split(command, comments=True)
            if simple and len(commands) == 1 and tokens == commands[0].get("tokens"):
                current = self.filesystem.resolve_directory(current_directory)["path"]
                executable = self._trusted_executable(tokens[0], current)
                if executable is None:
                    raise AgentError("process.inspectionChanged", "Shell inspection changed")
                return [os.fspath(executable), *tokens[1:]], inspection
        return [os.fspath(self.shell), "-c", command], inspection

    @staticmethod
    def execution_environment(home: str, inspection: dict | None) -> dict[str, str]:
        return {
            "HOME": home,
            "LANG": os.environ.get("LANG", "C.UTF-8"),
            "PATH": (
                "/usr/bin:/bin"
                if inspection is not None and inspection.get("readOnly") is True
                else "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
            ),
        }

    async def exec(
        self,
        *,
        current_directory: str,
        program: str | None,
        args: list[str],
        command: str | None,
        timeout: float | None,
        stream: StreamCallback,
        expected_inspection_revision: str | None = None,
    ) -> dict:
        argv, inspection = self.execution_argv(
            current_directory=current_directory,
            program=program,
            args=args,
            command=command,
            expected_inspection_revision=expected_inspection_revision,
        )
        cwd, cwd_fd = self._cwd(current_directory)
        environment = self.execution_environment(cwd, inspection)
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
