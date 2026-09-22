import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from matoloop_agent.config import AgentConfig
from matoloop_agent.connection import MatoLoopAgent
from matoloop_agent.filesystem import AgentError
from matoloop_agent.protocol import Request
from matoloop_agent.search import MAX_MATCH_TEXT, MAX_RESULTS

AGENT_ID = "2ea504cc-e3c6-4e0d-aa05-080328100fe8"
REQUEST_ID = "bbc46aa8-698d-4554-92c2-1cd5c6cc33a6"


def configuration(root: Path) -> AgentConfig:
    config = AgentConfig.model_validate(
        {
            "backendUrl": "ws://localhost/api/execution-agents/connect",
            "agentId": AGENT_ID,
            "homeDirectory": root,
            "allowInsecure": True,
        }
    )
    config._data_directory = root
    return config


@unittest.skipUnless(shutil.which("rg"), "ripgrep is required")
class SearchTests(unittest.IsolatedAsyncioTestCase):
    async def test_explicit_files_and_empty_glob(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            agent = MatoLoopAgent(configuration(root), "secret")
            self.assertEqual(
                await agent.search.glob(str(root), "*.missing"), {"paths": [], "truncated": False}
            )
            for name in ("notes.txt", ".hidden.txt", "-option.txt"):
                with self.subTest(name=name):
                    target = root / name
                    target.write_text("other\nneedle\n")
                    (root / ".gitignore").write_text(name + "\n")
                    (root / "sibling.txt").write_text("needle\n")
                    result = await agent.dispatch(
                        Request.model_validate(
                            {
                                "type": "request",
                                "id": REQUEST_ID,
                                "operation": "filesystem.grep",
                                "arguments": {
                                    "path": str(target),
                                    "pattern": "needle",
                                    "include": "*.py",
                                },
                            }
                        )
                    )
                    self.assertEqual(
                        result,
                        {
                            "matches": [{"path": str(target), "line": 2, "text": "needle"}],
                            "truncated": False,
                        },
                    )
                    self.assertEqual(
                        await agent.search.grep(str(target), "absent", None),
                        {"matches": [], "truncated": False},
                    )
                    with self.assertRaises(AgentError):
                        await agent.search.grep(str(target), "[", None)
                    for count in (100, 101):
                        target.write_text("needle\n" * count)
                        result = await agent.search.grep(str(target), "needle", None)
                        self.assertEqual(len(result["matches"]), 100)
                        self.assertEqual(result["truncated"], count == 101)
                    target.write_bytes(b"needle\n\0needle\n")
                    self.assertEqual(
                        await agent.search.grep(str(target), "needle", None),
                        {"matches": [], "truncated": False},
                    )
            (root / ".git").mkdir()
            target = root / ".git/config"
            target.write_text("needle")
            self.assertEqual((await agent.search.grep(str(target), "needle", None))["matches"], [])
            with patch.object(agent.search, "_run", return_value=(2, False)):
                with self.assertRaises(AgentError):
                    await agent.search.glob(str(root), "*.txt")

    async def test_explicit_file_replacement_cannot_widen_access(self) -> None:
        for replacement in ("symlink", "directory", "fifo"):
            with self.subTest(replacement=replacement), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                target = root / "target.txt"
                outside = root / "outside.txt"
                outside.write_text("secret")
                target.write_text("safe")
                agent = MatoLoopAgent(configuration(root), "secret")
                original_open = agent.filesystem._open_regular

                def replace_then_open(
                    parent_fd,
                    name,
                    target=target,
                    outside=outside,
                    replacement=replacement,
                    original_open=original_open,
                ):
                    target.unlink()
                    if replacement == "symlink":
                        target.symlink_to(outside)
                    elif replacement == "directory":
                        target.mkdir()
                        (target / "secret.txt").write_text("secret")
                    else:
                        os.mkfifo(target)
                    return original_open(parent_fd, name)

                with patch.object(agent.filesystem, "_open_regular", side_effect=replace_then_open):
                    with self.assertRaises(AgentError):
                        await agent.search.grep(str(target), "secret", None)

    async def test_dispatches_glob_and_grep_without_hidden_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "src").mkdir()
            (root / "src" / "app.py").write_text("needle\n")
            (root / "src" / "app.txt").write_text("needle\n")
            (root / ".hidden.py").write_text("hidden needle\n")
            (root / ".ssh").mkdir()
            (root / ".ssh" / "secret.py").write_text("private needle\n")
            (root / "ignored.py").write_text("ignored needle\n")
            (root / "binary.py").write_bytes(b"\0binary needle\n")
            (root / ".gitignore").write_text("ignored.py\n")
            (root / ".git").mkdir()
            (root / ".git" / "secret.py").write_text("git needle\n")
            agent = MatoLoopAgent(configuration(root), "secret")
            self.assertIn("filesystem.glob", agent.operations)
            self.assertIn("filesystem.grep", agent.operations)

            glob = await agent.dispatch(
                Request.model_validate(
                    {
                        "type": "request",
                        "id": REQUEST_ID,
                        "operation": "filesystem.glob",
                        "arguments": {
                            "path": str(root),
                            "pattern": "*.py",
                        },
                    }
                )
            )
            self.assertEqual(
                glob,
                {
                    "paths": [str(root / "binary.py"), str(root / "src/app.py")],
                    "truncated": False,
                },
            )

            grep = await agent.dispatch(
                Request.model_validate(
                    {
                        "type": "request",
                        "id": "15cd7b96-411a-42a5-9247-768890e99118",
                        "operation": "filesystem.grep",
                        "arguments": {
                            "path": str(root),
                            "pattern": "needle",
                            "include": "*.py",
                        },
                    }
                )
            )
            self.assertEqual(
                [(match["path"], match["line"]) for match in grep["matches"]],
                [(str(root / "src/app.py"), 1)],
            )
            self.assertFalse(grep["truncated"])

    async def test_rejects_invalid_regex_and_truncates_match_text(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "long.txt").write_text("x" * (MAX_MATCH_TEXT + 10) + "\nx\n")
            agent = MatoLoopAgent(configuration(root), "secret")
            long = await agent.search.grep(str(root), "x+", "long.txt")
            self.assertEqual(len(long["matches"][0]["text"]), MAX_MATCH_TEXT)
            self.assertEqual([match["line"] for match in long["matches"]], [1, 2])
            self.assertTrue(long["truncated"])
            with self.assertRaisesRegex(AgentError, "Invalid grep pattern"):
                await agent.search.grep(str(root), "[", None)

    async def test_result_limit_requires_an_extra_eligible_result(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            agent = MatoLoopAgent(configuration(root), "secret")
            (root / ".hidden.txt").write_text("match\n")
            (root / "ignored.txt").write_text("match\n")
            (root / ".gitignore").write_text("ignored.txt\n")
            for index in range(MAX_RESULTS):
                (root / f"{index:03}.txt").write_text("match\n")
            for extra in (False, True):
                with self.subTest(extra=extra):
                    if extra:
                        (root / "extra.txt").write_text("match\n")
                    glob = await agent.search.glob(str(root), "*.txt")
                    grep = await agent.search.grep(str(root), "match", "*.txt")
                    self.assertEqual(len(glob["paths"]), MAX_RESULTS)
                    self.assertEqual(len(grep["matches"]), MAX_RESULTS)
                    self.assertEqual(glob["truncated"], extra)
                    self.assertEqual(grep["truncated"], extra)

    async def test_match_text_limit_excludes_line_endings(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "exact.txt").write_bytes(b"x" * MAX_MATCH_TEXT + b"\r\n")
            agent = MatoLoopAgent(configuration(root), "secret")
            result = await agent.search.grep(str(root), "x+", None)
            self.assertEqual(result["matches"][0]["text"], "x" * MAX_MATCH_TEXT)
            self.assertFalse(result["truncated"])

    async def test_path_limits_search_to_subtree_and_prefixes_results(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "src").mkdir()
            (root / "src" / "inside.py").write_text("needle\n")
            (root / "outside.py").write_text("needle\n")
            agent = MatoLoopAgent(configuration(root), "secret")
            glob = await agent.search.glob(str(root / "src"), "*.py")
            grep = await agent.search.grep(str(root / "src"), "needle", "*.py")

            self.assertEqual(glob["paths"], [str(root / "src/inside.py")])
            self.assertEqual(
                [match["path"] for match in grep["matches"]], [str(root / "src/inside.py")]
            )

    async def test_does_not_traverse_symlink_directories(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            root = base / "root"
            outside = base / "outside"
            root.mkdir()
            outside.mkdir()
            (root / "inside.txt").write_text("public marker\n")
            (outside / "secret.txt").write_text("private marker\n")
            (root / "escape").symlink_to(outside, target_is_directory=True)
            agent = MatoLoopAgent(configuration(root), "secret")
            glob = await agent.search.glob(str(root), "*.txt")
            grep = await agent.search.grep(str(root), "marker", None)
            self.assertEqual(glob["paths"], [str(root / "inside.txt")])
            self.assertEqual(
                [match["path"] for match in grep["matches"]], [str(root / "inside.txt")]
            )


class SearchCapabilityTests(unittest.TestCase):
    def test_does_not_advertise_search_without_ripgrep(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch(
                "matoloop_agent.search.FilesystemSearch._find_ripgrep",
                return_value=None,
            ):
                agent = MatoLoopAgent(configuration(root), "secret")
            self.assertNotIn("filesystem.glob", agent.operations)
            self.assertNotIn("filesystem.grep", agent.operations)


if __name__ == "__main__":
    unittest.main()
