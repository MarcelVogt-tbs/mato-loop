import hashlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mato_loop_execution_agent import workspace as workspace_module
from mato_loop_execution_agent.config import AgentConfig
from mato_loop_execution_agent.workspace import AgentError, Workspace

AGENT_ID = "2ea504cc-e3c6-4e0d-aa05-080328100fe8"
ROOT_ID = "4d0a316f-5fb5-4ec5-a5c4-04aed66c5e6d"


def config(root: Path, data: Path) -> AgentConfig:
    return AgentConfig.model_validate(
        {
            "backendUrl": "ws://localhost/api/execution-agents/connect",
            "token": "secret",
            "agentId": AGENT_ID,
            "name": "Agent",
            "roots": [{"id": ROOT_ID, "label": "Projects", "path": root}],
            "dataDir": data,
            "allowInsecure": True,
            "maxFileBytes": 1024,
            "maxTransferBytes": 4096,
        }
    )


class WorkspaceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        base = Path(self.temporary.name)
        self.root = base / "root"
        self.root.mkdir()
        self.workspace = Workspace(config(self.root, base / "data"))
        self.revision = self.workspace.roots[ROOT_ID].revision

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_full_file_lifecycle_and_revision_check(self) -> None:
        self.workspace.mkdir(ROOT_ID, self.revision, ".", "src", False)
        written = self.workspace.write(
            ROOT_ID, self.revision, ".", "src/app.py", "one\ntwo\n", None
        )
        self.assertEqual(written["sha256"], hashlib.sha256(b"one\ntwo\n").hexdigest())
        read = self.workspace.read(ROOT_ID, self.revision, ".", "src/app.py", 2, 1, 2000)
        self.assertEqual(read["content"], "2: two")
        self.assertEqual(
            self.workspace.list(ROOT_ID, self.revision, ".", "src", 0, 10)["entries"][0]["name"],
            "app.py",
        )
        with self.assertRaisesRegex(AgentError, "changed"):
            self.workspace.write(ROOT_ID, self.revision, ".", "src/app.py", "stale", "0" * 64)
        self.workspace.rename(ROOT_ID, self.revision, ".", "src/app.py", "src/main.py")
        self.workspace.copy_or_move(
            ROOT_ID,
            self.revision,
            ".",
            "src/main.py",
            ROOT_ID,
            self.revision,
            ".",
            "copy.py",
            move=False,
        )
        self.workspace.copy_or_move(
            ROOT_ID,
            self.revision,
            ".",
            "copy.py",
            ROOT_ID,
            self.revision,
            ".",
            "moved.py",
            move=True,
        )
        self.workspace.delete(ROOT_ID, self.revision, ".", "src", True)
        self.assertEqual((self.root / "moved.py").read_text(), "one\ntwo\n")
        self.assertFalse(any(path.name.startswith(".write-") for path in self.root.rglob("*")))
        self.assertEqual(stat_mode(self.root / "moved.py"), 0o600)

    def test_rejects_unsafe_paths_symlinks_and_stale_roots(self) -> None:
        outside = self.root.parent / "outside"
        outside.mkdir()
        (outside / "secret").write_text("secret")
        (self.root / "link").symlink_to(outside, target_is_directory=True)

        for path in ("../outside/secret", "/etc/passwd", "link/secret"):
            with self.subTest(path=path), self.assertRaises(AgentError):
                self.workspace.read(ROOT_ID, self.revision, ".", path, 1, 1, 100)
        with self.assertRaises(AgentError):
            self.workspace.stat(ROOT_ID, "0" * 64, ".", ".")

    def test_import_is_atomic_and_hashes_content(self) -> None:
        source = self.root.parent / "download"
        source.write_bytes(b"binary\x00data")
        original_open = os.open
        with patch.object(workspace_module.os, "open", side_effect=original_open) as opened:
            result = self.workspace.import_file(
                ROOT_ID, self.revision, ".", "asset.bin", source, False
            )
        self.assertEqual(sum(call.args[0] == source for call in opened.call_args_list), 1)
        self.assertEqual(result["sha256"], hashlib.sha256(source.read_bytes()).hexdigest())
        with self.workspace.open_export(ROOT_ID, self.revision, ".", "asset.bin") as (
            handle,
            size,
            digest,
        ):
            self.assertEqual(handle.read(), b"binary\x00data")
            self.assertEqual(size, 11)
            self.assertEqual(digest, result["sha256"])

    def test_import_no_replace_publication_wins_race_safely(self) -> None:
        source = self.root.parent / "download-race"
        source.write_bytes(b"incoming")
        original = workspace_module._rename_no_replace

        def race(source_fd: int, source_name: str, destination_fd: int, name: str) -> None:
            descriptor = os.open(
                name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=destination_fd
            )
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(b"winner")
            original(source_fd, source_name, destination_fd, name)

        with patch.object(workspace_module, "_rename_no_replace", side_effect=race):
            with self.assertRaises(AgentError):
                self.workspace.import_file(ROOT_ID, self.revision, ".", "raced.bin", source, False)
        self.assertEqual((self.root / "raced.bin").read_bytes(), b"winner")
        self.assertFalse(any(path.name.startswith(".import-") for path in self.root.iterdir()))

    def test_copy_enforces_limits_again_while_streaming(self) -> None:
        source = self.root / "growing.bin"
        source.write_bytes(b"1234")
        workspace = Workspace(
            config(self.root, self.root.parent / "copy-data").model_copy(
                update={"max_transfer_bytes": 4}
            )
        )
        revision = workspace.roots[ROOT_ID].revision
        original = workspace._usage_at

        def grow_after_scan(
            parent: int, name: str, forbidden: set[tuple[int, int]]
        ) -> tuple[int, int]:
            result = original(parent, name, forbidden)
            source.write_bytes(b"12345")
            return result

        with patch.object(workspace, "_usage_at", side_effect=grow_after_scan):
            with self.assertRaises(AgentError):
                workspace.copy_or_move(
                    ROOT_ID,
                    revision,
                    ".",
                    "growing.bin",
                    ROOT_ID,
                    revision,
                    ".",
                    "copy.bin",
                    move=False,
                )
        self.assertFalse((self.root / "copy.bin").exists())

    def test_read_response_is_bounded_by_transport_size(self) -> None:
        base = self.root.parent
        limited = config(self.root, base / "limited-data").model_copy(
            update={"max_message_bytes": 1024}
        )
        workspace = Workspace(limited)
        revision = workspace.roots[ROOT_ID].revision
        (self.root / "long.txt").write_text("x" * 1000)
        with self.assertRaisesRegex(AgentError, "response limit"):
            workspace.read(ROOT_ID, revision, ".", "long.txt", 1, 1, 1000)


def stat_mode(path: Path) -> int:
    return os.stat(path, follow_symlinks=False).st_mode & 0o777


if __name__ == "__main__":
    unittest.main()
