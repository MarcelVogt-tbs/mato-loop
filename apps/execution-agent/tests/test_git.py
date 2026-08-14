import asyncio
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pydantic import ValidationError

from mato_loop_execution_agent.config import AgentConfig
from mato_loop_execution_agent.connection import ExecutionAgent
from mato_loop_execution_agent.git import GIT_OPERATIONS, GitRunner
from mato_loop_execution_agent.protocol import Request, validate_arguments
from mato_loop_execution_agent.workspace import AgentError, Workspace

AGENT_ID = "2ea504cc-e3c6-4e0d-aa05-080328100fe8"
ROOT_ID = "4d0a316f-5fb5-4ec5-a5c4-04aed66c5e6d"
REQUEST_ID = "bbc46aa8-698d-4554-92c2-1cd5c6cc33a6"
GIT = Path(shutil.which("git") or "/usr/bin/git")


def configuration(root: Path, *, git: bool = True, allow_network: bool = False) -> AgentConfig:
    values = {
        "backendUrl": "ws://localhost/api/execution-agents/connect",
        "agentId": AGENT_ID,
        "name": "Agent",
        "roots": [{"id": ROOT_ID, "label": "Root", "path": root}],
        "dataDir": root / "data",
        "allowInsecure": True,
        "allowNetwork": allow_network,
    }
    if git:
        values["gitExecutable"] = GIT
    return AgentConfig.model_validate(values)


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

            workspace = Workspace(configuration(root))
            runner = GitRunner(configuration(root), workspace)
            revision = workspace.roots[ROOT_ID].revision
            cloned = await runner.clone(ROOT_ID, revision, str(root), "source", "repo", False)
            self.assertFalse(cloned["truncated"])

            repository = root / "repo"
            git("config", "user.name", "MatoLoop Test", cwd=repository)
            git("config", "user.email", "test@example.invalid", cwd=repository)
            status = await runner.status(ROOT_ID, revision, str(repository))
            self.assertRegex(status["indexTree"], r"^[0-9a-f]{64}$")

            (repository / "file.txt").write_text("first\nsecond\n", encoding="utf-8")
            unstaged = await runner.diff(ROOT_ID, revision, str(repository), False, ["file.txt"])
            self.assertIn("+second", unstaged["output"])
            added = await runner.add(ROOT_ID, revision, str(repository), ["file.txt"])
            staged = await runner.diff(ROOT_ID, revision, str(repository), True, [])
            self.assertIn("+second", staged["output"])

            with self.assertRaisesRegex(AgentError, "Git index changed") as changed:
                await runner.commit(ROOT_ID, revision, str(repository), "second", "0" * 64)
            self.assertEqual(changed.exception.code, "git.indexChanged")
            committed = await runner.commit(
                ROOT_ID, revision, str(repository), "second", added["indexTree"]
            )
            self.assertRegex(committed["commit"], r"^[0-9a-f]{40}$")
            self.assertRegex(committed["tree"], r"^[0-9a-f]{40}$")
            history = await runner.log(ROOT_ID, revision, str(repository), 20, ["file.txt"])
            self.assertIn("second", history["output"])

            marker = root / "filter-ran"
            git("config", "filter.unsafe.clean", f"touch {marker}", cwd=repository)
            (repository / ".gitattributes").write_text("* filter=unsafe\n", encoding="utf-8")
            with self.assertRaises(AgentError) as unsafe:
                await runner.add(ROOT_ID, revision, str(repository), [".gitattributes"])
            self.assertEqual(unsafe.exception.code, "git.unsafeRepository")
            self.assertFalse(marker.exists())

            git("config", "--unset", "filter.unsafe.clean", cwd=repository)
            git("config", "extensions.worktreeConfig", "true", cwd=repository)
            git("config", "--worktree", "filter.unsafe.clean", f"touch {marker}", cwd=repository)
            with self.assertRaises(AgentError) as unsafe_worktree:
                await runner.add(ROOT_ID, revision, str(repository), ["file.txt"])
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
            workspace = Workspace(config)
            runner = GitRunner(config, workspace)
            revision = workspace.roots[ROOT_ID].revision

            with (
                patch.object(runner, "_https_source") as resolve,
                patch.object(asyncio, "create_subprocess_exec") as start,
                self.assertRaises(AgentError) as denied,
            ):
                await runner.clone(
                    ROOT_ID, revision, str(root), "https://example.com/repo.git", "repo", True
                )
            self.assertEqual(denied.exception.code, "git.networkDenied")
            resolve.assert_not_called()
            start.assert_not_called()

            with self.assertRaises(AgentError) as invalid_source:
                await runner.clone(ROOT_ID, revision, str(root), str(outside), "repo", False)
            self.assertEqual(invalid_source.exception.code, "git.invalidSource")

            repository = root / "repository"
            repository.mkdir()
            (repository / ".git").symlink_to(outside, target_is_directory=True)
            with self.assertRaises(AgentError) as invalid_repository:
                await runner.status(ROOT_ID, revision, str(repository))
            self.assertEqual(invalid_repository.exception.code, "git.notRepository")

    def test_strict_schemas_and_capability_omission(self) -> None:
        base = {
            "type": "request",
            "id": REQUEST_ID,
            "operation": "integration.git.clone",
            "arguments": {
                "rootId": ROOT_ID,
                "rootRevision": "a" * 64,
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
                    "rootId": ROOT_ID,
                    "rootRevision": "a" * 64,
                    "currentDirectory": "/workspace",
                    "paths": [],
                },
            }
        )
        with self.assertRaises(ValidationError):
            validate_arguments(add)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            disabled = ExecutionAgent(configuration(root, git=False), "key")
            enabled = ExecutionAgent(configuration(root), "key")
            self.assertTrue(GIT_OPERATIONS.isdisjoint(disabled.operations))
            self.assertTrue(GIT_OPERATIONS.issubset(enabled.operations))


if __name__ == "__main__":
    unittest.main()
