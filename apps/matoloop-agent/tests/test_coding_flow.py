import hashlib
import tempfile
import unittest
from pathlib import Path

from test_git import GIT, git
from test_process import configuration

from matoloop_agent.filesystem import Filesystem
from matoloop_agent.git import GitRunner
from matoloop_agent.process import ProcessRunner


class CodingFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_revision_patch_preserves_user_change_and_executable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            script = root / "check.sh"
            original = '#!/bin/sh\n# original comment\ntest "broken" = "fixed"\n'
            script.write_text(original, encoding="utf-8")
            script.chmod(0o755)
            git("init", "--initial-branch=main", cwd=root)
            git("add", "check.sh", cwd=root)
            baseline = git("ls-files", "--stage", cwd=root)
            self.assertTrue(baseline.startswith("100755 "))
            user_content = original.replace("# original comment", "# existing user change")
            script.write_text(user_content, encoding="utf-8")

            config = configuration(root)
            config.git_executable = GIT
            filesystem = Filesystem(config)
            process = ProcessRunner(config, filesystem)
            repository = GitRunner(config, filesystem)
            read = filesystem.read(str(script), 1, 20, 2000)
            self.assertIn("# existing user change", read["content"])
            self.assertEqual(read["sha256"], hashlib.sha256(script.read_bytes()).hexdigest())

            async def stream(_channel: str, _data: str) -> None:
                return None

            for expected_exit in (1, 0):
                if expected_exit == 0:
                    patched = filesystem.patch(
                        f"*** Begin Patch\n*** Update File: {script}\n@@\n"
                        '-test "broken" = "fixed"\n+test "fixed" = "fixed"\n'
                        "*** End Patch\n",
                        {str(script): read["sha256"]},
                    )
                    self.assertEqual(
                        patched["files"][0]["sha256"],
                        hashlib.sha256(script.read_bytes()).hexdigest(),
                    )
                inspected = process.inspect_shell("./check.sh", str(root))
                result = await process.exec(
                    current_directory=str(root),
                    command="./check.sh",
                    timeout=2,
                    stream=stream,
                    expected_inspection_revision=inspected["inspectionRevision"],
                )
                self.assertEqual(result["exitCode"], expected_exit)
                self.assertFalse(result["timedOut"])

            self.assertEqual(
                script.read_text(encoding="utf-8"),
                user_content.replace('test "broken"', 'test "fixed"'),
            )
            self.assertEqual(script.stat().st_mode & 0o7777, 0o755)
            diff = await repository.diff(str(root), False, ["check.sh"])
            self.assertFalse(diff["truncated"])
            self.assertIn("+# existing user change", diff["output"])
            self.assertIn('-test "broken" = "fixed"', diff["output"])
            self.assertIn('+test "fixed" = "fixed"', diff["output"])
            self.assertNotIn("mode change", diff["output"])
            self.assertEqual(git("ls-files", "--stage", cwd=root), baseline)
