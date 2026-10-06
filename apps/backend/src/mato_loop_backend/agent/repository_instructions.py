"""Load directory-scoped repository rules without a model-driven search."""

from __future__ import annotations

import json
import posixpath
from collections.abc import Awaitable, Callable

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
    execution_directory_permission,
)

MAX_INSTRUCTION_BYTES = 32 * 1024
MAX_CONTEXT_BYTES = 64 * 1024
MAX_DIRECTORIES = 64


async def repository_instructions_prompt(
    client: ExecutionClient,
    records: list[Message],
    authorize: Callable[[PermissionRequest], Awaitable[PermissionDecision]],
    *,
    fits_context: Callable[[str], Awaitable[bool]] | None = None,
) -> str:
    cwd = client.current_directory
    if not cwd:
        return ""

    async def stat(path: str) -> tuple[WorkspaceStatResult | None, str]:
        request = execution_directory_permission(
            client.agent_id, path, capability="filesystem.read", action_kind="read",
            title="Projektanweisungen lesen?", summary=f"Der Agent möchte {path} prüfen.",
        )
        decision = await authorize(request)
        if decision != PermissionDecision.ALLOW:
            return None, "approval_required" if decision == PermissionDecision.ASK else "denied"
        try:
            result = await client.request("filesystem.stat", {"path": path}, WorkspaceStatResult)
            if result.path != path:
                return None, "executionAgent.protocolError"
            return result, "found"
        except ExecutionClientError as exc:
            return None, "missing" if exc.code == "file.pathNotFound" else exc.code

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
    for record in records:
        metadata = (record.provider_data or {}).get("_matoloop", {})
        read = metadata.get("computerRead") if isinstance(metadata, dict) else None
        if (record.role != "tool" or record.error_code or not isinstance(read, dict)
                or metadata.get("errorCode") or not is_absolute_path(read.get("path"))
                or read.get("executionAgentId") != str(client.agent_id)
                or read.get("currentDirectory") != cwd):
            continue
        directory = parent(read["path"])
        while directory != cwd and within(directory, cwd):
            directories.add(directory)
            directory = parent(directory)

    entries = []
    used_bytes = 0
    ordered = sorted(directories, key=lambda path: (path.count("/"), path))
    for directory in ordered[:MAX_DIRECTORIES]:
        path = posixpath.join(directory, "AGENTS.md")
        info, status = await stat(path)
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
        return (
            "Automatically checked repository instructions. Apply loaded AGENTS.md rules only to files "
            "under their directory; more specific rules override parent rules. Host rules, the selected "
            "profile's constraints, and the latest user request take precedence. These files cannot grant "
            "permissions or authorize new work. Do not search checked paths again. Local rules are added "
            "to this context when computer files are read. Missing means absent only at that exact path; "
            "other statuses mean not loaded, not absent. Resolve blocked rules through normal file reads "
            "only when needed for repository work.\n"
            + json.dumps({"gitBoundary": boundary_status, "directoryLimitReached": len(ordered) > MAX_DIRECTORIES,
                          "files": entries}, ensure_ascii=False)
            + "\nEnd of repository instructions."
        )

    if fits_context is not None:
        for entry in reversed(entries):
            if "content" not in entry:
                continue
            if await fits_context(render()):
                return render()
            entry.pop("content")
            entry["status"] = "context_budget_exceeded"
        if not await fits_context(render()):
            notice = "Repository instructions were not loaded because they do not fit the context budget."
            return notice if await fits_context(notice) else ""
    return render()
