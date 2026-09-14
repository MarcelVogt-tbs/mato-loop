from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterator
from dataclasses import dataclass, field, replace
from enum import StrEnum
from pathlib import PurePosixPath
from typing import BinaryIO
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.api.errors import ApiError
from mato_loop_backend.execution import ExecutionAgentError, execution_agent_connections
from mato_loop_backend.models import (
    ChatSession,
    ExecutionAgent,
    PermissionGrant,
    UserAccount,
)
from mato_loop_backend.sessions.service import SessionServiceError, get_owned_session
from mato_loop_backend.settings import InstanceLimits, SettingsStore
from mato_loop_backend.workspace import (
    FileRoot,
    RootFileEntry,
    RootFileError,
    RootFileService,
)

logger = logging.getLogger(__name__)


class FileScope(StrEnum):
    FILESYSTEM = "filesystem"
    SESSION = "session"


@dataclass(slots=True)
class _FileContext:
    root_files: RootFileService | None = None
    root: FileRoot | None = None
    current_directory: str | None = None
    agent_id: UUID | None = None
    agent_writable: bool = True
    agent_operations: frozenset[str] = frozenset()
    instance_limits: InstanceLimits = field(default_factory=InstanceLimits)


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_AGENT_ERROR_CODES = {
    "file.changed": (409, "file.changed"),
    "file.invalidEncoding": (400, "file.invalidEncoding"),
    "file.tooLarge": (413, "file.tooLarge"),
    "operation.tooLarge": (413, "file.operationTooLarge"),
    "path.exists": (409, "file.pathAlreadyExists"),
    "path.accessDenied": (403, "file.accessDenied"),
    "path.invalid": (400, "file.invalidPath"),
    "path.invalidDestination": (400, "file.invalidDestination"),
    "path.notDirectory": (404, "file.directoryNotFound"),
    "path.notEmpty": (409, "file.directoryNotEmpty"),
    "path.notFile": (400, "file.notAFile"),
    "path.notFound": (404, "file.pathNotFound"),
    "path.unavailable": (500, "file.unavailable"),
    "path.rootMutation": (400, "file.rootMutationForbidden"),
    "path.unsupportedType": (400, "file.unsupportedType"),
    "root.stale": (503, "executionAgent.unavailable"),
    "root.unavailable": (503, "executionAgent.unavailable"),
    "root.unknown": (503, "executionAgent.unavailable"),
}
_MANAGER_ERROR_CODES = {
    "executionAgent.operationUnavailable",
    "executionAgent.outcomeUnknown",
    "executionAgent.protocolError",
    "executionAgent.replaced",
    "executionAgent.requestTooLarge",
    "executionAgent.responseTooLarge",
    "executionAgent.revoked",
    "executionAgent.timeout",
    "executionAgent.unavailable",
}


def _normalize_agent_path(path: str) -> str:
    if not isinstance(path, str) or not path or "\\" in path:
        raise ApiError(400, "file.invalidPath")
    if any(ord(character) < 32 or 127 <= ord(character) <= 159 for character in path):
        raise ApiError(400, "file.invalidPath")
    parsed = PurePosixPath(path)
    if (
        not parsed.is_absolute()
        or parsed.as_posix() != path
        or (
            path != "/"
            and any(part in {"", ".", ".."} for part in path.split("/")[1:])
        )
    ):
        raise ApiError(400, "file.invalidPath")
    return parsed.as_posix()


async def _agent_context(
    db: AsyncSession,
    user: UserAccount,
    agent_id: UUID,
    *,
    current_directory: str | None = None,
    writable: bool = True,
) -> _FileContext:
    owned = await db.scalar(
        select(ExecutionAgent).where(
            ExecutionAgent.id == agent_id,
            ExecutionAgent.user_id == user.id,
            ExecutionAgent.revoked_at.is_(None),
        )
    )
    if owned is None:
        raise ApiError(404, "executionAgent.notFound")
    snapshot = execution_agent_connections.snapshot(agent_id)
    if snapshot is None:
        raise ApiError(503, "executionAgent.unavailable")
    return _FileContext(
        root=FileRoot.WORKSPACE,
        current_directory=current_directory,
        agent_id=agent_id,
        agent_writable=writable,
        agent_operations=snapshot,
    )


async def _file_context(
    scope: FileScope,
    session_id: UUID | None,
    user: UserAccount,
    db: AsyncSession,
    *,
    current_directory: str | None = None,
    root_identity: str | None = None,
    execution_agent_id: UUID | None = None,
    for_update: bool = False,
    instance_limits: InstanceLimits | None = None,
) -> _FileContext:
    instance_limits = instance_limits or await SettingsStore.load(db, InstanceLimits)
    session = None
    if session_id is not None:
        if for_update:
            try:
                candidate = await get_owned_session(db, session_id, user.id, for_update=True)
            except SessionServiceError as exc:
                raise ApiError(exc.status_code, exc.code) from exc
        else:
            candidate = await db.get(ChatSession, session_id)
        if candidate is None or candidate.user_id != user.id:
            raise ApiError(404, "session.notFound")
        session = candidate
    if scope is FileScope.SESSION:
        if session is None:
            raise ApiError(400, "file.sessionRequired")
        root_session = session
        if root_identity is not None and root_identity != str(session.id):
            try:
                root_session_id = UUID(root_identity)
            except (TypeError, ValueError) as exc:
                raise ApiError(404, "file.fileNotFound") from exc
            if str(root_session_id) != root_identity or session.project_id is None:
                raise ApiError(404, "file.fileNotFound")
            statement = select(ChatSession).where(
                ChatSession.id == root_session_id,
                ChatSession.user_id == user.id,
                ChatSession.project_id == session.project_id,
                ChatSession.session_type == "chat",
            )
            if for_update:
                statement = statement.with_for_update()
            root_session = await db.scalar(statement)
            if root_session is None:
                raise ApiError(404, "file.fileNotFound")
        return _FileContext(
            root_files=RootFileService(
                root_session.id,
                project_id=root_session.project_id,
                session_quota_bytes=instance_limits.session_quota_bytes,
                max_editable_text_bytes=instance_limits.max_editable_text_bytes,
            ),
            root=FileRoot.SESSION,
            instance_limits=instance_limits,
        )
    if root_identity is not None:
        raise ApiError(400, "file.invalidRoot")
    if execution_agent_id is not None:
        return replace(
            await _agent_context(
                db,
                user,
                execution_agent_id,
                current_directory=current_directory,
            ),
            instance_limits=instance_limits,
        )
    if session is not None:
        selected_agent_id = session.preferred_execution_agent_id
        if selected_agent_id is None:
            raise ApiError(400, "file.workspaceNotSelected")
        return replace(
            await _agent_context(
                db,
                user,
                selected_agent_id,
                current_directory=current_directory or session.current_directory,
            ),
            instance_limits=instance_limits,
        )
    raise ApiError(400, "file.workspaceNotSelected")


def _root_entry_payload(entry: RootFileEntry) -> dict:
    return {
        "name": entry.name,
        "path": entry.path,
        "kind": entry.kind,
        "size": entry.size,
        "modifiedAt": entry.modified_at,
        "hasChildren": entry.kind == "directory",
    }


def _context_error(exc: RootFileError) -> ApiError:
    if exc.code in {"file.changed", "file.pathAlreadyExists"}:
        return ApiError(409, exc.code)
    if exc.code in {
        "file.directoryNotFound",
        "file.fileNotFound",
        "file.pathNotFound",
        "file.revisionNotFound",
    }:
        return ApiError(404, exc.code)
    if exc.code in {
        "file.operationTooLarge",
        "file.tooLarge",
    }:
        return ApiError(413, exc.code)
    if exc.code == "file.sessionQuotaExceeded":
        return ApiError(507, exc.code)
    return ApiError(400, exc.code)


def _join_path(parent: str, name: str) -> str:
    return name if parent == "." else f"{parent.rstrip('/')}/{name}"


def _is_same_or_descendant(path: str, parent: str) -> bool:
    path_parts = PurePosixPath(path).parts
    parent_parts = PurePosixPath(parent).parts
    return path_parts[: len(parent_parts)] == parent_parts


async def _clear_file_grants(
    db: AsyncSession,
    user: UserAccount,
    context: _FileContext,
    affected_paths: tuple[str, ...],
) -> None:
    root_name = context.root.value if context.root is not None else None
    root_identity = None
    if context.root_files is not None and context.root is not None:
        root_identity = context.root_files.root_identity(context.root)
    grants = list(
        (
            await db.scalars(
                select(PermissionGrant)
                .where(
                    PermissionGrant.resource_type.in_(("execution_directory", "root_path"))
                )
                .join(ChatSession, ChatSession.id == PermissionGrant.session_id)
                .where(ChatSession.user_id == user.id)
            )
        ).all()
    )
    for grant in grants:
        remove = False
        if grant.resource_type == "execution_directory":
            if context.agent_id is None:
                continue
            try:
                scope = json.loads(grant.resource_scope)
                grant_path = scope["path"]
                remove = (
                    scope["agentId"] == str(context.agent_id)
                    and isinstance(grant_path, str)
                    and any(
                        _is_same_or_descendant(grant_path, path)
                        for path in affected_paths
                    )
                )
            except (KeyError, TypeError, json.JSONDecodeError):
                remove = True
        elif grant.resource_type == "root_path":
            try:
                scope = json.loads(grant.resource_scope)
                grant_path = scope["path"]
                remove = (
                    scope["root"] == root_name
                    and scope["identity"] == root_identity
                    and isinstance(grant_path, str)
                    and any(
                        _is_same_or_descendant(grant_path, path)
                        for path in affected_paths
                    )
                )
            except (KeyError, TypeError, json.JSONDecodeError):
                remove = True
        if remove:
            await db.delete(grant)


async def _clear_context_grants(
    db: AsyncSession,
    user: UserAccount,
    context: _FileContext,
    paths: tuple[str, ...],
) -> None:
    await _clear_file_grants(db, user, context, paths)


def _require_agent_operations(context: _FileContext, *operations: str) -> None:
    if context.agent_id is not None and not set(operations) <= context.agent_operations:
        raise ApiError(503, "executionAgent.operationUnavailable")


def _list_context(
    context: _FileContext, path: str, offset: int
) -> tuple[list[dict], bool, int | None]:
    assert context.root_files is not None and context.root is not None
    result = context.root_files.list_entries(context.root, path, offset=offset, limit=1_000)
    return (
        [_root_entry_payload(entry) for entry in result.entries],
        result.has_more,
        result.next_offset,
    )


def _read_context(context: _FileContext, path: str, limit: int) -> bytes:
    assert context.root_files is not None and context.root is not None
    return context.root_files.read_bytes(context.root, path, limit=limit)


def _replace_text_context(
    context: _FileContext, path: str, content: str, expected_revision: str
) -> str:
    assert context.root_files is not None and context.root is not None
    return context.root_files.replace_text_file(
        context.root, path, content, expected_revision
    )


def _open_context(context: _FileContext, path: str) -> BinaryIO:
    assert context.root_files is not None and context.root is not None
    return context.root_files.open_file(context.root, path)


def _stream_file(handle: BinaryIO) -> Iterator[bytes]:
    with handle:
        while chunk := handle.read(1024 * 1024):
            yield chunk


def _execution_error(code: str, *, manager: bool = False) -> ApiError:
    if code in _AGENT_ERROR_CODES:
        status_code, safe_code = _AGENT_ERROR_CODES[code]
        return ApiError(status_code, safe_code)
    if manager and code in _MANAGER_ERROR_CODES:
        return ApiError(503, code)
    return ApiError(502, "executionAgent.protocolError")


async def _agent_request(
    context: _FileContext, operation: str, arguments: dict, *, timeout: float | None = None
) -> dict:
    if (
        context.agent_id is None
    ):
        raise ApiError(500, "internal.unexpected")
    if not context.agent_writable and operation in {
        "filesystem.copy",
        "filesystem.delete",
        "filesystem.import",
        "filesystem.mkdir",
        "filesystem.move",
        "filesystem.rename",
        "filesystem.write",
    }:
        raise ApiError(403, "http.forbidden")
    try:
        request_kwargs = {"timeout": timeout} if timeout is not None else {}
        response = await execution_agent_connections.request(
            context.agent_id, operation, arguments, **request_kwargs
        )
    except ExecutionAgentError as exc:
        raise _execution_error(exc.code, manager=True) from exc
    if not response.ok:
        error_code = response.error.code if response.error else ""
        logger.warning("MatoLoop Agent operation %s failed: %s", operation, error_code)
        raise _execution_error(error_code)
    if not isinstance(response.result, dict):
        raise ApiError(502, "executionAgent.protocolError")
    return response.result


def _agent_entry(value: object, *, expected_path: str | None = None) -> dict:
    if not isinstance(value, dict):
        raise ApiError(502, "executionAgent.protocolError")
    try:
        path = _normalize_agent_path(value["path"])
    except (ApiError, KeyError, TypeError) as exc:
        raise ApiError(502, "executionAgent.protocolError") from exc
    if expected_path is not None and path != expected_path:
        raise ApiError(502, "executionAgent.protocolError")
    kind = value.get("kind")
    if kind not in {"directory", "file", "symlink", "other"}:
        raise ApiError(502, "executionAgent.protocolError")
    size = value.get("size")
    modified_at = value.get("modifiedAt")
    if size is not None and (not isinstance(size, int) or isinstance(size, bool) or size < 0):
        raise ApiError(502, "executionAgent.protocolError")
    if modified_at is not None and not isinstance(modified_at, int | float):
        raise ApiError(502, "executionAgent.protocolError")
    return {
        "name": PurePosixPath(path).name if path != "." else ".",
        "path": path,
        "kind": kind,
        "size": size,
        "modifiedAt": modified_at,
        "hasChildren": kind == "directory",
    }


async def _agent_stat(context: _FileContext, path: str) -> dict:
    normalized = _normalize_agent_path(path)
    return _agent_entry(
        await _agent_request(context, "filesystem.stat", {"path": normalized}),
        expected_path=normalized,
    )
