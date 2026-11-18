"""Exercise portable macOS branches and explicitly gated native Darwin acceptance."""

import ctypes
import errno
import os
import stat
import sys
import tempfile
import unittest
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import Mock, patch

from matoloop_agent import filesystem as filesystem_module
from matoloop_agent.config import AgentConfig
from matoloop_agent.errors import AgentError
from matoloop_agent.filesystem import Filesystem
from matoloop_agent.process import ProcessRunner
from matoloop_agent.search import FilesystemSearch
from matoloop_agent.temporary import temporary_directory
from matoloop_agent.terminal import TerminalManager


@unittest.skipUnless(os.name == "posix", "POSIX descriptors are required")
class MacOSPosixTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.config = AgentConfig(homeDirectory=self.root, shellExecutable=Path("/bin/bash"))
        self.config._data_directory = self.root / "data"
        self.filesystem = Filesystem(self.config)
        self.runner = ProcessRunner(self.config, self.filesystem)

    def test_darwin_rename_flags_and_destination_conflict(self) -> None:
        rename = Mock(return_value=0)
        with (
            patch("sys.platform", "darwin"),
            patch.object(filesystem_module, "_renameatx_np", rename),
        ):
            filesystem_module._rename_no_replace(10, "from", 11, "to")
            filesystem_module._rename_exchange(10, "from", 11, "to")
            self.assertEqual(
                [call.args for call in rename.call_args_list],
                [(10, b"from", 11, b"to", 4), (10, b"from", 11, b"to", 2)],
            )
            rename.return_value = -1
            with patch.object(ctypes, "get_errno", return_value=errno.EEXIST):
                with self.assertRaises(AgentError) as raised:
                    filesystem_module._rename_no_replace(10, "from", 11, "to")
            self.assertEqual(raised.exception.code, "path.exists")

    def test_darwin_null_redirect_uses_native_device_identity(self) -> None:
        native = sys.platform == "darwin"
        with (
            patch("sys.platform", "darwin"),
            nullcontext()
            if native
            else patch.object(
                self.filesystem,
                "resolve_path_target",
                return_value={"path": "/dev/null", "mode": stat.S_IFCHR, "rdev": os.makedev(3, 2)},
            ),
        ):
            self.assertEqual(
                self.runner.inspect_shell("pwd >/dev/null", str(self.root))["targets"][0]["kind"],
                "discard",
            )

    def test_darwin_toolchain_listing_uses_installed_ls(self) -> None:
        native = sys.platform == "darwin"

        def resolve(path, _current, **_kwargs):
            if path != "/bin/ls":
                raise AgentError("path.notFound", "Only native ls is installed")
            return {"path": path, "mode": stat.S_IFREG}

        with (
            nullcontext() if native else patch("shutil.which", return_value="/bin/ls"),
            nullcontext()
            if native
            else patch.object(self.filesystem, "inspect_toolchain_directory", return_value=[]),
            nullcontext()
            if native
            else patch.object(self.filesystem, "resolve_path_target", side_effect=resolve),
            patch("sys.platform", "darwin"),
        ):
            self.assertIn(
                "/bin/ls",
                [
                    target["path"]
                    for target in self.runner.inspect_shell("ls /bin/ls", str(self.root))["targets"]
                ],
            )

    def test_inspection_without_o_path_still_rejects_directory_links(self) -> None:
        directory = self.root / "directory"
        directory.mkdir()
        link = self.root / "link"
        link.symlink_to(directory, target_is_directory=True)
        with patch.dict(os.__dict__):
            os.__dict__.pop("O_PATH", None)
            self.filesystem.resolve_path_target("directory/file", str(self.root), must_exist=False)
            with self.assertRaises(AgentError) as raised:
                self.filesystem.resolve_path_target("link/file", str(self.root), must_exist=False)
        self.assertEqual(raised.exception.code, "path.symlinkUnsupported")

    def test_temporary_root_canonicalizes_only_the_configured_scratch_base(self) -> None:
        alias = self.root / "alias"
        alias.symlink_to(self.root, target_is_directory=True)
        with patch("sys.platform", "darwin"), patch("tempfile.gettempdir", return_value=str(alias)):
            self.assertEqual(temporary_directory(), str(self.root / "mato-loop"))

    def test_search_finds_managed_ripgrep_without_path(self) -> None:
        executable = self.root / "tools/bin/rg"
        executable.parent.mkdir(parents=True)
        executable.write_text("#!/bin/sh\nexit 0\n")
        executable.chmod(0o700)
        with patch("sys.platform", "darwin"), patch("shutil.which", return_value=None):
            search = FilesystemSearch(self.filesystem)
        self.assertEqual(search.executable, executable)

    def test_copy_move_reject_existing_same_inode_before_source_quarantine(self) -> None:
        source = self.root / "source"
        destination = self.root / "alias"
        source.write_text("preserved")
        destination.hardlink_to(source)
        for move in (False, True):
            with self.subTest(move=move), self.assertRaises(AgentError) as raised:
                self.filesystem.copy_or_move(str(source), str(destination), move=move)
            self.assertEqual(raised.exception.code, "path.invalidDestination")
        self.assertEqual(sorted(path.name for path in self.root.iterdir()), ["alias", "source"])

    async def test_process_trampoline_keeps_fd_cwd_after_directory_replacement(self) -> None:
        current = self.root / "workspace"
        current.mkdir()
        (current / "marker").write_text("protected")
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "marker").write_text("escaped")
        original = self.runner._cwd

        def swap(path):
            cwd, fd = original(path)
            current.rename(self.root / "saved")
            current.symlink_to(outside, target_is_directory=True)
            return cwd, fd

        output = []

        async def stream(channel, text):
            output.append((channel, text))

        # Test the same auxiliary interpreter on Linux, not a mocked spawn.
        with patch("sys.platform", "darwin"), patch.object(self.runner, "_cwd", side_effect=swap):
            result = await self.runner.exec(
                current_directory=str(current), command="cat marker", timeout=5, stream=stream
            )
        self.assertEqual((result["exitCode"], output), (0, [("stdout", "protected")]))

    async def test_search_trampoline_keeps_fd_cwd_after_directory_replacement(self) -> None:
        current = self.root / "workspace"
        current.mkdir()
        (current / "marker").write_text("protected")
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "marker").write_text("escaped")
        search = FilesystemSearch(self.filesystem)
        search.executable = Path("/bin/cat").resolve()
        original = search._cwd

        def swap(path):
            result = original(path)
            current.rename(self.root / "saved")
            current.symlink_to(outside, target_is_directory=True)
            return result

        output = []
        with patch("sys.platform", "darwin"), patch.object(search, "_cwd", side_effect=swap):
            result = await search._run(
                str(current), ["marker"], b"\n", lambda record: output.append(record) or False
            )
        self.assertEqual((result, output), ((0, False), [b"protected"]))

    @unittest.skipUnless(sys.platform == "darwin", "Native Darwin filesystem is required")
    def test_native_filesystem_lifecycle(self) -> None:
        directory = self.root / "files"
        self.filesystem.mkdir(str(directory), False)
        source = directory / "source.txt"
        written = self.filesystem.write(str(source), "before\n", None)
        self.filesystem.edit(str(source), "before", "after", False, written["sha256"])
        renamed = directory / "renamed.txt"
        self.filesystem.rename(str(source), str(renamed))
        copied = directory / "copied.txt"
        self.filesystem.copy_or_move(str(renamed), str(copied), move=False)
        moved = directory / "moved.txt"
        self.filesystem.copy_or_move(str(copied), str(moved), move=True)
        self.assertEqual(moved.read_text(), "after\n")
        self.assertFalse(source.exists() or copied.exists())
        self.filesystem.delete(str(directory), True)
        self.assertFalse(directory.exists())

    @unittest.skipUnless(sys.platform == "darwin", "Native case-insensitive volume is required")
    def test_native_case_alias_copy_move_preserve_source_identity(self) -> None:
        source = self.root / "CaseSensitiveName.txt"
        alias = self.root / "casesensitivename.txt"
        source.write_text("preserved")
        if not alias.exists() or not source.samefile(alias):
            self.skipTest("The native test volume is case-sensitive")
        before = source.stat()
        for move in (False, True):
            with self.subTest(move=move), self.assertRaises(AgentError) as raised:
                self.filesystem.copy_or_move(str(source), str(alias), move=move)
            self.assertEqual(raised.exception.code, "path.invalidDestination")
            after = source.stat()
            self.assertEqual((after.st_dev, after.st_ino), (before.st_dev, before.st_ino))
            self.assertEqual(source.read_text(), "preserved")

    @unittest.skipUnless(sys.platform == "darwin", "Native Darwin process and PTY are required")
    async def test_native_subprocess_and_pty(self) -> None:
        terminal = TerminalManager(self.config, self.runner)
        self.addAsyncCleanup(terminal.close_all)
        for pty in (False, True):
            output = []

            async def stream(channel, text, output=output):
                output.append(text)

            arguments = dict(
                current_directory=str(self.root), command="printf native", timeout=5, stream=stream
            )
            with self.subTest(pty=pty):
                if pty:
                    result = await terminal.run(
                        terminal_id="native",
                        rows=24,
                        columns=80,
                        expected_inspection_revision=None,
                        **arguments,
                    )
                else:
                    result = await self.runner.exec(**arguments)
                self.assertEqual((result["exitCode"], "".join(output)), (0, "native"))

    @unittest.skipUnless(sys.platform == "darwin", "Native Darwin search is required")
    async def test_native_search(self) -> None:
        search = FilesystemSearch(self.filesystem)
        self.assertIsNotNone(
            search.executable, "Native search requires ripgrep on PATH or in managed tools"
        )
        target = self.root / "search.txt"
        target.write_text("native marker\n")
        self.assertEqual(
            await search.glob(str(self.root), "*.txt"),
            {"paths": [str(target)], "truncated": False},
        )
        self.assertEqual(
            await search.grep(str(self.root), "marker", None),
            {
                "matches": [{"path": str(target), "line": 1, "text": "native marker"}],
                "truncated": False,
            },
        )
