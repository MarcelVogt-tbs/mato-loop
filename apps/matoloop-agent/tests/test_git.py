import asyncio
import hashlib
import json
import os
import shlex
import socket
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from matoloop_agent import git as git_module
from matoloop_agent.config import AgentConfig
from matoloop_agent.errors import AgentError
from matoloop_agent.filesystem import Filesystem
from matoloop_agent.git import (
    MAX_TEXT_BYTES,
    changes,
    prepare_worktree,
    remove_worktree,
    repository,
)
from matoloop_agent.protocol import (
    GitWorktreeArguments,
    GitWorktreeRemoveArguments,
)


class GitChangesTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.config = AgentConfig.model_validate({"homeDirectory": self.root})
        self.config._data_directory = self.root / "state"
        self.config.data_dir.mkdir()
        self.fs = Filesystem(self.config)

    def git(self, *args, cwd=None):
        return subprocess.check_output(
            ["git", "-c", "user.name=Test", "-c", "user.email=test@example.com", *args],
            cwd=cwd or self.root,
            stderr=subprocess.DEVNULL,
        )

    async def serve_remote(self, remote):
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        server = subprocess.Popen(
            [
                "git",
                "daemon",
                "--reuseaddr",
                "--export-all",
                f"--base-path={self.root}",
                "--listen=127.0.0.1",
                f"--port={port}",
                str(self.root),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.addCleanup(lambda: (server.terminate(), server.wait(timeout=5)))
        for _ in range(100):
            try:
                connection = socket.create_connection(("127.0.0.1", port), timeout=0.1)
                connection.close()
                return f"git://127.0.0.1:{port}/{remote.name}"
            except OSError:
                await asyncio.sleep(0.02)
        self.fail("Git test server did not start")

    def arguments(self, source, remote="origin"):
        project, session = str(uuid4()), str(uuid4())
        return GitWorktreeArguments(
            path=str(source),
            commonDirectory=str(source / ".git"),
            ownerId=str(uuid4()),
            projectId=project,
            sessionId=session,
            destinations=[
                (self.config.data_dir / "worktrees" / session[:8]).as_posix(),
                (self.config.data_dir / "worktrees" / project / session).as_posix(),
            ],
            remote=remote,
            targetBranch="develop",
            remoteIdentity=hashlib.sha256(
                self.git("remote", "get-url", "--all", remote, cwd=source)
                + b"\0"
                + self.git("remote", "get-url", "--push", "--all", remote, cwd=source)
            ).hexdigest()
            if remote
            else None,
        )

    async def managed_source(self):
        source = self.root / "source"
        self.git("init", "-b", "develop", str(source))
        (source / "file.txt").write_text("old\n")
        (source / ".gitattributes").write_text("file.txt filter=probe\n")
        self.git("add", ".", cwd=source)
        self.git("commit", "-m", "Initial", cwd=source)
        remote = self.root / "remote.git"
        self.git("clone", "--bare", str(source), str(remote))
        self.git("remote", "add", "origin", await self.serve_remote(remote), cwd=source)
        return source, self.arguments(source)

    async def test_plain_directory_and_unborn_repository(self):
        self.assertIsNone((await changes(self.fs, str(self.root)))["repository"])
        self.git("init")
        (self.root / "new.txt").write_text("new\n")
        self.assertEqual(
            (await changes(self.fs, str(self.root)))["files"],
            [{"path": "new.txt", "status": "added", "additions": 1, "deletions": 0}],
        )

    async def test_repository_created_later_is_detected(self):
        await repository(self.fs, str(self.root))
        self.git("init", "-b", "develop")
        self.git("remote", "add", "origin", "https://example.com/project.git")
        result = await repository(self.fs, str(self.root))
        self.assertEqual(
            (result["repository"], result["defaultRemote"], result["defaultBranch"]),
            (str(self.root), "origin", "develop"),
        )

    async def test_repository_does_not_expose_credentials_in_remote_addresses(self):
        self.git("init")
        self.git("remote", "add", "origin", "https://user:secret@example.com/repo.git")
        result = await repository(self.fs, str(self.root))
        self.assertIsNone(result["remoteUrls"]["origin"])

    async def test_repository_lists_local_branches_without_tags_or_remote_refs(self):
        self.git("init", "-b", "develop")
        self.git("commit", "--allow-empty", "-m", "Initial")
        self.git("branch", "feature/release")
        self.git("tag", "feature/release")
        self.git("update-ref", "refs/remotes/origin/remote-only", "HEAD")
        result = await repository(self.fs, str(self.root))
        self.assertEqual(result["localBranches"], ["develop", "feature/release"])

    async def test_repository_truncates_branch_choices_without_failing_inspection(self):
        self.git("init", "-b", "develop")
        self.git("commit", "--allow-empty", "-m", "Initial")
        commit = self.git("rev-parse", "HEAD").decode().strip()
        subprocess.run(
            ["git", "update-ref", "--stdin"],
            cwd=self.root,
            input="".join(f"create refs/heads/branch-{index} {commit}\n" for index in range(1000)),
            text=True,
            check=True,
            capture_output=True,
        )
        result = await repository(self.fs, str(self.root))
        self.assertEqual(
            (len(result["localBranches"]), result["localBranchesTruncated"]), (1000, True)
        )

    async def test_repository_identity_includes_additional_push_destinations(self):
        self.git("init")
        self.git("remote", "add", "origin", "https://example.com/project.git")
        self.git("config", "remote.origin.pushurl", "https://example.com/project.git")
        before = (await repository(self.fs, str(self.root)))["remoteIdentities"]["origin"]
        self.git(
            "config", "--add", "remote.origin.pushurl", "https://other.example.com/project.git"
        )
        self.assertNotEqual(
            (await repository(self.fs, str(self.root)))["remoteIdentities"]["origin"], before
        )

    async def test_repository_target_uses_the_branch_name_when_a_tag_has_the_same_name(self):
        self.git("init", "-b", "release")
        self.git("commit", "--allow-empty", "-m", "Initial")
        self.git("tag", "release")
        self.assertEqual((await repository(self.fs, str(self.root)))["defaultBranch"], "release")

    async def test_repository_reports_resolved_git_metadata_for_policy_checks(self):
        source = self.root / "source"
        self.git("init", str(source))
        metadata = self.root / "metadata"
        (source / ".git").rename(metadata)
        (source / ".git").symlink_to(metadata, target_is_directory=True)
        result = await repository(self.fs, str(source))
        self.assertEqual(result["commonDirectory"], str(metadata))

    async def test_managed_worktree_fetches_remote_and_reuses_existing_work(self):
        source = self.root / "source"
        self.git("init", "-b", "develop", str(source))
        (source / "file.txt").write_text("old\n")
        self.git("add", ".", cwd=source)
        self.git("commit", "-m", "Initial", cwd=source)
        remote = self.root / "remote.git"
        self.git("clone", "--bare", str(source), str(remote))
        self.git("remote", "add", "origin", str(remote), cwd=source)
        other = self.root / "other"
        self.git("clone", str(remote), str(other))
        (other / "file.txt").write_text("server\n")
        self.git("add", ".", cwd=other)
        self.git("commit", "-m", "Server change", cwd=other)
        self.git("push", "origin", "develop", cwd=other)
        (source / "file.txt").write_text("local work\n")
        self.git("remote", "set-url", "origin", await self.serve_remote(remote), cwd=source)
        args = self.arguments(source)
        result = await prepare_worktree(self.config, self.fs, args)
        self.assertEqual(result["branch"], f"worktree-{args.session_id[:8]}")
        worktree = Path(result["path"])
        self.assertEqual(
            ((worktree / "file.txt").read_text(), (source / "file.txt").read_text()),
            ("server\n", "local work\n"),
        )
        (worktree / "file.txt").write_text("session work\n")
        await prepare_worktree(self.config, self.fs, args)
        self.assertEqual((worktree / "file.txt").read_text(), "session work\n")

    async def test_worktree_rejects_an_existing_branch_with_the_same_prefix(self):
        source, args = await self.managed_source()
        self.git("branch", f"worktree-{args.session_id[:8]}", cwd=source)
        with self.assertRaises(AgentError) as error:
            await prepare_worktree(self.config, self.fs, args)
        self.assertEqual(error.exception.code, "git.worktreeChanged")

    async def test_worktree_rejects_an_existing_directory_with_the_same_prefix(self):
        source, args = await self.managed_source()
        (self.config.data_dir / "worktrees" / args.session_id[:8]).mkdir(parents=True)
        with self.assertRaises(AgentError) as error:
            await prepare_worktree(self.config, self.fs, args)
        self.assertEqual(error.exception.code, "git.worktreeChanged")

    async def test_legacy_worktree_branch_is_recovered_and_removable(self):
        source, args = await self.managed_source()
        result = await prepare_worktree(self.config, self.fs, args)
        legacy_path = self.config.data_dir / "worktrees" / args.project_id / args.session_id
        legacy_path.parent.mkdir(parents=True)
        self.git("worktree", "move", result["path"], str(legacy_path), cwd=source)
        result["path"] = str(legacy_path)
        (Path(result["gitDirectory"]) / "matoloop-session").unlink()
        branch = f"matoloop/{args.owner_id}/{args.session_id}"
        self.git("branch", "-m", branch, cwd=result["path"])
        record = self.config.data_dir / "worktree-state" / f"{args.owner_id}-{args.session_id}.json"
        state = json.loads(record.read_text())
        state["branch"] = branch
        state["path"] = result["path"]
        record.write_text(json.dumps(state))
        recovered = await prepare_worktree(
            self.config,
            self.fs,
            args.model_copy(update={"destinations": None}),
        )
        await remove_worktree(
            self.config,
            self.fs,
            GitWorktreeRemoveArguments(
                ownerId=args.owner_id,
                projectId=args.project_id,
                sessionId=args.session_id,
                path=result["path"],
                commonDirectory=str(source / ".git"),
            ),
        )
        self.assertEqual(recovered["branch"], branch)

    async def test_preparation_requires_an_authorized_destination(self):
        source, args = await self.managed_source()
        for destinations in (None, [str(self.root / "outside")]):
            with self.subTest(destinations=destinations):
                with self.assertRaises(AgentError) as error:
                    await prepare_worktree(
                        self.config,
                        self.fs,
                        args.model_copy(update={"destinations": destinations}),
                    )
                self.assertEqual(error.exception.code, "git.worktreeChanged")

    async def test_worktree_without_remote_uses_the_committed_local_target(self):
        source = self.root / "source"
        self.git("init", "-b", "develop", str(source))
        (source / "file.txt").write_text("local target\n")
        self.git("add", ".", cwd=source)
        self.git("commit", "-m", "Local target", cwd=source)
        self.git("checkout", "-b", "other", cwd=source)
        (source / "file.txt").write_text("other branch\n")
        self.git("commit", "-am", "Other branch", cwd=source)
        (source / "file.txt").write_text("uncommitted work\n")
        result = await prepare_worktree(self.config, self.fs, self.arguments(source, remote=None))
        self.assertEqual(
            (
                (Path(result["path"]) / "file.txt").read_text(),
                (source / "file.txt").read_text(),
                result["remote"],
                result["remoteIdentity"],
            ),
            ("local target\n", "uncommitted work\n", None, None),
        )

    @unittest.skipUnless(os.geteuid() == 0, "Requires ownership changes")
    async def test_worktree_checkout_uses_process_ownership_with_foreign_owned_agent_data(self):
        source, _ = await self.managed_source()
        os.chown(self.config.data_dir, 1000, 1000)
        result = await prepare_worktree(self.config, self.fs, self.arguments(source, remote=None))
        self.assertEqual((Path(result["path"]) / "file.txt").read_text(), "old\n")

    async def test_local_worktree_requires_a_committed_target(self):
        source = self.root / "source"
        self.git("init", "-b", "develop", str(source))
        with self.assertRaises(AgentError) as error:
            await prepare_worktree(self.config, self.fs, self.arguments(source, remote=None))
        self.assertEqual(error.exception.code, "git.localBranchRequired")
        self.assertFalse(
            (await repository(self.fs, str(source), target_branch="develop"))["targetHasCommit"]
        )

    async def test_worktree_removal_discards_local_changes_and_preserves_the_branch(self):
        source, args = await self.managed_source()
        result = await prepare_worktree(self.config, self.fs, args)
        path = Path(result["path"])
        self.assertEqual(path, self.config.data_dir / "worktrees" / args.session_id[:8])
        remove = GitWorktreeRemoveArguments(
            ownerId=args.owner_id,
            projectId=args.project_id,
            sessionId=args.session_id,
            path=str(path),
            commonDirectory=str(source / ".git"),
        )
        self.git("config", "core.excludesFile", str(self.root / "ignore"), cwd=source)
        (self.root / "ignore").write_text("local.config\n")
        (path / "local.config").write_text("ignored settings")
        (path / "file.txt").write_text("uncommitted changes")
        (path / "untracked.txt").write_text("untracked content")
        await remove_worktree(self.config, self.fs, remove)
        await remove_worktree(self.config, self.fs, remove)
        self.assertTrue(
            not path.exists()
            and bool(self.git("rev-parse", f"refs/heads/{result['branch']}", cwd=source))
        )
        self.git("branch", "-D", result["branch"], cwd=source)
        replacement = args.model_copy(update={"session_id": args.session_id[:8] + str(uuid4())[8:]})
        await prepare_worktree(self.config, self.fs, replacement)
        (path / "untracked.txt").write_text("another session's work")
        with self.assertRaises(AgentError):
            await remove_worktree(self.config, self.fs, remove)

    async def test_failed_remote_fetch_does_not_create_a_stale_worktree(self):
        source = self.root / "source"
        self.git("init", "-b", "develop", str(source))
        self.git("remote", "add", "origin", "git://127.0.0.1:1/missing.git", cwd=source)
        args = self.arguments(source)
        with self.assertRaises(AgentError) as error:
            await prepare_worktree(self.config, self.fs, args)
        self.assertEqual(error.exception.code, "git.fetchFailed")

    async def test_preparation_does_not_execute_hooks_or_checkout_filters(self):
        source, args = await self.managed_source()
        marker = self.root / "executed"
        script = shlex.join(
            [sys.executable, "-c", f"from pathlib import Path; Path({str(marker)!r}).touch()"]
        )
        hook = source / ".git" / "hooks" / "reference-transaction"
        hook.write_text(f"#!/bin/sh\n{script}\n")
        hook.chmod(0o700)
        filters = self.root / "branch-filters"
        for option in ("smudge", "process"):
            self.git("config", "--file", str(filters), f"filter.probe.{option}", script)
        self.git("config", "--file", str(filters), "filter.probe.required", "true")
        self.git("config", "includeIf.onbranch:worktree-*.path", str(filters), cwd=source)
        await prepare_worktree(self.config, self.fs, args)
        self.assertFalse(marker.exists())

    async def test_preparation_finishes_when_filesystem_requests_fill_the_shared_executor(self):
        source, args = await self.managed_source()
        loop = asyncio.get_running_loop()
        loop.set_default_executor(ThreadPoolExecutor(max_workers=1))
        entered = asyncio.Event()
        blocked = None
        original = git_module._git

        def stat():
            loop.call_soon_threadsafe(entered.set)
            return self.fs.stat(str(source))

        async def checkout_then_stat(directory, *arguments, **kwargs):
            nonlocal blocked
            result = await original(directory, *arguments, **kwargs)
            if arguments[:2] == ("worktree", "add"):
                blocked = asyncio.create_task(asyncio.to_thread(stat))
                await entered.wait()
            return result

        try:
            with patch.object(git_module, "_git", checkout_then_stat):
                result = await asyncio.wait_for(prepare_worktree(self.config, self.fs, args), 10)
            self.assertEqual((Path(result["path"]) / "file.txt").read_text(), "old\n")
        finally:
            if blocked:
                await blocked

    async def test_preparation_rejects_linked_destination_before_checkout(self):
        source, args = await self.managed_source()
        destination = self.config.data_dir / "worktrees" / args.session_id[:8]
        destination.parent.mkdir(parents=True)
        outside = self.root / "outside"
        outside.mkdir()
        destination.symlink_to(outside, target_is_directory=True)
        with self.assertRaises(AgentError):
            await prepare_worktree(self.config, self.fs, args)
        self.assertEqual(list(outside.iterdir()), [])

    async def test_preparation_rejects_redirected_objects(self):
        source, args = await self.managed_source()
        objects = source / ".git" / "objects"
        outside = self.root / "outside-objects"
        objects.rename(outside)
        objects.symlink_to(outside, target_is_directory=True)
        with self.assertRaises(AgentError) as error:
            await prepare_worktree(self.config, self.fs, args)
        self.assertEqual(error.exception.code, "git.worktreeFailed")

    async def test_preparation_rejects_local_remote_transport(self):
        source, _ = await self.managed_source()
        self.git("remote", "set-url", "origin", str(self.root / "remote.git"), cwd=source)
        with self.assertRaises(AgentError) as error:
            await prepare_worktree(self.config, self.fs, self.arguments(source))
        self.assertEqual(error.exception.code, "git.localRemoteUnsupported")

    async def test_recovery_rejects_changed_preparation_inputs(self):
        source, args = await self.managed_source()
        await prepare_worktree(self.config, self.fs, args)
        with self.assertRaises(AgentError) as error:
            await prepare_worktree(
                self.config, self.fs, args.model_copy(update={"target_branch": "other"})
            )
        self.assertEqual(error.exception.code, "git.worktreeChanged")

    async def test_recovery_reads_completed_records_with_long_unicode_repository_paths(self):
        source, _ = await self.managed_source()
        destination = self.root.joinpath(*["ä" * 100] * 10)
        destination.parent.mkdir(parents=True)
        source.rename(destination)
        args = self.arguments(destination)
        result = await prepare_worktree(self.config, self.fs, args)
        self.assertEqual(
            (await prepare_worktree(self.config, self.fs, args))["path"], result["path"]
        )

    async def test_recovery_rejects_replaced_repository(self):
        source, args = await self.managed_source()
        result = await prepare_worktree(self.config, self.fs, args)
        destination = Path(result["path"])
        destination.rename(self.root / "saved-work")
        self.git("init", "-b", result["branch"], str(destination))
        with self.assertRaises(AgentError) as error:
            await prepare_worktree(self.config, self.fs, args)
        self.assertEqual(error.exception.code, "git.worktreeChanged")

    async def test_recovery_does_not_adopt_incomplete_checkout(self):
        source, args = await self.managed_source()
        result = await prepare_worktree(self.config, self.fs, args)
        state_path = (
            self.config.data_dir / "worktree-state" / f"{args.owner_id}-{args.session_id}.json"
        )
        state = json.loads(state_path.read_text())
        state["ready"] = False
        state_path.write_text(json.dumps(state))
        with self.assertRaises(AgentError):
            await prepare_worktree(self.config, self.fs, args)
        self.assertEqual((Path(result["path"]) / "file.txt").read_text(), "old\n")

    async def test_retry_rejects_a_branch_created_after_failed_checkout(self):
        source, args = await self.managed_source()
        original = git_module._git

        async def fail_checkout(directory, *arguments, **kwargs):
            if arguments[:2] == ("worktree", "add"):
                return 1, b"", b"checkout failed"
            return await original(directory, *arguments, **kwargs)

        with patch.object(git_module, "_git", fail_checkout), self.assertRaises(AgentError):
            await prepare_worktree(self.config, self.fs, args)
        self.git("branch", f"worktree-{args.session_id[:8]}", cwd=source)
        with self.assertRaises(AgentError) as error:
            await prepare_worktree(self.config, self.fs, args)
        self.assertEqual(error.exception.code, "git.worktreeChanged")

    async def test_git_timeout_terminates_children_and_finishes_cleanup(self):
        self.git("init")
        pid_file = self.root / "child.pid"
        script = (
            "import subprocess,sys,time; from pathlib import Path; "
            "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); "
            f"Path({str(pid_file)!r}).write_text(str(child.pid)); time.sleep(60)"
        )
        command = shlex.join([sys.executable, "-c", script])
        with self.assertRaises(AgentError):
            await asyncio.wait_for(
                git_module._git(
                    str(self.root), "-c", f"alias.probe=!{command}", "probe", timeout_seconds=0.4
                ),
                5,
            )
        status = Path(f"/proc/{pid_file.read_text()}/stat")
        self.assertTrue(not status.exists() or status.read_text().split()[2] == "Z")

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
        # Ignore sudo ownership exemptions and runner-wide safe.directory entries.
        with (
            patch.dict(os.environ, {"SUDO_UID": "0"}),
            patch.object(
                git_module, "_git", partial(git_module._git, config={"safe.directory": ""})
            ),
            self.assertRaises(AgentError) as error,
        ):
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
        before = "a" * (256 * 1024)
        after = "b" * (256 * 1024)
        (self.root / "boundary.txt").write_text(before)
        self.git("add", "boundary.txt")
        self.git("commit", "-m", "Initial")
        (self.root / "boundary.txt").write_text(after)
        result = await changes(self.fs, str(self.root), "boundary.txt")
        self.assertEqual(
            (result["reason"], result["before"], result["after"]), (None, before, after)
        )
        (self.root / "large.txt").write_bytes(b"a" * (MAX_TEXT_BYTES + 1))
        (self.root / "escaped.txt").write_bytes(b"\x01" * MAX_TEXT_BYTES)
        (self.root / "binary").write_bytes(b"a\0b")
        (self.root / "link").symlink_to("/etc/passwd")
        for file, reason in [
            ("large.txt", "tooLarge"),
            ("escaped.txt", "tooLarge"),
            ("binary", "binary"),
            ("link", "unsupported"),
        ]:
            with self.subTest(file=file):
                result = await changes(self.fs, str(self.root), file)
                self.assertEqual(
                    (result["reason"], result["before"], result["after"]), (reason, "", "")
                )
