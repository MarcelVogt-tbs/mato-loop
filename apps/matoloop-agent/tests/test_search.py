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
    async def test_dispatches_glob_and_grep_with_ignore_and_hidden_semantics(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "src").mkdir()
            (root / "src" / "app.py").write_text("needle\n")
            (root / "src" / "app.txt").write_text("needle\n")
            (root / ".hidden.py").write_text("hidden needle\n")
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
                [(str(root / ".hidden.py"), 1), (str(root / "src/app.py"), 1)],
            )
            self.assertFalse(grep["truncated"])

    async def test_rejects_invalid_regex_and_truncates_results_and_text(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for index in range(MAX_RESULTS + 1):
                (root / f"{index:03}.txt").write_text("match\n")
            (root / "long.txt").write_text("x" * (MAX_MATCH_TEXT + 10))
            agent = MatoLoopAgent(configuration(root), "secret")
            result = await agent.search.grep(str(root), "match", None)
            self.assertEqual(len(result["matches"]), MAX_RESULTS)
            self.assertTrue(result["truncated"])
            long = await agent.search.grep(str(root), "x+", "long.txt")
            self.assertEqual(len(long["matches"][0]["text"]), MAX_MATCH_TEXT)
            with self.assertRaisesRegex(AgentError, "Invalid grep pattern"):
                await agent.search.grep(str(root), "[", None)

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
