import errno
import hashlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

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

    def test_inspection_distinguishes_missing_paths_from_hard_errors(self) -> None:
        for path in ("missing", "missing/child"):
            with self.subTest(path=path):
                self.assertEqual(
                    self.filesystem.resolve_path_target(path, str(self.root), must_exist=False),
                    {"path": self.path(path)},
                )
                with self.assertRaises(AgentError) as raised:
                    self.filesystem.resolve_path_target(path, str(self.root), must_exist=True)
                self.assertEqual(raised.exception.code, "path.notFound")
        (self.root / "real").mkdir()
        (self.root / "real/file").write_text("safe")
        (self.root / "link").symlink_to("real", target_is_directory=True)
        for path in ("link", "link/file", "link/../real/file", "link/..", "real/file/child"):
            with self.subTest(path=path), self.assertRaises(AgentError) as raised:
                self.filesystem.resolve_path_target(path, str(self.root), must_exist=False)
            self.assertNotEqual(raised.exception.code, "path.notFound")
        with patch.object(
            filesystem_module.os, "stat", side_effect=PermissionError("private detail")
        ):
            with self.assertRaises(AgentError) as raised:
                self.filesystem.resolve_path_target("real/file", str(self.root), must_exist=False)
        self.assertEqual(raised.exception.code, "path.unavailable")
        self.assertNotIn("private detail", str(raised.exception))
        for path in (".", "real/..", "real/"):
            self.assertEqual(
                self.filesystem.resolve_path_target(path, str(self.root), must_exist=True)[
                    "targetKind"
                ],
                "directory",
            )

    def test_inspection_exceptions_do_not_change_direct_text_tool_semantics(self) -> None:
        target = self.root / "target"
        target.write_text("original\n")
        link = self.root / "link"
        link.symlink_to(target)
        revision = hashlib.sha256(target.read_bytes()).hexdigest()
        for path in (str(link), "/dev/null"):
            operations = (
                lambda path=path: self.filesystem.read(path, 1, 1, 100),
                lambda path=path: self.filesystem.write(path, "changed", None),
                lambda path=path: self.filesystem.edit(path, "original", "changed", False),
                lambda path=path: self.filesystem.patch(
                    f"*** Begin Patch\n*** Update File: {path}\n"
                    "@@\n-original\n+changed\n*** End Patch\n",
                    {path: revision},
                ),
            )
            for operation in operations:
                with self.subTest(path=path), self.assertRaises(AgentError):
                    operation()
        self.assertEqual(target.read_text(), "original\n")
        self.assertTrue(link.is_symlink())

    def test_read_distinguishes_unavailable_files(self) -> None:
        original_open = os.open
        cases = (
            (FileNotFoundError(errno.ENOENT, "private detail"), "path.notFound"),
            (PermissionError(errno.EACCES, "private detail"), "path.accessDenied"),
            (OSError(errno.EIO, "private detail"), "path.unavailable"),
        )
        for error, code in cases:

            def fail_file_open(path, flags, *args, _error=error, **kwargs):
                if path == "file":
                    raise _error
                return original_open(path, flags, *args, **kwargs)

            with (
                self.subTest(code=code),
                patch.object(filesystem_module.os, "open", side_effect=fail_file_open),
                self.assertRaises(AgentError) as raised,
            ):
                self.filesystem.read(self.path("file"), 1, 1, 100)
            self.assertEqual(raised.exception.code, code)
            self.assertNotIn("private detail", str(raised.exception))

    def test_read_missing_parent_reports_missing_path(self) -> None:
        with self.assertRaises(AgentError) as raised:
            self.filesystem.read(self.path("missing/file"), 1, 1, 100)
        self.assertEqual(raised.exception.code, "path.notFound")

    def test_export_rejects_fifo_replacement_without_blocking(self) -> None:
        source = self.root / "source"
        source.write_text("public")
        self.assertEqual(self.filesystem.stat(str(source))["kind"], "file")
        original_open = os.open

        def replace_before_open(path, flags, *args, **kwargs):
            if path == "source":
                self.assertTrue(flags & os.O_NONBLOCK, "FIFO rejection must not block")
                source.unlink()
                os.mkfifo(source)
            return original_open(path, flags, *args, **kwargs)

        with patch.object(filesystem_module.os, "open", side_effect=replace_before_open):
            with self.assertRaises(AgentError) as raised:
                with self.filesystem.open_export(str(source)):
                    self.fail("FIFO must not be exported")
        self.assertEqual(raised.exception.code, "path.notFile")

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
        self.assertFalse(any(path.name.startswith(".write-") for path in self.root.iterdir()))

    def test_edit_rejects_a_stale_revision(self) -> None:
        target = self.root / "app.py"
        target.write_text("current\n")
        with self.assertRaisesRegex(AgentError, "changed"):
            self.filesystem.edit(os.fspath(target), "current", "updated", False, "0" * 64)
        self.assertEqual(target.read_text(), "current\n")

    def test_text_mutations_preserve_only_ordinary_modes_before_publication(self) -> None:
        target = self.root / "script.sh"
        for operation in ("write", "upsert", "edit", "patch"):
            for mode in (0o755, 0o640, 0o6755):
                with self.subTest(operation=operation, mode=oct(mode)):
                    target.write_text("old\n")
                    target.chmod(mode)
                    revision = hashlib.sha256(target.read_bytes()).hexdigest()
                    sync = os.fsync
                    staged_modes = []

                    def check_staged(fd, staged_modes=staged_modes, sync=sync):
                        staged_modes.append(os.fstat(fd).st_mode & 0o7777)
                        self.assertEqual(target.read_text(), "old\n")
                        sync(fd)

                    with patch.object(filesystem_module.os, "fsync", side_effect=check_staged):
                        with patch.object(filesystem_module, "_fsync"):
                            if operation == "write":
                                self.filesystem.write(str(target), "new\n", revision)
                            elif operation == "upsert":
                                self.filesystem.upsert(str(target), "new\n")
                            elif operation == "edit":
                                self.filesystem.edit(str(target), "old", "new", False)
                            else:
                                self.filesystem.patch(
                                    f"*** Begin Patch\n*** Update File: {target}\n"
                                    "@@\n-old\n+new\n*** End Patch\n",
                                    {str(target): revision},
                                )
                    self.assertEqual(staged_modes, [mode & 0o777])
                    self.assertEqual(target.stat().st_mode & 0o7777, mode & 0o777)
                    self.assertEqual(target.read_text(), "new\n")

    def test_new_text_files_are_private(self) -> None:
        for operation in ("write", "upsert", "patch"):
            with self.subTest(operation=operation):
                target = self.root / operation
                previous_umask = os.umask(0o777)
                try:
                    if operation == "write":
                        self.filesystem.write(str(target), "new\n", None)
                    elif operation == "upsert":
                        self.filesystem.upsert(str(target), "new\n")
                    else:
                        self.filesystem.patch(
                            f"*** Begin Patch\n*** Add File: {target}\n+new\n*** End Patch\n",
                            {},
                        )
                finally:
                    os.umask(previous_umask)
                self.assertEqual(target.stat().st_mode & 0o7777, 0o600)

    def test_direct_mutations_use_destination_ownership(self) -> None:
        parent = self.root / "owned"
        parent.mkdir()
        try:
            os.chown(parent, os.geteuid() + 1, os.getegid() + 1)
        except PermissionError:
            self.skipTest("Changing ownership requires a privileged test process")
        owner = parent.stat()
        source = self.root / "source"
        source.write_bytes(b"source")
        source_directory = self.root / "source-directory"
        source_directory.mkdir()
        (source_directory / "child").write_text("child")

        self.filesystem.write(str(parent / "write"), "write", None)
        self.filesystem.upsert(str(parent / "upsert"), "upsert")
        self.filesystem.import_file(str(parent / "import"), source, False)
        self.filesystem.copy_or_move(str(source), str(parent / "copy"), move=False)
        self.filesystem.copy_or_move(str(source_directory), str(parent / "tree"), move=False)
        self.filesystem.mkdir(str(parent / "one/two"), True)
        self.filesystem.patch(
            f"*** Begin Patch\n*** Add File: {parent / 'patch'}\n+patch\n*** End Patch\n",
            {},
        )

        for path in (
            "write",
            "upsert",
            "import",
            "copy",
            "tree",
            "tree/child",
            "one",
            "one/two",
            "patch",
        ):
            with self.subTest(path=path):
                metadata = (parent / path).stat()
                self.assertEqual((metadata.st_uid, metadata.st_gid), (owner.st_uid, owner.st_gid))

    def test_replacements_preserve_ownership_and_ordinary_mode(self) -> None:
        parent = self.root / "replacements"
        parent.mkdir()
        source = self.root / "source"
        source.write_text("new")
        target = parent / "target"
        for operation in ("write", "upsert", "edit", "patch", "import"):
            with self.subTest(operation=operation):
                target.write_text("old")
                try:
                    os.chown(target, os.geteuid() + 1, os.getegid() + 1)
                except PermissionError:
                    self.skipTest("Changing ownership requires a privileged test process")
                target.chmod(0o6754)
                before = target.stat()
                if operation == "write":
                    self.filesystem.write(str(target), "new", hashlib.sha256(b"old").hexdigest())
                elif operation == "upsert":
                    self.filesystem.upsert(str(target), "new")
                elif operation == "edit":
                    self.filesystem.edit(str(target), "old", "new", False)
                elif operation == "patch":
                    self.filesystem.patch(
                        f"*** Begin Patch\n*** Update File: {target}\n"
                        "@@\n-old\n+new\n*** End Patch\n",
                        {str(target): hashlib.sha256(b"old").hexdigest()},
                    )
                else:
                    self.filesystem.import_file(str(target), source, True)
                after = target.stat()
                self.assertEqual((after.st_uid, after.st_gid), (before.st_uid, before.st_gid))
                self.assertEqual(after.st_mode & 0o7777, 0o754)

    def test_move_uses_destination_ownership(self) -> None:
        source = self.root / "source"
        source.write_text("source")
        parent = self.root / "owned"
        parent.mkdir()
        try:
            os.chown(parent, os.geteuid() + 1, os.getegid() + 1)
        except PermissionError:
            self.skipTest("Changing ownership requires a privileged test process")
        owner = parent.stat()
        destination = parent / "destination"
        self.filesystem.copy_or_move(str(source), str(destination), move=True)

        metadata = destination.stat()
        self.assertEqual((metadata.st_uid, metadata.st_gid), (owner.st_uid, owner.st_gid))
        self.assertFalse(source.exists())

    def test_non_root_operations_keep_kernel_assigned_ownership(self) -> None:
        with (
            patch.object(filesystem_module.os, "geteuid", return_value=1000),
            patch.object(filesystem_module.os, "fchown") as chown,
        ):
            self.filesystem._set_ownership(1, Mock(st_uid=2000, st_gid=2000))
        chown.assert_not_called()

    def test_ownership_failure_does_not_publish_new_paths(self) -> None:
        source = self.root / "source"
        source.write_text("source")
        operations = {
            "write": lambda path: self.filesystem.write(path, "new", None),
            "upsert": lambda path: self.filesystem.upsert(path, "new"),
            "import": lambda path: self.filesystem.import_file(path, source, False),
            "copy": lambda path: self.filesystem.copy_or_move(str(source), path, move=False),
            "mkdir": lambda path: self.filesystem.mkdir(path, False),
        }
        for name, operation in operations.items():
            target = self.root / name
            with (
                self.subTest(operation=name),
                patch.object(filesystem_module.os, "fchown", side_effect=PermissionError),
                self.assertRaises(PermissionError),
            ):
                operation(str(target))
            self.assertFalse(target.exists())

        replacements = {
            "write-existing": lambda path: self.filesystem.write(
                path, "new", hashlib.sha256(b"old").hexdigest()
            ),
            "upsert-existing": lambda path: self.filesystem.upsert(path, "new"),
            "import-existing": lambda path: self.filesystem.import_file(path, source, True),
        }
        for name, operation in replacements.items():
            target = self.root / name
            target.write_text("old")
            with (
                self.subTest(operation=name),
                patch.object(filesystem_module.os, "fchown", side_effect=PermissionError),
                self.assertRaises(PermissionError),
            ):
                operation(str(target))
            self.assertEqual(target.read_text(), "old")

    def test_patch_execution_failure_reports_completed_operations_and_destination(self) -> None:
        source = self.root / "source"
        source.write_text("source\n")
        destination = self.path("destination")
        revision = hashlib.sha256(source.read_bytes()).hexdigest()
        for failure in (AgentError("file.changed", "private detail"), OSError("private detail")):
            with self.subTest(failure=type(failure).__name__):
                added = self.root / type(failure).__name__
                patch_text = (
                    f"*** Begin Patch\n*** Add File: {added}\n+added\n"
                    f"*** Update File: {source}\n*** Move to: {destination}\n"
                    f"*** Add File: {self.path('later')}\n+later\n*** End Patch\n"
                )
                with patch.object(self.filesystem, "_move_revision", side_effect=failure):
                    with self.assertRaises(AgentError) as caught:
                        self.filesystem.patch(patch_text, {str(source): revision})
                self.assertEqual(caught.exception.code, "file.patchIncomplete")
                self.assertEqual(
                    caught.exception.params,
                    {
                        "completedOperations": 1,
                        "totalOperations": 3,
                        "failedOperationIndex": 1,
                        "path": str(source),
                        "destination": destination,
                    },
                )
                self.assertNotIn("private detail", str(caught.exception))
                self.assertEqual(added.read_text(), "added\n")
                self.assertFalse((self.root / "later").exists())

    def test_patch_fsync_failure_after_first_publication_reports_uncertain_failed_operation(
        self,
    ) -> None:
        first = self.root / "first"
        second = self.root / "second"
        patch_text = (
            f"*** Begin Patch\n*** Add File: {first}\n+first\n"
            f"*** Add File: {second}\n+second\n*** End Patch\n"
        )
        with patch.object(filesystem_module, "_fsync", side_effect=OSError("sync failed")):
            with self.assertRaises(AgentError) as caught:
                self.filesystem.patch(patch_text, {})
        self.assertEqual(caught.exception.code, "file.patchIncomplete")
        self.assertEqual(
            caught.exception.params,
            {
                "completedOperations": 0,
                "totalOperations": 2,
                "failedOperationIndex": 0,
                "path": str(first),
            },
        )
        self.assertIn("including in the failed operation", str(caught.exception))
        self.assertIn("Reread", str(caught.exception))
        self.assertEqual(first.read_text(), "first\n")
        self.assertFalse(second.exists())

    def test_patch_adds_updates_moves_and_deletes_files(self) -> None:
        updated = self.root / "updated.txt"
        moved = self.root / "moved.txt"
        deleted = self.root / "deleted.txt"
        updated.write_text("one\ntwo\n")
        moved.write_text("move me\n")
        deleted.write_text("delete me\n")
        revisions = {
            os.fspath(path): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in (updated, moved, deleted)
        }
        patch_text = f"""*** Begin Patch
*** Add File: {self.path("added.txt")}
+added
*** Update File: {updated}
@@
-one
+ONE
 two
*** Update File: {moved}
*** Move to: {self.path("renamed.txt")}
*** Delete File: {deleted}
*** End Patch
"""

        result = self.filesystem.patch(patch_text, revisions)

        self.assertEqual((self.root / "added.txt").read_text(), "added\n")
        self.assertEqual(updated.read_text(), "ONE\ntwo\n")
        self.assertFalse(moved.exists())
        self.assertEqual((self.root / "renamed.txt").read_text(), "move me\n")
        self.assertFalse(deleted.exists())
        self.assertEqual(
            [item["operation"] for item in result["files"]],
            ["add", "update", "move", "delete"],
        )
        self.assertEqual(result["files"][2]["destination"], self.path("renamed.txt"))

    def test_patch_preflights_every_revision_before_mutating(self) -> None:
        target = self.root / "target.txt"
        target.write_text("current\n")
        patch_text = f"""*** Begin Patch
*** Add File: {self.path("must-not-exist.txt")}
+new
*** Update File: {target}
@@
-current
+changed
*** End Patch
"""
        with self.assertRaisesRegex(AgentError, "changed") as caught:
            self.filesystem.patch(patch_text, {os.fspath(target): "0" * 64})
        self.assertEqual(caught.exception.code, "file.changed")
        self.assertIsNone(caught.exception.params)
        self.assertFalse((self.root / "must-not-exist.txt").exists())
        self.assertEqual(target.read_text(), "current\n")

    def test_patch_rejects_move_with_hunks_before_mutating(self) -> None:
        target = self.root / "target.txt"
        target.write_text("current\n")
        patch_text = f"""*** Begin Patch
*** Update File: {target}
*** Move to: {self.path("moved.txt")}
@@
-current
+changed
*** End Patch
"""
        revision = hashlib.sha256(target.read_bytes()).hexdigest()

        with self.assertRaisesRegex(AgentError, "Move cannot include update hunks"):
            self.filesystem.patch(patch_text, {os.fspath(target): revision})

        self.assertEqual(target.read_text(), "current\n")
        self.assertFalse((self.root / "moved.txt").exists())

    def test_patch_bounds_total_prepared_file_content(self) -> None:
        first = self.root / "first.txt"
        second = self.root / "second.txt"
        first.write_text("a" * 600)
        second.write_text("b" * 600)
        patch_text = f"""*** Begin Patch
*** Delete File: {first}
*** Delete File: {second}
*** End Patch
"""
        revisions = {
            os.fspath(path): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in (first, second)
        }

        with self.assertRaisesRegex(AgentError, "operation limit"):
            Filesystem(config(self.root, self.root / "data", maxTransferBytes=1024)).patch(
                patch_text, revisions
            )

        self.assertTrue(first.exists())
        self.assertTrue(second.exists())

    def test_patch_rejects_symlinks_duplicate_paths_and_invalid_markers(self) -> None:
        target = self.root / "target.txt"
        target.write_text("safe\n")
        link = self.root / "link.txt"
        link.symlink_to(target)
        digest = hashlib.sha256(target.read_bytes()).hexdigest()
        patches = (
            (
                f"*** Begin Patch\n*** Delete File: {link}\n*** End Patch\n",
                {os.fspath(link): digest},
            ),
            (
                f"*** Begin Patch\n*** Add File: {self.path('same.txt')}\n+x\n"
                f"*** Delete File: {self.path('same.txt')}\n*** End Patch\n",
                {self.path("same.txt"): digest},
            ),
            ("*** Begin Patch\n*** Add File: relative\n+x\n*** End Patch\n", {}),
        )
        for patch_text, revisions in patches:
            with self.subTest(patch=patch_text), self.assertRaises(AgentError):
                self.filesystem.patch(patch_text, revisions)
        self.assertEqual(target.read_text(), "safe\n")

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
