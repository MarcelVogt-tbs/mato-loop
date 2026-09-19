import asyncio
import json
import os
import shlex
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pydantic import ValidationError

from matoloop_agent.config import AgentConfig
from matoloop_agent.filesystem import AgentError, Filesystem
from matoloop_agent.process import EXECUTION_PATH, ProcessRunner
from matoloop_agent.terminal import PTY_AVAILABLE, TerminalManager


@unittest.skipUnless(PTY_AVAILABLE, "Linux process and PTY support is required")
class ProcessExecutionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        self.current = self.home / "workspace"
        self.current.mkdir()
        self.config = AgentConfig.model_validate(
            {"homeDirectory": self.home, "shellExecutable": Path("/bin/sh"), "maxOutputBytes": 1024}
        )
        self.runner = ProcessRunner(self.config, Filesystem(self.config))
        self.terminal = TerminalManager(self.config, self.runner)
        self.addAsyncCleanup(self.terminal.close_all)

    async def execute(self, pty: bool, command: str, **kwargs):
        output = []

        async def stream(channel, data):
            output.append((channel, data))

        arguments = {
            "current_directory": str(self.current),
            "command": command,
            "timeout": 10,
            "stream": stream,
            "expected_inspection_revision": None,
            **kwargs,
        }
        if pty:
            result = await self.terminal.run(terminal_id="test", rows=24, columns=80, **arguments)
        else:
            result = await self.runner.exec(**arguments)
        return result, "".join(data for _, data in output)

    async def test_shebang_script_finds_sibling_helper_with_original_arguments(self) -> None:
        directory = self.current / "bin"
        directory.mkdir()
        script = directory / "check"
        helper = directory / "helper"
        script.write_text('#!/bin/sh\nexec "$(dirname "$0")/helper" "$@"\n')
        helper.write_text('#!/bin/sh\nprintf "%s|%s|%s" "$0" "$1" "$2"\n')
        script.chmod(0o755)
        helper.chmod(0o755)
        command = shlex.join([str(script), "argument with spaces", "second"])
        inspection = self.runner.inspect_shell(command, str(self.current))
        revision = inspection["inspectionRevision"]
        argv, _ = self.runner.execution_argv(
            current_directory=str(self.current),
            command=command,
            expected_inspection_revision=revision,
        )
        self.assertEqual(argv, [str(script), "argument with spaces", "second"])
        for pty in (False, True):
            for expected_revision in (None, revision):
                with self.subTest(pty=pty, direct=expected_revision is not None):
                    result, output = await self.execute(
                        pty,
                        command,
                        expected_inspection_revision=expected_revision,
                    )
                    self.assertEqual(result["exitCode"], 0, output)
                    self.assertEqual(output, f"{helper}|argument with spaces|second")

    async def test_inspected_multiline_python_executes_unchanged(self) -> None:
        command = "python3 -c 'if True:\n\tprint(42)'"
        inspection = self.runner.inspect_shell(command, str(self.current))
        self.assertTrue(inspection["analysisComplete"])
        self.assertNotIn("prefix", inspection["commands"][0])
        for pty in (False, True):
            with self.subTest(pty=pty):
                result, output = await self.execute(
                    pty, command, expected_inspection_revision=inspection["inspectionRevision"]
                )
                self.assertEqual(result["exitCode"], 0, output)
                self.assertEqual(output.strip(), "42")

    async def test_nested_programs_observe_stable_paths_and_restricted_environment(self) -> None:
        credential = self.home / "credential"
        credential.write_text("inherited-secret")
        descriptor = os.open(credential, os.O_RDONLY)
        os.set_inheritable(descriptor, True)
        self.addCleanup(os.close, descriptor)
        probe = (
            "import os, subprocess, sys\n"
            f"expected = {str(self.current)!r}\n"
            "assert os.getcwd() == os.environ['PWD'] == os.environ['HOME'] == expected\n"
            "assert not {'API_TOKEN', 'AWS_SECRET_ACCESS_KEY', 'BASH_ENV', 'ENV', "
            "'PYTHONPATH', 'LD_PRELOAD', 'VIRTUAL_ENV'} & os.environ.keys()\n"
            "assert not any(os.path.realpath('/proc/self/fd/' + fd) == "
            f"{str(credential)!r} for fd in os.listdir('/proc/self/fd'))\n"
            "if len(sys.argv) == 1:\n"
            "    for key in ('PWD', 'HOME'):\n"
            "        subprocess.run([sys.executable, 'probe.py', 'child'], "
            "cwd=os.environ[key], close_fds=True, check=True)\n"
            "print('nested-ok')\n"
        )
        (self.current / "probe.py").write_text(probe)
        (self.current / "package.json").write_text(
            json.dumps({"scripts": {"probe": "python3 probe.py"}})
        )
        commands = ["python3 probe.py", "python3 probe.py; printf '%s' \"$PWD\""]
        if shutil.which("uv", path=EXECUTION_PATH):
            python = shutil.which("python3", path=EXECUTION_PATH)
            self.assertIsNotNone(python)
            commands.append(
                "uv run --offline --no-project --no-config --no-python-downloads "
                f"--python {shlex.quote(str(Path(python).resolve()))} python probe.py"
            )
        if shutil.which("npm", path=EXECUTION_PATH):
            commands.append("npm run probe")
        for pty in (False, True):
            for command in commands:
                with self.subTest(pty=pty, command=command):
                    inspection = self.runner.inspect_shell(command, str(self.current))
                    with patch.dict(
                        os.environ,
                        {
                            "API_TOKEN": "secret",
                            "AWS_SECRET_ACCESS_KEY": "secret",
                            "BASH_ENV": "/not-allowed",
                            "ENV": "/not-allowed",
                            "PWD": "/proc/self/fd/999",
                            "HOME": "/not-allowed",
                        },
                    ):
                        result, output = await self.execute(
                            pty,
                            command,
                            expected_inspection_revision=inspection["inspectionRevision"],
                        )
                    self.assertEqual(result["exitCode"], 0, output)
                    self.assertEqual(output.count("nested-ok"), 3, output)
                    self.assertNotIn("/proc/self/fd/", output)

    async def test_isolated_process_receives_only_explicit_environment_additions(self) -> None:
        with patch.dict(os.environ, {"HOST_SECRET": "not-inherited"}):
            result, output = await self.execute(
                False,
                'printf \'%s|%s\' "$CUSTOM_VALUE" "${HOST_SECRET-unset}"',
                environment={"CUSTOM_VALUE": "explicit"},
            )
        self.assertEqual(result["exitCode"], 0)
        self.assertEqual(output, "explicit|unset")

        result, output = await self.execute(False, "printf '%s' \"${CUSTOM_VALUE-unset}\"")
        self.assertEqual(result["exitCode"], 0)
        self.assertEqual(output, "unset")

    async def test_timeout_default_local_cap_and_bounded_failure_output(self) -> None:
        self.assertEqual(self.runner.execution_timeout(None), 60)
        self.assertEqual(self.runner.execution_timeout(3600), 3600)
        for value in (0, -1, 3601, float("nan"), float("inf"), True):
            with self.assertRaises(AgentError):
                self.runner.execution_timeout(value)
            with self.assertRaises(ValidationError):
                AgentConfig.model_validate({"homeDirectory": self.home, "maxTimeoutSeconds": value})
        self.runner.default_timeout = 0.1
        self.runner.max_timeout = 0.2
        for pty in (False, True):
            for timeout in (None, 3600):
                with self.subTest(pty=pty, timeout=timeout):
                    result, output = await self.execute(
                        pty,
                        "printf evidence; sleep 10",
                        timeout=timeout,
                    )
                    self.assertTrue(result["timedOut"])
                    self.assertIn("evidence", output)
                    self.assertLess(result["durationMs"], 4000)
            result, output = await self.execute(
                pty,
                "python3 -c 'print(\"x\" * 4000); raise SystemExit(7)'",
            )
            self.assertEqual(result["exitCode"], 7)
            self.assertTrue(result["stdoutTruncated"])
            self.assertLessEqual(len(output.encode()), 1024)

    async def test_cwd_swaps_and_executable_validation_do_not_follow_symlinks(self) -> None:
        outside = self.home / "outside"
        outside.mkdir()
        (outside / "marker").write_text("escaped")
        (self.current / "marker").write_text("protected")
        original_cwd = self.runner._cwd
        saved = self.home / "saved"

        def swap_cwd(current):
            result = original_cwd(current)
            self.current.rename(saved)
            self.current.symlink_to(outside, target_is_directory=True)
            return result

        for pty in (False, True):
            with self.subTest(pty=pty):
                with patch.object(self.runner, "_cwd", side_effect=swap_cwd):
                    result, output = await self.execute(pty, "cat marker")
                self.assertEqual(result["exitCode"], 0)
                self.assertEqual(output, "protected")
                with self.assertRaises(AgentError):
                    await self.execute(pty, "pwd")
                self.current.unlink()
                saved.rename(self.current)

        shell = self.home / "shell"
        malicious = outside / "shell"
        malicious.write_text("#!/bin/sh\nprintf escaped\n")
        malicious.chmod(0o755)
        linked = self.home / "linked"
        linked.symlink_to(outside, target_is_directory=True)
        for pty in (False, True):
            shell.symlink_to(malicious)
            for executable in (shell, linked / "shell"):
                self.runner.shell = self.terminal.shell = executable
                with self.subTest(pty=pty, executable=executable), self.assertRaises(AgentError):
                    await self.execute(pty, "printf protected")
            shell.unlink()
            os.mkfifo(shell)
            self.runner.shell = self.terminal.shell = shell
            with self.assertRaises(AgentError):
                await self.execute(pty, "printf protected")
            shell.unlink()

    async def test_cancellation_kills_descendants_and_closes_descriptors(self) -> None:
        script = self.current / "child.py"
        script.write_text(
            "import os, signal, time\n"
            "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
            "open('pid', 'w').write(str(os.getpid()))\n"
            "time.sleep(30)\n"
        )
        for pty in (False, True):
            for cancel in (False, True):
                with self.subTest(pty=pty, cancel=cancel):
                    pid_file = self.current / "pid"
                    before = set(os.listdir("/proc/self/fd"))
                    task = asyncio.create_task(
                        self.execute(
                            pty,
                            f"python3 {shlex.quote(str(script))} & wait",
                            timeout=0.5 if not cancel else 30,
                        )
                    )
                    async with asyncio.timeout(5):
                        while not pid_file.exists():
                            await asyncio.sleep(0.01)
                    pid = int(pid_file.read_text())
                    if cancel:
                        task.cancel()
                        with self.assertRaises(asyncio.CancelledError):
                            await task
                    else:
                        result, _ = await task
                        self.assertTrue(result["timedOut"])
                    async with asyncio.timeout(3):
                        while Path(f"/proc/{pid}/stat").exists():
                            if Path(f"/proc/{pid}/stat").read_text().split()[2] == "Z":
                                break
                            await asyncio.sleep(0.01)
                    pid_file.unlink()
                    self.assertEqual(set(os.listdir("/proc/self/fd")), before)

    async def test_start_failure_and_cancellation_close_descriptors(self) -> None:
        for failure in (OSError(), asyncio.CancelledError()):
            for pty in (False, True):
                before = set(os.listdir("/proc/self/fd"))
                spawn = (
                    "matoloop_agent.terminal.pty.fork" if pty else "asyncio.create_subprocess_exec"
                )
                with (
                    self.subTest(pty=pty, failure=type(failure)),
                    patch(spawn, side_effect=failure),
                ):
                    with self.assertRaises(
                        AgentError if isinstance(failure, OSError) else asyncio.CancelledError
                    ):
                        await self.execute(pty, "pwd")
                self.assertEqual(set(os.listdir("/proc/self/fd")), before)

    async def test_invalid_syntax_never_reaches_process_or_pty_spawn(self) -> None:
        for pty in (False, True):
            for revision in (None, "0" * 64):
                with (
                    self.subTest(pty=pty, revision=revision),
                    patch("asyncio.create_subprocess_exec") as spawn,
                    patch("matoloop_agent.terminal.pty.fork") as fork,
                ):
                    with self.assertRaises(AgentError) as raised:
                        await self.execute(
                            pty,
                            "touch must-not-exist; cat 'unterminated",
                            expected_inspection_revision=revision,
                        )
                    self.assertEqual(raised.exception.code, "process.invalidSyntax")
                    self.assertEqual(str(raised.exception), "Shell command has invalid syntax")
                    spawn.assert_not_called()
                    fork.assert_not_called()
                    self.assertFalse((self.current / "must-not-exist").exists())

    def test_incomplete_revisions_bind_exact_command_cwd_and_disable_direct_exec(self) -> None:
        commands = ["echo " + "x" * 1025, "echo " + "y" * 1025, "", " "]
        revisions = set()
        for cwd in (self.current, self.home):
            for command in commands:
                inspection = self.runner.inspect_shell(command, str(cwd))
                self.assertFalse(inspection["analysisComplete"])
                self.assertTrue(inspection["readOnly"])
                self.assertEqual(inspection["commands"], [])
                revisions.add(inspection["inspectionRevision"])
        self.assertEqual(len(revisions), len(commands) * 2)
        command = "cat missing"
        inspection = self.runner.inspect_shell(command, str(self.current))
        self.assertFalse(inspection["analysisComplete"])
        self.assertEqual(inspection["commands"][0]["prefix"], ["cat"])
        argv, _ = self.runner.execution_argv(
            current_directory=str(self.current),
            command=command,
            expected_inspection_revision=inspection["inspectionRevision"],
        )
        self.assertEqual(argv, [str(self.runner.shell), "-c", command])
        for changed_command, cwd in ((command + " ", self.current), (command, self.home)):
            with self.assertRaises(AgentError) as raised:
                self.runner.execution_argv(
                    current_directory=str(cwd),
                    command=changed_command,
                    expected_inspection_revision=inspection["inspectionRevision"],
                )
            self.assertEqual(raised.exception.code, "process.inspectionChanged")

    async def test_posix_read_write_redirect_reaches_shell_unchanged(self) -> None:
        command = ": 3<>state"
        for pty in (False, True):
            for inspected in (False, True):
                with self.subTest(pty=pty, inspected=inspected):
                    inspection = self.runner.inspect_shell(command, str(self.current))
                    self.assertEqual(inspection["classification"], "write")
                    result, _ = await self.execute(
                        pty,
                        command,
                        expected_inspection_revision=inspection["inspectionRevision"]
                        if inspected
                        else None,
                    )
                    self.assertEqual(result["exitCode"], 0)
                    self.assertEqual((self.current / "state").read_bytes(), b"")
                    (self.current / "state").unlink()

    async def test_unsupported_ansi_redirect_quoting_never_reaches_spawn(self) -> None:
        for shell in ("bash", "dash"):
            self.runner.shell = Path("/usr/bin") / shell
            for pty in (False, True):
                for revision in (None, "0" * 64):
                    for operator in (">", "1<>"):
                        with (
                            self.subTest(
                                shell=shell, pty=pty, revision=revision, operator=operator
                            ),
                            patch("asyncio.create_subprocess_exec") as spawn,
                            patch("matoloop_agent.terminal.pty.fork") as fork,
                        ):
                            with self.assertRaises(AgentError) as raised:
                                await self.execute(
                                    pty,
                                    f"printf x {operator}$'/outside/link'",
                                    expected_inspection_revision=revision,
                                )
                            self.assertEqual(
                                raised.exception.code, "process.unsupportedTargetQuoting"
                            )
                            spawn.assert_not_called()
                            fork.assert_not_called()
