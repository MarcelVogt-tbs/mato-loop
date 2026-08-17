from __future__ import annotations

import asyncio
import base64
import errno
import hashlib
import json
import logging
import re
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass, field, replace
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Annotated, BinaryIO, Literal
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, File, Header, Query, Request, UploadFile, status
from fastapi.responses import Response, StreamingResponse
from pydantic import AliasChoices, BaseModel, ConfigDict, Field
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
    ExecutionClient,
    ExecutionClientError,
    TransferError,
    execution_agent_connections,
    stage_file,
    stream_ticket,
    transfer_tickets,
)
from mato_loop_backend.integrations.openscad import (
    OpenScadConfig,
    OpenScadRenderArguments,
    export_artifact,
    render_project,
    validate_binary_stl,
    validate_off,
)
from mato_loop_backend.models import ChatSession, ExecutionAgent, PermissionGrant, UserAccount
from mato_loop_backend.safe_svg import MAX_SAFE_SVG_BYTES, validate_svg
from mato_loop_backend.security import hash_token, require_user
from mato_loop_backend.settings import (
    FileMediaSettings,
    SettingsStore,
    load_effective_integrations,
)
from mato_loop_backend.workspace import (
    MAX_EDITABLE_TEXT_BYTES,
    FileRoot,
    RootFileEntry,
    RootFileError,
    RootFileService,
    validate_upload_filename,
)

router = APIRouter(prefix="/workspace")
_validate_svg = validate_svg
logger = logging.getLogger(__name__)

MAX_TEXT_PREVIEW_BYTES = MAX_EDITABLE_TEXT_BYTES
_IMAGE_PREVIEW_BYTES = MAX_SAFE_SVG_BYTES
_STL_PREVIEW_BYTES = MAX_FILE_ATTACHMENT_BYTES
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


class OpenScadFileRenderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    path: str = Field(strict=True, min_length=1, max_length=1_200)
    expected_revision: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
        validation_alias=AliasChoices("expected_revision", "expectedRevision"),
    )
    output: Literal["preview", "stl"] = "preview"


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
    root_files: RootFileService | None = None
    root: FileRoot | None = None
    working_directory: str | None = None
    agent_id: UUID | None = None
    agent_root_id: UUID | None = None
    agent_root_revision: str | None = None
    agent_writable: bool = True
    agent_operations: frozenset[str] = frozenset()
    file_settings: FileMediaSettings = field(default_factory=FileMediaSettings)


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
    file_settings: FileMediaSettings | None = None,
) -> _FileContext:
    file_settings = file_settings or await SettingsStore.load(db, FileMediaSettings)
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
    if scope is FileScope.SESSION:
        if session is None:
            raise ApiError(400, "file.sessionRequired")
        if root_identity is not None and root_identity != str(session.id):
            raise ApiError(404, "file.fileNotFound")
        return _FileContext(
            root_files=RootFileService(
                session.id,
                session_quota_bytes=file_settings.session_quota_bytes,
                max_editable_text_bytes=file_settings.max_editable_text_bytes,
            ),
            root=FileRoot.SESSION,
            file_settings=file_settings,
        )
    if root_revision is not None:
        if (
            execution_agent_id is None
            or root_id is None
            or working_directory is None
        ):
            raise ApiError(400, "file.invalidRoot")
        return replace(
            await _agent_context(
                db,
                user,
                execution_agent_id,
                root_id,
                working_directory,
                root_identity,
                writable=session is None or session.workspace_access == "read-write",
                expected_revision=root_revision,
            ),
            file_settings=file_settings,
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
        return replace(
            await _agent_context(
                db,
                user,
                selected_agent_id,
                selected_root_id,
                session.working_directory,
                root_identity,
                writable=session.workspace_access == "read-write",
            ),
            file_settings=file_settings,
        )
    if execution_agent_id is not None or root_id is not None:
        if execution_agent_id is None or root_id is None:
            raise ApiError(400, "file.workspaceNotSelected")
        return replace(
            await _agent_context(
                db,
                user,
                execution_agent_id,
                root_id,
                working_directory,
                root_identity,
            ),
            file_settings=file_settings,
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
    return name if parent == "." else f"{parent}/{name}"


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
                    PermissionGrant.resource_type.in_(("execution_root_path", "root_path"))
                )
                .join(ChatSession, ChatSession.id == PermissionGrant.session_id)
                .where(ChatSession.user_id == user.id)
            )
        ).all()
    )
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
        error_code = response.error.code if response.error else ""
        logger.warning("Execution agent operation %s failed: %s", operation, error_code)
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
    context: _FileContext, path: str, request: Request, *, max_bytes: int
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
        context, normalized, request, max_bytes=max_bytes
    )
    try:
        data = handle.read(max_bytes + 1)
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
    except RootFileError as exc:
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
        assert context.root_files is not None and context.root is not None
        path = _join_path(payload.parent, payload.name)
        await asyncio.to_thread(context.root_files.create_directory, context.root, path)
        return {"name": payload.name, "path": path, "kind": "directory"}
    except RootFileError as exc:
        raise _context_error(exc) from exc


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
    max_upload_bytes = context.file_settings.max_upload_bytes
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
                stage_file, file.file, max_bytes=max_upload_bytes
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
        else:
            assert context.root_files is not None and context.root is not None
            path = _join_path(parent, validate_upload_filename(file.filename))
            created = await asyncio.to_thread(
                context.root_files.create_file,
                context.root,
                path,
                file.file,
                max_bytes=max_upload_bytes,
            )
            entry = _root_entry_payload(created)
        return {"entry": entry}
    except RootFileError as exc:
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
    image_preview_bytes = context.file_settings.max_image_preview_bytes
    text_preview_bytes = context.file_settings.max_editable_text_bytes
    suffix = PurePosixPath(path).suffix.casefold()
    file_preview_bytes = (
        min(image_preview_bytes, MAX_SAFE_SVG_BYTES)
        if suffix == ".svg"
        else image_preview_bytes
    )
    try:
        if suffix == ".stl":
            if context.agent_id is not None:
                if request is None:
                    raise ApiError(500, "internal.unexpected")
                handle, _, _ = await _agent_export(
                    context, path, request, max_bytes=_STL_PREVIEW_BYTES
                )
                with handle:
                    data = handle.read(_STL_PREVIEW_BYTES + 1)
            else:
                data = await asyncio.to_thread(
                    _read_context, context, path, _STL_PREVIEW_BYTES
                )
            try:
                await asyncio.to_thread(validate_binary_stl, data)
            except ValueError:
                return {"kind": "none", "reason": "unsupported"}
            return {"kind": "stl"}
        if context.agent_id is not None and suffix not in {*_IMAGE_SUFFIXES, ".svg"}:
            if request is None:
                raise ApiError(500, "internal.unexpected")
            content, revision = await _agent_text_preview(
                context, path, request, max_bytes=text_preview_bytes
            )
            if "\0" in content:
                return {"kind": "none", "reason": "unsupported"}
            return {
                "kind": (
                    "openscad"
                    if suffix == ".scad"
                    else "markdown" if suffix in _MARKDOWN_SUFFIXES else "text"
                ),
                "content": content,
                "encoding": "utf-8",
                "revision": revision,
            }
        if context.agent_id is not None:
            if request is None:
                raise ApiError(500, "internal.unexpected")
            handle, _, _ = await _agent_export(
                context, path, request, max_bytes=file_preview_bytes
            )
            with handle:
                data = handle.read(file_preview_bytes + 1)
        else:
            data = await asyncio.to_thread(
                _read_context, context, path, file_preview_bytes
            )
    except RootFileError as exc:
        raise _context_error(exc) from exc

    mime_type = image_mime_type(data)
    if mime_type is not None:
        if len(data) > image_preview_bytes:
            return {"kind": "none", "reason": "tooLarge"}
        try:
            validated = await asyncio.to_thread(
                validate_image, data, mime_type, max_bytes=image_preview_bytes
            )
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
    if len(data) > text_preview_bytes:
        return {"kind": "none", "reason": "tooLarge"}
    try:
        content = data.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return {"kind": "none", "reason": "unsupported"}
    if "\0" in content:
        return {"kind": "none", "reason": "unsupported"}
    return {
        "kind": (
            "openscad"
            if suffix == ".scad"
            else "markdown" if suffix in _MARKDOWN_SUFFIXES else "text"
        ),
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
    if len(payload.content.encode("utf-8")) > context.file_settings.max_editable_text_bytes:
        raise ApiError(413, "file.tooLarge")
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
    await _clear_file_grants(db, user, context, (payload.path,))
    await db.commit()

    context = await _file_context(
        scope,
        session_id,
        user,
        db,
        working_directory=working_directory,
        for_update=session_id is not None,
    )
    try:
        revision = await asyncio.to_thread(
            _replace_text_context,
            context,
            payload.path,
            payload.content,
            payload.expected_revision,
        )
    except RootFileError as exc:
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
    suffix = PurePosixPath(path).suffix.casefold()
    image_preview_bytes = context.file_settings.max_image_preview_bytes
    max_bytes = _STL_PREVIEW_BYTES if suffix == ".stl" else image_preview_bytes
    if suffix == ".svg":
        max_bytes = min(max_bytes, MAX_SAFE_SVG_BYTES)
    try:
        if context.agent_id is not None:
            if request is None:
                raise ApiError(500, "internal.unexpected")
            handle, _, _ = await _agent_export(
                context, path, request, max_bytes=max_bytes
            )
            with handle:
                data = handle.read(max_bytes + 1)
        else:
            data = await asyncio.to_thread(
                _read_context, context, path, max_bytes
            )
        if suffix == ".stl":
            await asyncio.to_thread(validate_binary_stl, data)
            mime_type = "model/stl"
        elif suffix == ".svg":
            await asyncio.to_thread(_validate_svg, data)
            mime_type = "image/svg+xml"
        else:
            mime_type = image_mime_type(data)
            if mime_type is None or len(data) > image_preview_bytes:
                raise ApiError(415, "file.previewUnavailable")
            await asyncio.to_thread(
                validate_image, data, mime_type, max_bytes=image_preview_bytes
            )
    except RootFileError as exc:
        raise _context_error(exc) from exc
    except (AttachmentError, ValueError) as exc:
        raise ApiError(415, "file.previewUnavailable") from exc
    headers = _SVG_RESPONSE_HEADERS if mime_type == "image/svg+xml" else _FILE_RESPONSE_HEADERS
    return Response(content=data, media_type=mime_type, headers=headers)


async def _openscad_client(
    db: AsyncSession,
    user: UserAccount,
    context: _FileContext,
    session: ChatSession | None,
) -> tuple[ExecutionClient, OpenScadConfig]:
    if context.agent_id is not None:
        agent_id = context.agent_id
        root_id = context.agent_root_id
        root_revision = context.agent_root_revision
        working_directory = context.working_directory or "."
    elif session is not None and session.preferred_execution_agent_id is not None:
        if session.preferred_root_id is None:
            raise ApiError(400, "file.workspaceNotSelected")
        agent_context = await _agent_context(
            db,
            user,
            session.preferred_execution_agent_id,
            session.preferred_root_id,
            session.working_directory,
            None,
            writable=False,
        )
        agent_id = agent_context.agent_id
        root_id = agent_context.agent_root_id
        root_revision = agent_context.agent_root_revision
        working_directory = agent_context.working_directory or "."
    else:
        raise ApiError(409, "openscad.notConfigured")
    assert agent_id is not None and root_id is not None and root_revision is not None

    agent = await db.scalar(
        select(ExecutionAgent).where(
            ExecutionAgent.id == agent_id,
            ExecutionAgent.user_id == user.id,
            ExecutionAgent.revoked_at.is_(None),
        )
    )
    if agent is None:
        raise ApiError(404, "executionAgent.notFound")
    current_directory = session.current_directory if session is not None else None
    if (
        current_directory is None
        and agent.home_directory is not None
        and agent.home_root_id == root_id
        and agent.home_relative_directory is not None
    ):
        try:
            relative = PurePosixPath(working_directory).relative_to(
                PurePosixPath(agent.home_relative_directory)
            )
        except ValueError:
            pass
        else:
            current_directory = (PurePosixPath(agent.home_directory) / relative).as_posix()
    if current_directory is None or not current_directory.startswith("/"):
        raise ApiError(400, "file.workspaceNotSelected")

    snapshot = execution_agent_connections.snapshot(agent_id)
    required = {"integration.openscad.render", "cache.export"}
    if snapshot is None:
        raise ApiError(503, "executionAgent.unavailable")
    if not required <= snapshot[0]:
        raise ApiError(503, "executionAgent.operationUnavailable")
    settings = await load_effective_integrations(db, user.id)
    instance = next(
        (
            item
            for item in settings.instances
            if item.enabled
            and item.integration_type == "openscad"
            and item.adapter_type == "openscad"
            and item.execution_agent_id == agent_id
        ),
        None,
    )
    if instance is None:
        raise ApiError(409, "openscad.notConfigured")
    try:
        config = OpenScadConfig.model_validate(instance.config)
    except ValueError as exc:
        raise ApiError(409, "integration.invalidConfiguration") from exc
    return (
        ExecutionClient(
            agent_id,
            root_id,
            root_revision,
            working_directory,
            snapshot[0],
            current_directory,
        ),
        config,
    )


def _openscad_render_api_error(exc: ExecutionClientError) -> ApiError:
    if exc.code in {
        "file.changed",
        "file.workspaceNotSelected",
        "openscad.invalidExecutable",
    }:
        status_code = 409
    elif exc.code in {"file.directoryNotFound", "file.pathNotFound"}:
        status_code = 404
    elif exc.code in {
        "file.invalidPath",
        "file.notAFile",
        "file.unsupportedType",
    }:
        status_code = 400
    elif exc.code in {
        "file.operationTooLarge",
        "file.tooLarge",
        "openscad.projectTooLarge",
        "executionAgent.requestTooLarge",
    }:
        status_code = 413
    elif exc.code in {
        "openscad.invalidArchive",
        "openscad.previewFailed",
        "openscad.renderFailed",
        "openscad.unsafeProject",
        "tool.invalidArguments",
    }:
        status_code = 422
    elif exc.code == "executionAgent.timeout":
        status_code = 504
    elif exc.code in {
        "executionAgent.protocolError",
        "executionAgent.responseTooLarge",
        "openscad.invalidArtifact",
    } or exc.code.startswith("cache."):
        status_code = 502
    else:
        status_code = 503
    return ApiError(status_code, exc.code)


@router.post("/files/openscad/render")
async def render_openscad_file(
    payload: OpenScadFileRenderRequest,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
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
    if payload.expected_revision is not None:
        try:
            if context.agent_id is not None:
                result = await _agent_request(
                    context,
                    "workspace.read",
                    {
                        "path": _normalize_agent_path(payload.path),
                        "offset": 1,
                        "limit": 1,
                        "maxLineLength": 1,
                    },
                )
                revision = result.get("sha256")
                if not isinstance(revision, str) or not _SHA256_RE.fullmatch(revision):
                    raise ApiError(502, "executionAgent.protocolError")
            else:
                source = await asyncio.to_thread(
                    _read_context, context, payload.path, MAX_TEXT_PREVIEW_BYTES
                )
                if len(source) > MAX_TEXT_PREVIEW_BYTES:
                    raise ApiError(413, "file.tooLarge")
                revision = hashlib.sha256(source).hexdigest()
        except RootFileError as exc:
            raise _context_error(exc) from exc
        if revision != payload.expected_revision:
            raise ApiError(409, "file.changed")
    session = None
    if session_id is not None:
        candidate = await db.get(ChatSession, session_id)
        if candidate is None or candidate.user_id != user.id:
            raise ApiError(404, "session.notFound")
        session = candidate
    client, config = await _openscad_client(db, user, context, session)
    try:
        arguments = OpenScadRenderArguments(
            root="session" if scope is FileScope.SESSION else "workspace",
            path=payload.path,
            output=payload.output,
        )
    except ValueError as exc:
        raise ApiError(422, "validation.invalid", field="path") from exc
    root_files = context.root_files or RootFileService(session_id or UUID(int=0))
    try:
        rendered = await render_project(
            client,
            executable_path=config.binary_path,
            session_id=session_id or UUID(int=0),
            arguments=arguments,
            root_files=root_files,
        )
        data = await export_artifact(client, rendered.artifact)
        await asyncio.to_thread(
            validate_off if payload.output == "preview" else validate_binary_stl,
            data,
        )
    except ExecutionClientError as exc:
        raise _openscad_render_api_error(exc) from exc
    except RootFileError as exc:
        raise _context_error(exc) from exc
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        raise ApiError(502, "openscad.invalidArtifact") from exc
    return Response(
        content=data,
        media_type=rendered.artifact.content_type,
        headers={
            **_FILE_RESPONSE_HEADERS,
            "Content-Disposition": (
                f"{'attachment' if payload.output == 'stl' else 'inline'}; "
                f"filename*=UTF-8''{quote(rendered.artifact.filename, safe='')}"
            ),
        },
    )


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
    except RootFileError as exc:
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
    await _clear_file_grants(
        db,
        user,
        context,
        (payload.path, destination_path),
    )
    await db.commit()
    try:
        assert context.root_files is not None and context.root is not None
        renamed = await asyncio.to_thread(
            context.root_files.rename_path,
            context.root,
            payload.path,
            name,
        )
        entry = _root_entry_payload(renamed)
    except RootFileError as exc:
        raise _context_error(exc) from exc
    except OSError as exc:
        if exc.errno in {errno.EINVAL, errno.ENOSYS, errno.EOPNOTSUPP}:
            raise ApiError(501, "file.renameUnavailable") from exc
        raise
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

    raise ApiError(400, "file.workspaceNotSelected")


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
        except RootFileError as exc:
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
        except RootFileError as exc:
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
    except RootFileError as exc:
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
        except RootFileError as exc:
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
