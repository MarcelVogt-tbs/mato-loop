from __future__ import annotations

import asyncio
import base64
import errno
import hashlib
import zipfile
from dataclasses import replace
from pathlib import PurePosixPath
from typing import Annotated, BinaryIO, Literal
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, File, Header, Query, Request, UploadFile, status
from fastapi.responses import Response, StreamingResponse
from pydantic import AliasChoices, BaseModel, ConfigDict, Field, ValidationError, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.api.errors import ApiError
from mato_loop_backend.api.workspace_service import (
    _AGENT_ERROR_CODES as _AGENT_ERROR_CODES,
)
from mato_loop_backend.api.workspace_service import (
    _MANAGER_ERROR_CODES as _MANAGER_ERROR_CODES,
)
from mato_loop_backend.api.workspace_service import (
    _SHA256_RE as _SHA256_RE,
)
from mato_loop_backend.api.workspace_service import (
    FileScope as FileScope,
)
from mato_loop_backend.api.workspace_service import (
    _agent_context as _agent_context,
)
from mato_loop_backend.api.workspace_service import (
    _agent_entry as _agent_entry,
)
from mato_loop_backend.api.workspace_service import (
    _agent_request as _agent_request,
)
from mato_loop_backend.api.workspace_service import (
    _agent_stat as _agent_stat,
)
from mato_loop_backend.api.workspace_service import (
    _clear_context_grants as _clear_context_grants,
)
from mato_loop_backend.api.workspace_service import (
    _clear_file_grants as _clear_file_grants,
)
from mato_loop_backend.api.workspace_service import (
    _context_error as _context_error,
)
from mato_loop_backend.api.workspace_service import (
    _execution_error as _execution_error,
)
from mato_loop_backend.api.workspace_service import (
    _file_context as _file_context,
)
from mato_loop_backend.api.workspace_service import (
    _FileContext as _FileContext,
)
from mato_loop_backend.api.workspace_service import (
    _is_same_or_descendant as _is_same_or_descendant,
)
from mato_loop_backend.api.workspace_service import (
    _join_path as _join_path,
)
from mato_loop_backend.api.workspace_service import (
    _list_context as _list_context,
)
from mato_loop_backend.api.workspace_service import (
    _normalize_agent_path as _normalize_agent_path,
)
from mato_loop_backend.api.workspace_service import (
    _open_context as _open_context,
)
from mato_loop_backend.api.workspace_service import (
    _read_context as _read_context,
)
from mato_loop_backend.api.workspace_service import (
    _replace_text_context as _replace_text_context,
)
from mato_loop_backend.api.workspace_service import (
    _require_agent_operations as _require_agent_operations,
)
from mato_loop_backend.api.workspace_service import (
    _root_entry_payload as _root_entry_payload,
)
from mato_loop_backend.api.workspace_service import (
    _stream_file as _stream_file,
)
from mato_loop_backend.db.session import get_db_session
from mato_loop_backend.execution import (
    EXPORT_TIMEOUT_SECONDS,
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
from mato_loop_backend.execution.paths import parent as computer_parent
from mato_loop_backend.files.archives import (
    ArchiveEntry,
    ArchivePage,
    FileArchiveError,
    create_zip_archive,
)
from mato_loop_backend.files.service import (
    MAX_EDITABLE_TEXT_BYTES,
    FileRoot,
    RootFileError,
    RootFileService,
    validate_upload_filename,
)
from mato_loop_backend.integrations.openscad.adapter import (
    OpenScadConfig,
    OpenScadRenderArguments,
    export_artifact,
    render_project,
    validate_binary_stl,
    validate_off,
)
from mato_loop_backend.media.attachments import (
    MAX_FILE_ATTACHMENT_BYTES,
    AttachmentError,
    image_mime_type,
    validate_image,
)
from mato_loop_backend.media.svg import MAX_SAFE_SVG_BYTES, validate_svg
from mato_loop_backend.models import ChatSession, ExecutionAgent, Message, UserAccount
from mato_loop_backend.security import hash_token, require_user
from mato_loop_backend.sessions.serialization import public_file_actions
from mato_loop_backend.settings import load_effective_integrations

router = APIRouter(prefix="/workspace")
_validate_svg = validate_svg

MAX_TEXT_PREVIEW_BYTES = MAX_EDITABLE_TEXT_BYTES
_IMAGE_PREVIEW_BYTES = MAX_SAFE_SVG_BYTES
_STL_PREVIEW_BYTES = MAX_FILE_ATTACHMENT_BYTES
_IMAGE_SUFFIXES = {".gif", ".jpeg", ".jpg", ".png", ".webp"}
_MARKDOWN_SUFFIXES = {".md", ".markdown"}
_AUDIO_MIME_TYPES = {
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".ogg": "audio/ogg",
    ".oga": "audio/ogg",
    ".m4a": "audio/mp4",
    ".aac": "audio/aac",
    ".flac": "audio/flac",
    ".opus": "audio/ogg",
}
_VIDEO_MIME_TYPES = {
    ".mp4": "video/mp4",
    ".webm": "video/webm",
    ".mov": "video/quicktime",
}
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


class CreateDirectoryRequest(BaseModel):
    parent: str = "."
    name: str = Field(min_length=1, max_length=255)


class GitChangedFile(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    path: str = Field(min_length=1, max_length=4096)
    status: Literal["added", "modified", "deleted"]
    additions: int | None = Field(ge=0)
    deletions: int | None = Field(ge=0)

    @field_validator("path")
    @classmethod
    def relative_path(cls, value: str) -> str:
        parts = value.split("/")
        if (
            "\\" in value or any(part in {"", ".", "..", ".git"} for part in parts)
            or any(ord(char) < 32 or 127 <= ord(char) <= 159 for char in value)
            or (len(value) > 1 and value[1] == ":")
        ):
            raise ValueError("Git paths must be normalized relative paths")
        return value


class GitChangesResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    repository: str | None = Field(max_length=4096)
    files: list[GitChangedFile] = Field(max_length=500)
    truncated: bool

    @field_validator("repository")
    @classmethod
    def absolute_repository(cls, value: str | None) -> str | None:
        if value is not None:
            try:
                return _normalize_agent_path(value)
            except ApiError as exc:
                raise ValueError("Git repository must be an absolute path") from exc
        return value


class GitFileDiffResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    repository: str = Field(min_length=1, max_length=4096)
    file: GitChangedFile
    before: str = Field(max_length=65_536)
    after: str = Field(max_length=65_536)
    reason: Literal["binary", "tooLarge", "unsupported"] | None


@router.get("/git-changes", response_model=GitChangesResponse | GitFileDiffResponse)
async def get_git_changes(
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
    session_id: Annotated[UUID, Query()],
    execution_agent_id: Annotated[UUID | None, Query()] = None,
    current_directory: Annotated[str | None, Query(max_length=4096)] = None,
    file: Annotated[str | None, Query(min_length=1, max_length=4096)] = None,
) -> GitChangesResponse | GitFileDiffResponse:
    if file is not None:
        try:
            GitChangedFile.relative_path(file)
        except ValueError as exc:
            raise ApiError(400, "file.invalidPath") from exc
    context = await _file_context(
        FileScope.FILESYSTEM, session_id, user, db,
        execution_agent_id=execution_agent_id, current_directory=current_directory,
    )
    _require_agent_operations(context, "git.changes")
    if not context.current_directory:
        raise ApiError(400, "file.workspaceNotSelected")
    arguments = {"path": _normalize_agent_path(context.current_directory)}
    if file is not None:
        arguments["file"] = file
    result = await _agent_request(context, "git.changes", arguments, timeout=60)
    try:
        if file is None:
            return GitChangesResponse.model_validate(result)
        parsed = GitFileDiffResponse.model_validate(result)
        _normalize_agent_path(parsed.repository)
        if parsed.file.path != file:
            raise ValueError("Agent returned a different Git file")
        return parsed
    except (ValidationError, ValueError, ApiError) as exc:
        raise ApiError(502, "executionAgent.protocolError") from exc


class RenameEntryRequest(BaseModel):
    path: str = Field(min_length=1, max_length=1_200)
    name: str = Field(min_length=1, max_length=255)


class UpdateFileRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(strict=True, min_length=1, max_length=1_200)
    content: str = Field(strict=True)
    expected_revision: str = Field(strict=True, pattern=r"^[0-9a-f]{64}$")


class RevisionActivationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    path: str = Field(strict=True, min_length=1, max_length=1_200)
    file_id: UUID = Field(alias="fileId")
    expected_current_revision: int = Field(
        alias="expectedCurrentRevision", strict=True, ge=1
    )
    expected_current_sha256: str | None = Field(
        default=None,
        alias="expectedCurrentSha256",
        strict=True,
        pattern=r"^[0-9a-f]{64}$",
    )


class RevisionActivationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    file_id: UUID = Field(alias="fileId")
    current_revision: int = Field(alias="currentRevision")
    current_sha256: str = Field(alias="currentSha256")


class WorkspaceUsageResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, populate_by_name=True)

    scope: Literal["user"]
    used_bytes: int = Field(alias="usedBytes", ge=0)
    quota_bytes: int | None = Field(alias="quotaBytes", gt=0)


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
    destination_current_directory: str | None = Field(default=None, max_length=4_096)


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


@router.get("/usage", response_model=WorkspaceUsageResponse)
async def get_workspace_usage(
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
    session_id: Annotated[UUID, Query()],
) -> WorkspaceUsageResponse:
    context = await _file_context(FileScope.SESSION, session_id, user, db)
    assert context.root_files is not None
    try:
        used_bytes = await asyncio.to_thread(context.root_files.user_usage)
    except RootFileError as exc:
        raise _context_error(exc) from exc
    return WorkspaceUsageResponse(
        scope="user",
        used_bytes=used_bytes,
        quota_bytes=context.root_files.user_quota_bytes,
    )


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
            "filesystem.export",
            {
                "path": normalized,
                "transfer": _transfer_spec(
                    request, context.agent_id, grant.ticket_id, grant.token
                ),
            },
            timeout=EXPORT_TIMEOUT_SECONDS,
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
        "filesystem.read",
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
    current_directory: str | None = None,
    root_identity: Annotated[str | None, Query(max_length=1_200)] = None,
    execution_agent_id: UUID | None = None,
) -> dict:
    context = await _file_context(
        scope,
        session_id,
        user,
        db,
        current_directory=current_directory,
        root_identity=root_identity,
        execution_agent_id=execution_agent_id,
    )
    try:
        if context.agent_id is not None:
            normalized = _normalize_agent_path(path)
            result = await _agent_request(
                context,
                "filesystem.list",
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
                expected_parent = computer_parent(entry["path"])
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
    current_directory: str | None = None,
    execution_agent_id: UUID | None = None,
) -> dict:
    context = await _file_context(
        scope,
        session_id,
        user,
        db,
        current_directory=current_directory,
        execution_agent_id=execution_agent_id,
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
                context, "filesystem.mkdir", {"path": path, "parents": False}
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
    current_directory: str | None = None,
    execution_agent_id: UUID | None = None,
) -> dict:
    context = await _file_context(
        scope,
        session_id,
        user,
        db,
        current_directory=current_directory,
        execution_agent_id=execution_agent_id,
        for_update=session_id is not None,
    )
    max_upload_bytes = context.instance_limits.max_upload_bytes
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
                    "filesystem.import",
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
    current_directory: str | None = None,
    root_identity: Annotated[str | None, Query(max_length=1_200)] = None,
    execution_agent_id: UUID | None = None,
) -> dict:
    context = await _file_context(
        scope,
        session_id,
        user,
        db,
        current_directory=current_directory,
        root_identity=root_identity,
        execution_agent_id=execution_agent_id,
    )
    image_preview_bytes = context.instance_limits.max_image_preview_bytes
    text_preview_bytes = context.instance_limits.max_editable_text_bytes
    suffix = PurePosixPath(path).suffix.casefold()
    file_preview_bytes = (
        min(image_preview_bytes, MAX_SAFE_SVG_BYTES)
        if suffix == ".svg"
        else image_preview_bytes
    )
    try:
        if suffix in _AUDIO_MIME_TYPES:
            if context.agent_id is not None:
                entry = await _agent_stat(context, _normalize_agent_path(path))
                if entry["kind"] != "file":
                    raise ApiError(400, "file.notAFile")
            else:
                handle = await asyncio.to_thread(_open_context, context, path)
                handle.close()
            return {"kind": "audio", "mimeType": _AUDIO_MIME_TYPES[suffix]}
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

    return await _preview_bytes(data, suffix, image_preview_bytes, text_preview_bytes)


async def _preview_bytes(
    data: bytes, suffix: str, image_preview_bytes: int, text_preview_bytes: int
) -> dict:
    if suffix in _AUDIO_MIME_TYPES:
        return {"kind": "audio", "mimeType": _AUDIO_MIME_TYPES[suffix]}
    if suffix == ".stl":
        try:
            await asyncio.to_thread(validate_binary_stl, data)
        except ValueError:
            return {"kind": "none", "reason": "unsupported"}
        return {"kind": "stl", "mimeType": "model/stl"}
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
    request: Request = None,
    scope: FileScope = FileScope.FILESYSTEM,
    session_id: UUID | None = None,
    current_directory: str | None = None,
    execution_agent_id: UUID | None = None,
) -> dict:
    context = await _file_context(
        scope,
        session_id,
        user,
        db,
        current_directory=current_directory,
        execution_agent_id=execution_agent_id,
        for_update=session_id is not None,
    )
    if len(payload.content.encode("utf-8")) > context.instance_limits.max_editable_text_bytes:
        raise ApiError(413, "file.tooLarge")
    if context.agent_id is not None:
        path = _normalize_agent_path(payload.path)
        await _clear_file_grants(db, user, context, (path,))
        await db.commit()
        result = await _agent_request(
            context,
            "filesystem.write",
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
        return {
            "revision": revision,
            "entry": await _agent_stat(context, path),
        }
    await _clear_file_grants(db, user, context, (payload.path,))
    await db.commit()

    context = await _file_context(
        scope,
        session_id,
        user,
        db,
        current_directory=current_directory,
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


@router.get("/files/revisions")
async def list_file_revisions(
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
    path: str,
    session_id: UUID,
    root_identity: Annotated[str | None, Query(max_length=1_200)] = None,
) -> dict:
    context = await _file_context(
        FileScope.SESSION, session_id, user, db, root_identity=root_identity
    )
    assert context.root_files is not None
    try:
        revisions = await asyncio.to_thread(context.root_files.list_revisions, path)
    except RootFileError as exc:
        raise _context_error(exc) from exc
    return {
        "fileId": str(revisions[0].file_id) if revisions else None,
        "items": [
            {
                "revision": item.revision,
                "createdAt": item.created_at,
                "sizeBytes": item.size_bytes,
                "sha256": item.sha256,
                "current": item.current,
            }
            for item in revisions
        ],
    }


@router.get("/files/revisions/{revision}/preview")
async def preview_file_revision(
    revision: int,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
    path: str,
    session_id: UUID,
    root_identity: Annotated[str | None, Query(max_length=1_200)] = None,
    file_id: UUID | None = None,
) -> dict:
    context = await _file_context(
        FileScope.SESSION, session_id, user, db, root_identity=root_identity
    )
    assert context.root_files is not None
    suffix = PurePosixPath(path).suffix.casefold()
    max_bytes = max(
        context.instance_limits.max_image_preview_bytes,
        context.instance_limits.max_editable_text_bytes,
    )
    if suffix == ".stl":
        max_bytes = _STL_PREVIEW_BYTES
    try:
        if file_id is not None:
            metadata = await asyncio.to_thread(context.root_files.file_revision, file_id, revision)
            suffix = PurePosixPath(metadata.path).suffix.casefold()
            if suffix == ".stl":
                max_bytes = _STL_PREVIEW_BYTES
        handle = await asyncio.to_thread(
            context.root_files.open_revision_by_id if file_id else context.root_files.open_revision,
            file_id or path, revision,
        )
        with handle:
            if suffix in _AUDIO_MIME_TYPES:
                return {"kind": "audio", "mimeType": _AUDIO_MIME_TYPES[suffix]}
            data = handle.read(max_bytes + 1)
    except RootFileError as exc:
        raise _context_error(exc) from exc
    if len(data) > max_bytes:
        return {"kind": "none", "reason": "tooLarge"}
    return await _preview_bytes(
        data, suffix, context.instance_limits.max_image_preview_bytes,
        context.instance_limits.max_editable_text_bytes,
    )


@router.get("/files/revisions/{revision}/content")
async def content_file_revision(
    revision: int,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
    path: str,
    session_id: UUID,
    root_identity: Annotated[str | None, Query(max_length=1_200)] = None,
    file_id: UUID | None = None,
) -> Response:
    context = await _file_context(
        FileScope.SESSION, session_id, user, db, root_identity=root_identity
    )
    assert context.root_files is not None
    try:
        metadata = await asyncio.to_thread(
            context.root_files.file_revision, file_id, revision
        ) if file_id else None
        suffix = PurePosixPath(metadata.path if metadata else path).suffix.casefold()
        max_bytes = (
            _STL_PREVIEW_BYTES if suffix == ".stl"
            else context.instance_limits.max_upload_bytes if suffix in _AUDIO_MIME_TYPES
            else context.instance_limits.max_image_preview_bytes
        )
        handle = await asyncio.to_thread(
            context.root_files.open_revision_by_id if file_id else context.root_files.open_revision,
            file_id or path, revision,
        )
        with handle:
            data = handle.read(max_bytes + 1)
    except RootFileError as exc:
        raise _context_error(exc) from exc
    if len(data) > max_bytes:
        raise ApiError(415, "file.previewUnavailable")
    preview = await _preview_bytes(data, suffix, max_bytes, 0)
    if preview["kind"] not in {"image", "stl", "audio"}:
        raise ApiError(415, "file.previewUnavailable")
    mime_type = preview["mimeType"]
    return Response(
        content=data, media_type=mime_type,
        headers=_SVG_RESPONSE_HEADERS if mime_type == "image/svg+xml" else _FILE_RESPONSE_HEADERS,
    )


@router.get("/files/revisions/{revision}/download")
async def download_file_revision(
    revision: int,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
    path: str,
    session_id: UUID,
    root_identity: Annotated[str | None, Query(max_length=1_200)] = None,
    file_id: UUID | None = None,
) -> StreamingResponse:
    context = await _file_context(
        FileScope.SESSION, session_id, user, db, root_identity=root_identity
    )
    assert context.root_files is not None
    try:
        handle = await asyncio.to_thread(
            context.root_files.open_revision_by_id if file_id else context.root_files.open_revision,
            file_id or path, revision,
        )
    except RootFileError as exc:
        raise _context_error(exc) from exc
    filename = PurePosixPath(path).name or "download"
    return StreamingResponse(
        _stream_file(handle),
        media_type="application/octet-stream",
        headers={
            **_FILE_RESPONSE_HEADERS,
            "Content-Disposition": (
                'attachment; filename="download"; '
                f"filename*=UTF-8''{quote(filename, safe='')}"
            ),
        },
    )


@router.post(
    "/files/revisions/{revision}/activate", response_model=RevisionActivationResponse
)
async def activate_file_revision(
    revision: int,
    payload: RevisionActivationRequest,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
    session_id: UUID,
    root_identity: Annotated[str | None, Query(max_length=1_200)] = None,
) -> RevisionActivationResponse:
    context = await _file_context(
        FileScope.SESSION, session_id, user, db, for_update=True, root_identity=root_identity
    )
    await _clear_file_grants(db, user, context, (payload.path,))
    await db.commit()
    context = await _file_context(
        FileScope.SESSION, session_id, user, db, for_update=True, root_identity=root_identity
    )
    assert context.root_files is not None
    try:
        result = await asyncio.to_thread(
            context.root_files.activate_revision,
            payload.path,
            revision,
            expected_file_id=payload.file_id,
            expected_current_revision=payload.expected_current_revision,
            expected_current_sha256=payload.expected_current_sha256,
        )
    except RootFileError as exc:
        raise _context_error(exc) from exc
    return RevisionActivationResponse(
        fileId=result.file_id,
        currentRevision=result.current_revision,
        currentSha256=result.sha256,
    )


@router.get("/files/content")
async def preview_image(
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
    path: str,
    request: Request = None,
    scope: FileScope = FileScope.FILESYSTEM,
    session_id: UUID | None = None,
    current_directory: str | None = None,
    root_identity: Annotated[str | None, Query(max_length=1_200)] = None,
    execution_agent_id: UUID | None = None,
) -> Response:
    context = await _file_context(
        scope,
        session_id,
        user,
        db,
        current_directory=current_directory,
        root_identity=root_identity,
        execution_agent_id=execution_agent_id,
    )
    suffix = PurePosixPath(path).suffix.casefold()
    image_preview_bytes = context.instance_limits.max_image_preview_bytes
    max_bytes = _STL_PREVIEW_BYTES if suffix in {".stl", ".off"} else image_preview_bytes
    if suffix in _AUDIO_MIME_TYPES or suffix in _VIDEO_MIME_TYPES:
        max_bytes = context.instance_limits.max_upload_bytes
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
        if len(data) > max_bytes:
            raise ApiError(413, "file.tooLarge")
        if suffix in _AUDIO_MIME_TYPES:
            mime_type = _AUDIO_MIME_TYPES[suffix]
        elif suffix in _VIDEO_MIME_TYPES:
            mime_type = _VIDEO_MIME_TYPES[suffix]
        elif suffix == ".stl":
            await asyncio.to_thread(validate_binary_stl, data)
            mime_type = "model/stl"
        elif suffix == ".off":
            await asyncio.to_thread(validate_off, data)
            mime_type = "model/vnd.off"
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
        current_directory = context.current_directory
    elif session is not None and session.preferred_execution_agent_id is not None:
        agent_context = await _agent_context(
            db,
            user,
            session.preferred_execution_agent_id,
            current_directory=session.current_directory,
            writable=False,
        )
        agent_id = agent_context.agent_id
        current_directory = agent_context.current_directory
    else:
        raise ApiError(409, "openscad.notConfigured")
    assert agent_id is not None

    agent = await db.scalar(
        select(ExecutionAgent).where(
            ExecutionAgent.id == agent_id,
            ExecutionAgent.user_id == user.id,
            ExecutionAgent.revoked_at.is_(None),
        )
    )
    if agent is None:
        raise ApiError(404, "executionAgent.notFound")
    current_directory = current_directory or (
        session.current_directory if session is not None else None
    )
    if current_directory is None or not current_directory.startswith("/"):
        raise ApiError(400, "file.workspaceNotSelected")

    snapshot = execution_agent_connections.snapshot(agent_id)
    required = {"integration.openscad.render", "cache.export"}
    if snapshot is None:
        raise ApiError(503, "executionAgent.unavailable")
    if not required <= snapshot:
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
            snapshot,
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
    elif exc.code == "file.accessDenied":
        status_code = 403
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
    elif exc.code == "file.unavailable":
        status_code = 500
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
    current_directory: str | None = None,
    root_identity: Annotated[str | None, Query(max_length=1_200)] = None,
    execution_agent_id: UUID | None = None,
) -> Response:
    context = await _file_context(
        scope,
        session_id,
        user,
        db,
        current_directory=current_directory,
        root_identity=root_identity,
        execution_agent_id=execution_agent_id,
        for_update=session_id is not None,
    )
    if payload.expected_revision is not None:
        try:
            if context.agent_id is not None:
                result = await _agent_request(
                    context,
                    "filesystem.read",
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


@router.get("/files/provided/{session_id}/{message_id}/{index}/{action}")
async def provided_file(
    session_id: UUID,
    message_id: UUID,
    index: int,
    action: Literal["target", "download", "content"],
    request: Request,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
):
    message = await db.scalar(
        select(Message).join(ChatSession, Message.session_id == ChatSession.id).where(
            Message.id == message_id, Message.session_id == session_id,
            ChatSession.user_id == user.id, Message.role == "tool",
        )
    )
    references = public_file_actions(message) if message is not None else []
    if index < 0 or index >= len(references) or references[index]["operation"] != "provide":
        raise ApiError(404, "file.fileNotFound")
    reference = references[index]
    scope = FileScope.SESSION if reference["root"] == "session" else FileScope.FILESYSTEM
    context = await _file_context(
        scope, session_id, user, db,
        root_identity=reference.get("rootIdentity"),
        execution_agent_id=UUID(reference["executionAgentId"]) if reference.get("executionAgentId") else None,
        current_directory=reference.get("currentDirectory"),
    )
    path = reference["path"]
    if reference.get("fileId") and not reference.get("fileRevision"):
        raise ApiError(404, "file.fileNotFound")
    if reference.get("fileId") and reference.get("fileRevision"):
        try:
            revision = await asyncio.to_thread(
                context.root_files.file_revision, UUID(reference["fileId"]), reference["fileRevision"]
            )
            path = revision.path
        except RootFileError as exc:
            raise _context_error(exc) from exc
    if action in {"target", "content"}:
        if reference["kind"] != "file":
            raise ApiError(400, "file.notAFile")
        if action == "content":
            return await preview_image(
                user, db, path, request, scope, session_id,
                current_directory=reference.get("currentDirectory"),
                root_identity=reference.get("rootIdentity"),
                execution_agent_id=context.agent_id,
            )
        return {**reference, "path": path, "filename": PurePosixPath(path).name, "sessionId": str(session_id)}
    return await download_file(
        user, db, path, request, scope, session_id,
        current_directory=reference.get("currentDirectory"),
        root_identity=reference.get("rootIdentity"),
        execution_agent_id=context.agent_id,
        archive="zip" if reference["kind"] in {"directory", "collection"} else None,
        paths=reference.get("paths"),
        root_contents=reference.get("archiveRootContents", False),
    )


@router.get("/files/download")
async def download_file(
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
    path: str,
    request: Request = None,
    scope: FileScope = FileScope.FILESYSTEM,
    session_id: UUID | None = None,
    current_directory: str | None = None,
    root_identity: Annotated[str | None, Query(max_length=1_200)] = None,
    execution_agent_id: UUID | None = None,
    archive: Literal["zip"] | None = None,
    paths: Annotated[list[str] | None, Query(min_length=1, max_length=1_000)] = None,
    root_contents: bool = False,
) -> StreamingResponse:
    if paths is not None and archive != "zip":
        raise ApiError(400, "file.invalidPath")
    context = await _file_context(
        scope,
        session_id,
        user,
        db,
        current_directory=current_directory,
        root_identity=root_identity,
        execution_agent_id=execution_agent_id,
    )
    try:
        if archive == "zip":
            if context.agent_id is not None:
                if request is None:
                    raise ApiError(500, "internal.unexpected")
                _require_agent_operations(
                    context, "filesystem.stat", "filesystem.list", "filesystem.export"
                )

                async def stat_source(source: str) -> ArchiveEntry:
                    item = await _agent_stat(context, source)
                    return ArchiveEntry(
                        item["path"], item["name"], item["kind"], item["size"]
                    )

                async def list_source(source: str, offset: int) -> ArchivePage:
                    result = await _agent_request(
                        context,
                        "filesystem.list",
                        {"path": source, "offset": offset, "limit": 1_000},
                    )
                    values = result.get("entries")
                    if not isinstance(values, list):
                        raise ApiError(502, "executionAgent.protocolError")
                    entries = tuple(
                        ArchiveEntry(
                            item["path"], item["name"], item["kind"], item["size"]
                        )
                        for value in values
                        for item in (_agent_entry(value),)
                    )
                    has_more = result.get("hasMore")
                    next_offset = result.get("nextOffset")
                    if not isinstance(has_more, bool) or (
                        next_offset is not None
                        and (isinstance(next_offset, bool) or not isinstance(next_offset, int))
                    ):
                        raise ApiError(502, "executionAgent.protocolError")
                    return ArchivePage(entries, has_more, next_offset)

                async def open_source(source: str, limit: int) -> BinaryIO:
                    handle, _, _ = await _agent_export(
                        context, source, request, max_bytes=limit
                    )
                    return handle
            else:
                assert context.root_files is not None and context.root is not None

                async def stat_source(source: str) -> ArchiveEntry:
                    kind = await asyncio.to_thread(
                        context.root_files.path_kind, context.root, source
                    )
                    size = None
                    if kind == "file":
                        resolved = await asyncio.to_thread(
                            context.root_files.resolve, context.root, source
                        )
                        size = resolved.stat(follow_symlinks=False).st_size
                    return ArchiveEntry(source, PurePosixPath(source).name, kind, size)

                async def list_source(source: str, offset: int) -> ArchivePage:
                    page = await asyncio.to_thread(
                        context.root_files.list_directory,
                        context.root,
                        source,
                        offset=offset,
                        limit=1_000,
                    )
                    return ArchivePage(
                        tuple(
                            ArchiveEntry(item.path, item.name, item.kind, item.size)
                            for item in page.entries
                        ),
                        page.has_more,
                        page.next_offset,
                    )

                async def open_source(source: str, _limit: int) -> BinaryIO:
                    return await asyncio.to_thread(
                        context.root_files.open_file, context.root, source
                    )

            archived = await create_zip_archive(
                [
                    _normalize_agent_path(path)
                    if context.agent_id is not None
                    else context.root_files.normalize_relative_path(path)
                    for path in (paths or [path])
                ],
                stat_path=stat_source,
                list_directory=list_source,
                open_file=open_source,
                file_list=paths is not None,
                root_contents=root_contents,
            )
            handle, size, filename, media_type = (
                archived.content,
                archived.size,
                archived.filename,
                "application/zip",
            )
        elif context.agent_id is not None:
            if request is None:
                raise ApiError(500, "internal.unexpected")
            handle, size, _ = await _agent_export(
                context, path, request, max_bytes=MAX_TRANSFER_BYTES
            )
            filename = PurePosixPath(path).name or "download"
            media_type = "application/octet-stream"
        else:
            handle = await asyncio.to_thread(_open_context, context, path)
            size = None
            filename = PurePosixPath(path).name or "download"
            media_type = "application/octet-stream"
    except FileArchiveError as exc:
        status_code = 413 if exc.code == "file.operationTooLarge" else 400
        raise ApiError(status_code, exc.code, field=exc.field) from exc
    except RootFileError as exc:
        raise _context_error(exc) from exc
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
        media_type=media_type,
        headers=headers,
    )


@router.patch("/entries")
async def rename_entry(
    payload: RenameEntryRequest,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
    scope: FileScope = FileScope.FILESYSTEM,
    session_id: UUID | None = None,
    current_directory: str | None = None,
    execution_agent_id: UUID | None = None,
) -> dict:
    context = await _file_context(
        scope,
        session_id,
        user,
        db,
        current_directory=current_directory,
        execution_agent_id=execution_agent_id,
        for_update=session_id is not None,
    )
    try:
        name = validate_upload_filename(payload.name)
    except RootFileError as exc:
        raise _context_error(exc) from exc
    parent = computer_parent(payload.path) if context.agent_id is not None else PurePosixPath(payload.path).parent.as_posix()
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
            "filesystem.rename",
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
    current_directory: str | None = None,
    execution_agent_id: UUID | None = None,
) -> None:
    context = await _file_context(
        scope,
        session_id,
        user,
        db,
        current_directory=current_directory,
        execution_agent_id=execution_agent_id,
        for_update=session_id is not None,
    )
    if context.agent_id is not None:
        normalized = _normalize_agent_path(path)
        await _clear_file_grants(db, user, context, (normalized,))
        await db.commit()
        result = await _agent_request(
            context,
            "filesystem.delete",
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
            "filesystem.import",
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
    current_directory: str | None,
    execution_agent_id: UUID | None,
) -> dict:
    source = await _file_context(
        scope,
        session_id,
        user,
        db,
        current_directory=current_directory,
        execution_agent_id=execution_agent_id,
        for_update=move and session_id is not None,
    )
    destination_session_id = (
        payload.destination_session_id
        if payload.destination_execution_agent_id is not None
        else payload.destination_session_id or session_id
    )
    destination = await _file_context(
        payload.destination_scope,
        destination_session_id,
        user,
        db,
        current_directory=payload.destination_current_directory,
        execution_agent_id=payload.destination_execution_agent_id,
        for_update=destination_session_id is not None,
    )
    try:
        if source.agent_id is not None:
            source_path = _normalize_agent_path(payload.path)
        elif source.root_files is not None:
            source_path = source.root_files.normalize_relative_path(payload.path)
        else:
            raise ApiError(400, "file.invalidRoot")
        if destination.agent_id is not None:
            destination_path = _normalize_agent_path(payload.destination)
        elif destination.root_files is not None:
            destination_path = destination.root_files.normalize_relative_path(
                payload.destination
            )
        else:
            raise ApiError(400, "file.invalidRoot")
    except RootFileError as exc:
        raise _context_error(exc) from exc
    same_agent = source.agent_id is not None and source.agent_id == destination.agent_id
    same_managed_root = (
        source.root_files is not None
        and destination.root_files is not None
        and source.root is not None
        and destination.root is not None
        and source.root_files.root_identity(source.root)
        == destination.root_files.root_identity(destination.root)
    )
    if move and not (same_agent or same_managed_root):
        raise ApiError(400, "file.crossBoundaryMoveUnavailable")
    if destination.agent_id is not None and not destination.agent_writable:
        raise ApiError(403, "http.forbidden")
    if move and source.agent_id is not None and not source.agent_writable:
        raise ApiError(403, "http.forbidden")
    if same_agent:
        _require_agent_operations(
            source, "filesystem.move" if move else "filesystem.copy"
        )
        _require_agent_operations(destination, "filesystem.stat")
    else:
        _require_agent_operations(source, "filesystem.stat", "filesystem.export")
        _require_agent_operations(destination, "filesystem.import", "filesystem.stat")

    if move:
        await _clear_context_grants(db, user, source, (source_path,))
    await _clear_context_grants(db, user, destination, (destination_path,))
    await db.commit()

    if same_agent:
        operation_context = source if move or source.agent_writable else replace(
            source, agent_writable=True
        )
        result = await _agent_request(
            operation_context,
            "filesystem.move" if move else "filesystem.copy",
            {
                "path": source_path,
                "destination": destination_path,
            },
        )
        if result.get("path") != destination_path:
            raise ApiError(502, "executionAgent.protocolError")
        return {"entry": await _agent_stat(destination, destination_path)}

    if same_managed_root:
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
    current_directory: str | None = None,
    execution_agent_id: UUID | None = None,
) -> dict:
    return await _copy_or_move_entry(
        payload,
        user,
        db,
        request,
        move=False,
        scope=scope,
        session_id=session_id,
        current_directory=current_directory,
        execution_agent_id=execution_agent_id,
    )


@router.post("/entries/move")
async def move_entry(
    payload: CopyMoveEntryRequest,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
    request: Request,
    scope: FileScope = FileScope.FILESYSTEM,
    session_id: UUID | None = None,
    current_directory: str | None = None,
    execution_agent_id: UUID | None = None,
) -> dict:
    return await _copy_or_move_entry(
        payload,
        user,
        db,
        request,
        move=True,
        scope=scope,
        session_id=session_id,
        current_directory=current_directory,
        execution_agent_id=execution_agent_id,
    )
