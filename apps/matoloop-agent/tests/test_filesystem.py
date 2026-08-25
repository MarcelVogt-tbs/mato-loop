import errno
import hashlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from matoloop_agent import filesystem as filesystem_module
from matoloop_agent.config import AgentConfig
from matoloop_agent.filesystem import AgentError, Filesystem


def config(home: Path, data: Path, **updates) -> AgentConfig:
    configuration = AgentConfig.model_validate(
        {
            "homeDirectory": home,
            "maxFileBytes": 1024,
            "maxTransferBytes": 4096,
            **updates,
        }
    )
    configuration._data_directory = data
    return configuration


class FilesystemTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.filesystem = Filesystem(config(self.root, self.root / "data"))

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def path(self, name: str) -> str:
        return os.fspath(self.root / name)

    def test_full_file_lifecycle_uses_absolute_paths(self) -> None:
        self.filesystem.mkdir(self.path("src"), False)
        written = self.filesystem.write(self.path("src/app.py"), "one\ntwo\n", None)
        self.assertEqual(written["sha256"], hashlib.sha256(b"one\ntwo\n").hexdigest())
        with self.assertRaisesRegex(AgentError, "exists"):
            self.filesystem.write(self.path("src/app.py"), "overwrite", None)
        read = self.filesystem.read(self.path("src/app.py"), 2, 1, 2000)
        self.assertEqual(read["content"], "2: two")
        listed = self.filesystem.list(self.path("src"), 0, 10)
        self.assertEqual(listed["entries"][0]["path"], self.path("src/app.py"))
        with self.assertRaisesRegex(AgentError, "changed"):
            self.filesystem.write(self.path("src/app.py"), "stale", "0" * 64)
        self.filesystem.rename(self.path("src/app.py"), self.path("src/main.py"))
        self.filesystem.copy_or_move(self.path("src/main.py"), self.path("copy.py"), move=False)
        self.filesystem.copy_or_move(self.path("copy.py"), self.path("moved.py"), move=True)
        self.filesystem.delete(self.path("src"), True)
        self.assertEqual((self.root / "moved.py").read_text(), "one\ntwo\n")
        self.assertEqual(os.stat(self.root / "moved.py").st_mode & 0o777, 0o600)

    def test_rejects_relative_non_normalized_and_symlink_paths(self) -> None:
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "secret").write_text("secret")
        (self.root / "link").symlink_to(outside, target_is_directory=True)
        for path in (
            "relative",
            self.path("outside/../outside/secret"),
            self.path("link/secret"),
        ):
            with self.subTest(path=path), self.assertRaises(AgentError):
                self.filesystem.read(path, 1, 1, 100)

    def test_every_directory_component_is_opened_without_following_links(self) -> None:
        real = self.root / "real"
        real.mkdir()
        (real / "file").write_text("safe")
        link = self.root / "link"
        link.symlink_to(real, target_is_directory=True)
        with patch.object(filesystem_module.os, "open", wraps=os.open) as opened:
            with self.assertRaises(AgentError):
                self.filesystem.read(os.fspath(link / "file"), 1, 1, 100)
        component_opens = [call for call in opened.call_args_list if call.kwargs.get("dir_fd")]
        self.assertTrue(component_opens)
        self.assertTrue(all(call.args[1] & os.O_NOFOLLOW for call in component_opens))

    def test_import_export_are_atomic_and_bounded(self) -> None:
        source = self.root / "download"
        source.write_bytes(b"binary\x00data")
        result = self.filesystem.import_file(self.path("asset.bin"), source, False)
        self.assertEqual(result["sha256"], hashlib.sha256(source.read_bytes()).hexdigest())
        with self.filesystem.open_export(self.path("asset.bin")) as (handle, size, digest):
            self.assertEqual(handle.read(), b"binary\x00data")
            self.assertEqual(size, 11)
            self.assertEqual(digest, result["sha256"])

        growing = self.root / "growing.bin"
        growing.write_bytes(b"1234")
        limited = Filesystem(config(self.root, self.root / "data", maxTransferBytes=4))
        original = limited._usage_at

        def grow(parent: int, name: str, forbidden) -> tuple[int, int]:
            usage = original(parent, name, forbidden)
            growing.write_bytes(b"12345")
            return usage

        with patch.object(limited, "_usage_at", side_effect=grow), self.assertRaises(AgentError):
            limited.copy_or_move(os.fspath(growing), self.path("copy.bin"), move=False)
        self.assertFalse((self.root / "copy.bin").exists())

    def test_compare_and_swap_write_restores_concurrent_edit(self) -> None:
        target = self.root / "app.py"
        target.write_text("old")
        expected = hashlib.sha256(b"old").hexdigest()
        exchange = filesystem_module._rename_exchange
        raced = False

        def race(source_fd: int, source: str, destination_fd: int, destination: str) -> None:
            nonlocal raced
            if not raced:
                raced = True
                winner = self.root / "winner"
                winner.write_text("concurrent")
                os.replace(winner, target)
            exchange(source_fd, source, destination_fd, destination)

        with patch.object(filesystem_module, "_rename_exchange", side_effect=race):
            with self.assertRaisesRegex(AgentError, "changed"):
                self.filesystem.write(os.fspath(target), "agent", expected)
        self.assertEqual(target.read_text(), "concurrent")

    def test_cross_device_move_preserves_a_concurrently_replaced_source(self) -> None:
        source = self.root / "source.txt"
        destination = self.root / "destination.txt"
        source.write_text("original")
        rename = filesystem_module._rename_no_replace
        copy = self.filesystem._copy_at

        def cross_device(
            source_fd: int, source_name: str, destination_fd: int, destination_name: str
        ) -> None:
            if source_name == source.name and destination_name == destination.name:
                raise OSError(errno.EXDEV, os.strerror(errno.EXDEV))
            rename(source_fd, source_name, destination_fd, destination_name)

        def replace_source(*args, **kwargs):
            identity = copy(*args, **kwargs)
            replacement = self.root / "replacement.txt"
            replacement.write_text("replacement")
            os.replace(replacement, source)
            return identity

        with (
            patch.object(filesystem_module, "_rename_no_replace", side_effect=cross_device),
            patch.object(self.filesystem, "_copy_at", side_effect=replace_source),
            self.assertRaisesRegex(AgentError, "changed"),
        ):
            self.filesystem.copy_or_move(os.fspath(source), os.fspath(destination), move=True)

        self.assertEqual(source.read_text(), "replacement")
        self.assertFalse(destination.exists())
        self.assertFalse(
            any(path.name.startswith((".copy-", ".move-")) for path in self.root.iterdir())
        )


if __name__ == "__main__":
    unittest.main()
