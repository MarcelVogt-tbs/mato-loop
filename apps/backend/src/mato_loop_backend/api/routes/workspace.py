from __future__ import annotations

import asyncio
import errno
import hashlib
import json
from collections.abc import Iterator
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Annotated, BinaryIO
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, File, Query, UploadFile, status
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.api.errors import ApiError
from mato_loop_backend.attachments import (
    MAX_FILE_ATTACHMENT_BYTES,
    AttachmentError,
    image_mime_type,
    validate_image,
)
from mato_loop_backend.db.session import get_db_session
from mato_loop_backend.models import ChatSession, PermissionGrant, UserAccount
from mato_loop_backend.safe_svg import MAX_SAFE_SVG_BYTES, validate_svg
from mato_loop_backend.security import require_user
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

router = APIRouter(prefix="/workspace", dependencies=[Depends(require_user)])
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


@dataclass(slots=True)
class _FileContext:
    workspace: WorkspaceService | None = None
    root_files: RootFileService | None = None
    root: FileRoot | None = None
    working_directory: str | None = None


async def _file_context(
    scope: FileScope,
    session_id: UUID | None,
    user: UserAccount,
    db: AsyncSession,
    *,
    working_directory: str | None = None,
    root_identity: str | None = None,
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
    if session is not None and root_identity is not None:
        try:
            root_files = RootFileService(
                session.id,
                working_directory=root_identity,
            )
            root_files.root_identity(FileRoot.WORKSPACE)
        except RootFileError as exc:
            raise ApiError(404, "file.fileNotFound") from exc
        return _FileContext(
            root_files=root_files,
            root=FileRoot.WORKSPACE,
            working_directory=root_identity,
        )
    if session is not None and session.working_directory:
        return _FileContext(
            root_files=RootFileService(
                session.id,
                working_directory=session.working_directory,
            ),
            root=FileRoot.WORKSPACE,
            working_directory=session.working_directory,
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
                .where(PermissionGrant.resource_type.in_(("path", "root_path")))
                .join(ChatSession, ChatSession.id == PermissionGrant.session_id)
                .where(ChatSession.user_id == user.id)
            )
        ).all()
    )
    global_workspace = WorkspaceService()
    for grant in grants:
        remove = False
        if grant.resource_type == "root_path":
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
) -> dict:
    context = await _file_context(
        scope,
        session_id,
        user,
        db,
        working_directory=working_directory,
        root_identity=root_identity,
    )
    try:
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
) -> dict:
    context = await _file_context(
        scope,
        session_id,
        user,
        db,
        working_directory=working_directory,
        for_update=session_id is not None,
    )
    try:
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
    parent: str = Query(default="."),
    scope: FileScope = FileScope.FILESYSTEM,
    session_id: UUID | None = None,
    working_directory: str | None = None,
) -> dict:
    context = await _file_context(
        scope,
        session_id,
        user,
        db,
        working_directory=working_directory,
        for_update=session_id is not None,
    )
    try:
        if context.workspace is not None:
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
    scope: FileScope = FileScope.FILESYSTEM,
    session_id: UUID | None = None,
    working_directory: str | None = None,
    root_identity: Annotated[str | None, Query(max_length=1_200)] = None,
) -> dict:
    context = await _file_context(
        scope,
        session_id,
        user,
        db,
        working_directory=working_directory,
        root_identity=root_identity,
    )
    try:
        data = await asyncio.to_thread(
            _read_context, context, path, _IMAGE_PREVIEW_BYTES
        )
    except (WorkspaceError, RootFileError) as exc:
        raise _context_error(exc) from exc

    suffix = PurePosixPath(path).suffix.casefold()
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
) -> dict:
    context = await _file_context(
        scope,
        session_id,
        user,
        db,
        working_directory=working_directory,
        for_update=session_id is not None,
    )
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
    scope: FileScope = FileScope.FILESYSTEM,
    session_id: UUID | None = None,
    working_directory: str | None = None,
    root_identity: Annotated[str | None, Query(max_length=1_200)] = None,
) -> Response:
    context = await _file_context(
        scope,
        session_id,
        user,
        db,
        working_directory=working_directory,
        root_identity=root_identity,
    )
    try:
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
    scope: FileScope = FileScope.FILESYSTEM,
    session_id: UUID | None = None,
    working_directory: str | None = None,
    root_identity: Annotated[str | None, Query(max_length=1_200)] = None,
) -> StreamingResponse:
    context = await _file_context(
        scope,
        session_id,
        user,
        db,
        working_directory=working_directory,
        root_identity=root_identity,
    )
    try:
        handle = await asyncio.to_thread(_open_context, context, path)
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
) -> dict:
    context = await _file_context(
        scope,
        session_id,
        user,
        db,
        working_directory=working_directory,
        for_update=session_id is not None,
    )
    try:
        name = validate_upload_filename(payload.name)
    except RootFileError as exc:
        raise _context_error(exc) from exc
    parent = PurePosixPath(payload.path).parent.as_posix()
    destination_path = _join_path(parent, name)
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
) -> None:
    context = await _file_context(
        scope,
        session_id,
        user,
        db,
        working_directory=working_directory,
        for_update=session_id is not None,
    )
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
