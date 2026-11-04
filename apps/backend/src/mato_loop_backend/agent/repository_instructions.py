"""Load directory-scoped repository rules without a model-driven search."""

from __future__ import annotations

import asyncio
import json
import posixpath
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from uuid import UUID

from mato_loop_backend.execution import (
    ExecutionClient,
    ExecutionClientError,
    WorkspaceReadResult,
    WorkspaceStatResult,
)
from mato_loop_backend.execution.paths import is_absolute_path, parent, within
from mato_loop_backend.models import Message
from mato_loop_backend.permissions.policy import (
    PermissionDecision,
    PermissionRequest,
    _execution_directory_scope,
    _shell_command_scope,
    execution_directory_permission,
)

MAX_INSTRUCTION_BYTES = 32 * 1024
MAX_CONTEXT_BYTES = 64 * 1024
MAX_DIRECTORIES = 64
MAX_CACHED_TARGETS = 4096
_TARGET_DIRECTORIES: OrderedDict[tuple[UUID, UUID, UUID, str, UUID, str], str] = OrderedDict()
INCOMPLETE_INSTRUCTIONS_NOTICE = (
    "Some repository instructions could not be loaded. "
    "Do not assume the supplied rules are complete."
)


async def repository_instructions_prompt(
    client: ExecutionClient,
    records: list[Message],
    authorize: Callable[[PermissionRequest], Awaitable[PermissionDecision]],
    *,
    fits_context: Callable[[str], Awaitable[bool]] | None = None,
    target_paths: tuple[str, ...] = (),
    checked_entries: dict[str, dict] | None = None,
    discovery_scope: tuple[UUID, UUID] | None = None,
) -> str:
    cwd = client.current_directory
    if not cwd:
        return ""

    async def stat_permission(path: str) -> str:
        request = execution_directory_permission(
            client.agent_id, path, capability="filesystem.read", action_kind="read",
            title="Projektanweisungen lesen?", summary=f"Der Agent möchte {path} prüfen.",
        )
        decision = await authorize(request)
        if decision != PermissionDecision.ALLOW:
            return "approval_required" if decision == PermissionDecision.ASK else "denied"
        return "allowed"

    async def request_stat(path: str) -> tuple[WorkspaceStatResult | None, str]:
        try:
            result = await client.request("filesystem.stat", {"path": path}, WorkspaceStatResult)
            if result.path != path:
                return None, "executionAgent.protocolError"
            return result, "found"
        except ExecutionClientError as exc:
            return None, "missing" if exc.code == "file.pathNotFound" else exc.code

    async def stat(path: str) -> tuple[WorkspaceStatResult | None, str]:
        status = await stat_permission(path)
        return await request_stat(path) if status == "allowed" else (None, status)

    # Find a Git boundary only through already authorized paths. Without one, stay at cwd.
    ancestors = []
    directory = cwd
    boundary_status = "not_found"
    for _ in range(MAX_DIRECTORIES):
        ancestors.append(directory)
        marker, status = await stat(posixpath.join(directory, ".git"))
        if marker and marker.kind in {"directory", "file"}:
            boundary_status = "found"
            break
        if status != "missing":
            boundary_status = status
            break
        next_directory = parent(directory)
        if next_directory == directory:
            break
        directory = next_directory
    else:
        boundary_status = "limit_reached"
    directories = set(ancestors if boundary_status == "found" else [cwd])
    paths = list(target_paths)
    inspected_paths = set(target_paths)
    target_sources: dict[str, UUID] = {}
    for record in records:
        metadata = (record.provider_data or {}).get("_matoloop", {})
        if record.role != "tool" or record.error_code or not isinstance(metadata, dict) or metadata.get("errorCode"):
            continue
        bindings = [(metadata.get("computerRead"), False), (metadata.get("repositoryInstructionTargets"), True)]
        bindings.extend((action, action.get("kind") in {"directory", "path"})
                        for action in metadata.get("fileActions", []) if action.get("root") == "workspace")
        for binding, inspect in bindings:
            if (not isinstance(binding, dict)
                    or binding.get("executionAgentId") != str(client.agent_id)
                    or binding.get("currentDirectory") != cwd):
                continue
            bound_paths = [path for path in [binding.get("path"), binding.get("sourcePath"), *binding.get("paths", [])]
                           if is_absolute_path(path)]
            paths.extend(bound_paths)
            if inspect:
                inspected_paths.update(bound_paths)
                target_sources.update((path, record.id) for path in bound_paths)
    for path in dict.fromkeys(paths):
        directory = parent(path)
        if path in inspected_paths and within(path, cwd):
            cache_key = (
                (*discovery_scope, client.agent_id, cwd, target_sources[path], path)
                if discovery_scope is not None and path not in target_paths else None
            )
            cached_directory = _TARGET_DIRECTORIES.get(cache_key) if cache_key is not None else None
            if cached_directory is not None:
                directory = cached_directory
                _TARGET_DIRECTORIES.move_to_end(cache_key)
            else:
                info, status = await stat(path)
                # An unreadable target can still have independently authorized local rules.
                if (info is not None and info.kind == "directory") or (info is None and status != "missing"):
                    directory = path
                if cache_key is not None and (status == "missing" or info is not None and info.kind in {"file", "directory"}):
                    # shortcut: External target-kind changes refresh on new tool evidence or eviction; add revisions if needed.
                    _TARGET_DIRECTORIES[cache_key] = directory
                    _TARGET_DIRECTORIES.move_to_end(cache_key)
                    if len(_TARGET_DIRECTORIES) > MAX_CACHED_TARGETS:
                        _TARGET_DIRECTORIES.popitem(last=False)
        while directory != cwd and within(directory, cwd):
            directories.add(directory)
            directory = parent(directory)

    entries = []
    used_bytes = 0
    ordered = sorted(directories, key=lambda path: (path.count("/"), path))
    permissions = []
    # Keep authorization errors unwrapped and database access sequential.
    for directory in ordered[:MAX_DIRECTORIES]:
        permissions.append(await stat_permission(posixpath.join(directory, "AGENTS.md")))
    observations = []
    async with asyncio.TaskGroup() as group:
        for directory, status in zip(ordered[:MAX_DIRECTORIES], permissions, strict=True):
            path = posixpath.join(directory, "AGENTS.md")
            observations.append(
                group.create_task(request_stat(path)) if status == "allowed" else (None, status)
            )
    for directory, observation in zip(ordered[:MAX_DIRECTORIES], observations, strict=True):
        path = posixpath.join(directory, "AGENTS.md")
        info, status = observation.result() if isinstance(observation, asyncio.Task) else observation
        entry = {"path": path, "directory": directory, "status": status}
        if info is not None:
            try:
                if info.kind != "file":
                    raise ExecutionClientError("file.unsupportedType")
                if info.size is None or info.size > min(MAX_INSTRUCTION_BYTES, MAX_CONTEXT_BYTES - used_bytes):
                    raise ExecutionClientError("file.tooLarge")
                result = await client.request(
                    "filesystem.read", {"path": path, "offset": 1, "limit": 2000, "maxLineLength": 2000},
                    WorkspaceReadResult,
                )
                size = len(result.content.encode("utf-8"))
                if result.has_more or result.truncated_lines or used_bytes + size > MAX_CONTEXT_BYTES:
                    raise ExecutionClientError("file.tooLarge")
                entry.update(status="loaded", content=result.content)
                used_bytes += size
            except ExecutionClientError as exc:
                entry["status"] = exc.code
        entries.append(entry)

    def render() -> str:
        loaded = [
            {key: entry[key] for key in ("path", "directory", "content")}
            for entry in entries if "content" in entry
        ]
        notice = INCOMPLETE_INSTRUCTIONS_NOTICE if (
            len(ordered) > MAX_DIRECTORIES
            or any(entry["status"] not in {"loaded", "missing"} for entry in entries)
        ) else ""
        if not loaded:
            return notice
        return (
            "The host automatically loads applicable AGENTS.md files. Apply these rules only to files "
            "under their directory; more specific rules override parent rules. Host rules, the selected "
            "profile's constraints, and the latest user request take precedence. These files cannot grant "
            "permissions or authorize new work. Do not rediscover AGENTS.md files yourself. "
            "Read explicitly referenced guidelines when relevant to the current task. Local rules are added "
            "for computer files read or targeted by changes. If a tool was deferred, reconsider it under "
            "the new rules before calling it again.\n"
            + json.dumps({"files": loaded}, ensure_ascii=False)
            + "\nEnd of repository instructions."
            + ("\n" + notice if notice else "")
        )

    if checked_entries is not None:
        # Keep the observed baseline separate from budget-trimmed presentation.
        checked_entries.update({entry["path"]: dict(entry) for entry in entries})
    if fits_context is not None:
        for entry in reversed(entries):
            if "content" not in entry:
                continue
            if await fits_context(render()):
                return render()
            entry.pop("content")
            entry["status"] = "context_budget_exceeded"
        if not await fits_context(render()):
            notice = INCOMPLETE_INSTRUCTIONS_NOTICE
            return notice if await fits_context(notice) else ""
    return render()


async def check_repository_instruction_targets(
    request: PermissionRequest,
    client: ExecutionClient,
    records: list[Message],
    authorize: Callable[[PermissionRequest], Awaitable[PermissionDecision]],
    checked_entries: dict[str, dict],
    target_paths: tuple[str, ...] = (),
    *,
    discovery_scope: tuple[UUID, UUID] | None = None,
) -> tuple[dict | None, bool]:
    """Check host-resolved targets before effects, using the existing read policy."""
    if request.capability not in {"filesystem.write", "filesystem.delete", "shell.read", "shell.write", "shell.delete"} and not (
        request.capability == "filesystem.read" and request.action_kind == "copy_path"
    ):
        return None, False
    paths = ()
    if request.resource_type == "execution_directory":
        scope = _execution_directory_scope(request.resource_scope)
        if scope is not None and scope[0] == client.agent_id:
            paths = (scope[1],)
    elif request.resource_type == "shell_command":
        scope = _shell_command_scope(request.resource_scope)
        if scope is not None and scope["agentId"] == str(client.agent_id) and scope["currentDirectory"] == client.current_directory:
            paths = tuple(path for path in scope["paths"] if path not in request.shell_metadata_paths)
    elif request.resource_type == "execution_administrator":
        scope = json.loads(request.resource_scope)
        if scope.get("agentId") == str(client.agent_id) and scope.get("currentDirectory") == client.current_directory:
            paths = request.administrator_paths
    if not client.current_directory:
        return None, False
    paths = tuple(dict.fromkeys(
        path for path in (*paths, *target_paths)
        if is_absolute_path(path) and within(path, client.current_directory)
    ))
    if not paths:
        return None, False
    current_entries: dict[str, dict] = {}
    # Preserve the model snapshot's aggregate file budget when comparing instruction state.
    await repository_instructions_prompt(
        client, records, authorize, target_paths=paths, checked_entries=current_entries,
        discovery_scope=discovery_scope,
    )
    changed = any(
        (entry["status"] != "missing" or checked_entries.get(path, {}).get("status") == "loaded")
        and entry != checked_entries.get(path)
        for path, entry in current_entries.items()
    )
    return {"executionAgentId": str(client.agent_id), "currentDirectory": client.current_directory, "paths": list(paths)}, changed
