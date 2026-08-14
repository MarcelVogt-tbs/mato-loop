from __future__ import annotations

import asyncio
import base64
import errno
import hashlib
import json
import re
from collections.abc import Iterator
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Annotated, BinaryIO
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, File, Header, Query, Request, UploadFile, status
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from mato_loop_backend.api.errors import ApiError
from mato_loop_backend.attachments import (
    MAX_FILE_ATTACHMENT_BYTES,
    AttachmentError,
    image_mime_type,
    validate_image,
)
from mato_loop_backend.db.session import get_db_session
from mato_loop_backend.execution import (
    MAX_TRANSFER_BYTES,
    ExecutionAgentError,
    TransferError,
    execution_agent_connections,
    stage_file,
    stream_ticket,
    transfer_tickets,
)
from mato_loop_backend.models import ChatSession, ExecutionAgent, PermissionGrant, UserAccount
from mato_loop_backend.safe_svg import MAX_SAFE_SVG_BYTES, validate_svg
from mato_loop_backend.security import hash_token, require_user
from mato_loop_backend.workspace import (
    MAX_EDITABLE_TEXT_BYTES,
    FileRoot,
    RootFileEntry,
    RootFileError,
    RootFileService,
    WorkspaceError,
    WorkspaceService,
    validate_upload_filename,
)

router = APIRouter(prefix="/workspace")
_validate_svg = validate_svg

MAX_TEXT_PREVIEW_BYTES = MAX_EDITABLE_TEXT_BYTES
_IMAGE_PREVIEW_BYTES = MAX_SAFE_SVG_BYTES
_IMAGE_SUFFIXES = {".gif", ".jpeg", ".jpg", ".png", ".webp"}
_MARKDOWN_SUFFIXES = {".md", ".markdown"}
_FILE_RESPONSE_HEADERS = {
    "Cache-Control": "private, no-store",
    "Cross-Origin-Resource-Policy": "same-origin",
    "Content-Security-Policy": "sandbox; default-src 'none'; frame-ancestors 'none'",
    "X-Content-Type-Options": "nosniff",
}
_SVG_RESPONSE_HEADERS = {
    **_FILE_RESPONSE_HEADERS,
    "Content-Security-Policy": (
        "sandbox; default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'none'"
    ),
}


class FileScope(StrEnum):
    FILESYSTEM = "filesystem"
    SESSION = "session"


class CreateDirectoryRequest(BaseModel):
    parent: str = "."
    name: str = Field(min_length=1, max_length=255)


class RenameEntryRequest(BaseModel):
    path: str = Field(min_length=1, max_length=1_200)
    name: str = Field(min_length=1, max_length=255)


class UpdateFileRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(strict=True, min_length=1, max_length=1_200)
    content: str = Field(strict=True)
    expected_revision: str = Field(strict=True, pattern=r"^[0-9a-f]{64}$")


class CopyMoveEntryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1, max_length=1_200)
    destination: str = Field(min_length=1, max_length=1_200)
    destination_scope: FileScope
    destination_session_id: UUID | None = None
    destination_execution_agent_id: UUID | None = None
    destination_root_id: UUID | None = None
    destination_working_directory: str | None = Field(default=None, max_length=1_200)


@dataclass(slots=True)
class _FileContext:
    workspace: WorkspaceService | None = None
    root_files: RootFileService | None = None
    root: FileRoot | None = None
    working_directory: str | None = None
    agent_id: UUID | None = None
    agent_root_id: UUID | None = None
    agent_root_revision: str | None = None
    agent_writable: bool = True
    agent_operations: frozenset[str] = frozenset()


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_AGENT_ERROR_CODES = {
    "file.changed": (409, "file.changed"),
    "file.invalidEncoding": (400, "file.invalidEncoding"),
    "file.tooLarge": (413, "file.tooLarge"),
    "operation.tooLarge": (413, "file.operationTooLarge"),
    "path.exists": (409, "file.pathAlreadyExists"),
    "path.invalid": (400, "file.invalidPath"),
    "path.invalidDestination": (400, "file.invalidDestination"),
    "path.notDirectory": (404, "file.directoryNotFound"),
    "path.notEmpty": (409, "file.directoryNotEmpty"),
    "path.notFile": (400, "file.notAFile"),
    "path.notFound": (404, "file.pathNotFound"),
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
    if path == ".":
        return path
    parsed = PurePosixPath(path)
    if parsed.is_absolute() or any(part in {"", ".", ".."} for part in path.split("/")):
        raise ApiError(400, "file.invalidPath")
    return parsed.as_posix()


async def _agent_context(
    db: AsyncSession,
    user: UserAccount,
    agent_id: UUID,
    root_id: UUID,
    working_directory: str | None,
    root_identity: str | None,
    *,
    writable: bool = True,
    expected_revision: str | None = None,
) -> _FileContext:
    owned = await db.scalar(
        select(ExecutionAgent)
        .options(selectinload(ExecutionAgent.roots))
        .where(
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
    operations, roots = snapshot
    stored_root = next((item for item in owned.roots if item.id == root_id), None)
    root = next((item for item in roots if item.id == root_id), None)
    if (
        stored_root is None
        or root is None
        or root.revision != stored_root.revision
        or expected_revision is not None and root.revision != expected_revision
        or not _SHA256_RE.fullmatch(root.revision)
    ):
        raise ApiError(404, "file.workspaceNotSelected")
    if root_identity is not None and root_identity != str(root_id):
        raise ApiError(404, "file.fileNotFound")
    return _FileContext(
        root=FileRoot.WORKSPACE,
        working_directory=_normalize_agent_path(working_directory or "."),
        agent_id=agent_id,
        agent_root_id=root_id,
        agent_root_revision=root.revision,
        agent_writable=writable,
        agent_operations=operations,
    )


async def _file_context(
    scope: FileScope,
    session_id: UUID | None,
    user: UserAccount,
    db: AsyncSession,
    *,
    working_directory: str | None = None,
    root_identity: str | None = None,
    execution_agent_id: UUID | None = None,
    root_id: UUID | None = None,
    root_revision: str | None = None,
    for_update: bool = False,
) -> _FileContext:
    session = None
    if session_id is not None:
        if for_update:
            candidate = await db.scalar(
                select(ChatSession)
                .where(ChatSession.id == session_id, ChatSession.user_id == user.id)
                .with_for_update()
            )
        else:
            candidate = await db.get(ChatSession, session_id)
        if candidate is None or candidate.user_id != user.id:
            raise ApiError(404, "session.notFound")
        session = candidate
    if root_identity is not None and session is None:
        raise ApiError(400, "file.sessionRequired")
    if scope is FileScope.SESSION:
        if session is None:
            raise ApiError(400, "file.sessionRequired")
        if root_identity is not None and root_identity != str(session.id):
            raise ApiError(404, "file.fileNotFound")
        return _FileContext(
            root_files=RootFileService(
                session.id,
                working_directory=session.working_directory,
            ),
            root=FileRoot.SESSION,
        )
    if root_revision is not None:
        if (
            session is None
            or execution_agent_id is None
            or root_id is None
            or working_directory is None
        ):
            raise ApiError(400, "file.invalidRoot")
        return await _agent_context(
            db,
            user,
            execution_agent_id,
            root_id,
            working_directory,
            root_identity,
            writable=session.workspace_access == "read-write",
            expected_revision=root_revision,
        )
    if session is not None:
        selected_agent_id = session.preferred_execution_agent_id
        selected_root_id = session.preferred_root_id
        if selected_agent_id is None or selected_root_id is None:
            raise ApiError(400, "file.workspaceNotSelected")
        if (
            execution_agent_id is not None and execution_agent_id != selected_agent_id
        ) or (root_id is not None and root_id != selected_root_id):
            raise ApiError(404, "file.fileNotFound")
        return await _agent_context(
            db,
            user,
            selected_agent_id,
            selected_root_id,
            session.working_directory,
            root_identity,
            writable=session.workspace_access == "read-write",
        )
    elif execution_agent_id is not None or root_id is not None:
        if execution_agent_id is None or root_id is None:
            raise ApiError(400, "file.workspaceNotSelected")
        return await _agent_context(
            db,
            user,
            execution_agent_id,
            root_id,
            working_directory,
            root_identity,
        )
    if session is None and working_directory:
        global_workspace = WorkspaceService()
        try:
            root = global_workspace.resolve_read_target(working_directory)
        except WorkspaceError as exc:
            raise _context_error(exc) from exc
        if not root.is_dir():
            raise ApiError(400, "workspace.invalidWorkingDirectory")
        return _FileContext(
            root_files=RootFileService(
                UUID(int=0),
                working_directory=global_workspace.relative(root),
            ),
            root=FileRoot.WORKSPACE,
            working_directory=global_workspace.relative(root),
        )
    return _FileContext(workspace=WorkspaceService())


def _root_entry_payload(entry: RootFileEntry) -> dict:
    return {
        "name": entry.name,
        "path": entry.path,
        "kind": entry.kind,
        "size": entry.size,
        "modifiedAt": entry.modified_at,
        "hasChildren": entry.kind == "directory",
    }


def _context_error(exc: WorkspaceError | RootFileError) -> ApiError:
    if exc.code in {"file.changed", "file.pathAlreadyExists", "workspace.pathAlreadyExists"}:
        return ApiError(409, exc.code)
    if exc.code in {
        "file.directoryNotFound",
        "file.fileNotFound",
        "file.pathNotFound",
        "workspace.directoryNotFound",
        "workspace.fileNotFound",
        "workspace.pathNotFound",
    }:
        return ApiError(404, exc.code)
    if exc.code in {"file.tooLarge", "workspace.fileTooLarge"}:
        return ApiError(413, exc.code)
    if exc.code == "file.sessionQuotaExceeded":
        return ApiError(507, exc.code)
    return ApiError(400, exc.code)


def _join_path(parent: str, name: str) -> str:
    return name if parent == "." else f"{parent}/{name}"


def _is_same_or_descendant(path: str, parent: str) -> bool:
    path_parts = PurePosixPath(path).parts
    parent_parts = PurePosixPath(parent).parts
    return path_parts[: len(parent_parts)] == parent_parts


async def _assert_paths_not_in_use(
    db: AsyncSession,
    user: UserAccount,
    workspace: WorkspaceService,
    targets: tuple[Path, ...],
) -> None:
    sessions = list(
        (
            await db.scalars(
                select(ChatSession).where(
                    ChatSession.user_id == user.id,
                    ChatSession.working_directory.is_not(None),
                )
            )
        ).all()
    )
    for item in sessions:
        workdir = workspace.resolve(item.working_directory)
        if any(target == workdir or target in workdir.parents for target in targets):
            raise ApiError(
                409,
                "workspace.pathInUse",
                params={"sessionTitle": item.title},
            )


async def _lock_user(db: AsyncSession, user_id: UUID) -> None:
    await db.scalar(
        select(UserAccount.id)
        .where(UserAccount.id == user_id)
        .with_for_update()
    )


async def _clear_file_grants(
    db: AsyncSession,
    user: UserAccount,
    context: _FileContext,
    affected_paths: tuple[str, ...],
    *,
    global_targets: tuple[Path, ...] = (),
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
                    PermissionGrant.resource_type.in_(
                        ("execution_root_path", "path", "root_path")
                    )
                )
                .join(ChatSession, ChatSession.id == PermissionGrant.session_id)
                .where(ChatSession.user_id == user.id)
            )
        ).all()
    )
    global_workspace = WorkspaceService()
    for grant in grants:
        remove = False
        if grant.resource_type == "execution_root_path":
            if context.agent_id is None:
                continue
            try:
                scope = json.loads(grant.resource_scope)
                grant_path = scope["path"]
                remove = (
                    scope["agentId"] == str(context.agent_id)
                    and scope["rootId"] == str(context.agent_root_id)
                    and scope["rootRevision"] == context.agent_root_revision
                    and scope["workingDirectory"] == context.working_directory
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
        elif global_targets:
            try:
                grant_path = global_workspace.resolve(grant.resource_scope)
                remove = any(
                    target == grant_path or target in grant_path.parents
                    for target in global_targets
                )
            except WorkspaceError:
                remove = True
        if remove:
            await db.delete(grant)


async def _clear_context_grants(
    db: AsyncSession,
    user: UserAccount,
    context: _FileContext,
    paths: tuple[str, ...],
) -> None:
    global_targets: tuple[Path, ...] = ()
    if (
        context.agent_id is None
        and context.root is not FileRoot.SESSION
        and (context.workspace is not None or context.root is FileRoot.WORKSPACE)
    ):
        workspace = WorkspaceService()
        try:
            global_targets = tuple(
                workspace.resolve(
                    _join_path(context.working_directory, path)
                    if context.working_directory
                    else path
                )
                for path in paths
            )
        except WorkspaceError as exc:
            raise _context_error(exc) from exc
    await _clear_file_grants(
        db,
        user,
        context,
        paths,
        global_targets=global_targets,
    )


def _require_agent_operations(context: _FileContext, *operations: str) -> None:
    if context.agent_id is not None and not set(operations) <= context.agent_operations:
        raise ApiError(503, "executionAgent.operationUnavailable")


def _list_context(
    context: _FileContext, path: str, offset: int
) -> tuple[list[dict], bool, int | None]:
    if context.workspace is not None:
        return (context.workspace.list_entries(path), False, None) if offset == 0 else ([], False, None)
    assert context.root_files is not None and context.root is not None
    result = context.root_files.list_entries(context.root, path, offset=offset, limit=1_000)
    return (
        [_root_entry_payload(entry) for entry in result.entries],
        result.has_more,
        result.next_offset,
    )


def _read_context(context: _FileContext, path: str, limit: int) -> bytes:
    if context.workspace is not None:
        return context.workspace.read_bytes(path, limit=limit)
    assert context.root_files is not None and context.root is not None
    return context.root_files.read_bytes(context.root, path, limit=limit)


def _replace_text_context(
    context: _FileContext, path: str, content: str, expected_revision: str
) -> str:
    if context.workspace is not None:
        return context.workspace.replace_text_file(path, content, expected_revision)
    assert context.root_files is not None and context.root is not None
    return context.root_files.replace_text_file(
        context.root, path, content, expected_revision
    )


def _open_context(context: _FileContext, path: str) -> BinaryIO:
    if context.workspace is not None:
        return context.workspace.open_file(path)
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
    context: _FileContext, operation: str, arguments: dict
) -> dict:
    if (
        context.agent_id is None
        or context.agent_root_id is None
        or context.agent_root_revision is None
    ):
        raise ApiError(500, "internal.unexpected")
    if not context.agent_writable and operation in {
        "workspace.copy",
        "workspace.delete",
        "workspace.import",
        "workspace.mkdir",
        "workspace.move",
        "workspace.rename",
        "workspace.write",
    }:
        raise ApiError(403, "http.forbidden")
    payload = {
        "rootId": str(context.agent_root_id),
        "rootRevision": context.agent_root_revision,
        "workingDirectory": context.working_directory or ".",
        **arguments,
    }
    try:
        response = await execution_agent_connections.request(
            context.agent_id, operation, payload
        )
    except ExecutionAgentError as exc:
        raise _execution_error(exc.code, manager=True) from exc
    if not response.ok:
        raise _execution_error(response.error.code if response.error else "")
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
        await _agent_request(context, "workspace.stat", {"path": normalized}),
        expected_path=normalized,
    )


def _transfer_spec(
    request: Request, agent_id: UUID, ticket_id: UUID, token: str
) -> dict[str, str]:
    del request
    try:
        url = execution_agent_connections.transfer_url(agent_id, ticket_id)
    except ExecutionAgentError as exc:
        raise ApiError(503, exc.code) from exc
    return {"url": url, "token": token}


def _bearer_token(authorization: str | None) -> str:
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not token or " " in token:
        raise ApiError(401, "transfer.invalidTicket")
    return token


async def _transfer_agent(db: AsyncSession, authorization: str | None) -> UUID:
    agent_id = await db.scalar(
        select(ExecutionAgent.id).where(
            ExecutionAgent.token_hash == hash_token(_bearer_token(authorization)),
            ExecutionAgent.revoked_at.is_(None),
        )
    )
    if agent_id is None:
        raise ApiError(401, "transfer.invalidTicket")
    return agent_id


@router.get("/transfers/{ticket_id}", name="workspace_transfer")
async def get_workspace_transfer(
    ticket_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    authorization: Annotated[str | None, Header()] = None,
    x_mato_transfer_token: Annotated[str | None, Header()] = None,
) -> StreamingResponse:
    agent_id = await _transfer_agent(db, authorization)
    try:
        ticket = transfer_tickets.claim(
            ticket_id,
            x_mato_transfer_token or "",
            "download",
            owner_id=agent_id,
        )
    except TransferError as exc:
        raise ApiError(
            410 if exc.code == "transfer.expired" else 404, exc.code
        ) from exc
    assert ticket.expected_sha256 is not None
    return StreamingResponse(
        stream_ticket(ticket),
        media_type="application/octet-stream",
        headers={
            "Content-Length": str(ticket.expected_size),
            "Digest": (
                "sha-256="
                + base64.b64encode(bytes.fromhex(ticket.expected_sha256)).decode()
            ),
            **_FILE_RESPONSE_HEADERS,
        },
    )


@router.put("/transfers/{ticket_id}", name="workspace_transfer_upload")
async def put_workspace_transfer(
    ticket_id: UUID,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    authorization: Annotated[str | None, Header()] = None,
    x_mato_transfer_token: Annotated[str | None, Header()] = None,
    content_length: Annotated[int | None, Header(ge=0)] = None,
    digest: Annotated[str | None, Header()] = None,
) -> Response:
    agent_id = await _transfer_agent(db, authorization)
    try:
        ticket = transfer_tickets.claim(
            ticket_id,
            x_mato_transfer_token or "",
            "upload",
            owner_id=agent_id,
        )
        await transfer_tickets.receive(
            ticket,
            request.stream(),
            content_length=content_length,
            digest_header=digest,
        )
    except TransferError as exc:
        raise ApiError(
            410 if exc.code == "transfer.expired" else 400, exc.code
        ) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


async def _agent_export(
    context: _FileContext,
    path: str,
    request: Request,
    *,
    max_bytes: int,
) -> tuple[BinaryIO, int, str]:
    normalized = _normalize_agent_path(path)
    entry = await _agent_stat(context, normalized)
    if entry["kind"] != "file":
        raise ApiError(400, "file.notAFile")
    size = entry["size"]
    if not isinstance(size, int) or size > max_bytes:
        raise ApiError(413, "file.tooLarge")
    assert context.agent_id is not None
    grant, ticket = transfer_tickets.create_upload(context.agent_id, size=size)
    try:
        result = await _agent_request(
            context,
            "workspace.export",
            {
                "path": normalized,
                "transfer": _transfer_spec(
                    request, context.agent_id, grant.ticket_id, grant.token
                ),
            },
        )
        result_size = result.get("size")
        result_sha256 = result.get("sha256")
        if (
            result_size != ticket.actual_size
            or result_sha256 != ticket.actual_sha256
            or not isinstance(result_size, int)
            or not isinstance(result_sha256, str)
            or not _SHA256_RE.fullmatch(result_sha256)
            or ticket.content is None
        ):
            raise ApiError(502, "executionAgent.protocolError")
        return ticket.content, result_size, result_sha256
    except BaseException:
        if ticket.content is not None:
            ticket.content.close()
        raise
    finally:
        transfer_tickets.revoke(grant.ticket_id)


async def _agent_text_preview(
    context: _FileContext, path: str, request: Request
) -> tuple[str, str]:
    normalized = _normalize_agent_path(path)
    result = await _agent_request(
        context,
        "workspace.read",
        {"path": normalized, "offset": 1, "limit": 1, "maxLineLength": 1},
    )
    revision = result.get("sha256")
    if not isinstance(revision, str) or not _SHA256_RE.fullmatch(revision):
        raise ApiError(502, "executionAgent.protocolError")
    handle, _, digest = await _agent_export(
        context, normalized, request, max_bytes=MAX_TEXT_PREVIEW_BYTES
    )
    try:
        data = handle.read(MAX_TEXT_PREVIEW_BYTES + 1)
    finally:
        handle.close()
    if digest != revision:
        raise ApiError(409, "file.changed")
    try:
        return data.decode("utf-8", errors="strict"), revision
    except UnicodeDecodeError as exc:
        raise ApiError(400, "file.invalidEncoding") from exc


@router.get("/entries")
async def list_entries(
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
    path: str = Query(default="."),
    offset: int = Query(default=0, ge=0),
    scope: FileScope = FileScope.FILESYSTEM,
    session_id: UUID | None = None,
    working_directory: str | None = None,
    root_identity: Annotated[str | None, Query(max_length=1_200)] = None,
    execution_agent_id: UUID | None = None,
    root_id: UUID | None = None,
    root_revision: Annotated[str | None, Query(pattern=r"^[0-9a-f]{64}$")] = None,
) -> dict:
    context = await _file_context(
        scope,
        session_id,
        user,
        db,
        working_directory=working_directory,
        root_identity=root_identity,
        execution_agent_id=execution_agent_id,
        root_id=root_id,
        root_revision=root_revision,
    )
    try:
        if context.agent_id is not None:
            normalized = _normalize_agent_path(path)
            result = await _agent_request(
                context,
                "workspace.list",
                {"path": normalized, "offset": offset, "limit": 1_000},
            )
            raw_entries = result.get("entries")
            has_more = result.get("hasMore")
            next_offset = result.get("nextOffset")
            if (
                not isinstance(raw_entries, list)
                or not isinstance(has_more, bool)
                or next_offset is not None
                and (not isinstance(next_offset, int) or isinstance(next_offset, bool))
            ):
                raise ApiError(502, "executionAgent.protocolError")
            entries = [_agent_entry(entry) for entry in raw_entries]
            for entry in entries:
                expected_parent = PurePosixPath(entry["path"]).parent.as_posix()
                if expected_parent != normalized:
                    raise ApiError(502, "executionAgent.protocolError")
            return {
                "path": normalized,
                "entries": entries,
                "hasMore": has_more,
                "nextOffset": next_offset,
            }
        entries, has_more, next_offset = await asyncio.to_thread(
            _list_context, context, path, offset
        )
        return {
            "path": path,
            "entries": entries,
            "hasMore": has_more,
            "nextOffset": next_offset,
        }
    except (WorkspaceError, RootFileError) as exc:
        raise _context_error(exc) from exc


@router.post("/directories", status_code=status.HTTP_201_CREATED)
async def create_directory(
    payload: CreateDirectoryRequest,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
    scope: FileScope = FileScope.FILESYSTEM,
    session_id: UUID | None = None,
    working_directory: str | None = None,
    execution_agent_id: UUID | None = None,
    root_id: UUID | None = None,
) -> dict:
    context = await _file_context(
        scope,
        session_id,
        user,
        db,
        working_directory=working_directory,
        execution_agent_id=execution_agent_id,
        root_id=root_id,
        for_update=session_id is not None,
    )
    try:
        if context.agent_id is not None:
            path = _normalize_agent_path(
                _join_path(payload.parent, validate_upload_filename(payload.name))
            )
            await _clear_file_grants(db, user, context, (path,))
            await db.commit()
            result = await _agent_request(
                context, "workspace.mkdir", {"path": path, "parents": False}
            )
            if result.get("path") != path:
                raise ApiError(502, "executionAgent.protocolError")
            return {
                "name": PurePosixPath(path).name,
                "path": path,
                "kind": "directory",
            }
        if context.workspace is not None:
            return await asyncio.to_thread(
                context.workspace.create_directory, payload.parent, payload.name
            )
        assert context.root_files is not None and context.root is not None
        path = _join_path(payload.parent, payload.name)
        await asyncio.to_thread(context.root_files.create_directory, context.root, path)
        return {"name": payload.name, "path": path, "kind": "directory"}
    except (WorkspaceError, RootFileError) as exc:
        raise _context_error(exc) from exc
    except FileExistsError as exc:
        raise ApiError(409, "workspace.pathAlreadyExists") from exc
    except FileNotFoundError as exc:
        raise ApiError(400, "workspace.parentNotFound") from exc


@router.post("/files", status_code=status.HTTP_201_CREATED)
async def upload_file(
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
    file: Annotated[UploadFile, File()],
    request: Request = None,
    parent: str = Query(default="."),
    scope: FileScope = FileScope.FILESYSTEM,
    session_id: UUID | None = None,
    working_directory: str | None = None,
    execution_agent_id: UUID | None = None,
    root_id: UUID | None = None,
) -> dict:
    context = await _file_context(
        scope,
        session_id,
        user,
        db,
        working_directory=working_directory,
        execution_agent_id=execution_agent_id,
        root_id=root_id,
        for_update=session_id is not None,
    )
    try:
        if context.agent_id is not None:
            if request is None:
                raise ApiError(500, "internal.unexpected")
            path = _normalize_agent_path(
                _join_path(parent, validate_upload_filename(file.filename))
            )
            await _clear_file_grants(db, user, context, (path,))
            await db.commit()
            staged, size, digest = await asyncio.to_thread(
                stage_file, file.file, max_bytes=MAX_FILE_ATTACHMENT_BYTES
            )
            assert context.agent_id is not None
            grant, _ = transfer_tickets.create_download(
                context.agent_id, staged, size=size, sha256=digest
            )
            try:
                result = await _agent_request(
                    context,
                    "workspace.import",
                    {
                        "path": path,
                        "transfer": _transfer_spec(
                            request, context.agent_id, grant.ticket_id, grant.token
                        ),
                        "size": size,
                        "sha256": digest,
                        "overwrite": False,
                    },
                )
                if (
                    result.get("path") != path
                    or result.get("size") != size
                    or result.get("sha256") != digest
                ):
                    raise ApiError(502, "executionAgent.protocolError")
            finally:
                transfer_tickets.revoke(grant.ticket_id)
            entry = await _agent_stat(context, path)
        elif context.workspace is not None:
            entry = await asyncio.to_thread(
                context.workspace.create_file,
                parent,
                file.filename,
                file.file,
                max_bytes=MAX_FILE_ATTACHMENT_BYTES,
            )
        else:
            assert context.root_files is not None and context.root is not None
            path = _join_path(parent, validate_upload_filename(file.filename))
            created = await asyncio.to_thread(
                context.root_files.create_file,
                context.root,
                path,
                file.file,
                max_bytes=MAX_FILE_ATTACHMENT_BYTES,
            )
            entry = _root_entry_payload(created)
        return {"entry": entry}
    except (WorkspaceError, RootFileError) as exc:
        raise _context_error(exc) from exc
    except OSError as exc:
        if exc.errno == errno.ENOSPC:
            raise ApiError(507, "file.storageFull") from exc
        raise
    finally:
        await file.close()


@router.get("/files/preview")
async def preview_file(
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
    path: str,
    request: Request = None,
    scope: FileScope = FileScope.FILESYSTEM,
    session_id: UUID | None = None,
    working_directory: str | None = None,
    root_identity: Annotated[str | None, Query(max_length=1_200)] = None,
    execution_agent_id: UUID | None = None,
    root_id: UUID | None = None,
    root_revision: Annotated[str | None, Query(pattern=r"^[0-9a-f]{64}$")] = None,
) -> dict:
    context = await _file_context(
        scope,
        session_id,
        user,
        db,
        working_directory=working_directory,
        root_identity=root_identity,
        execution_agent_id=execution_agent_id,
        root_id=root_id,
        root_revision=root_revision,
    )
    suffix = PurePosixPath(path).suffix.casefold()
    try:
        if context.agent_id is not None and suffix not in {*_IMAGE_SUFFIXES, ".svg"}:
            if request is None:
                raise ApiError(500, "internal.unexpected")
            content, revision = await _agent_text_preview(context, path, request)
            if "\0" in content:
                return {"kind": "none", "reason": "unsupported"}
            return {
                "kind": "markdown" if suffix in _MARKDOWN_SUFFIXES else "text",
                "content": content,
                "encoding": "utf-8",
                "revision": revision,
            }
        if context.agent_id is not None:
            if request is None:
                raise ApiError(500, "internal.unexpected")
            handle, _, _ = await _agent_export(
                context, path, request, max_bytes=_IMAGE_PREVIEW_BYTES
            )
            with handle:
                data = handle.read(_IMAGE_PREVIEW_BYTES + 1)
        else:
            data = await asyncio.to_thread(
                _read_context, context, path, _IMAGE_PREVIEW_BYTES
            )
    except (WorkspaceError, RootFileError) as exc:
        raise _context_error(exc) from exc

    mime_type = image_mime_type(data)
    if mime_type is not None:
        if len(data) > _IMAGE_PREVIEW_BYTES:
            return {"kind": "none", "reason": "tooLarge"}
        try:
            validated = await asyncio.to_thread(validate_image, data, mime_type)
        except AttachmentError:
            return {"kind": "none", "reason": "invalidImage"}
        return {
            "kind": "image",
            "mimeType": validated.mime_type,
            "width": validated.width,
            "height": validated.height,
        }
    if suffix == ".svg":
        try:
            await asyncio.to_thread(_validate_svg, data)
        except ValueError as exc:
            return {"kind": "none", "reason": str(exc)}
        return {"kind": "image", "mimeType": "image/svg+xml"}
    if suffix in _IMAGE_SUFFIXES:
        return {"kind": "none", "reason": "invalidImage"}
    if len(data) > MAX_TEXT_PREVIEW_BYTES:
        return {"kind": "none", "reason": "tooLarge"}
    try:
        content = data.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return {"kind": "none", "reason": "unsupported"}
    if "\0" in content:
        return {"kind": "none", "reason": "unsupported"}
    return {
        "kind": "markdown" if suffix in _MARKDOWN_SUFFIXES else "text",
        "content": content,
        "encoding": "utf-8",
        "revision": hashlib.sha256(data).hexdigest(),
    }


@router.put("/files")
async def update_file(
    payload: UpdateFileRequest,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
    scope: FileScope = FileScope.FILESYSTEM,
    session_id: UUID | None = None,
    working_directory: str | None = None,
    execution_agent_id: UUID | None = None,
    root_id: UUID | None = None,
) -> dict:
    context = await _file_context(
        scope,
        session_id,
        user,
        db,
        working_directory=working_directory,
        execution_agent_id=execution_agent_id,
        root_id=root_id,
        for_update=session_id is not None,
    )
    if context.agent_id is not None:
        path = _normalize_agent_path(payload.path)
        await _clear_file_grants(db, user, context, (path,))
        await db.commit()
        result = await _agent_request(
            context,
            "workspace.write",
            {
                "path": path,
                "content": payload.content,
                "expectedSha256": payload.expected_revision,
            },
        )
        revision = result.get("sha256")
        if (
            result.get("path") != path
            or not isinstance(revision, str)
            or not _SHA256_RE.fullmatch(revision)
        ):
            raise ApiError(502, "executionAgent.protocolError")
        return {"revision": revision, "entry": await _agent_stat(context, path)}
    context_identity = (
        context.root,
        context.working_directory,
        context.workspace is not None,
    )
    global_targets: tuple[Path, ...] = ()
    if context.root is not FileRoot.SESSION:
        global_workspace = WorkspaceService()
        global_path = (
            _join_path(context.working_directory, payload.path)
            if context.working_directory
            else payload.path
        )
        try:
            global_targets = (global_workspace.resolve(global_path),)
        except WorkspaceError as exc:
            raise _context_error(exc) from exc
    await _clear_file_grants(
        db,
        user,
        context,
        (payload.path,),
        global_targets=global_targets,
    )
    await db.commit()

    if global_targets:
        await _lock_user(db, user.id)
    context = await _file_context(
        scope,
        session_id,
        user,
        db,
        working_directory=working_directory,
        for_update=session_id is not None,
    )
    if context_identity != (
        context.root,
        context.working_directory,
        context.workspace is not None,
    ):
        raise ApiError(409, "file.changed")
    try:
        revision = await asyncio.to_thread(
            _replace_text_context,
            context,
            payload.path,
            payload.content,
            payload.expected_revision,
        )
    except (WorkspaceError, RootFileError) as exc:
        raise _context_error(exc) from exc
    except OSError as exc:
        if exc.errno in {errno.ENOSPC, getattr(errno, "EDQUOT", errno.ENOSPC)}:
            raise ApiError(507, "file.storageFull") from exc
        if exc.errno == errno.EFBIG:
            raise ApiError(413, "file.tooLarge") from exc
        raise
    finally:
        await db.commit()
    return {"revision": revision}


@router.get("/files/content")
async def preview_image(
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
    path: str,
    request: Request = None,
    scope: FileScope = FileScope.FILESYSTEM,
    session_id: UUID | None = None,
    working_directory: str | None = None,
    root_identity: Annotated[str | None, Query(max_length=1_200)] = None,
    execution_agent_id: UUID | None = None,
    root_id: UUID | None = None,
    root_revision: Annotated[str | None, Query(pattern=r"^[0-9a-f]{64}$")] = None,
) -> Response:
    context = await _file_context(
        scope,
        session_id,
        user,
        db,
        working_directory=working_directory,
        root_identity=root_identity,
        execution_agent_id=execution_agent_id,
        root_id=root_id,
        root_revision=root_revision,
    )
    try:
        if context.agent_id is not None:
            if request is None:
                raise ApiError(500, "internal.unexpected")
            handle, _, _ = await _agent_export(
                context, path, request, max_bytes=_IMAGE_PREVIEW_BYTES
            )
            with handle:
                data = handle.read(_IMAGE_PREVIEW_BYTES + 1)
        else:
            data = await asyncio.to_thread(
                _read_context, context, path, _IMAGE_PREVIEW_BYTES
            )
        if PurePosixPath(path).suffix.casefold() == ".svg":
            await asyncio.to_thread(_validate_svg, data)
            mime_type = "image/svg+xml"
        else:
            mime_type = image_mime_type(data)
            if mime_type is None or len(data) > _IMAGE_PREVIEW_BYTES:
                raise ApiError(415, "file.previewUnavailable")
            await asyncio.to_thread(validate_image, data, mime_type)
    except (WorkspaceError, RootFileError) as exc:
        raise _context_error(exc) from exc
    except (AttachmentError, ValueError) as exc:
        raise ApiError(415, "file.previewUnavailable") from exc
    headers = _SVG_RESPONSE_HEADERS if mime_type == "image/svg+xml" else _FILE_RESPONSE_HEADERS
    return Response(content=data, media_type=mime_type, headers=headers)


@router.get("/files/download")
async def download_file(
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
    path: str,
    request: Request = None,
    scope: FileScope = FileScope.FILESYSTEM,
    session_id: UUID | None = None,
    working_directory: str | None = None,
    root_identity: Annotated[str | None, Query(max_length=1_200)] = None,
    execution_agent_id: UUID | None = None,
    root_id: UUID | None = None,
    root_revision: Annotated[str | None, Query(pattern=r"^[0-9a-f]{64}$")] = None,
) -> StreamingResponse:
    context = await _file_context(
        scope,
        session_id,
        user,
        db,
        working_directory=working_directory,
        root_identity=root_identity,
        execution_agent_id=execution_agent_id,
        root_id=root_id,
        root_revision=root_revision,
    )
    try:
        if context.agent_id is not None:
            if request is None:
                raise ApiError(500, "internal.unexpected")
            handle, size, _ = await _agent_export(
                context, path, request, max_bytes=MAX_TRANSFER_BYTES
            )
        else:
            handle = await asyncio.to_thread(_open_context, context, path)
            size = None
    except (WorkspaceError, RootFileError) as exc:
        raise _context_error(exc) from exc
    filename = PurePosixPath(path).name or "download"
    headers = {
        **_FILE_RESPONSE_HEADERS,
        "Content-Disposition": (
            "attachment; filename=\"download\"; "
            f"filename*=UTF-8''{quote(filename, safe='')}"
        ),
    }
    if size is not None:
        headers["Content-Length"] = str(size)
    return StreamingResponse(
        _stream_file(handle),
        media_type="application/octet-stream",
        headers=headers,
    )


@router.patch("/entries")
async def rename_entry(
    payload: RenameEntryRequest,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
    scope: FileScope = FileScope.FILESYSTEM,
    session_id: UUID | None = None,
    working_directory: str | None = None,
    execution_agent_id: UUID | None = None,
    root_id: UUID | None = None,
) -> dict:
    context = await _file_context(
        scope,
        session_id,
        user,
        db,
        working_directory=working_directory,
        execution_agent_id=execution_agent_id,
        root_id=root_id,
        for_update=session_id is not None,
    )
    try:
        name = validate_upload_filename(payload.name)
    except RootFileError as exc:
        raise _context_error(exc) from exc
    parent = PurePosixPath(payload.path).parent.as_posix()
    destination_path = _join_path(parent, name)
    if context.agent_id is not None:
        source_path = _normalize_agent_path(payload.path)
        destination_path = _normalize_agent_path(destination_path)
        await _clear_file_grants(
            db, user, context, (source_path, destination_path)
        )
        await db.commit()
        result = await _agent_request(
            context,
            "workspace.rename",
            {"path": source_path, "destination": destination_path},
        )
        if result.get("path") != destination_path:
            raise ApiError(502, "executionAgent.protocolError")
        return {"entry": await _agent_stat(context, destination_path)}
    global_targets: tuple[Path, ...] = ()
    if context.root is not FileRoot.SESSION:
        global_workspace = WorkspaceService()
        global_path = (
            _join_path(context.working_directory, payload.path)
            if context.working_directory
            else payload.path
        )
        try:
            source = global_workspace.resolve(global_path)
        except WorkspaceError as exc:
            raise _context_error(exc) from exc
        destination = source.parent / name
        await _assert_paths_not_in_use(
            db, user, global_workspace, (source, destination)
        )
        global_targets = (source, destination)
    await _clear_file_grants(
        db,
        user,
        context,
        (payload.path, destination_path),
        global_targets=global_targets,
    )
    await db.commit()
    if global_targets:
        await _lock_user(db, user.id)
        await _assert_paths_not_in_use(
            db, user, WorkspaceService(), global_targets
        )
    try:
        if context.workspace is not None:
            entry = await asyncio.to_thread(
                context.workspace.rename, payload.path, name
            )
        else:
            assert context.root_files is not None and context.root is not None
            renamed = await asyncio.to_thread(
                context.root_files.rename_path,
                context.root,
                payload.path,
                name,
            )
            entry = _root_entry_payload(renamed)
    except (WorkspaceError, RootFileError) as exc:
        raise _context_error(exc) from exc
    except OSError as exc:
        if exc.errno in {errno.EINVAL, errno.ENOSYS, errno.EOPNOTSUPP}:
            raise ApiError(501, "file.renameUnavailable") from exc
        raise
    if global_targets:
        await db.commit()
    return {"entry": entry}


@router.delete("/entries", status_code=status.HTTP_204_NO_CONTENT)
async def delete_entry(
    path: str,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
    scope: FileScope = FileScope.FILESYSTEM,
    session_id: UUID | None = None,
    working_directory: str | None = None,
    execution_agent_id: UUID | None = None,
    root_id: UUID | None = None,
) -> None:
    context = await _file_context(
        scope,
        session_id,
        user,
        db,
        working_directory=working_directory,
        execution_agent_id=execution_agent_id,
        root_id=root_id,
        for_update=session_id is not None,
    )
    if context.agent_id is not None:
        normalized = _normalize_agent_path(path)
        await _clear_file_grants(db, user, context, (normalized,))
        await db.commit()
        result = await _agent_request(
            context,
            "workspace.delete",
            {"path": normalized, "recursive": True},
        )
        if result:
            raise ApiError(502, "executionAgent.protocolError")
        return
    if context.root is FileRoot.SESSION:
        await _clear_file_grants(db, user, context, (path,))
        await db.commit()
        try:
            assert context.root_files is not None
            await asyncio.to_thread(
                context.root_files.delete_path,
                FileRoot.SESSION,
                path,
                recursive=True,
            )
        except RootFileError as exc:
            raise _context_error(exc) from exc
        return

    global_workspace = WorkspaceService()
    global_path = _join_path(context.working_directory, path) if context.working_directory else path
    try:
        target = global_workspace.resolve(global_path)
    except WorkspaceError as exc:
        raise _context_error(exc) from exc
    await _assert_paths_not_in_use(db, user, global_workspace, (target,))
    await _clear_file_grants(
        db,
        user,
        context,
        (path,),
        global_targets=(target,),
    )
    await db.commit()
    await _lock_user(db, user.id)
    await _assert_paths_not_in_use(db, user, global_workspace, (target,))
    try:
        if context.workspace is not None:
            await asyncio.to_thread(context.workspace.delete, path)
        else:
            assert context.root_files is not None and context.root is FileRoot.WORKSPACE
            await asyncio.to_thread(
                context.root_files.delete_path,
                FileRoot.WORKSPACE,
                path,
                recursive=True,
            )
    except (WorkspaceError, RootFileError) as exc:
        raise _context_error(exc) from exc
    await db.commit()


async def _import_agent_file(
    context: _FileContext,
    path: str,
    source: BinaryIO,
    size: int,
    digest: str,
    request: Request,
) -> dict:
    assert context.agent_id is not None
    grant, _ = transfer_tickets.create_download(
        context.agent_id, source, size=size, sha256=digest
    )
    try:
        result = await _agent_request(
            context,
            "workspace.import",
            {
                "path": path,
                "transfer": _transfer_spec(
                    request, context.agent_id, grant.ticket_id, grant.token
                ),
                "size": size,
                "sha256": digest,
                "overwrite": False,
            },
        )
        if (
            result.get("path") != path
            or result.get("size") != size
            or result.get("sha256") != digest
        ):
            raise ApiError(502, "executionAgent.protocolError")
    finally:
        transfer_tickets.revoke(grant.ticket_id)
    return await _agent_stat(context, path)


async def _copy_or_move_entry(
    payload: CopyMoveEntryRequest,
    user: UserAccount,
    db: AsyncSession,
    request: Request,
    *,
    move: bool,
    scope: FileScope,
    session_id: UUID | None,
    working_directory: str | None,
    execution_agent_id: UUID | None,
    root_id: UUID | None,
) -> dict:
    source = await _file_context(
        scope,
        session_id,
        user,
        db,
        working_directory=working_directory,
        execution_agent_id=execution_agent_id,
        root_id=root_id,
        for_update=session_id is not None,
    )
    destination_session_id = (
        payload.destination_session_id
        if payload.destination_execution_agent_id is not None
        or payload.destination_root_id is not None
        else payload.destination_session_id or session_id
    )
    destination = await _file_context(
        payload.destination_scope,
        destination_session_id,
        user,
        db,
        working_directory=payload.destination_working_directory,
        execution_agent_id=payload.destination_execution_agent_id,
        root_id=payload.destination_root_id,
        for_update=destination_session_id is not None,
    )
    source_path = _normalize_agent_path(payload.path)
    destination_path = _normalize_agent_path(payload.destination)
    same_agent = source.agent_id is not None and source.agent_id == destination.agent_id
    if move and not same_agent and (
        source.agent_id is not None or destination.agent_id is not None
    ):
        raise ApiError(400, "file.crossBoundaryMoveUnavailable")
    if destination.agent_id is not None and not destination.agent_writable:
        raise ApiError(403, "http.forbidden")
    if move and source.agent_id is not None and not source.agent_writable:
        raise ApiError(403, "http.forbidden")
    if same_agent:
        _require_agent_operations(
            source, "workspace.move" if move else "workspace.copy"
        )
        _require_agent_operations(destination, "workspace.stat")
    else:
        _require_agent_operations(source, "workspace.stat", "workspace.export")
        _require_agent_operations(destination, "workspace.import", "workspace.stat")

    if move:
        await _clear_context_grants(db, user, source, (source_path,))
    await _clear_context_grants(db, user, destination, (destination_path,))
    await db.commit()

    if same_agent:
        assert destination.agent_root_id is not None
        operation_context = source if move or source.agent_writable else replace(
            source, agent_writable=True
        )
        result = await _agent_request(
            operation_context,
            "workspace.move" if move else "workspace.copy",
            {
                "path": source_path,
                "destination": destination_path,
                "destinationRootId": str(destination.agent_root_id),
                "destinationRootRevision": destination.agent_root_revision,
                "destinationWorkingDirectory": destination.working_directory or ".",
            },
        )
        if result.get("path") != destination_path:
            raise ApiError(502, "executionAgent.protocolError")
        return {"entry": await _agent_stat(destination, destination_path)}

    if (
        source.root_files is not None
        and destination.root_files is not None
        and source.root_files.session_id == destination.root_files.session_id
    ):
        assert source.root is not None and destination.root is not None
        operation = source.root_files.move_path if move else source.root_files.copy_path
        try:
            await asyncio.to_thread(
                operation,
                source.root,
                source_path,
                destination.root,
                destination_path,
            )
            parent = PurePosixPath(destination_path).parent.as_posix()
            listed = await asyncio.to_thread(
                destination.root_files.list_entries,
                destination.root,
                parent,
                offset=0,
                limit=1_000,
            )
            entry = next(
                (item for item in listed.entries if item.path == destination_path),
                None,
            )
            if entry is None:
                raise ApiError(502, "internal.unexpected")
        except (WorkspaceError, RootFileError) as exc:
            raise _context_error(exc) from exc
        return {"entry": _root_entry_payload(entry)}

    if source.agent_id is not None:
        handle, size, digest = await _agent_export(
            source, source_path, request, max_bytes=MAX_TRANSFER_BYTES
        )
    else:
        try:
            source_handle = await asyncio.to_thread(_open_context, source, source_path)
            try:
                handle, size, digest = await asyncio.to_thread(
                    stage_file, source_handle, max_bytes=MAX_TRANSFER_BYTES
                )
            finally:
                source_handle.close()
        except (WorkspaceError, RootFileError) as exc:
            raise _context_error(exc) from exc

    try:
        if destination.agent_id is not None:
            entry = await _import_agent_file(
                destination, destination_path, handle, size, digest, request
            )
        elif destination.root_files is not None and destination.root is not None:
            created = await asyncio.to_thread(
                destination.root_files.create_file,
                destination.root,
                destination_path,
                handle,
                max_bytes=MAX_TRANSFER_BYTES,
            )
            entry = _root_entry_payload(created)
        else:
            raise ApiError(400, "file.invalidRoot")
    except (WorkspaceError, RootFileError) as exc:
        raise _context_error(exc) from exc
    finally:
        handle.close()

    if move:
        try:
            if source.root_files is not None and source.root is not None:
                await asyncio.to_thread(
                    source.root_files.delete_path,
                    source.root,
                    source_path,
                    recursive=False,
                )
            elif source.workspace is not None:
                await asyncio.to_thread(source.workspace.delete, source_path)
        except (WorkspaceError, RootFileError) as exc:
            raise _context_error(exc) from exc
    return {"entry": entry}


@router.post("/entries/copy")
async def copy_entry(
    payload: CopyMoveEntryRequest,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
    request: Request,
    scope: FileScope = FileScope.FILESYSTEM,
    session_id: UUID | None = None,
    working_directory: str | None = None,
    execution_agent_id: UUID | None = None,
    root_id: UUID | None = None,
) -> dict:
    return await _copy_or_move_entry(
        payload,
        user,
        db,
        request,
        move=False,
        scope=scope,
        session_id=session_id,
        working_directory=working_directory,
        execution_agent_id=execution_agent_id,
        root_id=root_id,
    )


@router.post("/entries/move")
async def move_entry(
    payload: CopyMoveEntryRequest,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
    request: Request,
    scope: FileScope = FileScope.FILESYSTEM,
    session_id: UUID | None = None,
    working_directory: str | None = None,
    execution_agent_id: UUID | None = None,
    root_id: UUID | None = None,
) -> dict:
    return await _copy_or_move_entry(
        payload,
        user,
        db,
        request,
        move=True,
        scope=scope,
        session_id=session_id,
        working_directory=working_directory,
        execution_agent_id=execution_agent_id,
        root_id=root_id,
    )
