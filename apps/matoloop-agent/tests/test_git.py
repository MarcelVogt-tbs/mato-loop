import asyncio
import os
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from matoloop_agent import git as git_module
from matoloop_agent.config import AgentConfig
from matoloop_agent.errors import AgentError
from matoloop_agent.filesystem import Filesystem
from matoloop_agent.git import MAX_TEXT_BYTES, changes


class GitChangesTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.fs = Filesystem(AgentConfig.model_validate({"homeDirectory": self.root}))

    def git(self, *args, cwd=None):
        return subprocess.check_output(
            ["git", "-c", "user.name=Test", "-c", "user.email=test@example.com", *args],
            cwd=cwd or self.root,
            stderr=subprocess.DEVNULL,
        )

    async def test_plain_directory_and_unborn_repository(self):
        self.assertIsNone((await changes(self.fs, str(self.root)))["repository"])
        self.git("init")
        (self.root / "new.txt").write_text("new\n")
        self.assertEqual(
            (await changes(self.fs, str(self.root)))["files"],
            [{"path": "new.txt", "status": "added", "additions": 1, "deletions": 0}],
        )

    async def test_combines_index_working_tree_new_and_deleted_without_touching_index(self):
        self.git("init")
        (self.root / "edit.txt").write_text("old\n")
        (self.root / "deleted.txt").write_text("gone\n")
        (self.root / "untrack.txt").write_text("keep on disk\n")
        (self.root / ".gitignore").write_text("ignored.txt\n")
        self.git("add", ".")
        self.git("commit", "-m", "Initial")
        self.git("rm", "--cached", "untrack.txt")
        (self.root / "edit.txt").write_text("staged\n")
        self.git("add", "edit.txt")
        (self.root / "edit.txt").write_text("working\nextra\n")
        (self.root / "deleted.txt").unlink()
        (self.root / "new [1].txt").write_text("new\n")
        (self.root / "ignored.txt").write_text("ignored\n")
        index = (self.root / ".git/index").read_bytes()
        result = await changes(self.fs, str(self.root))
        self.assertEqual(
            result["files"],
            [
                {"path": "deleted.txt", "status": "deleted", "additions": 0, "deletions": 1},
                {"path": "edit.txt", "status": "modified", "additions": 2, "deletions": 1},
                {"path": "new [1].txt", "status": "added", "additions": 1, "deletions": 0},
                {"path": "untrack.txt", "status": "deleted", "additions": 0, "deletions": 1},
            ],
        )
        (self.root / "unrelated\nfile").write_text("not selected\n")
        diff = await changes(self.fs, str(self.root), "edit.txt")
        self.assertEqual((diff["before"], diff["after"]), ("old\n", "working\nextra\n"))
        self.assertEqual((self.root / ".git/index").read_bytes(), index)

    async def test_inspection_does_not_execute_clean_or_process_filters(self):
        self.git("init")
        (self.root / "sample.txt").write_text("old\n")
        self.git("add", ".")
        self.git("commit", "-m", "Initial")
        marker = self.root / "filter-executed"
        command = shlex.join(
            [
                sys.executable,
                "-c",
                f"from pathlib import Path; Path({str(marker)!r}).touch(); raise SystemExit(1)",
            ]
        )
        for option in ("clean", "process"):
            self.git("config", f"filter.probe.{option}", command)
        self.git("config", "filter.probe.required", "true")
        (self.root / ".gitattributes").write_text("sample.txt filter=probe\n")
        (self.root / "sample.txt").write_text("new\n")
        await changes(self.fs, str(self.root))
        await changes(self.fs, str(self.root), "sample.txt")
        config_path = self.root / ".git/config"
        late_config = config_path.read_text().replace('[filter "probe"]', '[filter "late"]')
        inspect_filters = git_module._disabled_filters
        writer = None

        def introduce_filter():
            self.fs.upsert(str(config_path), late_config)
            self.fs.upsert(str(self.root / ".gitattributes"), "sample.txt filter=late\n")

        async def snapshot_then_write(directory):
            nonlocal writer
            config = await inspect_filters(directory)
            writer = asyncio.create_task(asyncio.to_thread(introduce_filter))
            await asyncio.wait({writer}, timeout=0.1)
            return config

        with patch.object(git_module, "_disabled_filters", snapshot_then_write):
            try:
                await changes(self.fs, str(self.root))
            finally:
                if writer:
                    await writer
        self.assertFalse(marker.exists())

    @unittest.skipUnless(os.geteuid() == 0, "Requires ownership changes")
    async def test_unsafe_repository_is_an_access_error_not_a_missing_repository(self):
        self.git("init")
        os.chown(self.root, 1000, -1)
        # Git also trusts SUDO_UID when tests run as root through sudo.
        with patch.dict(os.environ, {"SUDO_UID": "0"}), self.assertRaises(AgentError) as error:
            await changes(self.fs, str(self.root))
        self.assertEqual(error.exception.code, "path.accessDenied")

    async def test_worktree_is_detected_from_subdirectory_and_isolated(self):
        self.git("init")
        (self.root / "file.txt").write_text("base\n")
        self.git("add", ".")
        self.git("commit", "-m", "Initial")
        worktree = self.root / "worktree"
        self.git("worktree", "add", "-b", "other", str(worktree))
        (self.root / "file.txt").write_text("main\n")
        (worktree / "file.txt").write_text("other\n")
        (worktree / "subdir").mkdir()
        diff = await changes(self.fs, str(worktree / "subdir"), "file.txt")
        self.assertEqual((diff["repository"], diff["after"]), (str(worktree), "other\n"))

    async def test_preview_limits_and_symlinks_do_not_expose_external_contents(self):
        self.git("init")
        (self.root / "large.txt").write_bytes(b"a" * (MAX_TEXT_BYTES + 1))
        (self.root / "binary").write_bytes(b"a\0b")
        (self.root / "link").symlink_to("/etc/passwd")
        for file, reason in [
            ("large.txt", "tooLarge"),
            ("binary", "binary"),
            ("link", "unsupported"),
        ]:
            with self.subTest(file=file):
                result = await changes(self.fs, str(self.root), file)
                self.assertEqual(
                    (result["reason"], result["before"], result["after"]), (reason, "", "")
                )
