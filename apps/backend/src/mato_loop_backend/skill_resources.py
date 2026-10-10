"""Materialize run-owned skill files through session storage and Agent transfers."""

from __future__ import annotations

import asyncio
import hashlib
import io
from pathlib import PurePosixPath
from uuid import UUID, uuid4

from mato_loop_backend.execution.client import ExecutionClient, ExecutionResult, WorkspacePathResult
from mato_loop_backend.execution.manager import execution_agent_connections
from mato_loop_backend.execution.transfers import transfer_tickets
from mato_loop_backend.files.service import FileRoot, RootFileError, RootFileService
from mato_loop_backend.models.agent import AgentRun
from mato_loop_backend.skill_packages import (
    MAX_FILE_BYTES,
    MAX_PACKAGE_BYTES,
    MAX_PACKAGE_DIRECTORIES,
    MAX_PACKAGE_FILES,
    SkillError,
    SkillFile,
    SkillPackage,
)


def materialize(files: RootFileService, package: SkillPackage, prefix: str) -> str:
    """Publish editable session files through the normal quota and revision machinery."""
    created: set[str] = set()
    for path, entry in package.files.items():
        target = PurePosixPath(prefix, path)
        for parent in reversed(target.parents):
            name = parent.as_posix()
            if name == "." or name in created:
                continue
            # Reusing a parent does not mutate its run-owned descendants.
            try:
                exists = files.path_kind(FileRoot.SESSION, name) == "directory"
            except RootFileError as exc:
                if exc.code != "file.pathNotFound":
                    raise
                exists = False
            if not exists:
                try:
                    files.create_directory(FileRoot.SESSION, name)
                except RootFileError as exc:
                    if exc.code != "file.pathAlreadyExists" or files.path_kind(FileRoot.SESSION, name) != "directory":
                        raise
            created.add(name)
        files.create_file(FileRoot.SESSION, target.as_posix(), io.BytesIO(entry.content), max_bytes=MAX_FILE_BYTES)
    return prefix


def read_draft(files: RootFileService, prefix: str, executable_paths: list[str]) -> SkillPackage:
    entries: dict[str, SkillFile] = {}
    total = 0
    directories = 0
    pending = [prefix]
    while pending:
        directory = pending.pop()
        directories += 1
        if directories > MAX_PACKAGE_DIRECTORIES:
            raise SkillError("file.tooLarge")
        offset = 0
        while True:
            page = files.list_entries(FileRoot.SESSION, directory, offset=offset, limit=MAX_PACKAGE_FILES + 1)
            for entry in page.entries:
                path = entry.path[len(prefix) + 1:]
                if not entry.path.startswith(prefix + "/"):
                    raise SkillError("file.invalidPath")
                if entry.kind == "directory":
                    pending.append(entry.path)
                    if len(pending) + len(entries) > MAX_PACKAGE_FILES:
                        raise SkillError("file.tooLarge")
                elif entry.kind == "file":
                    content = files.read_bytes(FileRoot.SESSION, entry.path, limit=min(MAX_FILE_BYTES, MAX_PACKAGE_BYTES - total))
                    total += len(content)
                    if len(entries) >= MAX_PACKAGE_FILES or total > MAX_PACKAGE_BYTES:
                        raise SkillError("file.tooLarge")
                    entries[path] = SkillFile(content, path in executable_paths)
                else:
                    raise SkillError("file.unsupportedType")
            if not page.has_more:
                break
            offset = page.next_offset
    return SkillPackage(entries)


async def prepare_computer(client: ExecutionClient, run: AgentRun, package: SkillPackage) -> str:
    archive = await asyncio.to_thread(package.archive)
    digest = hashlib.sha256(archive).hexdigest()
    grant, _ = transfer_tickets.create_download(client.agent_id, io.BytesIO(archive), size=len(archive), sha256=digest)
    # Keep every selected computer until terminal cleanup, including computers switched away from.
    clients = getattr(run, "_skill_clients", None)
    if clients is None:
        clients = run._skill_clients = {}
    clients[client.agent_id] = client
    try:
        result = await client.request("cache.skills.prepare", {
            "runId": str(run.id), "sessionId": str(run.session_id),
            "name": package.name, "revision": package.revision,
            "size": len(archive), "sha256": digest,
            "transfer": {"url": execution_agent_connections.transfer_url(client.agent_id, grant.ticket_id), "token": grant.token},
        }, WorkspacePathResult)
        return result.path
    finally:
        transfer_tickets.revoke(grant.ticket_id)


async def release_computers(run: AgentRun) -> None:
    async def release(client: ExecutionClient):
        try:
            await client.request("cache.skills.release", {"runId": str(run.id), "sessionId": str(run.session_id)}, ExecutionResult, timeout=10)
        except Exception:
            # Disconnect/restart drops leases; the Agent can then evict the sparse cache.
            pass

    await asyncio.gather(*(release(client) for client in getattr(run, "_skill_clients", {}).values()))


def session_package_path(run_id: UUID, package: SkillPackage) -> str:
    return f"skills/{run_id}/{uuid4()}/{package.name}"
