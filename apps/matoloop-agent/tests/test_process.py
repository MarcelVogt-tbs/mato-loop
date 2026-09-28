import asyncio
import os
import shlex
import stat
import subprocess
import sys
import tempfile
import textwrap
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from matoloop_agent.config import AgentConfig
from matoloop_agent.filesystem import AgentError, Filesystem
from matoloop_agent.process import EXECUTION_PATH, ProcessRunner

SCREENSHOT_COMMAND = (
    "python3 --version; uv --version; node --version; npm --version; "
    "which python3.13 2>/dev/null; ls /usr/bin/python* 2>/dev/null"
)


def configuration(home: Path) -> AgentConfig:
    config = AgentConfig.model_validate(
        {
            "homeDirectory": home,
            "shellExecutable": Path("/bin/sh"),
            "maxOutputBytes": 1024,
        }
    )
    config._data_directory = home
    return config


class ProcessTests(unittest.IsolatedAsyncioTestCase):
    async def test_elevated_shell_with_linked_sudo_keeps_password_out_of_command(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sudo = root / "sudo-rs"
            sudo.write_text(
                textwrap.dedent("""\
                #!/usr/bin/python3
                import os
                import subprocess
                import sys
                from pathlib import Path

                args = sys.argv[1:]
                preserve = next((arg.partition('=')[2].split(',') for arg in args
                                 if arg.startswith('--preserve-env=')), [])
                args = [arg for arg in args if not arg.startswith('--preserve-env=')]
                timestamp = Path(__file__).with_suffix('.authenticated')
                if args == ['-n', '-v']:
                    sys.exit(0 if timestamp.exists() else 1)
                if args[:4] == ['-S', '-p', '', '-v']:
                    if sys.stdin.readline() != 'correct\\n':
                        sys.exit(1)
                    timestamp.touch()
                    sys.exit(0)
                if args[:2] == ['-n', '--'] and timestamp.exists():
                    environment = {'PATH': os.environ.get('PATH', '/usr/bin:/bin')}
                    environment.update({name: os.environ[name] for name in preserve
                                        if name in os.environ and name != 'DROP_ENV'})
                    sys.exit(subprocess.run(args[2:], stdin=subprocess.DEVNULL,
                                            env=environment).returncode)
                if args[:4] == ['-S', '-p', '', '--']:
                    if sys.stdin.readline() != 'correct\\n':
                        sys.exit(1)
                    environment = {'PATH': os.environ.get('PATH', '/usr/bin:/bin')}
                    environment.update({name: os.environ[name] for name in preserve
                                        if name in os.environ and name != 'DROP_ENV'})
                    sys.exit(subprocess.run(args[4:], stdin=subprocess.DEVNULL,
                                            env=environment).returncode)
                sys.exit(1)
            """)
            )
            sudo.chmod(0o755)
            alternative = root / "sudo-alternative"
            alternative.symlink_to(sudo)
            sudo_link = root / "sudo"
            sudo_link.symlink_to(alternative)
            config = configuration(root)
            runner = ProcessRunner(config, Filesystem(config))
            chunks = []

            async def stream(channel, data):
                chunks.append((channel, data))

            with patch("matoloop_agent.process.shutil.which", return_value=str(sudo_link)):
                with self.assertRaises(AgentError) as raised:
                    await runner.exec(
                        current_directory=str(root),
                        command="true",
                        timeout=2,
                        stream=stream,
                        elevated=True,
                    )
                self.assertEqual(raised.exception.code, "process.sudoAuthRequired")
                with self.assertRaises(AgentError) as raised:
                    await runner.exec(
                        current_directory=str(root),
                        command="true",
                        timeout=2,
                        stream=stream,
                        elevated=True,
                        sudo_password="wrong",
                    )
                self.assertEqual(raised.exception.code, "process.sudoAuthFailed")
                command = (
                    f"printf '%s' content > {root / 'protected'}; cat {root / 'protected'}; cat"
                )
                for cached_preflight in (False, True):
                    with self.subTest(cached_preflight=cached_preflight):
                        chunks.clear()
                        result = await runner.exec(
                            current_directory=str(root),
                            command=command,
                            timeout=2,
                            stream=stream,
                            elevated=True,
                            sudo_password="correct",
                        )
                        self.assertEqual(result["exitCode"], 0)
                        self.assertEqual((root / "protected").read_text(), "content")
                        self.assertEqual(
                            "".join(data for channel, data in chunks if channel == "stdout"),
                            "content",
                        )
                        self.assertNotIn("correct", str(chunks))

                chunks.clear()
                result = await runner.exec(
                    current_directory=str(root),
                    command='test -n "$APP_TOKEN"; printf received',
                    timeout=2,
                    stream=stream,
                    elevated=True,
                    sudo_password="correct",
                    environment={"APP_TOKEN": "private-value"},
                )
                self.assertEqual(result["exitCode"], 0)
                self.assertEqual(chunks, [("stdout", "received")])
                self.assertNotIn("private-value", str(chunks))

                chunks.clear()
                result = await runner.exec(
                    current_directory=str(root),
                    command='test -n "$APP_TOKEN"; printf received',
                    timeout=2,
                    stream=stream,
                    elevated=True,
                    environment={"APP_TOKEN": "private-value"},
                )
                self.assertEqual(result["exitCode"], 0)
                self.assertEqual(chunks, [("stdout", "received")])

                chunks.clear()
                result = await runner.exec(
                    current_directory=str(root),
                    command="printf must-not-run",
                    timeout=2,
                    stream=stream,
                    elevated=True,
                    sudo_password="correct",
                    environment={"DROP_ENV": "private-value"},
                )
                self.assertEqual(result["exitCode"], 125)
                self.assertNotIn("must-not-run", str(chunks))
                self.assertIn("Explicit elevated environment unavailable", str(chunks))
                self.assertNotIn("private-value", str(chunks))

    async def test_elevated_shell_timeout_stops_command(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sudo = root / "sudo"
            sudo.write_text(
                textwrap.dedent("""\
                #!/usr/bin/python3
                import os
                import sys

                args = sys.argv[1:]
                if args == ['-n', '-v']:
                    sys.exit(0)
                if args[:2] == ['-n', '--']:
                    os.execv(args[2], args[2:])
                sys.exit(1)
            """)
            )
            sudo.chmod(0o755)
            config = configuration(root)
            runner = ProcessRunner(config, Filesystem(config))

            async def stream(_channel, _data):
                pass

            with patch("matoloop_agent.process.shutil.which", return_value=str(sudo)):
                result = await runner.exec(
                    current_directory=str(root),
                    command="sleep 5",
                    timeout=0.2,
                    stream=stream,
                    elevated=True,
                )
            self.assertTrue(result["timedOut"])
            self.assertLess(result["durationMs"], 3000)

    def test_inspection_respects_search_permissions_without_requiring_directory_reads(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                textwrap.dedent("""
                    import os
                    import tempfile
                    import unittest
                    from pathlib import Path

                    from matoloop_agent.config import AgentConfig
                    from matoloop_agent.filesystem import AgentError, Filesystem
                    from matoloop_agent.process import ProcessRunner

                    # Exercise actual kernel permissions even in the root-run development container.
                    if os.geteuid() == 0:
                        os.setgroups([])
                        os.setgid(65534)
                        os.setuid(65534)
                    check = unittest.TestCase()
                    with tempfile.TemporaryDirectory() as temporary:
                        root = Path(temporary)
                        parent = root / "protected"
                        parent.mkdir()
                        target = parent / "file"
                        target.write_text("private")
                        target.chmod(0)
                        config = AgentConfig(homeDirectory=root)
                        filesystem = Filesystem(config)
                        runner = ProcessRunner(config, filesystem)
                        command = f"sudo -n cat {target}"
                        try:
                            parent.chmod(0o100)
                            inspection = runner.inspect_shell(command, str(root))
                            check.assertIn(
                                str(target), [t["path"] for t in inspection["targets"]]
                            )
                            with check.assertRaises(AgentError) as raised:
                                filesystem.read(str(target), 1, 1, 100)
                            check.assertEqual(raised.exception.code, "path.accessDenied")
                            with check.assertRaises(AgentError) as raised:
                                filesystem.list(str(parent), 0, 10)
                            check.assertEqual(raised.exception.code, "path.accessDenied")

                            parent.chmod(0)
                            with check.assertRaises(AgentError) as raised:
                                runner.inspect_shell(command, str(root))
                            check.assertEqual(raised.exception.code, "path.accessDenied")
                        finally:
                            parent.chmod(0o700)
                    """),
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    async def test_exec_uses_absolute_descriptor_validated_cwd(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = configuration(root)
            runner = ProcessRunner(config, Filesystem(config))
            chunks = []

            async def stream(channel: str, data: str) -> None:
                chunks.append((channel, data))

            result = await runner.exec(
                current_directory=os.fspath(root),
                command="printf out; printf err >&2",
                timeout=2,
                stream=stream,
            )
            self.assertEqual(result["exitCode"], 0)
            self.assertEqual(dict(chunks), {"stdout": "out", "stderr": "err"})

            with self.assertRaises(AgentError):
                await runner.exec(
                    current_directory="relative",
                    command="pwd",
                    timeout=1,
                    stream=stream,
                )

    async def test_shell_timeout_and_inspection_revision(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            script = root / "script.py"
            source = root / "env.sh"
            script.write_text("print('unused')")
            source.write_text("VALUE=unused")
            config = configuration(root)
            runner = ProcessRunner(config, Filesystem(config))

            inspected = runner.inspect_shell(
                "python script.py < env.sh > output.txt", os.fspath(root)
            )
            self.assertTrue(inspected["analysisComplete"])
            self.assertEqual(inspected["classification"], "write")
            self.assertRegex(inspected["inspectionRevision"], r"^[0-9a-f]{64}$")
            self.assertEqual(
                [
                    (target["kind"], target["access"], target["path"])
                    for target in inspected["targets"]
                ],
                [
                    ("input", "read", os.fspath(source)),
                    ("output", "write", os.fspath(root / "output.txt")),
                    ("script", "read", os.fspath(script)),
                ],
            )
            original_revision = inspected["inspectionRevision"]
            script.write_text("print('changed')")
            changed = runner.inspect_shell(
                "python script.py < env.sh > output.txt", os.fspath(root)
            )
            self.assertNotEqual(changed["inspectionRevision"], original_revision)

            async def stream(_channel: str, _data: str) -> None:
                return None

            result = await runner.exec(
                current_directory=os.fspath(root),
                command="sleep 5",
                timeout=0.05,
                stream=stream,
            )
            self.assertTrue(result["timedOut"])

            task = asyncio.create_task(
                runner.exec(
                    current_directory=os.fspath(root),
                    command="sleep 5",
                    timeout=10,
                    stream=stream,
                )
            )
            await asyncio.sleep(0.05)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task

    def test_inspection_preserves_path_boundaries_and_allows_dynamic_shell(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "target"
            target.write_text("safe")
            (root / "link").symlink_to(target)
            config = configuration(root)
            runner = ProcessRunner(config, Filesystem(config))
            with self.assertRaises(AgentError) as raised:
                runner.inspect_shell("cat link", os.fspath(root))
            self.assertEqual(raised.exception.code, "path.unsupportedType")
            dynamic = runner.inspect_shell("echo $HOME", os.fspath(root))
            self.assertFalse(dynamic["analysisComplete"])
            self.assertEqual(dynamic["classification"], "read")
            self.assertEqual(dynamic["commands"][0]["tokens"], ["echo", "$HOME"])
            read_only = runner.inspect_shell("cat target", os.fspath(root))
            self.assertTrue(read_only["readOnly"])
            self.assertEqual(read_only["classification"], "read")
            self.assertEqual(
                read_only["commands"],
                [{"tokens": ["cat", "target"], "prefix": ["cat"], "classification": "read"}],
            )
            self.assertEqual(read_only["targets"][0]["path"], os.fspath(target))

            source = root / "env.sh"
            source.write_text("VALUE=safe")
            repository = root / "repository"
            repository.mkdir()
            inspections = {
                "generic": runner.inspect_shell("touch ./target", os.fspath(root)),
                "interpreter": runner.inspect_shell("python target", os.fspath(root)),
                "source": runner.inspect_shell("source env.sh", os.fspath(root)),
                "git": runner.inspect_shell("git -C repository status", os.fspath(root)),
            }
            self.assertTrue(all(item["analysisComplete"] for item in inspections.values()))
            self.assertEqual(inspections["generic"]["classification"], "write")
            self.assertTrue(
                all(
                    inspections[name]["classification"] == "read"
                    for name in ("interpreter", "source", "git")
                )
            )
            for name, expected in {
                "generic": target,
                "interpreter": target,
                "source": source,
                "git": repository,
            }.items():
                self.assertEqual(inspections[name]["targets"][0]["path"], os.fspath(expected))

    def test_explicit_executable_follows_only_its_symlink_chain(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            executable = root / "python-real"
            executable.write_text("#!/bin/sh\n")
            executable.chmod(0o755)
            bin_directory = root / ".venv/bin"
            bin_directory.mkdir(parents=True)
            python3 = bin_directory / "python3"
            python3.symlink_to("../../python-real")
            python = bin_directory / "python"
            python.symlink_to("python3")
            script = root / "pcap_analyze.py"
            script.write_text("print('ok')\n")
            runner = ProcessRunner(configuration(root), Filesystem(configuration(root)))
            command = ".venv/bin/python pcap_analyze.py client.pcapng server.pcapng"

            inspected = runner.inspect_shell(command, os.fspath(root))

            self.assertTrue(inspected["analysisComplete"])
            self.assertEqual(inspected["classification"], "read")
            self.assertEqual(
                [target["path"] for target in inspected["targets"]],
                [os.fspath(python), os.fspath(python3), os.fspath(executable), os.fspath(script)],
            )
            argv, _ = runner.execution_argv(
                current_directory=os.fspath(root),
                command=command,
                expected_inspection_revision=inspected["inspectionRevision"],
            )
            self.assertEqual(argv, [os.fspath(runner.shell), "-c", command])

            replacement = root / "python-replacement"
            replacement.write_text("#!/bin/sh\n")
            replacement.chmod(0o755)
            python3.unlink()
            python3.symlink_to("../../python-replacement")
            with self.assertRaises(AgentError) as raised:
                runner.execution_argv(
                    current_directory=os.fspath(root),
                    command=command,
                    expected_inspection_revision=inspected["inspectionRevision"],
                )
            self.assertEqual(raised.exception.code, "process.inspectionChanged")

    async def test_inspection_classifies_flat_commands_and_executes_read_pipeline(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = root / "first.txt"
            second = root / "second.txt"
            first.write_text("one\ntwo\n")
            second.write_text("three\n")
            config = configuration(root)
            runner = ProcessRunner(config, Filesystem(config))

            inspected = runner.inspect_shell(
                "cat first.txt | head -n 1 && cat second.txt; pwd",
                os.fspath(root),
            )
            self.assertTrue(inspected["analysisComplete"])
            self.assertEqual(inspected["classification"], "read")
            self.assertEqual(
                inspected["commands"],
                [
                    {
                        "tokens": ["cat", "first.txt"],
                        "prefix": ["cat"],
                        "classification": "read",
                    },
                    {
                        "tokens": ["head", "-n", "1"],
                        "prefix": ["head"],
                        "classification": "read",
                    },
                    {
                        "tokens": ["cat", "second.txt"],
                        "prefix": ["cat"],
                        "classification": "read",
                    },
                    {"tokens": ["pwd"], "prefix": ["pwd"], "classification": "read"},
                ],
            )

            output = []

            async def stream(channel: str, data: str) -> None:
                output.append((channel, data))

            executed = await runner.exec(
                current_directory=os.fspath(root),
                command="cat first.txt | head -n 1 && cat second.txt; pwd",
                timeout=2,
                stream=stream,
                expected_inspection_revision=inspected["inspectionRevision"],
            )
            self.assertEqual(executed["exitCode"], 0)
            stdout = "".join(data for channel, data in output if channel == "stdout")
            self.assertIn("one\nthree\n", stdout)

    async def test_inspection_allows_static_coding_discovery_pipeline(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = configuration(root)
            runner = ProcessRunner(config, Filesystem(config))
            command = (
                "pwd && printf '\\n--- top-level ---\\n' && "
                "find . -maxdepth 2 -mindepth 1 -not -path './.git/*' -print "
                "| sort | head -200"
            )

            inspected = runner.inspect_shell(command, os.fspath(root))

            self.assertTrue(inspected["analysisComplete"])
            self.assertTrue(inspected["readOnly"])
            self.assertEqual(inspected["classification"], "read")
            self.assertEqual(
                [unit["tokens"] for unit in inspected["commands"]],
                [
                    ["pwd"],
                    ["printf", "\\n--- top-level ---\\n"],
                    [
                        "find",
                        ".",
                        "-maxdepth",
                        "2",
                        "-mindepth",
                        "1",
                        "-not",
                        "-path",
                        "./.git/*",
                        "-print",
                    ],
                    ["sort"],
                    ["head", "-200"],
                ],
            )
            self.assertTrue(all(unit["classification"] == "read" for unit in inspected["commands"]))
            self.assertEqual(
                [(target["kind"], target["path"]) for target in inspected["targets"]],
                [("directory", os.fspath(root))],
            )

            quoted_pattern = runner.inspect_shell(
                "find . -type f -name '*.py' -print0", os.fspath(root)
            )
            self.assertTrue(quoted_pattern["readOnly"])

            before = list(root.iterdir())
            output = []

            async def stream(channel: str, data: str) -> None:
                output.append((channel, data))

            executed = await runner.exec(
                current_directory=os.fspath(root),
                command=command,
                timeout=2,
                stream=stream,
                expected_inspection_revision=inspected["inspectionRevision"],
            )
            self.assertEqual(executed["exitCode"], 0)
            self.assertIn("--- top-level ---", "".join(data for _, data in output))
            self.assertEqual(list(root.iterdir()), before)

    def test_inspection_recognizes_definite_mutations(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = configuration(root)
            runner = ProcessRunner(config, Filesystem(config))

            for command in (
                "find . -delete",
                "find . -exec cat {} ;",
                "find . -fprint output.txt",
                "sort -o output.txt",
                "sort --output=output.txt",
                "printf -v value test",
                "printf -vvalue test",
                "printf -v'x[$(touch output.txt)]' %s value",
                "printf %2hn PATH && find .",
                "printf '%s\\n' test > output.txt",
                'printf "$(touch output.txt)"',
                "printf safe#; touch output.txt",
                "find . -name victim# -delete && pwd",
            ):
                with self.subTest(command=command):
                    inspected = runner.inspect_shell(command, os.fspath(root))
                    self.assertFalse(inspected["readOnly"])
                    self.assertEqual(inspected["classification"], "write")

            for command in (
                "make test",
                "npm test",
                "python -c pass",
                "curl https://example.invalid",
                "curl -sXGET https://example.invalid",
                "curl -su user:o https://example.invalid",
                "curl -A sXPOST https://example.invalid",
                "curl -A '-sXPOST' https://example.invalid",
                "curl --user-agent '-sooutput' https://example.invalid",
                "find . -maxdepth invalid",
                "find . -name *.py",
                "printf target & pwd",
                "git branch --show-current",
                "git config user.name",
                "git remote -v",
                "git stash list",
                "git tag --list",
                "git worktree list",
            ):
                with self.subTest(command=command):
                    inspected = runner.inspect_shell(command, os.fspath(root))
                    self.assertTrue(inspected["analysisComplete"])
                    self.assertEqual(inspected["classification"], "read")

            for command in (
                "rm target",
                "npm install",
                "git commit -m test",
                "git branch feature",
                "git config user.name Test",
                "git stash",
                "curl -XPOST https://example.invalid",
                "curl -sXPOST https://example.invalid",
                "curl -sooutput.txt https://example.invalid",
                "curl -X GET -X POST https://example.invalid",
                "curl --request=GET --request=POST https://example.invalid",
                "curl -ooutput.txt https://example.invalid",
            ):
                with self.subTest(command=command):
                    self.assertEqual(
                        runner.inspect_shell(command, os.fspath(root))["classification"],
                        "write",
                    )

    def test_inspection_reads_compose_file_and_quoted_docker_template(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            compose = root / "temp-compose.yaml"
            compose.write_text("services: {}\n")
            config = configuration(root)
            runner = ProcessRunner(config, Filesystem(config))
            command = (
                f"cat {compose} && "
                "docker inspect lemonade-server --format '{{json .Config.Labels}}'"
            )

            inspected = runner.inspect_shell(command, os.fspath(root))
            self.assertTrue(inspected["analysisComplete"])
            self.assertTrue(inspected["readOnly"])
            self.assertEqual(inspected["classification"], "read")
            self.assertEqual(
                inspected["commands"],
                [
                    {
                        "tokens": ["cat", os.fspath(compose)],
                        "prefix": ["cat"],
                        "classification": "read",
                    },
                    {
                        "tokens": [
                            "docker",
                            "inspect",
                            "lemonade-server",
                            "--format",
                            "{{json .Config.Labels}}",
                        ],
                        "prefix": ["docker", "inspect"],
                        "classification": "read",
                    },
                ],
            )
            self.assertEqual([target["path"] for target in inspected["targets"]], [str(compose)])
            runner.execution_argv(
                current_directory=str(root),
                command=command,
                expected_inspection_revision=inspected["inspectionRevision"],
            )
            compose.write_text("services: {lemonade-server: {image: lemonade}}\n")
            with self.assertRaises(AgentError) as raised:
                runner.execution_argv(
                    current_directory=str(root),
                    command=command,
                    expected_inspection_revision=inspected["inspectionRevision"],
                )
            self.assertEqual(raised.exception.code, "process.inspectionChanged")
            compose.unlink()
            compose.symlink_to(root / "missing.yaml")
            with self.assertRaises(AgentError) as raised:
                runner.inspect_shell(command, str(root))
            self.assertEqual(raised.exception.code, "path.unsupportedType")

    def test_docker_exec_classifies_inner_commands_without_host_path_checks(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = configuration(root)
            runner = ProcessRunner(config, Filesystem(config))
            for wrapper in (
                "docker --context local exec",
                "docker container exec",
                "docker compose --project-name test exec",
            ):
                for payload, classification in (
                    ("lemonade config", "read"),
                    ("cat /container-only/file", "read"),
                    ("/container-only/bin/tool --inspect", "read"),
                    ("curl -fsS --max-time 15 http://example.invalid/health", "read"),
                    ("echo 'rm /tmp/file; echo x > /tmp/file'", "read"),
                    ("sh -c 'docker exec \"{server,rm}\" echo ok'", "read"),
                    ("curl --user-agent '-sXPOST' http://example.invalid", "read"),
                    ("sh -c 'cat /container-only/file | head -8'", "read"),
                    ('bash -lc \'printf "%s" "$(pwd)"\'', "read"),
                    ("sh -c 'echo ok >&2'", "read"),
                    ("sh -c 'cat < /container-only/file'", "read"),
                    ("docker exec nested lemonade config", "read"),
                    ("rm /container-only/file", "write"),
                    ("curl -XPOST http://example.invalid", "write"),
                    ("curl -sXPOST http://example.invalid", "write"),
                    ("curl -so/tmp/output http://example.invalid", "write"),
                    ("curl -X GET -X POST http://example.invalid", "write"),
                    ("curl -o /container-only/file http://example.invalid", "write"),
                    ("sh -c 'echo x > /tmp/file'", "write"),
                    ("sh -c 'echo x > /dev/null'", "write"),
                    ("sh -c 'cat <> /tmp/file'", "write"),
                    ("sh -c 'cat >&output'", "write"),
                    ("sh -c 'echo $(touch /tmp/file)'", "write"),
                    ("sh -c 'git < /tmp/input commit'", "write"),
                    ("sh -c 'exec rm /tmp/file'", "write"),
                    ("sh -c '. /container-only/script.sh'", "write"),
                    ("sh -c 'docker exec $(printf server) rm /tmp/file'", "write"),
                    ("bash -c 'docker exec {server,rm} /tmp/file'", "write"),
                    ("sh -c 'r\\\nm /tmp/file'", "write"),
                    ("sh -c 'git com\\\nmit -m test'", "write"),
                    (r"""sh -c 'sh -c "echo $(printf %s \;touch\ /tmp/file)"' """, "write"),
                    ("sh -c 'sh -c \"rm /tmp/file\"'", "write"),
                    ("sh -c '$COMMAND'", "write"),
                    ("sh -c 'if'", "write"),
                    ("sh /container-only/script.sh", "write"),
                    ("env FLAG=value rm /tmp/file", "write"),
                    ("docker exec nested rm /tmp/file", "write"),
                ):
                    command = f"{wrapper} -itu root -w /container-only server {payload}"
                    with self.subTest(command=command):
                        inspected = runner.inspect_shell(command, str(root))
                        self.assertEqual(inspected["classification"], classification)
                        self.assertEqual(inspected["readOnly"], classification == "read")
                        self.assertFalse(inspected["analysisComplete"])
                        self.assertEqual(inspected["targets"], [])
                        self.assertFalse(any("prefix" in unit for unit in inspected["commands"]))

            command = (
                "curl -fsS --max-time 15 http://192.168.1.32:13305/api/v1/health; "
                "curl -fsS --max-time 15 http://192.168.1.32:13305/api/v1/models; "
                "docker exec lemonade-server lemonade config; "
                "ps -eo pid,rss,args --sort=-rss | head -8"
            )
            inspected = runner.inspect_shell(command, str(root))
            self.assertEqual(inspected["classification"], "read")
            self.assertEqual(len(inspected["commands"]), 5)
            self.assertNotIn("prefix", inspected["commands"][2])
            argv, _ = runner.execution_argv(
                current_directory=str(root),
                command=command,
                expected_inspection_revision=inspected["inspectionRevision"],
            )
            self.assertEqual(argv[-2:], ["-c", command])
            with self.assertRaises(AgentError) as raised:
                runner.execution_argv(
                    current_directory=str(root),
                    command=command.replace("config", "status"),
                    expected_inspection_revision=inspected["inspectionRevision"],
                )
            self.assertEqual(raised.exception.code, "process.inspectionChanged")

            for options in ("--", "-e FLAG=1 --user=root --workdir=/app", "--interactive=false"):
                self.assertEqual(
                    runner.inspect_shell(
                        f"docker exec {options} server lemonade config", str(root)
                    )["classification"],
                    "read",
                )
            for suffix in (
                "",
                "server",
                "--user",
                "--future server cat",
                "--privileged server cat",
                "server $COMMAND",
                'server sh -c "$COMMAND"',
                "server sh -c $'rm /tmp/f'",
                "{server,rm} /tmp/file",
                "ser\\\nver rm /tmp/file",
            ):
                with self.subTest(suffix=suffix):
                    self.assertEqual(
                        runner.inspect_shell(f"docker exec {suffix}", str(root))["classification"],
                        "write",
                    )
            nested = ["echo", "ok"]
            for _ in range(10):
                nested = ["sh", "-c", shlex.join(nested)]
            for payload in (shlex.join(nested), "docker exec nested " * 10 + "echo ok"):
                self.assertEqual(
                    runner.inspect_shell("docker exec server " + payload, str(root))[
                        "classification"
                    ],
                    "write",
                )

    def test_docker_run_checks_bind_source_but_not_container_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            frontend = root / "apps/frontend"
            frontend.mkdir(parents=True)
            config = configuration(root)
            runner = ProcessRunner(config, Filesystem(config))
            command = (
                f"docker run --rm -v {frontend}:/workspace -w /workspace node:20-slim "
                "sh -lc 'corepack enable && yarn install --immutable && yarn lint && yarn build'"
            )

            inspected = runner.inspect_shell(command, str(root))

            self.assertTrue(inspected["analysisComplete"])
            self.assertEqual(inspected["classification"], "write")
            self.assertEqual(
                [(target["path"], target["access"]) for target in inspected["targets"]],
                [(str(frontend), "write")],
            )

    def test_docker_exec_preserves_outer_files_and_redirects(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = configuration(root)
            runner = ProcessRunner(config, Filesystem(config))
            env_file = root / "container.env"
            env_file.write_text("FLAG=1\n")
            compose_file = root / "compose.yaml"
            compose_file.write_text("services: {}\n")
            for option in (f"--env-file {env_file}", f"--env-file={env_file}"):
                inspected = runner.inspect_shell(
                    f"docker exec {option} server cat /container-only/file", str(root)
                )
                self.assertEqual(inspected["classification"], "read")
                self.assertEqual(
                    [target["path"] for target in inspected["targets"]], [str(env_file)]
                )
            inspected = runner.inspect_shell(
                "docker compose -f ./compose.yaml exec -T --index=1 "
                "server cat /container-only/file",
                str(root),
            )
            self.assertEqual(inspected["classification"], "read")
            self.assertEqual(
                [target["path"] for target in inspected["targets"]], [str(compose_file)]
            )
            for suffix, classification in (("> output", "write"), ("< container.env", "read")):
                inspected = runner.inspect_shell(
                    f"docker exec server cat /container-only/file {suffix}", str(root)
                )
                self.assertEqual(inspected["classification"], classification)
                self.assertEqual(len(inspected["targets"]), 1)
                self.assertEqual(inspected["targets"][0]["path"], str(root / suffix[2:]))
            inspected = runner.inspect_shell("docker exec server echo $(touch output)", str(root))
            self.assertEqual(inspected["classification"], "write")
            link = root / "link"
            link.symlink_to(env_file)
            self.assertEqual(
                runner.inspect_shell(f"docker exec server cat {link}", str(root))["targets"], []
            )
            for command in (
                f"docker exec --env-file {link} server cat /container-only/file",
                f"docker exec server cat /container-only/file > {link}",
                f"docker exec server cat $(cat {link})",
            ):
                with self.subTest(command=command), self.assertRaises(AgentError) as raised:
                    runner.inspect_shell(command, str(root))
                self.assertEqual(raised.exception.code, "path.unsupportedType")

    def test_docker_operations_and_option_aware_prefixes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "compose.yaml").write_text("services: {}\n")
            config = configuration(root)
            runner = ProcessRunner(config, Filesystem(config))
            for group, reads, writes in (
                ("", "ps logs inspect images info version", "run exec build pull rm"),
                (
                    "compose",
                    "ps logs config ls images top events port version",
                    "up down run exec build pull watch",
                ),
                (
                    "container",
                    "ls ps inspect logs top stats port diff wait",
                    "run exec rm start stop prune",
                ),
                ("image", "ls inspect history", "build rm pull push prune tag"),
                ("network", "ls inspect", "create connect disconnect rm prune"),
                ("volume", "ls inspect", "create rm prune update"),
                ("system", "df events info", "prune"),
            ):
                for options in (
                    "",
                    "--context foo",
                    "--context=foo --debug",
                    "-cfoo -Hunix:///var/run/docker.sock",
                    "--config config-dir --log-level debug --tlsverify",
                    "--context compose",
                ):
                    group_options = (
                        (
                            "",
                            "--progress plain -f ./compose.yaml",
                            "--progress=plain -f./compose.yaml",
                            "--ansi never --env-file .env --parallel 2 --profile dev "
                            "-p project --project-directory .",
                            "--file config --project-name ps --compatibility",
                            "--",
                        )
                        if group == "compose"
                        else ("",)
                    )
                    for extra in group_options:
                        for operation in (reads + " " + writes).split():
                            command = f"docker {options} {group} {extra} {operation}"
                            with self.subTest(command=command):
                                inspected = runner.inspect_shell(command, str(root))
                                classification = "read" if operation in reads.split() else "write"
                                self.assertEqual(inspected["analysisComplete"], operation != "exec")
                                self.assertEqual(inspected["classification"], classification)
                                self.assertEqual(inspected["readOnly"], classification == "read")
                                self.assertEqual(
                                    inspected["commands"][0]["classification"], classification
                                )
                                self.assertEqual(
                                    inspected["commands"][0].get("prefix"),
                                    None
                                    if operation == "exec"
                                    else ["docker", *([group] if group else []), operation],
                                )

            for command, classification, prefix in (
                ("docker -DHunix:///var/run/docker.sock ps", "read", ["docker", "ps"]),
                ("docker -Dc foo compose ps", "read", ["docker", "compose", "ps"]),
                ("docker -DDc foo compose down", "write", ["docker", "compose", "down"]),
                ("docker -DD ps", "read", ["docker", "ps"]),
            ):
                with self.subTest(command=command):
                    inspected = runner.inspect_shell(command, str(root))
                    self.assertTrue(inspected["analysisComplete"])
                    self.assertEqual(inspected["classification"], classification)
                    self.assertEqual(inspected["readOnly"], classification == "read")
                    self.assertEqual(inspected["commands"][0]["prefix"], prefix)

            for output in (
                "--output out.yaml",
                "--output=out.yaml",
                "-o out.yaml",
                "-oout.yaml",
                "-o=out.yaml",
                "--lock-image-digests",
            ):
                command = (
                    "docker --context foo compose --progress plain -f ./compose.yaml "
                    f"config {output}"
                )
                with self.subTest(command=command):
                    inspected = runner.inspect_shell(command, str(root))
                    self.assertTrue(inspected["analysisComplete"])
                    self.assertEqual(inspected["classification"], "write")
                    self.assertEqual(
                        inspected["commands"][0]["prefix"], ["docker", "compose", "config"]
                    )

            for command in ("docker", "docker --help", "docker --version", "docker -v"):
                with self.subTest(command=command):
                    inspected = runner.inspect_shell(command, str(root))
                    self.assertTrue(inspected["analysisComplete"])
                    self.assertTrue(inspected["readOnly"])
                    self.assertNotIn("prefix", inspected["commands"][0])

            for command in (
                "docker --unknown value compose ps",
                "docker -DXHunix:///var/run/docker.sock ps",
                "docker -D-context foo compose ps",
                "docker -Dc",
                "docker --context",
                "docker compose -f",
                "docker compose --unknown value ps",
                "docker compose",
                "docker system",
            ):
                with self.subTest(command=command):
                    inspected = runner.inspect_shell(command, str(root))
                    self.assertEqual(inspected["classification"], "read")
                    self.assertNotIn("prefix", inspected["commands"][0])

            for group in ("compose", "container", "image", "network", "volume", "system"):
                command = f"docker {group} future-operation"
                with self.subTest(command=command):
                    inspected = runner.inspect_shell(command, str(root))
                    self.assertEqual(inspected["classification"], "read")
                    self.assertEqual(
                        inspected["commands"][0]["prefix"], ["docker", group, "future-operation"]
                    )

    def test_inspection_prefixes_are_token_based_and_writes_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "target").write_text("safe")
            (root / "repository").mkdir()
            config = configuration(root)
            runner = ProcessRunner(config, Filesystem(config))

            write = runner.inspect_shell(
                "cat target > output.txt && git -C repository status",
                os.fspath(root),
            )
            self.assertEqual(write["classification"], "write")
            self.assertFalse(write["readOnly"])
            self.assertEqual(write["commands"][0]["classification"], "write")
            self.assertEqual(write["commands"][1]["prefix"], ["git", "status"])

            read = runner.inspect_shell("cat < target", os.fspath(root))
            self.assertEqual(read["classification"], "read")
            self.assertEqual(read["commands"][0]["tokens"], ["cat"])

            unknown = runner.inspect_shell("curl https://example.invalid", os.fspath(root))
            self.assertEqual(unknown["classification"], "read")
            self.assertTrue(unknown["analysisComplete"])
            self.assertEqual(unknown["commands"][0]["prefix"], ["curl"])

            interpreter = runner.inspect_shell("python -c pass", os.fspath(root))
            self.assertEqual(interpreter["classification"], "read")
            self.assertNotIn("prefix", interpreter["commands"][0])

            for command in ("./git status", "git -c alias.check=!true check"):
                with self.subTest(command=command):
                    unsafe_prefix = runner.inspect_shell(command, os.fspath(root))
                    self.assertNotIn("prefix", unsafe_prefix["commands"][0])

            nested = runner.inspect_shell("cat $(touch target)", os.fspath(root))
            self.assertEqual(nested["classification"], "write")
            self.assertEqual(len(nested["commands"]), 2)

            with self.assertRaises(AgentError) as raised:
                runner.inspect_shell("cat 'unterminated", os.fspath(root))
            self.assertEqual(raised.exception.code, "process.invalidSyntax")


class ShellInspectionTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        for directory in (
            "workspace",
            "outside",
            "confidential",
            "dev",
            *[p[1:] for p in EXECUTION_PATH.split(":") if p not in {"/bin", "/sbin"}],
        ):
            (self.root / directory).mkdir(parents=True, exist_ok=True)
        (self.root / "bin").symlink_to("usr/bin")
        (self.root / "sbin").symlink_to("usr/sbin")
        for name in ("ls", "python3.13"):
            (self.root / "usr/bin" / name).write_text("installed fixture")
            (self.root / "usr/bin" / name).chmod(0o755)
        (self.root / "usr/bin/python3").symlink_to("python3.13")
        (self.root / "outside/file").write_text("external")
        (self.root / "confidential/file").write_text("private")
        os.mknod(self.root / "dev/null", stat.S_IFCHR | 0o666, os.makedev(1, 3))
        config = configuration(self.root)
        self.filesystem = Filesystem(config)
        self.runner = ProcessRunner(config, self.filesystem)

        @contextmanager
        def fixture_root():
            fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                yield fd
            finally:
                os.close(fd)

        self.enterContext(patch.object(self.filesystem, "_root_fd", fixture_root))
        self.which = self.enterContext(
            patch("matoloop_agent.process.shutil.which", return_value="/usr/bin/ls")
        )
        self.spawn = self.enterContext(
            patch(
                "asyncio.create_subprocess_exec",
                side_effect=AssertionError("Inspection must not execute"),
            )
        )

    def inspect(self, command: str) -> dict:
        result = self.runner.inspect_shell(command, "/workspace")
        self.spawn.assert_not_called()
        return result

    def test_exact_screenshot_inspection_has_only_fingerprinted_special_targets(self) -> None:
        result = self.inspect(SCREENSHOT_COMMAND)
        self.assertTrue(result["analysisComplete"])
        self.assertEqual(result["classification"], "read")
        self.assertEqual(len(result["commands"]), 6)
        self.assertEqual(
            {(t["path"], t["kind"]) for t in result["targets"]},
            {
                ("/dev/null", "discard"),
                ("/usr/bin", "toolchain_metadata"),
                ("/usr/bin/ls", "toolchain_metadata"),
                ("/usr/bin/python3", "toolchain_metadata"),
                ("/usr/bin/python3.13", "toolchain_metadata"),
            },
        )
        for target in result["targets"]:
            self.assertTrue(
                {"device", "rdev", "inode", "mode", "size", "modifiedNs", "changedNs"}
                <= target.keys()
            )
        self.assertEqual(result["targets"][0]["rdev"], os.makedev(1, 3))
        self.assertEqual(
            self.inspect(SCREENSHOT_COMMAND)["inspectionRevision"], result["inspectionRevision"]
        )
        (self.root / "usr/bin/python3.13").write_text("changed installed fixture")
        self.assertNotEqual(
            self.inspect(SCREENSHOT_COMMAND)["inspectionRevision"], result["inspectionRevision"]
        )
        self.which.assert_called_with("ls", path=EXECUTION_PATH)

    def test_python_execution_operand_preserves_paths_and_mutation_classification(self) -> None:
        for command in (
            "python -m unittest -v",
            "python3 -munittest -v",
            "python3.13 -IB -W error -X dev -m unittest -v",
            "python3 -BWerror -Xdev -Imunittest -v",
        ):
            with self.subTest(command=command):
                result = self.inspect(command)
                self.assertTrue(result["analysisComplete"])
                self.assertEqual(result["classification"], "read")
                self.assertEqual(result["targets"], [])
                self.assertEqual(result["commands"][0]["prefix"][-1], "unittest")
        for arguments in (
            "-m pip install package",
            "-Im pip uninstall package",
            "-mpip install package",
        ):
            with self.subTest(arguments=arguments):
                self.assertEqual(self.inspect("python3 " + arguments)["classification"], "write")
        for arguments in (
            "/outside/file -m unittest",
            "-W error -X dev /outside/file -c pass",
            "-- /outside/file -m unittest",
        ):
            with self.subTest(arguments=arguments):
                result = self.inspect("python3 " + arguments)
                self.assertIn(
                    ("/outside/file", "script"),
                    {(t["path"], t["kind"]) for t in result["targets"]},
                )
        result = self.inspect("python3 -m unittest /outside/file > /confidential/output")
        self.assertEqual(result["classification"], "write")
        self.assertEqual(
            {t["path"] for t in result["targets"]}, {"/outside/file", "/confidential/output"}
        )
        (self.root / "workspace/link").symlink_to("../outside/file")
        for command in ("python3 link -m unittest", "python3 --future link"):
            with self.subTest(command=command), self.assertRaises(AgentError):
                self.inspect(command)
        for command in (
            "python3 --future value -m unittest",
            "python3 -m",
            "python3 -W",
            "python3 -m $MODULE",
        ):
            with self.subTest(command=command):
                result = self.inspect(command)
                self.assertFalse(result["analysisComplete"])
                self.assertTrue(all("prefix" not in unit for unit in result["commands"]))
        for command in ("python3 -cpass", "python3 -Ic 'pass' -m pip install package"):
            with self.subTest(command=command):
                result = self.inspect(command)
                self.assertEqual(result["targets"], [])
                self.assertNotIn("prefix", result["commands"][0])

    def test_literal_argument_whitespace_preserves_shell_boundaries_and_revision(self) -> None:
        code = "if True:\n\tprint(1)"
        for quote in ("'", '"'):
            command = f"python3 -c {quote}{code}{quote}"
            result = self.inspect(command)
            self.assertTrue(result["analysisComplete"])
            self.assertEqual(result["commands"][0]["tokens"], ["python3", "-c", code])
            self.assertNotIn("prefix", result["commands"][0])
            argv, _ = self.runner.execution_argv(
                current_directory="/workspace",
                command=command,
                expected_inspection_revision=result["inspectionRevision"],
            )
            self.assertEqual(argv[-2:], ["-c", command])
            with self.assertRaises(AgentError) as raised:
                self.runner.execution_argv(
                    current_directory="/workspace",
                    command=command.replace("print(1)", "print(2)"),
                    expected_inspection_revision=result["inspectionRevision"],
                )
            self.assertEqual(raised.exception.code, "process.inspectionChanged")
        result = self.inspect("python3 -c 'print(1)\nprint(2)'\nrm /outside/file")
        self.assertTrue(result["analysisComplete"])
        self.assertEqual(result["classification"], "write")
        self.assertIn("/outside/file", [t["path"] for t in result["targets"]])
        self.assertEqual(result["commands"][1]["tokens"], ["rm", "/outside/file"])
        with self.assertRaises(AgentError) as raised:
            self.inspect("python3 -c 'print(1)\nprint(2)")
        self.assertEqual(raised.exception.code, "process.invalidSyntax")

    def test_argument_whitespace_keeps_executable_prefix_and_control_restrictions(self) -> None:
        for whitespace in ("\n", "\t"):
            result = self.inspect(f"'py{whitespace}thon3' -c pass")
            self.assertFalse(result["analysisComplete"])
            self.assertEqual(result["commands"], [])
            result = self.inspect(f"git 'sta{whitespace}tus'")
            self.assertTrue(result["analysisComplete"])
            self.assertNotIn("prefix", result["commands"][0])
        for control in ("\r", "\x01", "\x1b", "\x7f", "\x80", "\x9f"):
            result = self.inspect(f"printf 'a{control}b'")
            self.assertFalse(result["analysisComplete"])
            self.assertEqual(result["commands"], [])

    def test_metadata_literals_globs_no_matches_aliases_and_normal_access(self) -> None:
        for operand in (
            "/usr/bin",
            "/usr/bin/python3.13",
            "/usr/bin/python*",
            "/usr/bin/python?.13",
            "/usr/bin/python[0-9].13",
            "/bin/python*",
            "/usr/bin/no-match*",
        ):
            with self.subTest(operand=operand):
                result = self.inspect(f"ls {operand}")
                self.assertTrue(result["analysisComplete"])
                self.assertTrue(result["readOnly"])
                self.assertTrue(all(t["kind"] == "toolchain_metadata" for t in result["targets"]))
                self.assertIn("/usr/bin", [t["path"] for t in result["targets"]])
        result = self.inspect("ls /bin/python*")
        self.assertIn("/bin", [t["path"] for t in result["targets"]])
        self.assertIn("/bin/python3", [t["path"] for t in result["targets"]])
        result = self.inspect("ls /usr/bin/missing")
        self.assertFalse(result["analysisComplete"])
        self.assertIn("/usr/bin/missing", [t["path"] for t in result["targets"]])
        result = self.inspect(
            "ls /usr/bin/python*; cat /usr/bin/python3.13; cat /dev/null; pwd >/dev/null"
        )
        self.assertTrue(result["readOnly"])
        kinds = {(t["path"], t["kind"]) for t in result["targets"]}
        self.assertTrue(
            {
                ("/usr/bin/python3.13", "path"),
                ("/usr/bin/python3.13", "toolchain_metadata"),
                ("/dev/null", "path"),
                ("/dev/null", "discard"),
            }
            <= kinds
        )

    def test_metadata_does_not_apply_to_shell_changes_wrappers_or_quoted_globs(self) -> None:
        for command in (
            "PATH=/outside ls /usr/bin/python*",
            "PATH=/outside; ls /usr/bin/python*",
            "ls() { pwd; }; ls /usr/bin/python*",
            "env ls /usr/bin/python*",
            "command ls /usr/bin/python*",
            "./ls /usr/bin/python*",
            "echo $(pwd); ls /usr/bin/python*",
            "ls /usr/bin/$(pwd)*",
            "ls '/usr/bin/python*'",
            'ls "/usr/bin/python*"',
            r"ls /usr/bin/python\*",
            "ls /usr/bin/python'*'",
            "ls /usr/bin/python* '/usr/bin/python*'",
            "ls /usr/bin/python* /usr/bin/python'*'",
            "ls /usr/bin/..",
            "read PATH; ls /usr/bin/python*",
            "trap 'ls() { pwd; }' EXIT; ls /usr/bin/python*",
            "ls /usr/bin/python[[:digit:]]*",
            "ls /usr/bin/python[^x]*",
            "find /usr/bin -name 'python*'",
            *[
                f"ls {flag} /usr/bin/python*"
                for flag in ("-L", "-H", "-R", "-lL", "--dereference", "--recursive", "--unknown")
            ],
        ):
            with self.subTest(command=command):
                result = self.inspect(command)
                self.assertTrue(result["readOnly"])
                self.assertFalse(any(t["kind"] == "toolchain_metadata" for t in result["targets"]))
        for installed in (None, "/usr/local/bin/ls", "/outside/wrapper"):
            with self.subTest(installed=installed):
                self.which.return_value = installed
                result = self.inspect("ls /usr/bin/python*")
                self.assertFalse(result["analysisComplete"])
                self.assertTrue(all(t["kind"] != "toolchain_metadata" for t in result["targets"]))

    def test_discard_is_only_verified_static_output_and_other_writes_stay_sticky(self) -> None:
        for command in (
            ">/dev/null",
            "pwd 2>/dev/null",
            "pwd >> /dev/null",
            "pwd >'/dev/null'",
            "pwd &>/dev/null",
        ):
            with self.subTest(command=command):
                result = self.inspect(command)
                self.assertTrue(result["readOnly"])
                self.assertTrue(result["analysisComplete"])
                self.assertEqual([t["kind"] for t in result["targets"]], ["discard"])
        for command in (
            "> output",
            "touch --version; pwd >/dev/null",
            "rm --help; " + SCREENSHOT_COMMAND,
            "ls /usr/bin/python* >/workspace/output",
            "pwd >$OUTPUT",
        ):
            with self.subTest(command=command):
                self.assertEqual(self.inspect(command)["classification"], "write")
        result = self.inspect("pwd </dev/null")
        self.assertEqual([t["kind"] for t in result["targets"]], ["input"])
        result = self.inspect("pwd <>/dev/null")
        self.assertEqual(result["classification"], "write")
        self.assertEqual([t["kind"] for t in result["targets"]], ["output"])
        null = self.root / "dev/null"
        null.unlink()
        for kind in ("file", "link", "wrong-device", "missing"):
            with self.subTest(kind=kind):
                if kind == "file":
                    null.write_text("not a device")
                elif kind == "link":
                    null.symlink_to("../outside/file")
                elif kind == "wrong-device":
                    os.mknod(null, stat.S_IFCHR | 0o666, os.makedev(1, 5))
                with self.assertRaises(AgentError):
                    self.inspect("pwd >/dev/null")
                if kind != "missing":
                    null.unlink()

    def test_unresolved_analysis_continues_and_hard_violations_are_not_missing(self) -> None:
        command = "cat missing /outside/file /confidential/file; unknown; touch ./new"
        result = self.inspect(command)
        self.assertFalse(result["analysisComplete"])
        self.assertEqual(result["classification"], "write")
        self.assertTrue(
            {"/workspace/missing", "/outside/file", "/confidential/file", "/workspace/new"}
            <= {t["path"] for t in result["targets"]}
        )
        self.assertTrue(all("prefix" in unit for unit in result["commands"]))
        result = self.inspect(
            "cat $MISSING /outside/file; cat /confidential/file; echo $(touch ./new)"
        )
        self.assertFalse(result["analysisComplete"])
        self.assertEqual(result["classification"], "write")
        self.assertTrue(all("prefix" not in unit for unit in result["commands"]))
        self.assertTrue(
            {"/outside/file", "/confidential/file", "/workspace/new"}
            <= {t["path"] for t in result["targets"]}
        )
        dynamic = self.inspect("cat /missing-parent/$MISSING")
        self.assertIn(
            {"path": "/missing-parent", "kind": "directory", "access": "read"},
            dynamic["targets"],
        )
        (self.root / "workspace/link").symlink_to("../outside/file")
        for command in (
            "cat missing link",
            "cat $MISSING link",
            "echo " + "x" * 1025 + "; cat link",
        ):
            with self.subTest(command=command), self.assertRaises(AgentError) as raised:
                self.inspect(command)
            self.assertEqual(raised.exception.code, "path.unsupportedType")

    def test_mixed_command_retains_local_access_and_classification(self) -> None:
        result = self.inspect("cat /outside/file > /workspace/output; touch /outside/file")
        self.assertEqual(result["classification"], "write")
        self.assertEqual(
            {(target["path"], target["access"]) for target in result["targets"]},
            {
                ("/outside/file", "read"),
                ("/outside/file", "write"),
                ("/workspace/output", "write"),
            },
        )

    def test_incomplete_extraction_is_read_unless_a_bounded_known_write_is_seen(self) -> None:
        for command in (
            "echo " + "x" * 1025,
            "echo " + "x " * 256,
            "; ".join(["pwd"] * 65),
            "$COMMAND",
            "cat missing",
            "git remote future-operation",
            "git stash future-operation",
            "docker compose future-operation",
        ):
            with self.subTest(command=command):
                result = self.inspect(command)
                self.assertTrue(result["readOnly"])
                self.assertLessEqual(len(result["commands"]), 64)
                if not result["analysisComplete"] and command != "cat missing":
                    self.assertTrue(all("prefix" not in unit for unit in result["commands"]))
        for command in (
            "echo " + "x" * 1025 + "; touch file",
            "touch " + "x" * 1025,
            "echo $(rm file)",
            ">file",
        ):
            with self.subTest(command=command):
                self.assertEqual(self.inspect(command)["classification"], "write")

    def test_metadata_no_follow_and_enumeration_bound(self) -> None:
        with patch("matoloop_agent.filesystem.os.scandir", wraps=os.scandir) as scanned:
            with patch("matoloop_agent.filesystem.os.stat", wraps=os.stat) as inspected:
                self.inspect("ls /usr/bin/python*")
        self.assertTrue(all(isinstance(call.args[0], int) for call in scanned.call_args_list))
        self.assertTrue(
            all(
                call.kwargs.get("follow_symlinks") is False and "dir_fd" in call.kwargs
                for call in inspected.call_args_list
            )
        )
        link = self.root / "usr/bin/python3"
        link.unlink()
        link.symlink_to("/outside", target_is_directory=True)
        with self.assertRaises(AgentError):
            self.inspect("ls /usr/bin/python*")
        link.unlink()
        link.symlink_to("/confidential/file")
        result = self.inspect("ls /usr/bin/python*")
        self.assertIn("/confidential/file", [t["path"] for t in result["targets"]])
        (self.root / "bin").unlink()
        (self.root / "bin").symlink_to("outside")
        with self.assertRaises(AgentError):
            self.inspect("ls /bin/python*")
        self.filesystem.max_entries = 1
        with self.assertRaises(AgentError) as raised:
            self.inspect("ls /usr/bin/no-match*")
        self.assertEqual(raised.exception.code, "process.inspectionLimitExceeded")

    def test_serialization_limits_do_not_hide_late_writers_or_paths(self) -> None:
        (self.root / "workspace/link").symlink_to("../outside/file")
        for command in (
            "pwd; " * 64 + "cat ./link",
            "cat " + "missing " * 256 + "./link",
            "cat --" + "x" * 1025 + " ./link",
            "cat $'can\\'t' ./link",
        ):
            with self.subTest(command=command), self.assertRaises(AgentError) as raised:
                self.inspect(command)
            self.assertEqual(raised.exception.code, "path.unsupportedType")
        for command in (
            "pwd; " * 64 + "rm ./victim",
            "find . " + "-print " * 256 + "-delete",
            "docker image save " + "--quiet " * 256 + "--output ./archive",
            "rm $'can\\'t'",
        ):
            with self.subTest(command=command):
                result = self.inspect(command)
                self.assertEqual(result["classification"], "write")
                self.assertFalse(result["analysisComplete"])
                self.assertLessEqual(len(result["commands"]), 64)
                self.assertTrue(all("prefix" not in unit for unit in result["commands"]))
                self.assertTrue(all(len(unit["tokens"]) <= 256 for unit in result["commands"]))
        result = self.inspect("pwd; " * 64 + "cat /outside/file /confidential/file")
        self.assertTrue(
            {"/outside/file", "/confidential/file"} <= {t["path"] for t in result["targets"]}
        )
        result = self.inspect("cat " + "missing " * 256 + "/outside/file /confidential/file")
        self.assertTrue(
            {"/outside/file", "/confidential/file"} <= {t["path"] for t in result["targets"]}
        )
        self.assertEqual(result["commands"], [])
        for command in ("cat " + " ".join(f"file{i}" for i in range(257)), " " * (256 * 1024 + 1)):
            with self.subTest(command=command), self.assertRaises(AgentError) as raised:
                self.inspect(command)
            self.assertEqual(raised.exception.code, "process.inspectionLimitExceeded")

    def test_dot_globs_retain_ordinary_parent_scopes(self) -> None:
        (self.root / "usr/bin/.env").write_text("private")
        for pattern in (".*", ".?", ".*?", "..*"):
            with self.subTest(pattern=pattern):
                result = self.inspect(f"ls /usr/bin/{pattern}")
                self.assertTrue(result["readOnly"])
                self.assertFalse(result["analysisComplete"])
                self.assertIn(
                    ("/usr", "directory"), {(t["path"], t["kind"]) for t in result["targets"]}
                )
                self.assertFalse(any(t["kind"] == "toolchain_metadata" for t in result["targets"]))

    def test_large_listings_use_fingerprints_without_losing_hidden_scopes(self) -> None:
        directory = self.root / "usr/bin"
        for index in range(300):
            (directory / f"tool{index}").write_text("tool")
            (directory / f"other{index}").write_text("other tool")
        result = self.inspect("ls -l /usr/bin")
        self.assertTrue(result["analysisComplete"])
        self.assertTrue(all(t["kind"] == "toolchain_metadata" for t in result["targets"]))
        self.assertLessEqual(len(result["targets"]), 2)
        self.assertTrue(any("listingFingerprint" in t for t in result["targets"]))
        (directory / "tool299").write_text("changed attributes")
        self.assertNotEqual(
            result["inspectionRevision"], self.inspect("ls -l /usr/bin")["inspectionRevision"]
        )
        result = self.inspect("ls /usr/bin/tool*")
        self.assertTrue(result["readOnly"])
        self.assertLessEqual(len(result["targets"]), 256)
        self.assertIn(
            ("/usr/bin", "directory"), {(t["path"], t["kind"]) for t in result["targets"]}
        )
        (directory / ".env").write_text("private")
        result = self.inspect("ls -la /usr/bin")
        self.assertIn(
            ("/usr/bin/.env", "path"), {(t["path"], t["kind"]) for t in result["targets"]}
        )
        self.assertLessEqual(len(result["targets"]), 256)
        result = self.inspect("ls /usr/bin/.env*")
        self.assertIn(
            ("/usr/bin/.env", "path"), {(t["path"], t["kind"]) for t in result["targets"]}
        )
        command = "ls /usr/bin/tool*; ls /usr/bin/other*"
        before = self.inspect(command)["inspectionRevision"]
        (directory / "other299").write_text("new attributes")
        self.assertNotEqual(before, self.inspect(command)["inspectionRevision"])
        result = self.inspect(
            "cat " + " ".join(f"file{i}" for i in range(250)) + "; ls /usr/bin/tool*"
        )
        self.assertLessEqual(len(result["targets"]), 256)
        self.assertIn(
            ("/usr/bin", "directory"), {(t["path"], t["kind"]) for t in result["targets"]}
        )
        for index in range(256):
            (directory / f".hidden{index}").write_text("hidden")
        with self.assertRaises(AgentError) as raised:
            self.inspect("ls -a /usr/bin")
        self.assertEqual(raised.exception.code, "process.inspectionLimitExceeded")

    def test_docker_archive_outputs_and_established_mutators(self) -> None:
        for operation in ("save", "export", "image save", "container export"):
            self.assertTrue(self.inspect(f"docker {operation} item")["readOnly"])
            for output in (
                "-o ./archive",
                "-o./archive",
                "--output ./archive",
                "--output=./archive",
            ):
                with self.subTest(operation=operation, output=output):
                    result = self.inspect(f"docker --context local {operation} item {output}")
                    self.assertEqual(result["classification"], "write")
        for operation in (
            "cp",
            "create",
            "import",
            "load",
            "pause",
            "rename",
            "unpause",
            "update",
            "compose scale",
            "container update",
            "image load",
        ):
            with self.subTest(operation=operation):
                self.assertEqual(
                    self.inspect(f"docker {operation} item")["classification"], "write"
                )
        for operation in ("future", "image future", "container future"):
            self.assertTrue(self.inspect(f"docker {operation} --output archive")["readOnly"])

    def test_read_write_redirect_normalization_preserves_original_syntax_and_data(self) -> None:
        for command in (
            "<>file",
            "3<>file",
            "cat <>file",
            "cat 3<>file",
            "cat <>one 4<>two",
            "cat <>/dev/null",
            "cat 3<>/dev/null",
        ):
            with self.subTest(command=command):
                result = self.inspect(command)
                self.assertTrue(result["analysisComplete"])
                self.assertEqual(result["classification"], "write")
                self.assertTrue(all(t["kind"] == "output" for t in result["targets"]))
                argv, _ = self.runner.execution_argv(
                    current_directory="/workspace",
                    command=command,
                    expected_inspection_revision=result["inspectionRevision"],
                )
                self.assertEqual(argv, [str(self.runner.shell), "-c", command])
        for command in ("printf '%s' '<>file'", "cat <<EOF\n<>file\nEOF\n"):
            with self.subTest(command=command):
                self.assertTrue(self.inspect(command)["readOnly"])
        command = "cat <<EOF\n<>literal\nEOF\ncat <>file"
        result = self.inspect(command)
        self.assertEqual(result["classification"], "write")
        self.assertEqual([t["path"] for t in result["targets"]], ["/workspace/file"])
        for command in ("cat < >file", "cat <>", "cat <>file; echo '"):
            with self.subTest(command=command), self.assertRaises(AgentError) as raised:
                self.inspect(command)
            self.assertEqual(raised.exception.code, "process.invalidSyntax")

    def test_external_toolchain_link_destinations_are_ordinary_targets(self) -> None:
        vendor = self.root / "opt/vendor"
        vendor.mkdir(parents=True)
        (vendor / "tool").write_text("installed vendor tool")
        (self.root / "usr/local/bin/tool").symlink_to("/opt/vendor/tool")
        result = self.inspect("ls /usr/local/bin/tool*")
        self.assertIn(
            ("/opt/vendor/tool", "path"), {(t["path"], t["kind"]) for t in result["targets"]}
        )
        self.assertTrue(
            all(
                t["path"] in EXECUTION_PATH.split(":")
                or str(Path(t["path"]).parent) in EXECUTION_PATH.split(":")
                for t in result["targets"]
                if t["kind"] == "toolchain_metadata"
            )
        )
        (vendor / "tool").unlink()
        (vendor / "tool").mkdir()
        with self.assertRaises(AgentError):
            self.inspect("ls /usr/local/bin/tool*")

    def test_ansi_c_path_quoting_never_produces_a_fabricated_target(self) -> None:
        (self.root / "outside/link").symlink_to("file")
        with self.assertRaises(AgentError) as raised:
            self.inspect("printf >$'/outside/file' /outside/link")
        self.assertEqual(raised.exception.code, "path.unsupportedType")
        for shell in ("bash", "dash"):
            self.runner.shell = Path("/usr/bin") / shell
            for word in (
                "$'/outside/link'",
                "$'/outside/file'",
                "$'/dev/null'",
                "$'/outside/'link",
                "./$'link'",
                r"$'\057outside\057link'",
                r"$'/outside/\x6cink'",
                r"$'/outside/file\0ignored'",
                r"$'/outside/\u006cink'",
                "$'/outside/\u00e4'",
            ):
                for operator in (">", ">>", "1<>", "<"):
                    with self.subTest(shell=shell, word=word, operator=operator):
                        with self.assertRaises(AgentError) as raised:
                            self.inspect(f"printf x {operator}{word}")
                        self.assertEqual(raised.exception.code, "process.unsupportedTargetQuoting")
                        self.assertEqual(
                            str(raised.exception), "Shell path target uses unsupported quoting"
                        )
            with self.assertRaises(AgentError) as raised:
                self.inspect("cat $'/outside/file'")
            self.assertEqual(raised.exception.code, "process.unsupportedTargetQuoting")
            result = self.inspect("printf '%s' $'ordinary data'; cat /outside/file")
            self.assertFalse(result["analysisComplete"])
            self.assertEqual([t["path"] for t in result["targets"]], ["/outside/file"])
            self.assertTrue(all("prefix" not in unit for unit in result["commands"]))

        literal = self.root / "workspace/$'/outside/file'"
        literal.parent.mkdir(parents=True)
        literal.write_text("literal dollar and quote characters")
        result = self.inspect("printf x > \"$'/outside/file'\"")
        self.assertTrue(result["analysisComplete"])
        self.assertEqual([t["path"] for t in result["targets"]], ["/workspace/$'/outside/file'"])

    def test_first_redirect_destination_and_trailing_argv_are_inspected(self) -> None:
        (self.root / "workspace/input").write_text("input")
        (self.root / "workspace/link").symlink_to("../outside/file")
        for command in (
            "printf 1<>./link x",
            "printf >./link x",
            "cat <input ./link",
            "cat <./link /outside/file",
            "cat <<EOF ./link\nnot an argument\nEOF\n",
        ):
            with self.subTest(command=command), self.assertRaises(AgentError) as raised:
                self.inspect(command)
            self.assertEqual(raised.exception.code, "path.unsupportedType")

        for command, tokens, targets in (
            (
                "cat <input /outside/file",
                ["cat", "/outside/file"],
                {("/workspace/input", "input"), ("/outside/file", "path")},
            ),
            (
                "cat <input /outside/file >out /confidential/file",
                ["cat", "/outside/file", "/confidential/file"],
                {
                    ("/workspace/input", "input"),
                    ("/workspace/out", "output"),
                    ("/outside/file", "path"),
                    ("/confidential/file", "path"),
                },
            ),
            (
                "printf >out x /outside/file",
                ["printf", "x", "/outside/file"],
                {("/workspace/out", "output"), ("/outside/file", "path")},
            ),
            (
                "cat >&2 /outside/file",
                ["cat", "/outside/file"],
                {("/outside/file", "path")},
            ),
            (
                "cat <<'EOF' /outside/file\n/ignored/body\nEOF\n",
                ["cat", "/outside/file"],
                {("/outside/file", "path")},
            ),
        ):
            with self.subTest(command=command):
                result = self.inspect(command)
                self.assertTrue(result["analysisComplete"])
                self.assertEqual(result["commands"][0]["tokens"], tokens)
                self.assertEqual({(t["path"], t["kind"]) for t in result["targets"]}, targets)

        result = self.inspect("docker >out image save -o ./archive")
        self.assertEqual(
            result["commands"][0]["tokens"], ["docker", "image", "save", "-o", "./archive"]
        )
        self.assertEqual(result["commands"][0]["prefix"], ["docker", "image", "save"])
        self.assertEqual(result["classification"], "write")

    def test_redirect_arguments_preserve_nested_command_and_data_ownership(self) -> None:
        (self.root / "workspace/input").write_text("input")
        result = self.inspect('echo "$(cat <input /outside/file)" >out last')
        self.assertFalse(result["analysisComplete"])
        self.assertEqual(
            [unit["tokens"] for unit in result["commands"]],
            [
                ["echo", "$(cat <input /outside/file)", "last"],
                ["cat", "/outside/file"],
            ],
        )
        self.assertEqual(
            {(t["path"], t["kind"]) for t in result["targets"]},
            {
                ("/workspace/input", "input"),
                ("/workspace/out", "output"),
                ("/outside/file", "path"),
            },
        )
        result = self.inspect("{ cat input; } >out")
        self.assertEqual(result["commands"][0]["tokens"], ["cat", "input"])
        result = self.inspect('printf >/dev/null "%s" "$(pwd)"')
        self.assertTrue(result["readOnly"])
        self.assertEqual([t["kind"] for t in result["targets"]], ["discard"])
        result = self.inspect("ls >/dev/null /usr/bin/python*")
        self.assertTrue(result["readOnly"])
        self.assertTrue(result["analysisComplete"])
        self.assertEqual(result["commands"][0]["tokens"], ["ls", "/usr/bin/python*"])
        self.assertTrue(
            all(t["kind"] in {"discard", "toolchain_metadata"} for t in result["targets"])
        )
        result = self.inspect("printf >$(printf '%s' $'name') /outside/file")
        self.assertFalse(result["analysisComplete"])
        self.assertEqual([t["path"] for t in result["targets"]], ["/outside/file"])


if __name__ == "__main__":
    unittest.main()
