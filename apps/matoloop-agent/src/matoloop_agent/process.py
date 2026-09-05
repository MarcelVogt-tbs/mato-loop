from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shlex
import signal
import stat
import time
from collections.abc import Awaitable, Callable
from pathlib import Path

import tree_sitter_bash
from tree_sitter import Language, Node, Parser

from .config import AgentConfig
from .filesystem import AgentError, Filesystem, _parts

StreamCallback = Callable[[str, str], Awaitable[None]]

_BASH_LANGUAGE = Language(tree_sitter_bash.language())
_ALWAYS_WRITES = {
    "chgrp",
    "chmod",
    "chown",
    "cp",
    "install",
    "kill",
    "ln",
    "mkdir",
    "mkfifo",
    "mknod",
    "mount",
    "mv",
    "reboot",
    "rm",
    "rmdir",
    "shutdown",
    "tee",
    "touch",
    "truncate",
    "umount",
    "unlink",
}
_WRITE_SUBCOMMANDS = {
    "apt": {"autoremove", "full-upgrade", "install", "purge", "remove", "upgrade"},
    "apt-get": {"autoremove", "dist-upgrade", "install", "purge", "remove", "upgrade"},
    "bun": {"add", "install", "link", "remove", "update"},
    "cargo": {"add", "build", "clean", "fix", "install", "remove", "update"},
    "docker": {
        "build",
        "commit",
        "compose",
        "container",
        "exec",
        "image",
        "kill",
        "login",
        "logout",
        "network",
        "pull",
        "push",
        "restart",
        "rm",
        "rmi",
        "run",
        "start",
        "stop",
        "system",
        "tag",
        "volume",
    },
    "git": {
        "add",
        "am",
        "apply",
        "bisect",
        "checkout",
        "cherry-pick",
        "clean",
        "clone",
        "commit",
        "fetch",
        "gc",
        "init",
        "merge",
        "mv",
        "pull",
        "push",
        "rebase",
        "reset",
        "restore",
        "revert",
        "rm",
        "switch",
    },
    "go": {"build", "clean", "generate", "get", "install", "mod", "work"},
    "kubectl": {
        "annotate",
        "apply",
        "attach",
        "autoscale",
        "cordon",
        "create",
        "delete",
        "drain",
        "edit",
        "exec",
        "expose",
        "label",
        "patch",
        "replace",
        "rollout",
        "run",
        "scale",
        "set",
        "taint",
        "uncordon",
    },
    "npm": {"add", "ci", "install", "link", "remove", "uninstall", "update"},
    "pip": {"install", "uninstall"},
    "pip3": {"install", "uninstall"},
    "pnpm": {"add", "deploy", "install", "link", "remove", "self-update", "update"},
    "systemctl": {
        "daemon-reload",
        "disable",
        "edit",
        "enable",
        "isolate",
        "kill",
        "mask",
        "reenable",
        "reload",
        "restart",
        "revert",
        "set-default",
        "start",
        "stop",
        "unmask",
    },
    "yarn": {"add", "install", "link", "remove", "set", "unlink", "upgrade"},
}
_FIND_WRITE_ACTIONS = {
    "-delete",
    "-exec",
    "-execdir",
    "-fls",
    "-fprint",
    "-fprint0",
    "-fprintf",
    "-ok",
    "-okdir",
}
_CURL_WRITE_OPTIONS = {
    "--data",
    "--data-ascii",
    "--data-binary",
    "--data-raw",
    "--data-urlencode",
    "--form",
    "--form-string",
    "--output",
    "--remote-name",
    "--remote-name-all",
    "--upload-file",
    "-F",
    "-O",
    "-T",
    "-d",
    "-o",
}
_MUTATING_HTTP_METHODS = {"DELETE", "PATCH", "POST", "PUT"}
_READ_PATH_COMMANDS = {
    "cat",
    "cmp",
    "head",
    "ls",
    "readlink",
    "realpath",
    "sha256sum",
    "stat",
    "tail",
    "wc",
}
_OPTIONS_WITH_VALUES = {
    "cmp": {"-i", "--ignore-initial", "-n", "--bytes"},
    "head": {"-c", "--bytes", "-n", "--lines"},
    "ls": {"-I", "--ignore", "-w", "--width"},
    "realpath": {"--relative-base", "--relative-to"},
    "stat": {"-c", "--format", "--printf"},
    "tail": {"-c", "--bytes", "-n", "--lines", "--pid", "-s", "--sleep-interval"},
}
_OUTPUT_REDIRECT = re.compile(r"^\s*(?:\d+)?\s*(&>>|&>|>>|>\||<>|>&|>|<<-?|<<<|<&|<)")
_INTERPRETERS = {"bash", "dash", "node", "perl", "python", "python3", "ruby", "sh"}
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


def _option_present(arguments: list[str], options: set[str]) -> bool:
    return any(
        argument in options
        or any(option.startswith("--") and argument.startswith(option + "=") for option in options)
        for argument in arguments
    )


def _short_option_present(arguments: list[str], options: set[str]) -> bool:
    return any(
        argument.startswith(option) and argument != option
        for argument in arguments
        for option in options
        if option.startswith("-") and not option.startswith("--")
    )


def _subcommand(arguments: list[str], value_options: set[str]) -> str | None:
    index = 0
    while index < len(arguments):
        argument = arguments[index]
        if argument == "--":
            return arguments[index + 1] if index + 1 < len(arguments) else None
        if argument in value_options:
            index += 2
            continue
        if argument.startswith("-"):
            index += 1
            continue
        return argument
    return None


def _definitely_writes(tokens: list[str]) -> bool:
    executable = Path(tokens[0]).name
    arguments = tokens[1:]
    if executable in _ALWAYS_WRITES:
        return True
    if executable in _WRITE_SUBCOMMANDS:
        value_options = (
            {"-C", "-c", "--git-dir", "--work-tree", "--namespace"}
            if executable == "git"
            else {"--cache", "--prefix", "--registry", "--userconfig"}
            if executable in {"npm", "pnpm", "yarn"}
            else set()
        )
        subcommand = _subcommand(arguments, value_options)
        if subcommand in _WRITE_SUBCOMMANDS[executable]:
            return True
        if executable == "git":
            if subcommand == "branch":
                remaining = arguments[arguments.index(subcommand) + 1 :]
                if any(
                    option in remaining
                    for option in {
                        "--contains",
                        "--format",
                        "--list",
                        "--merged",
                        "--no-contains",
                        "--no-merged",
                        "--show-current",
                    }
                ):
                    return False
                return any(
                    option in remaining
                    for option in {
                        "-c",
                        "-C",
                        "-d",
                        "-D",
                        "-m",
                        "-M",
                        "--copy",
                        "--delete",
                        "--edit-description",
                        "--move",
                        "--set-upstream-to",
                        "--unset-upstream",
                    }
                ) or any(not argument.startswith("-") for argument in remaining)
            if subcommand == "tag":
                remaining = arguments[arguments.index(subcommand) + 1 :]
                if any(option in remaining for option in {"-l", "--list"}):
                    return False
                return any(
                    option in remaining
                    for option in {"-a", "-d", "-f", "-s", "--delete", "--force", "--sign"}
                ) or any(not argument.startswith("-") for argument in remaining)
            if subcommand == "config":
                if any(
                    option in arguments
                    for option in {"--unset", "--unset-all", "--rename-section", "--remove-section"}
                ):
                    return True
                if any(
                    option in arguments
                    for option in {"--get", "--get-all", "--get-regexp", "--list", "-l"}
                ):
                    return False
                values = [
                    argument
                    for argument in arguments[arguments.index(subcommand) + 1 :]
                    if not argument.startswith("-")
                ]
                return len(values) > 1
            if subcommand == "remote":
                nested = _subcommand(arguments[arguments.index(subcommand) + 1 :], set())
                return nested not in {None, "get-url", "show"}
            if subcommand == "stash":
                nested = _subcommand(arguments[arguments.index(subcommand) + 1 :], set())
                return nested not in {"list", "show"}
            if subcommand == "worktree":
                nested = _subcommand(arguments[arguments.index(subcommand) + 1 :], set())
                return nested in {"add", "lock", "move", "prune", "remove", "repair", "unlock"}
    if executable == "curl":
        if _option_present(arguments, _CURL_WRITE_OPTIONS) or _short_option_present(
            arguments, {"-F", "-O", "-T", "-d", "-o"}
        ):
            return True
        for index, argument in enumerate(arguments):
            if argument in {"-X", "--request"} and index + 1 < len(arguments):
                return arguments[index + 1].upper() in _MUTATING_HTTP_METHODS
            if argument.startswith("-X") and argument != "-X":
                return argument[2:].upper() in _MUTATING_HTTP_METHODS
            if argument.startswith("--request="):
                return argument.partition("=")[2].upper() in _MUTATING_HTTP_METHODS
    if executable == "dd" and any(argument.startswith("of=") for argument in arguments):
        return True
    if executable == "find" and any(argument in _FIND_WRITE_ACTIONS for argument in arguments):
        return True
    if executable in {"perl", "sed"} and any(
        argument == "-i"
        or (argument.startswith("-i") and not argument.startswith("--"))
        or argument.startswith("--in-place")
        for argument in arguments
    ):
        return True
    if executable == "sort" and _option_present(arguments, {"-o", "--output"}):
        return True
    if executable == "printf" and (
        _option_present(arguments, {"-v"})
        or _short_option_present(arguments, {"-v"})
        or any(
            re.search(
                r"%(?:\d+\$)?[-+ #0']*(?:\*|\d+)?(?:\.(?:\*|\d+))?"
                r"(?:hh|h|ll|l|j|z|t|L)?n",
                argument,
            )
            for argument in arguments
        )
    ):
        return True
    if executable == "wget":
        return "--spider" not in arguments and not any(
            argument in {"-O", "--output-document"}
            and index + 1 < len(arguments)
            and arguments[index + 1] == "-"
            for index, argument in enumerate(arguments)
        )
    return False


def _path_operands(tokens: list[str]) -> list[tuple[str, str, bool]]:
    executable = Path(tokens[0]).name
    arguments = tokens[1:]
    if tokens[0] in {".", "source"}:
        return [(arguments[0], "source", True)] if arguments else []
    if executable == "find":
        paths = []
        for argument in arguments:
            if argument.startswith("-") or argument in {"!", "(", ")"}:
                break
            paths.append((argument, "path", True))
        return paths
    if executable == "git":
        return [
            (arguments[index + 1], "path", True)
            for index, argument in enumerate(arguments[:-1])
            if argument == "-C"
        ]
    if executable in _INTERPRETERS or executable.startswith("python"):
        if any(argument in {"-c", "--command", "-e", "--eval"} for argument in arguments):
            return []
        script = next((argument for argument in arguments if not argument.startswith("-")), None)
        return [(script, "script", True)] if script else []
    if executable not in _READ_PATH_COMMANDS:
        return []
    operands = []
    skip_next = False
    for argument in arguments:
        if skip_next:
            skip_next = False
            continue
        option = argument.split("=", 1)[0]
        if option in _OPTIONS_WITH_VALUES.get(executable, set()):
            skip_next = "=" not in argument
            continue
        if argument.startswith("-"):
            continue
        operands.append((argument, "path", True))
    return operands


def _nodes(root: Node, node_type: str) -> list[Node]:
    result: list[Node] = []
    stack = [root]
    while stack:
        node = stack.pop()
        if node.type == node_type:
            result.append(node)
        stack.extend(reversed(node.children))
    return result


def _node_text(source: bytes, node: Node) -> str:
    return source[node.start_byte : node.end_byte].decode("utf-8")


def _command_tokens(source: bytes, node: Node) -> list[str] | None:
    parts = []
    for index, child in enumerate(node.children):
        if node.field_name_for_child(index) in {"name", "argument"}:
            parts.append(_node_text(source, child))
    try:
        tokens = shlex.split(" ".join(parts), comments=False, posix=True)
    except ValueError:
        return None
    return tokens or None


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
        if not command.strip():
            return result()
        source = command.encode("utf-8")
        tree = Parser(_BASH_LANGUAGE).parse(source)
        if tree.root_node.has_error:
            return result()

        targets: list[dict] = []
        seen_targets: set[tuple[str, str]] = set()

        def add(path: str, kind: str, *, must_exist: bool) -> bool:
            try:
                target = self.filesystem.resolve_path_target(path, current, must_exist=must_exist)
            except AgentError:
                return False
            target["kind"] = "directory" if target.pop("targetKind", None) == "directory" else kind
            key = (target["path"], target["kind"])
            if key in seen_targets:
                return True
            seen_targets.add(key)
            targets.append(target)
            return True

        command_nodes = _nodes(tree.root_node, "command")
        if len(command_nodes) > 64:
            return result()
        commands: list[dict] = []
        for node in command_nodes:
            tokens = _command_tokens(source, node)
            if tokens is None or len(tokens) > 256 or any(len(token) > 1_024 for token in tokens):
                return result(targets=targets, commands=commands)
            command_unit = {
                "tokens": tokens,
                "classification": "write" if _definitely_writes(tokens) else "read",
            }
            prefix = _command_prefix(tokens)
            if prefix is not None:
                command_unit["prefix"] = prefix
            commands.append(command_unit)

        for redirect in _nodes(tree.root_node, "file_redirect"):
            text = _node_text(source, redirect)
            match = _OUTPUT_REDIRECT.match(text)
            if match is None:
                return result(targets=targets, commands=commands)
            operator = match.group(1)
            try:
                destination_parts = shlex.split(text[match.end() :].strip(), posix=True)
            except ValueError:
                return result(targets=targets, commands=commands)
            destination = destination_parts[0] if len(destination_parts) == 1 else ""
            descriptor_only = operator in {">&", "<&"} and destination.lstrip("&").isdigit()
            writes = (
                ">" in operator
                and operator not in {">&"}
                or (operator == ">&" and not descriptor_only)
            )
            if not writes:
                if (
                    operator == "<"
                    and destination
                    and not add(destination, "input", must_exist=True)
                ):
                    return result(targets=targets, commands=commands)
                continue
            if commands:
                commands[0]["classification"] = "write"
            if (
                destination
                and not descriptor_only
                and not add(destination, "output", must_exist=False)
            ):
                return result(targets=targets, commands=commands)

        for command_unit in commands:
            tokens = command_unit["tokens"]
            if "/" in tokens[0] and not add(tokens[0], "executable", must_exist=True):
                return result(targets=targets, commands=commands)
            for path, kind, must_exist in _path_operands(tokens):
                if not add(path, kind, must_exist=must_exist):
                    return result(targets=targets, commands=commands)
            for token in tokens[1:]:
                if (
                    token.startswith("-")
                    or "://" in token
                    or any(character in token for character in "$`*?[]{}~")
                    or not token.startswith(("/", "./", "../"))
                ):
                    continue
                if not add(token, "path", must_exist=False):
                    return result(targets=targets, commands=commands)

        return result(targets=targets, commands=commands, analysis_complete=True)

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
        command: str,
        expected_inspection_revision: str | None = None,
    ) -> tuple[list[str], dict | None]:
        inspection = None
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
            simple = not any(character in command for character in "();<>|&$`*?[]{}~\n\r")
            tokens = shlex.split(command, comments=True)
            if simple and len(commands) == 1 and tokens == commands[0].get("tokens"):
                current = self.filesystem.resolve_directory(current_directory)["path"]
                executable = self._trusted_executable(tokens[0], current)
                if executable is not None:
                    return [os.fspath(executable), *tokens[1:]], inspection
        return [os.fspath(self.shell), "-c", command], inspection

    @staticmethod
    def execution_environment(home: str, inspection: dict | None) -> dict[str, str]:
        return {
            "HOME": home,
            "LANG": os.environ.get("LANG", "C.UTF-8"),
            "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        }

    async def exec(
        self,
        *,
        current_directory: str,
        command: str,
        timeout: float | None,
        stream: StreamCallback,
        expected_inspection_revision: str | None = None,
    ) -> dict:
        argv, inspection = self.execution_argv(
            current_directory=current_directory,
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
