import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from matoloop_agent.administrator import run_administrator, sudo_options
from matoloop_agent.config import AgentConfig
from matoloop_agent.errors import AgentError
from matoloop_agent.filesystem import Filesystem
from matoloop_agent.process import ProcessRunner
from matoloop_agent.protocol import AdministratorArguments


class AdministratorTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name)
        self.config = AgentConfig(homeDirectory=self.home, shellExecutable=Path("/bin/sh"))
        self.file = self.home / "config.yaml"
        self.file.write_text("batchSize: 100\n")
        self.file.chmod(0o640)
        self.sudo = self.home / "sudo"
        self.sudo.write_text('#!/bin/sh\nshift 3\nshift\nexec "$@"\n')
        self.sudo.chmod(0o700)

    async def invoke(self, operation, *, stream=None, **arguments):
        # Only sudo authentication is replaced; the isolated worker is a real subprocess.
        with patch(
            "matoloop_agent.administrator.sudo_options",
            AsyncMock(return_value=(str(self.sudo), ["-S", "-p", ""])),
        ):
            return await run_administrator(
                self.config,
                AdministratorArguments(
                    operation=operation,
                    arguments={
                        **({"path": str(self.file)} if operation.startswith("filesystem.") else {}),
                        **arguments,
                    },
                    sudoPassword="private password",
                ),
                stream,
            )

    async def test_sudo_authentication_resolves_alias_and_keeps_password_on_stdin(self):
        self.sudo.write_text(
            "#!/bin/sh\n"
            'if [ "$1" = "-n" ]; then test -f "$0.cached"; exit $?; fi\n'
            "read -r password\n"
            'test "$password" = "correct" || exit 1\n'
            'touch "$0.cached"\n'
        )
        alias = self.home / "sudo-alias"
        alias.symlink_to(self.sudo)
        with patch("matoloop_agent.administrator.shutil.which", return_value=str(alias)):
            for password, code in [
                (None, "process.sudoAuthRequired"),
                ("wrong", "process.sudoAuthFailed"),
            ]:
                with self.subTest(password=password), self.assertRaises(AgentError) as failure:
                    await sudo_options(password, 2)
                self.assertEqual(failure.exception.code, code)
            self.assertEqual(await sudo_options("correct", 2), (str(self.sudo), ["-S", "-p", ""]))
            self.assertEqual(await sudo_options(None, 2), (str(self.sudo), ["-n"]))

    @unittest.skipUnless(
        os.geteuid() == 0, "The development container runs the privileged worker as root"
    )
    async def test_worker_streams_shell_output_before_completion(self):
        command = 'printf "%s\\n" "$APP_TOKEN"; sleep 0.4; printf "last\\n"'
        inspection = await self.invoke(
            "shell.inspect", command=command, currentDirectory=str(self.home)
        )
        first = asyncio.Event()

        async def stream(channel, value):
            if channel == "stdout" and "first" in value:
                first.set()

        running = asyncio.create_task(
            self.invoke(
                "shell.run",
                command=command,
                currentDirectory=str(self.home),
                expectedInspectionRevision=inspection["inspectionRevision"],
                environment={"APP_TOKEN": "first"},
                stream=stream,
            )
        )
        try:
            await asyncio.wait_for(first.wait(), 5)
            self.assertFalse(running.done())
        finally:
            await running

    @unittest.skipUnless(
        os.geteuid() == 0, "The development container runs the privileged worker as root"
    )
    async def test_worker_shell_timeout_stops_command(self):
        inspection = await self.invoke(
            "shell.inspect", command="sleep 5", currentDirectory=str(self.home)
        )
        result = await self.invoke(
            "shell.run",
            command="sleep 5",
            currentDirectory=str(self.home),
            expectedInspectionRevision=inspection["inspectionRevision"],
            timeoutSeconds=0.2,
        )
        self.assertTrue(result["timedOut"])

    @unittest.skipUnless(
        os.geteuid() == 0, "The development container runs the privileged worker as root"
    )
    async def test_worker_reads_and_atomically_edits_without_changing_mode_or_owner(self):
        before = self.file.stat()
        read = await self.invoke("filesystem.read")
        await self.invoke(
            "filesystem.edit", oldText="100", newText="8", expectedSha256=read["sha256"]
        )
        after = self.file.stat()
        self.assertEqual(
            (self.file.read_text(), after.st_mode & 0o777, after.st_uid, after.st_gid),
            ("batchSize: 8\n", 0o640, before.st_uid, before.st_gid),
        )

    @unittest.skipUnless(
        os.geteuid() == 0, "The development container runs the privileged worker as root"
    )
    async def test_worker_rejects_stale_revision_and_symlink(self):
        read = await self.invoke("filesystem.read")
        self.file.write_text("batchSize: 9\n")
        with self.assertRaises(AgentError) as changed:
            await self.invoke(
                "filesystem.write", content="batchSize: 8\n", expectedSha256=read["sha256"]
            )
        self.assertEqual(changed.exception.code, "file.changed")
        target = self.home / "target"
        self.file.rename(target)
        self.file.symlink_to(target)
        with self.assertRaises(AgentError):
            await self.invoke("filesystem.read")
        self.assertEqual(target.read_text(), "batchSize: 9\n")

    def test_elevated_classification_uses_the_same_parser_without_file_access(self):
        runner = ProcessRunner(self.config, Filesystem(self.config))
        with patch.object(
            runner.filesystem,
            "resolve_path_target",
            side_effect=AssertionError("Classification must not access files"),
        ):
            for command, expected in [
                ("cat /root/config.yaml", "read"),
                ("rm /root/config.yaml", "write"),
                ("cat /root/config.yaml > /root/copy.yaml", "write"),
                ("python3 -c 'print(1)'", "read"),
            ]:
                self.assertEqual(
                    runner.inspect_shell(command, str(self.home), resolve_paths=False)[
                        "classification"
                    ],
                    expected,
                )
