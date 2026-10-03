from __future__ import annotations

import asyncio
import os
import posixpath
from typing import Any, BinaryIO, Literal
from uuid import UUID

from anyio import CancelScope
from sqlalchemy import or_, select

import mato_loop_backend.tools.access as tools_access
from mato_loop_backend.execution import (
    ExecutionClient,
    ExecutionClientError,
    WorkspaceDeleteResult,
    WorkspaceGlobResult,
    WorkspaceGrepResult,
    WorkspaceListResult,
    WorkspacePatchResult,
    WorkspacePathResult,
    WorkspaceReadResult,
    WorkspaceStatResult,
    WorkspaceWriteResult,
)
from mato_loop_backend.files.archives import MAX_EXPORT_BYTES
from mato_loop_backend.files.service import (
    FileRevision,
    FileRoot,
    RootFileError,
    RootFileService,
    WorkingFileRevision,
)
from mato_loop_backend.models import AgentRun, ChatSession, Message
from mato_loop_backend.permissions.policy import (
    PermissionDecision,
    PermissionPolicy,
    PermissionRequest,
    execution_directory_permission,
    path_permission,
    project_session_path_permission,
)
from mato_loop_backend.tools.arguments import SessionFileReadArguments
from mato_loop_backend.tools.context import ToolContext as ToolContext
from mato_loop_backend.tools.contracts import ToolFileAction as ToolFileAction


def delegated_session_files(context: ToolContext, *roots: str) -> bool:
    return context.session.parent_session_id is not None and "session" in roots


async def create_output_stream(
    output_files: RootFileService, filename: str, source: BinaryIO
) -> FileRevision | WorkingFileRevision:
    pending = asyncio.create_task(
        asyncio.to_thread(
            output_files.create_output_stream,
            filename,
            source,
            max_bytes=MAX_EXPORT_BYTES,
        )
    )
    try:
        return await asyncio.shield(pending)
    except asyncio.CancelledError:
        async def finish_and_discard() -> None:
            result = (await asyncio.gather(pending, return_exceptions=True))[0]
            if isinstance(result, WorkingFileRevision) and result.run_id == output_files.run_id:
                await asyncio.to_thread(output_files.discard_run_output, result.file_id)

        cleanup = asyncio.create_task(finish_and_discard())
        while not cleanup.done():
            try:
                with CancelScope(shield=True):
                    await asyncio.shield(cleanup)
            except asyncio.CancelledError:
                continue
        cleanup.result()
        raise


def execution_path(context: ToolContext, path: str) -> str:
    client = workspace_client(context)
    if not path or "\\" in path or any(
        ord(character) < 32 or 127 <= ord(character) <= 159 for character in path
    ):
        raise ExecutionClientError("file.invalidPath")
    if path.startswith("/"):
        resolved = posixpath.normpath(path)
    elif client.current_directory is not None:
        resolved = posixpath.normpath(posixpath.join(client.current_directory, path))
    else:
        raise ExecutionClientError("file.workspaceNotSelected")
    if not resolved.startswith("/"):
        raise ExecutionClientError("file.invalidPath")
    return resolved


def file_path(
    context: ToolContext, root: FileRoot | str, path: str,
    *, root_files: RootFileService | None = None,
) -> str:
    return (
        execution_path(context, path)
        if FileRoot(root) is FileRoot.WORKSPACE
        else (root_files or context.root_files).normalize_relative_path(path)
    )


async def read_bytes(
    context: ToolContext, root: FileRoot | str, path: str,
    *, limit: int, root_files: RootFileService | None = None,
) -> bytes:
    """Read an authorized file, exporting computer bytes only on demand."""
    normalized = file_path(context, root, path, root_files=root_files)
    data = (
        await workspace_client(context).read_bytes(normalized, limit=limit)
        if FileRoot(root) is FileRoot.WORKSPACE
        else await asyncio.to_thread(
            (root_files or context.root_files).read_bytes, root, normalized, limit=limit,
        )
    )
    if len(data) > limit:
        raise RootFileError("file.tooLarge", "File exceeds the read limit")
    return data


def filesystem_permission(
    context: ToolContext,
    *,
    root: FileRoot | str,
    path: str,
    capability: str,
    action_kind: str,
    action_label: str,
    root_files: RootFileService | None = None,
    target_session_id: UUID | None = None,
) -> PermissionRequest:
    parent_grant = action_kind in {"write_file", "edit_file", "apply_patch"}
    if FileRoot(root) is FileRoot.WORKSPACE:
        client = workspace_client(context)
        absolute_path = execution_path(context, path)
        return execution_directory_permission(
            client.agent_id,
            absolute_path,
            capability=capability,
            action_kind=action_kind,
            title=f"{action_label} erlauben?",
            summary=f"Der Agent möchte {absolute_path} verwenden.",
            grant_path=posixpath.dirname(absolute_path) if parent_grant else absolute_path,
        )
    selected_files = root_files or context.root_files
    if target_session_id is not None and target_session_id != context.session.id:
        assert context.session.project_id is not None
        return project_session_path_permission(
            project_id=context.session.project_id,
            session_id=target_session_id,
            relative_path=path,
            action_kind=action_kind,
            action_label=action_label,
            capability=capability,
        )
    return path_permission(
        root_files=selected_files,
        root=root,
        capability=capability,
        relative_path=path,
        action_kind=action_kind,
        action_label=action_label,
        grant_path=(posixpath.dirname(path) or "." if parent_grant else path),
    )


def workspace_client(context: ToolContext) -> ExecutionClient:
    if context.execution_client is None:
        raise ExecutionClientError("file.workspaceNotSelected")
    return context.execution_client


async def active_file_is_dirty(
    context: ToolContext, root: FileRoot | str, path: str
) -> bool:
    message = await context.db.scalar(
        select(Message)
        .join(AgentRun, AgentRun.input_message_id == Message.id)
        .where(AgentRun.id == context.run_id)
    )
    runtime_data = (
        message.provider_data.get("_matoloop")
        if message is not None and isinstance(message.provider_data, dict)
        else None
    )
    active = runtime_data.get("activeFile") if isinstance(runtime_data, dict) else None
    if not isinstance(active, dict) or active.get("dirty") is not True:
        return False
    selected_root = FileRoot(root)
    return (
        active.get("root") == selected_root.value
        and (
            active.get("executionAgentId") == str(workspace_client(context).agent_id)
            if selected_root is FileRoot.WORKSPACE
            else active.get("rootIdentity") == context.root_files.root_identity(selected_root)
        )
        and active.get("path") == path
    )


async def directory_allowed(
    context: ToolContext,
    path: str,
    *,
    ask: bool,
    changes_directory: bool = False,
) -> bool:
    request = execution_directory_permission(
        workspace_client(context).agent_id,
        path,
        action_kind=("change_directory" if changes_directory else "use_directory"),
    )
    return await tools_access.allow(context, request) if ask else await PermissionPolicy.evaluate(
        context.db, context.session, request, context.root_files
    ) == PermissionDecision.ALLOW


def require_workspace_path(actual: str, expected: str) -> None:
    if actual != expected:
        raise ExecutionClientError("executionAgent.protocolError")


async def workspace_request(
    context: ToolContext,
    operation: str,
    arguments: dict[str, Any],
    result_model: type[WorkspaceDeleteResult]
    | type[WorkspaceGlobResult]
    | type[WorkspaceGrepResult]
    | type[WorkspaceListResult]
    | type[WorkspacePatchResult]
    | type[WorkspacePathResult]
    | type[WorkspaceReadResult]
    | type[WorkspaceStatResult]
    | type[WorkspaceWriteResult],
) -> Any:
    return await workspace_client(context).request(operation, arguments, result_model)


async def project_read_files(
    arguments: SessionFileReadArguments,
    context: ToolContext,
) -> tuple[RootFileService, UUID] | None:
    target_id = arguments.session_id or context.session.id
    if arguments.root == "workspace" or target_id == context.session.id:
        return context.root_files, context.session.id
    if context.session.project_id is None:
        return None
    target = await context.db.scalar(
        select(ChatSession).where(
            ChatSession.id == target_id,
            ChatSession.user_id == context.session.user_id,
            ChatSession.project_id == context.session.project_id,
            ChatSession.session_type == "chat",
            or_(ChatSession.parent_session_id.is_(None), ChatSession.parent_session_id == context.session.id),
        )
    )
    if target is None:
        return None
    return (
        RootFileService(
            target.id,
            project_id=target.project_id,
            run_id=context.root_files.run_id,
            session_storage=context.root_files.session_storage,
            session_quota_bytes=context.root_files.SESSION_QUOTA_BYTES,
            max_editable_text_bytes=context.root_files.max_editable_text_bytes,
        ),
        target.id,
    )


def regular_file_action(
    context: ToolContext,
    operation: Literal["write", "create", "edit", "delete", "mkdir", "copy", "move"],
    root: Literal["session", "workspace"],
    path: str,
    *,
    source_path: str | None = None,
    source_root: Literal["session", "workspace"] | None = None,
) -> tuple[ToolFileAction, ...]:
    path = (
        execution_path(context, path)
        if root == "workspace"
        else context.root_files.normalize_relative_path(path)
    )
    kind = "path" if operation == "delete" else context.root_files.path_kind(root, path)
    size = None
    if kind == "file":
        with context.root_files.open_file(root, path) as handle:
            size = os.fstat(handle.fileno()).st_size
    return (
        ToolFileAction(
            operation=operation,
            root=root,
            root_identity=context.root_files.root_identity(root),
            path=path,
            filename=path.rsplit("/", 1)[-1],
            size_bytes=size,
            kind=kind,
            source_path=source_path,
            source_root=source_root,
        ),
    )

def workspace_file_action(
    context: ToolContext,
    operation: Literal["write", "create", "edit", "delete", "mkdir", "copy", "move"],
    path: str,
    size: int | None = None,
    *,
    kind: Literal["file", "directory", "path"] = "file",
    source_path: str | None = None,
    source_root: Literal["session", "workspace"] | None = None,
) -> tuple[ToolFileAction, ...]:
    client = workspace_client(context)
    return (
        ToolFileAction(
            operation=operation,
            root="workspace",
            root_identity=None,
            path=path,
            filename=path.rsplit("/", 1)[-1],
            size_bytes=size,
            execution_agent_id=str(client.agent_id),
            current_directory=client.current_directory,
            kind=kind,
            source_path=source_path,
            source_root=source_root,
        ),
    )
