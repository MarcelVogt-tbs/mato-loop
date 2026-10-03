from __future__ import annotations

import asyncio
import fnmatch
import getopt
import hashlib
import json
import os
import posixpath
import re
import shlex
import shutil
import signal
import stat
import time
from collections.abc import Awaitable, Callable, Iterator
from pathlib import Path

import tree_sitter_bash
from tree_sitter import Language, Node, Parser

from .config import AgentConfig
from .errors import AgentError
from .filesystem import Filesystem, _parts, _path

StreamCallback = Callable[[str, str], Awaitable[None]]

_BASH_LANGUAGE = Language(tree_sitter_bash.language())
EXECUTION_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
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
    "shred",
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
        "cp",
        "create",
        "exec",
        "image",
        "import",
        "kill",
        "load",
        "login",
        "logout",
        "network",
        "pause",
        "pull",
        "push",
        "restart",
        "rename",
        "rm",
        "rmi",
        "run",
        "start",
        "stop",
        "system",
        "tag",
        "unpause",
        "update",
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
_DOCKER_READ_SUBCOMMANDS = {
    "compose": {"config", "events", "images", "logs", "ls", "port", "ps", "top", "version"},
    "container": {"diff", "inspect", "logs", "ls", "port", "ps", "stats", "top", "wait"},
    "image": {"history", "inspect", "ls"},
    "network": {"inspect", "ls"},
    "volume": {"inspect", "ls"},
    "system": {"df", "events", "info"},
}
_DOCKER_WRITE_SUBCOMMANDS = {
    "compose": {
        "build",
        "cp",
        "create",
        "down",
        "exec",
        "kill",
        "pause",
        "pull",
        "push",
        "restart",
        "rm",
        "run",
        "scale",
        "start",
        "stop",
        "unpause",
        "up",
        "watch",
    },
    "container": {
        "attach",
        "commit",
        "cp",
        "create",
        "exec",
        "kill",
        "pause",
        "prune",
        "rename",
        "restart",
        "rm",
        "run",
        "start",
        "stop",
        "unpause",
        "update",
    },
    "image": {"build", "import", "load", "prune", "pull", "push", "rm", "tag"},
    "network": {"connect", "create", "disconnect", "prune", "rm"},
    "volume": {"create", "prune", "rm", "update"},
    "system": {"prune"},
}
_DOCKER_EXEC_COMMANDS = (
    ["docker", "exec"],
    ["docker", "container", "exec"],
    ["docker", "compose", "exec"],
)
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
    "--json",
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
_DYNAMIC_SHELL_NODES = {
    "expansion",
    "simple_expansion",
    "command_substitution",
    "process_substitution",
    "arithmetic_expansion",
}
_PREFIX_ARITY = {
    ("aws",): 3,
    ("az",): 3,
    ("bun",): 2,
    ("bun", "run"): 3,
    ("cargo",): 2,
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


def _git_clean_dry_run(arguments: list[str]) -> bool:
    # GNU getopt can stop at operands under this host setting; never exempt a partial parse.
    if os.environ.get("POSIXLY_CORRECT"):
        return False
    try:
        _, remaining = getopt.getopt(
            arguments,
            "C:c:",
            [
                "git-dir=",
                "work-tree=",
                "namespace=",
                "config-env=",
                "no-pager",
                "paginate",
                "bare",
                "literal-pathspecs",
                "glob-pathspecs",
                "noglob-pathspecs",
                "icase-pathspecs",
                "no-optional-locks",
                "no-replace-objects",
            ],
        )
        if remaining[:1] != ["clean"]:
            return False
        options, _ = getopt.gnu_getopt(
            remaining[1:],
            "dfinqxXe:",
            ["dry-run", "force", "interactive", "quiet", "exclude="],
        )
    except getopt.GetoptError:
        return False
    return any(option in {"-n", "--dry-run"} for option, _ in options) and not any(
        option in {"-i", "--interactive"} for option, _ in options
    )


def _xargs_payload(
    arguments: list[str], depth: int = 0
) -> tuple[list[str], list[tuple[str, int, str]]] | None:
    """Return static argv and read-source operands with their original indices and kinds."""
    if depth > 8:
        return None
    required = {
        "--arg-file",
        "--delimiter",
        "--max-args",
        "--max-procs",
        "--max-chars",
        "--process-slot-var",
    }
    optional = {"--eof", "--replace", "--max-lines"}
    flags = {
        "--null",
        "--open-tty",
        "--interactive",
        "--no-run-if-empty",
        "--verbose",
        "--exit",
        "--show-limits",
        "--help",
        "--version",
    }
    files = []
    index = 0
    while index < len(arguments):
        argument = arguments[index]
        if argument == "--":
            index += 1
            break
        if argument == "-" or not argument.startswith("-"):
            break
        if argument.startswith("--"):
            option, separator, value = argument.partition("=")
            if option in required:
                if not separator:
                    index += 1
                    if index == len(arguments):
                        return None
                    value = arguments[index]
                if option == "--arg-file":
                    files.append((value, index, "input"))
            elif option not in optional and (option not in flags or separator):
                return None
        else:
            for offset, option in enumerate(argument[1:], start=1):
                if option in "0oprtx":
                    continue
                if option not in "adEILnPseil":
                    return None
                value = argument[offset + 1 :]
                if not value and option not in "eil":
                    index += 1
                    if index == len(arguments):
                        return None
                    value = arguments[index]
                if option == "a":
                    files.append((value, index, "input"))
                break
        index += 1
    payload = arguments[index:] or ["echo"]
    if "/" in payload[0]:
        files.append((payload[0], index, "executable"))
    if Path(payload[0]).name == "xargs":
        nested = _xargs_payload(payload[1:], depth + 1)
        if nested is not None:
            files.extend((path, index + 1 + offset, kind) for path, offset, kind in nested[1])
    return payload, files


def _docker_command(arguments: list[str]) -> tuple[list[str], list[str]] | None:
    # Classification and reusable prefixes must agree on the operation, not option values.
    semantic = ["docker"]
    value_options = {
        "--config",
        "--context",
        "-c",
        "--host",
        "-H",
        "--log-level",
        "-l",
        "--tlscacert",
        "--tlscert",
        "--tlskey",
    }
    flags = {"--debug", "-D", "--help", "-h", "--tls", "--tlsverify", "--version", "-v"}
    while arguments:
        argument = arguments[0]
        # Peel known boolean short flags before handling an optional value-taking option.
        while len(argument) > 2 and argument[:2] in flags and argument[2] not in "=-":
            argument = "-" + argument[2:]
        option = argument.partition("=")[0]
        if argument == "--":
            arguments = arguments[1:]
            if not arguments:
                return None
        elif argument in value_options:
            if len(arguments) < 2:
                return None
            arguments = arguments[2:]
            continue
        elif (
            option in value_options
            or option in flags
            or _short_option_present([argument], value_options)
        ):
            arguments = arguments[1:]
            continue
        elif argument.startswith("-"):
            # Unknown option arity cannot yield a trustworthy operation or prefix.
            return None
        semantic.append(arguments[0])
        arguments = arguments[1:]
        if len(semantic) == 3 or semantic[1] not in _DOCKER_READ_SUBCOMMANDS:
            return semantic, arguments
        if semantic[1] == "compose":
            value_options |= {
                "--ansi",
                "--env-file",
                "--file",
                "-f",
                "--parallel",
                "--profile",
                "--progress",
                "--project-directory",
                "--project-name",
                "-p",
            }
            flags |= {"--all-resources", "--compatibility", "--dry-run"}
    return (semantic, arguments) if len(semantic) == 1 else None


def _docker_exec_payload(
    arguments: list[str], *, compose: bool
) -> tuple[list[str], list[str]] | None:
    value_options = {"--env", "-e", "--user", "-u", "--workdir", "-w"}
    value_options |= {"--index"} if compose else {"--env-file", "--detach-keys"}
    flags = {"--detach", "-d", "--interactive", "-i", "--tty", "-t"}
    if compose:
        flags |= {"--no-TTY", "-T"}
    host_files = []
    while arguments:
        argument = arguments[0]
        if argument == "--":
            arguments = arguments[1:]
            break
        if not argument.startswith("-"):
            break
        while len(argument) > 2 and argument[:2] in flags and argument[2] not in "=-":
            argument = "-" + argument[2:]
        option, separator, value = argument.partition("=")
        if option in value_options:
            if not separator:
                if len(arguments) < 2:
                    return None
                value = arguments[1]
                arguments = arguments[1:]
            if option == "--env-file":
                host_files.append(value)
        elif option in flags and (not separator or value in {"true", "false"}):
            pass
        elif any(
            argument.startswith(option) and argument != option for option in ("-e", "-u", "-w")
        ):
            pass
        else:
            # Unknown option arity and privileged execution retain the write classification.
            return None
        arguments = arguments[1:]
    if len(arguments) < 2 or not arguments[0] or not arguments[1] or arguments[1].startswith("-"):
        return None
    return arguments[1:], host_files


def _docker_run_host_paths(arguments: list[str]) -> list[str] | None:
    value_options = {"--mount", "--volume", "-v", "--workdir", "-w"}
    flags = {
        "--detach",
        "-d",
        "--init",
        "--interactive",
        "-i",
        "--read-only",
        "--rm",
        "--tty",
        "-t",
    }
    host_paths = []
    while arguments:
        argument = arguments[0]
        if argument == "--":
            return host_paths if len(arguments) > 1 else None
        if not argument.startswith("-"):
            return host_paths
        while len(argument) > 2 and argument[:2] in flags and argument[2] not in "=-":
            argument = "-" + argument[2:]
        option, separator, value = argument.partition("=")
        if option in value_options:
            if not separator:
                if len(arguments) < 2:
                    return None
                value = arguments[1]
                arguments = arguments[1:]
            if option in {"--volume", "-v"}:
                parts = value.split(":")
                if len(parts) not in {1, 2, 3} or not all(parts):
                    return None
                if len(parts) > 1 and parts[0].startswith(("/", "./", "../")):
                    host_paths.append(parts[0])
            elif option == "--mount":
                fields = dict(field.split("=", 1) for field in value.split(",") if "=" in field)
                if fields.get("type") == "bind":
                    source = fields.get("source") or fields.get("src")
                    if not source:
                        return None
                    host_paths.append(source)
        elif option in flags and (not separator or value in {"true", "false"}):
            pass
        else:
            return None
        arguments = arguments[1:]
    return None


def _container_command_writes(tokens: list[str], depth: int) -> bool:
    # ponytail: bound wrapper nesting; unfamiliar shell forms keep the former write class.
    if depth > 8 or not tokens or any(c in tokens[0] for c in "$`*?[]{}~"):
        return True
    executable = Path(tokens[0]).name
    if executable == "xargs":
        payload = _xargs_payload(tokens[1:])
        return payload is None or _container_command_writes(payload[0], depth + 1)
    if executable not in {"sh", "bash", "dash"}:
        if tokens[0] == "." or executable in {"env", "sudo", "eval", "exec", "command", "source"}:
            return True
        return _definitely_writes(tokens, depth=depth)
    if len(tokens) < 3 or not re.fullmatch(r"-[elux]*c[elux]*", tokens[1]) or "\\\n" in tokens[2]:
        return True
    source = tokens[2].encode("utf-8")
    try:
        root = _parse_shell(tokens[2])
    except AgentError as exc:
        if exc.code != "process.invalidSyntax":
            raise
        return True
    if any(
        _nodes(root, kind)
        for kind in {"expansion", "simple_expansion", "variable_assignment", "ansi_c_string"}
    ):
        return True
    for redirect in _nodes(root, "file_redirect"):
        match = _OUTPUT_REDIRECT.match(_node_text(source, redirect))
        if match is None:
            return True
        operator = match.group(1)
        if ">" in operator:
            destination = redirect.child_by_field_name("destination")
            if (
                operator != ">&"
                or destination is None
                or not re.fullmatch(r"\d+-?|-", _node_text(source, destination))
            ):
                # Container /dev/null cannot use the host's verified discard exemption.
                return True
    commands = _nodes(root, "command")
    if len(commands) > 64:
        return True
    for command in commands:
        words = _command_words(command)
        inner = _command_tokens(source, words)
        if inner is None or len(inner) != len(words):
            return True
        if Path(inner[0]).name in {"docker", "sh", "bash", "dash"} and _unresolved_wrapper_words(
            source, words
        ):
            return True
        if _container_command_writes(inner, depth + 1):
            return True
    return False


def _python_command(arguments: list[str]) -> tuple[str, list[str]] | None:
    # Stop at the execution operand: subsequent options belong to the script/module.
    while arguments:
        argument, *remaining = arguments
        if argument == "--":
            return "script", remaining
        if argument == "-" or not argument.startswith("-"):
            return "script", arguments
        if argument in {"--help", "--help-env", "--help-xoptions", "--help-all", "--version"}:
            return "", []
        for index, option in enumerate(argument[1:], start=1):
            if option in "cmWX":
                value = argument[index + 1 :]
                if not value:
                    if not remaining:
                        return None
                    value, *remaining = remaining
                if option in "cm":
                    return option, [value, *remaining]
                break
            if option in "hV?":
                return "", []
            if option not in "bBdEiIOPqRsSuvx":
                return None
        arguments = remaining
    return "", []


def _curl_options(arguments: list[str]) -> Iterator[tuple[str, str, int]]:
    value_options = {
        "--url",
        "--user-agent",
        "--header",
        "--user",
        "--proxy-user",
        "--proxy",
        "--max-time",
        "--connect-timeout",
        "--retry",
        "--range",
        "--referer",
        "--cookie",
        "--cacert",
        "--cert",
        "--key",
        "--interface",
        "--resolve",
        "--connect-to",
        "--config",
        "--write-out",
        "--request",
        *(
            option
            for option in _CURL_WRITE_OPTIONS
            if option.startswith("--") and option not in {"--remote-name", "--remote-name-all"}
        ),
    }
    skip_next = False
    for index, argument in enumerate(arguments):
        if skip_next:
            skip_next = False
            continue
        if argument == "--":
            break
        option, separator, value = argument.partition("=")
        following = arguments[index + 1] if index + 1 < len(arguments) else ""
        if option in value_options:
            skip_next = not separator
            yield option, value if separator else following, index if separator else index + 1
        elif option.startswith("--") and option in _CURL_WRITE_OPTIONS:
            yield option, "", index
        elif argument.startswith("-") and not argument.startswith("--"):
            for offset, option in enumerate(argument[1:], start=1):
                if option == "O":
                    yield "-O", "", index
                elif option in "AbcCDeEFHKmPQrtTUuwxXyYzdo":
                    skip_next = offset == len(argument) - 1
                    value = argument[offset + 1 :] or following
                    yield "-" + option, value, index + 1 if skip_next else index
                    break
                # Only peel known switches; other options may consume the remaining text.
                elif option not in "#012346aBfgGiIjJklLMnNpRqsSvVZ":
                    break


def _definitely_writes(
    tokens: list[str], *, depth: int = 0, curl_discard_outputs: frozenset[int] = frozenset()
) -> bool:
    executable = Path(tokens[0]).name
    arguments = tokens[1:]
    if executable == "xargs":
        # Bound executor nesting; stdin-derived argv remains incomplete in inspection.
        if depth > 8:
            return True
        payload = _xargs_payload(arguments)
        return payload is not None and _definitely_writes(payload[0], depth=depth + 1)
    if executable.startswith("python"):
        python = _python_command(arguments)
        if python is not None and python[0] == "m" and python[1][:1] == ["pip"]:
            return _definitely_writes(python[1], depth=depth)
    if executable in _ALWAYS_WRITES:
        return True
    if executable == "docker":
        command = _docker_command(arguments)
        if command is None:
            return False
        semantic, remaining = command
        if len(semantic) == 1:
            return False
        if semantic in _DOCKER_EXEC_COMMANDS:
            payload = _docker_exec_payload(remaining, compose=semantic[1] == "compose")
            return payload is None or _container_command_writes(payload[0], depth + 1)
        group = semantic[1]
        if semantic[1:] in (["save"], ["export"], ["image", "save"], ["container", "export"]) and (
            _option_present(remaining, {"--output", "-o"})
            or _short_option_present(remaining, {"-o"})
        ):
            return True
        if group in _DOCKER_READ_SUBCOMMANDS:
            if semantic[2] in _DOCKER_WRITE_SUBCOMMANDS[group]:
                return True
            return (
                group == "compose"
                and semantic[2] == "config"
                and (
                    _option_present(remaining, {"--output", "-o", "--lock-image-digests"})
                    or _short_option_present(remaining, {"-o"})
                )
            )
        return group in _WRITE_SUBCOMMANDS["docker"]
    if executable in _WRITE_SUBCOMMANDS:
        value_options = (
            {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--config-env"}
            if executable == "git"
            else {"--cache", "--prefix", "--registry", "--userconfig"}
            if executable in {"npm", "pnpm", "yarn"}
            else set()
        )
        subcommand = _subcommand(arguments, value_options)
        if executable == "git" and subcommand == "clean":
            return not _git_clean_dry_run(arguments)
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
                return nested in {
                    "add",
                    "prune",
                    "remove",
                    "rename",
                    "rm",
                    "set-branches",
                    "set-head",
                    "set-url",
                    "update",
                }
            if subcommand == "stash":
                nested = _subcommand(arguments[arguments.index(subcommand) + 1 :], set())
                return nested in {
                    None,
                    "apply",
                    "branch",
                    "clear",
                    "create",
                    "drop",
                    "pop",
                    "push",
                    "save",
                    "store",
                    "export",
                    "import",
                }
            if subcommand == "worktree":
                nested = _subcommand(arguments[arguments.index(subcommand) + 1 :], set())
                return nested in {"add", "lock", "move", "prune", "remove", "repair", "unlock"}
    if executable == "curl":
        for option, value, index in _curl_options(arguments):
            if option in {"-o", "--output"}:
                if value != "-" and index not in curl_discard_outputs:
                    return True
            elif option in _CURL_WRITE_OPTIONS:
                return True
            elif option in {"-X", "--request"} and value.upper() in _MUTATING_HTTP_METHODS:
                return True
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


def _path_operands(tokens: list[str], depth: int = 0) -> list[tuple[str, str, bool]]:
    executable = Path(tokens[0]).name
    arguments = tokens[1:]
    if executable == "xargs":
        if depth > 8:
            return []
        payload = _xargs_payload(arguments)
        if payload is None:
            return []
        # Read sources are retained separately so executable symlink chains use the
        # ordinary executable resolver, not generic no-follow content inspection.
        return _path_operands(payload[0], depth + 1)
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
    if executable.startswith("python"):
        python = _python_command(arguments)
        if python is not None:
            if python[0] != "script" or not python[1] or python[1][0] == "-":
                return []
            return [(python[1][0], "script", True)]
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


def _parse_shell(command: str) -> Node:
    if len(command) > 256 * 1024:
        raise AgentError("process.inspectionLimitExceeded", "Shell inspection exceeds its limits")
    source = command.encode("utf-8")
    parser = Parser(_BASH_LANGUAGE)
    root = parser.parse(source).root_node
    if root.has_error:
        normalized = bytearray(source)
        redirect_starts = {node.start_byte for node in _nodes(root, "file_redirect")}
        # The grammar misses POSIX <>. Change only its proven redirect operator,
        # preserving byte offsets so inspection and execution still use the original text.
        for error in _nodes(root, "ERROR"):
            start, end = error.start_byte, error.end_byte
            if (
                source[start:end] == b"<"
                and source[start : end + 1] == b"<>"
                and (
                    end in redirect_starts
                    or (
                        error.parent.type == "file_redirect"
                        and any(
                            child.type == ">" and child.start_byte == end
                            for child in error.parent.children
                        )
                    )
                )
            ):
                normalized[start] = ord(">")
            elif (
                source[start:end] == b">"
                and source[start - 1 : end] == b"<>"
                and error.parent.type == "file_redirect"
                and any(
                    child.type == "<" and child.end_byte == start for child in error.parent.children
                )
            ):
                normalized[start - 1] = ord(">")
        if normalized != source:
            root = parser.parse(bytes(normalized)).root_node
        if root.has_error:
            raise AgentError("process.invalidSyntax", "Shell command has invalid syntax")
    return root


def _has_ansi_quoting(word: Node) -> bool:
    # Quote fragments belong to this word; substitutions have their own command owners.
    return word.type == "ansi_c_string" or (
        word.type in {"command_name", "concatenation", "string"}
        and any(_has_ansi_quoting(child) for child in word.named_children)
    )


def _command_tokens(source: bytes, words: list[Node]) -> list[str] | None:
    if any(_has_ansi_quoting(word) for word in words):
        return None
    try:
        tokens = shlex.split(
            " ".join(_node_text(source, word) for word in words), comments=False, posix=True
        )
    except ValueError:
        return None
    return tokens or None


def _command_words(node: Node) -> list[Node]:
    words = [
        child
        for index, child in enumerate(node.children)
        if node.field_name_for_child(index) in {"name", "argument"}
    ]
    redirects = list(node.children_by_field_name("redirect"))
    if (
        node.parent.type == "redirected_statement"
        and node.parent.child_by_field_name("body") == node
    ):
        redirects.extend(node.parent.children_by_field_name("redirect"))
    for redirect in redirects:
        # Repeated destinations after the first word are argv, not redirect targets.
        if redirect.type == "file_redirect":
            words.extend(redirect.children_by_field_name("destination")[1:])
        else:
            words.extend(redirect.children_by_field_name("argument"))
    return sorted(words, key=lambda word: word.start_byte)


def _unresolved_wrapper_words(source: bytes, words: list[Node]) -> bool:
    if any(
        _nodes(word, kind) for word in words for kind in _DYNAMIC_SHELL_NODES | {"ansi_c_string"}
    ) or (words and b"\\\n" in source[words[0].start_byte : words[-1].end_byte]):
        return True
    stack = list(words)
    while stack:
        word = stack.pop()
        if word.type in {"raw_string", "string"}:
            continue
        if not word.children and any(c in _node_text(source, word) for c in "*?[]{}~"):
            return True
        stack.extend(word.children)
    return False


def _command_prefix(tokens: list[str]) -> list[str] | None:
    if "/" in tokens[0]:
        return None
    executable = tokens[0]
    if executable == "xargs":
        return None
    arguments = tokens[1:]
    if executable.startswith("python"):
        python = _python_command(arguments)
        if python is None or python[0] == "c":
            return None
        if python[0] == "m":
            return [executable, python[1][0]]
    if executable == "docker":
        command = _docker_command(arguments)
        if command is not None and command[0] in _DOCKER_EXEC_COMMANDS:
            return None
        return command[0] if command is not None and len(command[0]) > 1 else None
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
        self.max_timeout = config.max_timeout_seconds
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

    def inspect_shell(
        self, command: str, current_directory: str, *, resolve_paths: bool = True
    ) -> dict:
        current = (
            self.filesystem.resolve_directory(current_directory)["path"]
            if resolve_paths
            else _path(_parts(current_directory))
        )
        targets: list[dict] = []
        commands: list[dict] = []
        analysis_complete = resolve_paths
        extraction_complete = True
        redirect_writes = False
        command_writes = False
        unsupported_target_quoting = False

        def result() -> dict:
            if unsupported_target_quoting:
                raise AgentError(
                    "process.unsupportedTargetQuoting", "Shell path target uses unsupported quoting"
                )
            if not extraction_complete:
                for unit in commands:
                    unit.pop("prefix", None)
            writes = redirect_writes or command_writes
            result = {
                "targets": targets,
                "analysisComplete": analysis_complete and extraction_complete,
                "readOnly": not writes,
                "classification": "write" if writes else "read",
                "commands": commands,
            }
            result["inspectionRevision"] = self._inspection_revision(
                {"command": command, "currentDirectory": current, **result}
            )
            return result

        root = _parse_shell(command)
        if not command.strip():
            analysis_complete = False
            return result()
        source = command.encode("utf-8")

        seen_targets: dict[tuple[str, str, str | None], dict] = {}

        def retain(target: dict, kind: str, access: str | None = None) -> None:
            target_kind = target.pop("targetKind", None)
            target["kind"] = (
                "directory"
                if target_kind == "directory" and kind not in {"toolchain_metadata", "discard"}
                else kind
            )
            if access is not None:
                target["access"] = access
            key = (target["path"], target["kind"], target.get("access"))
            if key in seen_targets:
                if "listingFingerprint" in target:
                    previous = seen_targets[key]
                    previous["listingFingerprint"] = hashlib.sha256(
                        (
                            previous.get("listingFingerprint", "") + target["listingFingerprint"]
                        ).encode()
                    ).hexdigest()
                return
            if len(targets) >= 256:
                raise AgentError(
                    "process.inspectionLimitExceeded", "Shell inspection exceeds its limits"
                )
            seen_targets[key] = target
            targets.append(target)

        def add(
            path: str,
            kind: str,
            *,
            access: str,
            must_exist: bool,
            literal: bool = True,
        ) -> None:
            nonlocal analysis_complete
            if not literal and any(c in path for c in "$`*?[]{}~"):
                analysis_complete = False
                # Keep the literal parent visible when expansion is unresolved.
                path = path.rpartition("/")[0]
                if not path or any(c in path for c in "$`*?[]{}~"):
                    return
                kind, must_exist = "directory", True
            # Missing literals still carry their absolute path for policy checks. Hard
            # no-follow/access errors must not be mistaken for incomplete analysis.
            target = (
                self.filesystem.resolve_path_target(path, current, must_exist=False)
                if resolve_paths
                else {"path": _path(_parts(posixpath.normpath(posixpath.join(current, path))))}
            )
            if must_exist and "mode" not in target:
                analysis_complete = False
            retain(target, kind, access)

        def discard(path: str) -> None:
            if not resolve_paths:
                retain({"path": path}, "discard")
                return
            target = self.filesystem.resolve_path_target(path, current, must_exist=True)
            if not stat.S_ISCHR(target["mode"]) or target["rdev"] != os.makedev(1, 3):
                raise AgentError("path.unsupportedType", "Discard target is not the null device")
            retain(target, "discard")

        command_nodes = _nodes(root, "command")
        if len(command_nodes) > 64:
            extraction_complete = False
        units = []
        dynamic_types = _DYNAMIC_SHELL_NODES
        for node in command_nodes:
            words = _command_words(node)
            tokens = _command_tokens(source, words)
            serializable = tokens is not None
            if tokens is None:
                extraction_complete = False
                tokens = []
                for word in words:
                    if _has_ansi_quoting(word):
                        tokens.append(_node_text(source, word))
                        continue
                    try:
                        values = shlex.split(_node_text(source, word), comments=False, posix=True)
                    except ValueError:
                        values = []
                    tokens.append(values[0] if len(values) == 1 else "$unresolved")
            output_indices = set()
            discard_indices = set()
            if tokens and Path(tokens[0]).name == "xargs":
                # Input can append arbitrary paths/argv; no reusable prefix proves them safe.
                analysis_complete = False
                payload = _xargs_payload(tokens[1:])
                if payload is not None:
                    for path, index, kind in payload[1]:
                        output_indices.add(index + 1)
                        if index + 1 >= len(words):
                            continue
                        word = words[index + 1]
                        if _has_ansi_quoting(word):
                            unsupported_target_quoting = True
                        elif kind == "executable" and not _unresolved_wrapper_words(source, [word]):
                            executable_targets = (
                                self.filesystem.resolve_executable_targets(path, current)
                                if resolve_paths
                                else [{"path": posixpath.normpath(posixpath.join(current, path))}]
                            )
                            for target in executable_targets:
                                retain(target, "executable", "read")
                        else:
                            add(
                                path,
                                kind,
                                access="read",
                                must_exist=True,
                                literal=not _unresolved_wrapper_words(source, [word]),
                            )
            if tokens and Path(tokens[0]).name == "curl":
                for option, path, index in _curl_options(tokens[1:]):
                    if option not in {"-o", "--output"} or index + 1 >= len(words):
                        continue
                    word = words[index + 1]
                    output_indices.add(index + 1)
                    if _has_ansi_quoting(word):
                        unsupported_target_quoting = True
                        continue
                    static = not any(_nodes(word, kind) for kind in _DYNAMIC_SHELL_NODES)
                    if static and path == "/dev/null":
                        discard(path)
                        discard_indices.add(index)
                    elif path and path != "-":
                        add(
                            path,
                            "output",
                            access="write",
                            must_exist=False,
                            literal=static and word.type in {"raw_string", "string"},
                        )
            unit_writes = bool(tokens) and _definitely_writes(
                tokens, curl_discard_outputs=frozenset(discard_indices)
            )
            docker = (
                _docker_command(tokens[1:]) if tokens and Path(tokens[0]).name == "docker" else None
            )
            if (
                docker is not None
                and docker[0] in _DOCKER_EXEC_COMMANDS
                and (
                    not serializable
                    or len(tokens) != len(words)
                    or _unresolved_wrapper_words(source, words)
                )
            ):
                unit_writes = True
            command_writes |= unit_writes
            if not tokens:
                continue
            if Path(tokens[0]).name.startswith("python") and _python_command(tokens[1:]) is None:
                extraction_complete = False
            if len(words) != len(tokens) or any(c in tokens[0] for c in "*?[]{}~"):
                extraction_complete = False
            command_unit = {
                "tokens": tokens,
                "classification": "write" if unit_writes else "read",
            }
            if (
                not serializable
                or len(commands) >= 64
                or len(tokens) > 256
                or any(
                    not token
                    or len(token) > 1_024
                    or any(
                        (ord(c) < 32 and not (index > 0 and c in "\n\t")) or 127 <= ord(c) <= 159
                        for c in token
                    )
                    for index, token in enumerate(tokens)
                )
            ):
                extraction_complete = False
            else:
                prefix = _command_prefix(tokens)
                if prefix is not None and not any(
                    any(c in "$`*?[]{}~" or ord(c) < 32 or 127 <= ord(c) <= 159 for c in token)
                    for token in prefix
                ):
                    command_unit["prefix"] = prefix
                commands.append(command_unit)
            units.append((command_unit, words, "write" if unit_writes else "read", output_indices))

        if any(
            _nodes(root, kind)
            for kind in {"function_definition", "variable_assignment", *dynamic_types}
        ):
            extraction_complete = False

        metadata_safe = (
            resolve_paths
            and extraction_complete
            and not any(
                Path(unit["tokens"][0]).name
                in {
                    ".",
                    "source",
                    "eval",
                    "exec",
                    "export",
                    "unset",
                    "alias",
                    "unalias",
                    "hash",
                    "enable",
                    "builtin",
                    "command",
                    "env",
                    "set",
                    "shopt",
                    "trap",
                    "read",
                    "getopts",
                    "declare",
                    "typeset",
                    "local",
                    "readonly",
                }
                for unit in commands
            )
        )

        for redirect in _nodes(root, "file_redirect"):
            text = _node_text(source, redirect)
            match = _OUTPUT_REDIRECT.match(text)
            if match is None:
                analysis_complete = False
                continue
            operator = match.group(1)
            destination_node = redirect.child_by_field_name("destination")
            destination_text = (
                _node_text(source, destination_node) if destination_node is not None else ""
            )
            ansi_quoted = destination_node is not None and _has_ansi_quoting(destination_node)
            dynamic = destination_node is not None and any(
                _nodes(destination_node, kind) for kind in dynamic_types
            )
            if ansi_quoted:
                unsupported_target_quoting = True
            try:
                destination_parts = [] if ansi_quoted else shlex.split(destination_text, posix=True)
            except ValueError:
                analysis_complete = False
                destination_parts = []
            destination = destination_parts[0] if len(destination_parts) == 1 else ""
            descriptor_only = (
                operator in {">&", "<&"} and re.fullmatch(r"\d+-?|-", destination) is not None
            )
            writes = (
                ">" in operator
                and operator not in {">&"}
                or (operator == ">&" and not descriptor_only)
            )
            static = (
                destination and not dynamic and not any(c in destination_text for c in "*?[]{}~")
            )
            if writes and static and destination == "/dev/null" and operator != "<>":
                discard(destination)
                continue
            if not writes:
                if operator in {"<", "<&"} and not descriptor_only:
                    if static:
                        add(destination, "input", access="read", must_exist=True)
                    else:
                        analysis_complete = False
                continue
            redirect_writes = True
            if commands:
                commands[0]["classification"] = "write"
            if static and not descriptor_only:
                add(destination, "output", access="write", must_exist=False)
            elif not descriptor_only:
                analysis_complete = False

        for command_unit, words, target_access, output_indices in units:
            tokens = command_unit["tokens"]
            ansi_words = {_node_text(source, word) for word in words if _has_ansi_quoting(word)}
            docker = _docker_command(tokens[1:]) if Path(tokens[0]).name == "docker" else None
            if docker is not None and docker[0] in _DOCKER_EXEC_COMMANDS:
                # Container paths cannot be verified against the agent host filesystem.
                analysis_complete = False
                semantic, remaining = docker
                payload = _docker_exec_payload(remaining, compose=semantic[1] == "compose")
                if payload is not None:
                    for path in payload[1]:
                        add(path, "input", access="read", must_exist=True)
                    tokens = tokens[: len(tokens) - len(remaining)]
            elif docker is not None and docker[0] == ["docker", "run"]:
                remaining = docker[1]
                host_paths = _docker_run_host_paths(remaining)
                if host_paths is not None:
                    for path in host_paths:
                        add(path, "path", access=target_access, must_exist=False)
                    tokens = tokens[: len(tokens) - len(remaining)]
            metadata_paths = set()
            installed_ls = (
                shutil.which(tokens[0], path=EXECUTION_PATH)
                if tokens[0] in {"ls", "/usr/bin/ls", "/bin/ls"}
                else None
            )
            literal_paths = {
                token
                for word, token in zip(words, tokens, strict=False)
                if word.type in {"raw_string", "string"}
                and not any(_nodes(word, kind) for kind in dynamic_types)
            }
            if (
                metadata_safe
                and tokens[0] in {"ls", "/usr/bin/ls", "/bin/ls"}
                and installed_ls in {"/usr/bin/ls", "/bin/ls"}
                and not any(
                    _node_text(source, word) != token and any(c in token for c in "*?[]")
                    for word, token in zip(words[1:], tokens[1:], strict=False)
                )
                and all(
                    not token.startswith("-") or token == "--" or re.fullmatch(r"-[aAldh1]+", token)
                    for token in tokens[1:]
                )
            ):
                for word, path in zip(words[1:], tokens[1:], strict=False):
                    raw = _node_text(source, word)
                    if path.startswith("-") or any(c in raw for c in "$`{}~\\"):
                        continue
                    glob = any(c in path for c in "*?[]")
                    if glob and (raw != path or any(s in path for s in ("[:", "[.", "[=", "[^"))):
                        continue
                    directory, _, basename = path.rpartition("/")
                    if path in EXECUTION_PATH.split(":"):
                        directory, basename = path, None
                    if directory not in EXECUTION_PATH.split(":") or basename in {"", ".", ".."}:
                        continue
                    if (
                        basename
                        and basename.startswith(".")
                        and any(fnmatch.fnmatchcase(dot, basename) for dot in (".", ".."))
                    ):
                        analysis_complete = False
                        add(directory, "path", access="read", must_exist=True)
                        if fnmatch.fnmatchcase("..", basename):
                            add(directory + "/..", "path", access="read", must_exist=True)
                        continue
                    for target in self.filesystem.inspect_toolchain_directory(
                        directory,
                        basename,
                        include_hidden=any(
                            token.startswith("-")
                            and not token.startswith("--")
                            and any(c in token for c in "aA")
                            for token in tokens[1:]
                        ),
                        max_targets=max(1, 256 - len(targets) - 3),
                    ):
                        if "mode" not in target:
                            analysis_complete = False
                        kind = target.pop("kind", "toolchain_metadata")
                        retain(target, kind, "read" if kind != "toolchain_metadata" else None)
                    metadata_paths.add(path)
                if metadata_paths:
                    retain(
                        self.filesystem.resolve_path_target(
                            "/usr/bin/ls", current, must_exist=True
                        ),
                        "toolchain_metadata",
                    )
                    if installed_ls == "/bin/ls":
                        for target in self.filesystem.inspect_toolchain_directory("/bin", "ls"):
                            kind = target.pop("kind", "toolchain_metadata")
                            retain(target, kind, "read" if kind != "toolchain_metadata" else None)
            if "/" in tokens[0] and not (
                metadata_paths and tokens[0] in {"/usr/bin/ls", "/bin/ls"}
            ):
                if tokens[0] in ansi_words:
                    unsupported_target_quoting = True
                else:
                    executable_targets = (
                        self.filesystem.resolve_executable_targets(tokens[0], current)
                        if resolve_paths
                        else [{"path": posixpath.normpath(posixpath.join(current, tokens[0]))}]
                    )
                    for target in executable_targets:
                        if "mode" not in target:
                            analysis_complete = False
                        retain(target, "executable", "read")
            for path, kind, must_exist in _path_operands(tokens):
                if path in metadata_paths:
                    continue
                if path in ansi_words:
                    unsupported_target_quoting = True
                    continue
                add(
                    path,
                    kind,
                    access=(
                        "read"
                        if kind in {"input", "source", "script", "executable"}
                        else target_access
                    ),
                    must_exist=must_exist,
                    literal=path in literal_paths,
                )
            for index, token in enumerate(tokens[1:], start=1):
                if index in output_indices:
                    continue
                if token in ansi_words:
                    if "/" in token:
                        unsupported_target_quoting = True
                    continue
                if (
                    token in metadata_paths
                    or token.startswith("-")
                    or "://" in token
                    or (token in literal_paths and any(c in token for c in "$`*?[]{}~"))
                    or not token.startswith(("/", "./", "../"))
                ):
                    continue
                add(
                    token,
                    "path",
                    access=target_access,
                    must_exist=False,
                    literal=token in literal_paths,
                )

        return result()

    @staticmethod
    def _inspection_revision(result: dict) -> str:
        return hashlib.sha256(
            json.dumps(result, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

    def _trusted_executable(self, executable: str, current_directory: str) -> Path | None:
        if "/" in executable:
            candidates = [Path(current_directory) / executable]
        else:
            candidates = [Path(directory) / executable for directory in EXECUTION_PATH.split(":")]
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
        root = _parse_shell(command)
        if _nodes(root, "ansi_c_string") and expected_inspection_revision is None:
            self.inspect_shell(command, current_directory)
        if expected_inspection_revision is not None:
            inspection = self.inspect_shell(command, current_directory)
            actual_revision = inspection.get("inspectionRevision") or self._inspection_revision(
                inspection
            )
            if actual_revision != expected_inspection_revision:
                raise AgentError("process.inspectionChanged", "Shell inspection changed")
        if (
            inspection is not None
            and inspection.get("readOnly") is True
            and inspection.get("analysisComplete") is True
            and not any(
                target.get("kind") == "executable" and stat.S_ISLNK(target.get("mode", 0))
                for target in inspection.get("targets", [])
            )
        ):
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
    def execution_environment(
        home: str, inspection: dict | None, environment: dict[str, str] | None = None
    ) -> dict[str, str]:
        return {
            "HOME": home,
            "PWD": home,
            "LANG": os.environ.get("LANG", "C.UTF-8"),
            "PATH": EXECUTION_PATH,
            **(environment or {}),
        }

    def execution_timeout(self, timeout: float | None) -> float:
        if timeout is not None and (isinstance(timeout, bool) or not 0 < timeout <= 3600):
            raise AgentError("request.invalid", "Process timeout is outside the allowed range")
        return min(self.default_timeout if timeout is None else timeout, self.max_timeout)

    def validate_executable(self, executable: str) -> None:
        # Path-based exec preserves shebang $0; this check is not atomic with exec.
        try:
            metadata = self.filesystem.stat(executable)
        except AgentError as exc:
            raise AgentError("process.startFailed", "Executable is unavailable") from exc
        if metadata["kind"] != "file":
            raise AgentError("process.startFailed", "Executable is not a regular file")

    async def exec(
        self,
        *,
        current_directory: str,
        command: str,
        timeout: float | None,
        stream: StreamCallback,
        expected_inspection_revision: str | None = None,
        environment: dict[str, str] | None = None,
    ) -> dict:
        timeout = self.execution_timeout(timeout)
        argv, inspection = self.execution_argv(
            current_directory=current_directory,
            command=command,
            expected_inspection_revision=expected_inspection_revision,
        )
        cwd, cwd_fd = self._cwd(current_directory)
        started = time.monotonic()
        try:
            # Procfs is only for descriptor-bound startup, never a logical HOME or PWD.
            process_environment = self.execution_environment(
                current_directory, inspection, environment
            )
            self.validate_executable(argv[0])
            process = await asyncio.create_subprocess_exec(
                *argv,
                cwd=cwd,
                env=process_environment,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
                pass_fds=(cwd_fd,),
            )
        except OSError as exc:
            raise AgentError("process.startFailed", "Process could not be started") from exc
        finally:
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
            async with asyncio.timeout(timeout):
                await process.wait()
                await asyncio.shield(asyncio.gather(*drain_tasks))
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
