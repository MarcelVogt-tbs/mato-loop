import asyncio
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch


@unittest.skipUnless(sys.platform == "win32", "native Windows test")
class WindowsRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        from matoloop_agent.config import AgentConfig
        from matoloop_agent.filesystem_windows import WindowsFilesystem
        from matoloop_agent.process_windows import WindowsProcess

        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name) / "workspace"
        self.home.mkdir()
        config_path = Path(self.temporary.name) / "config.json"
        config_path.write_text(json.dumps({"homeDirectory": str(self.home)}), encoding="utf-8")
        config = AgentConfig.load(config_path)
        self.config = config
        self.filesystem = WindowsFilesystem(config)
        self.process = WindowsProcess(config, self.filesystem)
        self.wire = self.home.as_posix()

    def test_file_operations_and_no_follow(self) -> None:
        from matoloop_agent.errors import AgentError

        nested = self.wire + "/nested/inside"
        self.filesystem.mkdir(nested, True)
        self.assertEqual(self.filesystem.stat(nested)["kind"], "directory")
        self.assertIn(
            "nested", [entry["name"] for entry in self.filesystem.list(self.wire, 0, 10)["entries"]]
        )
        path = self.wire + "/note.txt"
        written = self.filesystem.write(path, "one\n", None)
        self.assertEqual(self.filesystem.read(path, 1, 10, 2000)["sha256"], written["sha256"])
        self.filesystem.edit(path, "one", "two", False, written["sha256"])
        self.assertIn("two", self.filesystem.read(path, 1, 10, 2000)["content"])
        revision = self.filesystem.read(path, 1, 10, 2000)["sha256"]
        self.filesystem.patch(
            f"*** Begin Patch\n*** Update File: {path}\n@@\n-two\n+three\n*** End Patch",
            {path: revision},
        )
        self.assertIn("three", self.filesystem.read(path, 1, 10, 2000)["content"])
        with self.assertRaises(AgentError):
            self.filesystem.write(path, "stale", written["sha256"])
        link = self.home / "alias.txt"
        try:
            link.symlink_to(self.home / "note.txt")
        except OSError:
            self.skipTest("Symlink creation is not permitted on this Windows account")
        self.assertEqual(self.filesystem.stat(link.as_posix())["kind"], "symlink")
        with self.assertRaises(AgentError):
            self.filesystem.read(link.as_posix(), 1, 10, 2000)

    def test_powershell_inspection_and_revision(self) -> None:
        read = self.process.inspect_shell("Get-Content ./note.txt", self.wire)
        self.assertEqual(read["classification"], "read")
        self.assertIn(self.wire + "/note.txt", [target["path"] for target in read["targets"]])
        write = self.process.inspect_shell("Set-Content ./note.txt two", self.wire)
        self.assertEqual(write["classification"], "write")

        async def execute():
            chunks = []

            async def stream(channel, text):
                chunks.append((channel, text))

            result = await self.process.exec(
                current_directory=self.wire,
                command="Write-Output 'Windows ready'",
                timeout=10,
                stream=stream,
            )
            self.assertEqual(result["exitCode"], 0)
            self.assertIn("Windows ready", "".join(text for _, text in chunks))

        asyncio.run(execute())

    def test_pairing_state_is_protected_by_windows_dpapi(self) -> None:
        from pydantic import SecretStr

        from matoloop_agent.credentials import CredentialState
        from matoloop_agent.credentials_windows import WindowsCredentialStore

        state = CredentialState(
            version=1,
            backendUrl="wss://example.test/api/execution-agents/connect",
            agentId="2ea504cc-e3c6-4e0d-aa05-080328100fe8",
            accessKey=SecretStr("private-access-key"),
            lastPairingTokenHash="a" * 64,
        )
        with WindowsCredentialStore(Path(self.temporary.name)) as store:
            store.save(state)
            self.assertEqual(store.load().access_key.get_secret_value(), "private-access-key")
        self.assertNotIn(
            b"private-access-key",
            (Path(self.temporary.name) / "agent-state" / "credential.dat").read_bytes(),
        )

    def test_mutation_stays_in_original_parent_after_path_swap(self) -> None:
        from matoloop_agent import windows_handles

        original = self.home / "project"
        original.mkdir()
        moved = self.home / "original-project"
        rename = windows_handles.rename

        def swap_then_publish(handle, parent, name, *, replace=False):
            original.rename(moved)
            original.mkdir()
            return rename(handle, parent, name, replace=replace)

        with patch.object(windows_handles, "rename", side_effect=swap_then_publish):
            self.filesystem.write((original / "result.txt").as_posix(), "bounded", None)
        self.assertEqual((moved / "result.txt").read_text(), "bounded")
        self.assertFalse((original / "result.txt").exists())

    def test_delete_uses_the_opened_file_after_parent_swap(self) -> None:
        from matoloop_agent import windows_handles

        original = self.home / "project"
        original.mkdir()
        (original / "remove.txt").write_text("original", encoding="utf-8")
        moved = self.home / "original-project"
        remove = windows_handles.remove

        def swap_then_remove(handle):
            original.rename(moved)
            original.mkdir()
            (original / "remove.txt").write_text("replacement", encoding="utf-8")
            return remove(handle)

        with patch.object(windows_handles, "remove", side_effect=swap_then_remove):
            self.filesystem.delete((original / "remove.txt").as_posix(), False)
        self.assertFalse((moved / "remove.txt").exists())
        self.assertEqual((original / "remove.txt").read_text(), "replacement")

    def test_read_uses_the_opened_parent_after_path_swap(self) -> None:
        from contextlib import contextmanager

        from matoloop_agent import windows_handles

        original = self.home / "project"
        original.mkdir()
        (original / "note.txt").write_text("approved", encoding="utf-8")
        moved = self.home / "original-project"
        child = windows_handles.child

        @contextmanager
        def swap_before_leaf(parent, name, **kwargs):
            if name == "note.txt":
                original.rename(moved)
                original.mkdir()
                (original / "note.txt").write_text("wrong target", encoding="utf-8")
            with child(parent, name, **kwargs) as handle:
                yield handle

        with patch.object(windows_handles, "child", side_effect=swap_before_leaf):
            content = self.filesystem.read((original / "note.txt").as_posix(), 1, 10, 2000)
        self.assertIn("approved", content["content"])
        self.assertNotIn("wrong target", content["content"])

    def test_windows_installer_parses_without_running_setup(self) -> None:
        installer = Path(__file__).parents[3] / "scripts/install-matoloop-agent.ps1"
        script_path = str(installer).replace("'", "''")
        command = (
            "$tokens=$null; $errors=$null; "
            "[System.Management.Automation.Language.Parser]::ParseFile("
            f"'{script_path}', [ref]$tokens, [ref]$errors) | Out-Null; "
            "if ($errors.Count) { $errors | Out-String | Write-Output; exit 1 }"
        )
        result = subprocess.run(
            [self.process.shell, "-NoProfile", "-NonInteractive", "-Command", command],
            capture_output=True,
            text=True,
            timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stdout)

    def test_maintenance_handoff_uses_only_a_fixed_task_and_bounded_job(self) -> None:
        from matoloop_agent.platform_windows import WindowsPlatform

        platform = WindowsPlatform()
        process = AsyncMock()
        process.returncode = 0
        with (
            patch.object(platform, "_maintenance_available", return_value=True),
            patch(
                "matoloop_agent.platform_windows.asyncio.create_subprocess_exec",
                return_value=process,
            ) as start,
        ):
            asyncio.run(
                platform.start_maintenance(
                    self.config,
                    "update",
                    "2ea504cc-e3c6-4e0d-aa05-080328100fe8",
                    None,
                )
            )
        self.assertEqual(
            json.loads((self.config.data_dir / "maintenance-request.json").read_text()),
            {
                "action": "update",
                "jobId": "2ea504cc-e3c6-4e0d-aa05-080328100fe8",
            },
        )
        self.assertIn(
            "Start-ScheduledTask -TaskName 'MatoLoop Agent Maintenance'", start.call_args.args[-1]
        )
