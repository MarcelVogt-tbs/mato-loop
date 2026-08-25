import asyncio
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pydantic import ValidationError

from matoloop_agent.config import AgentConfig
from matoloop_agent.connection import MatoLoopAgent
from matoloop_agent.filesystem import AgentError, Filesystem
from matoloop_agent.git import GIT_OPERATIONS, GitRunner
from matoloop_agent.protocol import Request, validate_arguments

AGENT_ID = "2ea504cc-e3c6-4e0d-aa05-080328100fe8"
REQUEST_ID = "bbc46aa8-698d-4554-92c2-1cd5c6cc33a6"
GIT = Path(shutil.which("git") or "/usr/bin/git")


def configuration(root: Path, *, git: bool = True, allow_network: bool = False) -> AgentConfig:
    values = {
        "backendUrl": "ws://localhost/api/execution-agents/connect",
        "agentId": AGENT_ID,
        "homeDirectory": root,
        "allowInsecure": True,
        "allowNetwork": allow_network,
    }
    if git:
        values["gitExecutable"] = GIT
    config = AgentConfig.model_validate(values)
    config._data_directory = root
    return config


def git(*args: str, cwd: Path) -> str:
    return subprocess.run(
        [GIT, *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


class GitTests(unittest.IsolatedAsyncioTestCase):
    async def test_local_clone_status_diff_log_add_and_commit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            source.mkdir()
            git("init", "--initial-branch=main", cwd=source)
            git("config", "user.name", "MatoLoop Test", cwd=source)
            git("config", "user.email", "test@example.invalid", cwd=source)
            (source / "file.txt").write_text("first\n", encoding="utf-8")
            git("add", "file.txt", cwd=source)
            git("commit", "-m", "initial", cwd=source)

            config = configuration(root)
            runner = GitRunner(config, Filesystem(config))
            cloned = await runner.clone(str(root), "source", "repo", False)
            self.assertFalse(cloned["truncated"])

            repository = root / "repo"
            git("config", "user.name", "MatoLoop Test", cwd=repository)
            git("config", "user.email", "test@example.invalid", cwd=repository)
            status = await runner.status(str(repository))
            self.assertRegex(status["indexTree"], r"^[0-9a-f]{64}$")

            (repository / "file.txt").write_text("first\nsecond\n", encoding="utf-8")
            unstaged = await runner.diff(str(repository), False, ["file.txt"])
            self.assertIn("+second", unstaged["output"])
            added = await runner.add(str(repository), ["file.txt"])
            staged = await runner.diff(str(repository), True, [])
            self.assertIn("+second", staged["output"])

            with self.assertRaisesRegex(AgentError, "Git index changed") as changed:
                await runner.commit(str(repository), "second", "0" * 64)
            self.assertEqual(changed.exception.code, "git.indexChanged")
            committed = await runner.commit(str(repository), "second", added["indexTree"])
            self.assertRegex(committed["commit"], r"^[0-9a-f]{40}$")
            self.assertRegex(committed["tree"], r"^[0-9a-f]{40}$")
            history = await runner.log(str(repository), 20, ["file.txt"])
            self.assertIn("second", history["output"])

            marker = root / "filter-ran"
            git("config", "filter.unsafe.clean", f"touch {marker}", cwd=repository)
            (repository / ".gitattributes").write_text("* filter=unsafe\n", encoding="utf-8")
            with self.assertRaises(AgentError) as unsafe:
                await runner.add(str(repository), [".gitattributes"])
            self.assertEqual(unsafe.exception.code, "git.unsafeRepository")
            self.assertFalse(marker.exists())

            git("config", "--unset", "filter.unsafe.clean", cwd=repository)
            git("config", "extensions.worktreeConfig", "true", cwd=repository)
            git("config", "--worktree", "filter.unsafe.clean", f"touch {marker}", cwd=repository)
            with self.assertRaises(AgentError) as unsafe_worktree:
                await runner.add(str(repository), ["file.txt"])
            self.assertEqual(unsafe_worktree.exception.code, "git.unsafeRepository")
            self.assertFalse(marker.exists())

    async def test_network_denial_and_path_boundaries_precede_git(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            root = base / "root"
            outside = base / "outside"
            root.mkdir()
            outside.mkdir()
            config = configuration(root)
            runner = GitRunner(config, Filesystem(config))

            with (
                patch.object(runner, "_https_source") as resolve,
                patch.object(asyncio, "create_subprocess_exec") as start,
                self.assertRaises(AgentError) as denied,
            ):
                await runner.clone(str(root), "https://example.com/repo.git", "repo", True)
            self.assertEqual(denied.exception.code, "git.networkDenied")
            resolve.assert_not_called()
            start.assert_not_called()

            (root / "source-link").symlink_to(outside, target_is_directory=True)
            with self.assertRaises(AgentError) as invalid_source:
                await runner.clone(str(root), "source-link", "repo", False)
            self.assertEqual(invalid_source.exception.code, "git.invalidSource")

            repository = root / "repository"
            repository.mkdir()
            (repository / ".git").symlink_to(outside, target_is_directory=True)
            with self.assertRaises(AgentError) as invalid_repository:
                await runner.status(str(repository))
            self.assertEqual(invalid_repository.exception.code, "git.notRepository")

    def test_strict_schemas_and_capability_omission(self) -> None:
        base = {
            "type": "request",
            "id": REQUEST_ID,
            "operation": "integration.git.clone",
            "arguments": {
                "currentDirectory": "/workspace",
                "source": "source",
                "destination": "repo",
                "allowNetwork": False,
            },
        }
        request = Request.model_validate(base)
        self.assertFalse(validate_arguments(request).allow_network)
        request.arguments["unexpected"] = True
        with self.assertRaises(ValidationError):
            validate_arguments(request)

        add = Request.model_validate(
            {
                **base,
                "operation": "integration.git.add",
                "arguments": {
                    "currentDirectory": "/workspace",
                    "paths": [],
                },
            }
        )
        with self.assertRaises(ValidationError):
            validate_arguments(add)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            disabled = MatoLoopAgent(configuration(root, git=False), "key")
            enabled = MatoLoopAgent(configuration(root), "key")
            self.assertTrue(GIT_OPERATIONS.isdisjoint(disabled.operations))
            self.assertTrue(GIT_OPERATIONS.issubset(enabled.operations))


if __name__ == "__main__":
    unittest.main()
