from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from typing import Annotated, Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile, status
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from mato_loop_backend.agent import AgentRuntime
from mato_loop_backend.api.errors import ApiError
from mato_loop_backend.attachments import (
    MAX_ATTACHMENTS_PER_MESSAGE,
    MAX_FILE_ATTACHMENT_BYTES,
    AttachmentError,
    AttachmentInput,
    AttachmentService,
    classify_attachment,
    safe_filename,
)
from mato_loop_backend.db.session import get_db_session
from mato_loop_backend.error_contract import describe_error
from mato_loop_backend.events import run_events
from mato_loop_backend.memory import effective_memory
from mato_loop_backend.models import (
    AgentRun,
    ChatSession,
    MemoryMode,
    Message,
    MessageAttachment,
    TitleGenerationStatus,
    UserAccount,
    WebSearchPolicy,
)
from mato_loop_backend.security import require_user
from mato_loop_backend.session_storage import SessionStorage, SessionStorageError
from mato_loop_backend.settings import (
    AiSettings,
    MemorySettings,
    SettingsStore,
    effective_model_trust,
)
from mato_loop_backend.settings.models import (
    ModelAvailability,
    ModelCapability,
    TrustLevel,
)
from mato_loop_backend.workspace import WorkspaceError, WorkspaceService

router = APIRouter(prefix="/sessions", dependencies=[Depends(require_user)])
logger = logging.getLogger(__name__)
_LAST_MESSAGE_AT_UNSET = object()
_VISIBLE_MESSAGE_ROLES = ("user", "assistant")


def _model_available(ai: AiSettings, model_id: UUID | None) -> bool:
    if not ai.enabled or model_id is None:
        return False
    model = next(
        (
            item
            for item in ai.models
            if item.id == model_id
            and item.enabled
            and item.availability != ModelAvailability.MISSING
            and ModelCapability.CHAT in item.capabilities
        ),
        None,
    )
    if model is None:
        return False
    provider = next(
        (item for item in ai.providers if item.id == model.provider_id and item.enabled), None
    )
    if provider is None:
        return False
    return provider.adapter_type != "openai-codex" or provider.credential_id is not None


def _model_supports_tools(ai: AiSettings, model_id: UUID | None) -> bool:
    return any(
        item.id == model_id
        and ModelCapability.CHAT in item.capabilities
        and item.supports_tools
        for item in ai.models
    )


def _model_for_session(ai: AiSettings, model_id: UUID | None):
    return next(
        (
            item
            for item in ai.models
            if item.id == model_id and ModelCapability.CHAT in item.capabilities
        ),
        None,
    )


def _provider_for_session(ai: AiSettings, model):
    if model is None:
        return None
    return next((item for item in ai.providers if item.id == model.provider_id), None)


class SessionCreateRequest(BaseModel):
    content: str = Field(min_length=1, max_length=200_000)
    model_id: UUID | None = None
    memory_mode: MemoryMode = MemoryMode.FULL
    web_search_policy: WebSearchPolicy = WebSearchPolicy.AVAILABLE
    working_directory: str | None = None
    workspace_access: str = Field(default="read-only", pattern="^(read-only|read-write)$")


class SessionUpdateRequest(BaseModel):
    title: str | None = Field(default=None, max_length=240)
    pinned: bool | None = None
    model_id: UUID | None = None
    memory_mode: MemoryMode | None = None
    web_search_policy: WebSearchPolicy | None = None
    working_directory: str | None = None
    workspace_access: str | None = Field(default=None, pattern="^(read-only|read-write)$")


class SendMessageRequest(BaseModel):
    content: str = Field(min_length=1, max_length=200_000)


def _multipart_payload(value: str, model: type[BaseModel]) -> BaseModel:
    if len(value) > 1_000_000:
        raise ApiError(413, "validation.tooLong", field="payload")
    try:
        return model.model_validate_json(value)
    except ValidationError as exc:
        error = exc.errors(include_url=False)[0] if exc.errors() else {}
        field = next(
            (part for part in reversed(error.get("loc") or ()) if isinstance(part, str)),
            "payload",
        )
        raise ApiError(422, "validation.invalid", field=field) from exc


async def _attachment_uploads(files: list[UploadFile]) -> list[AttachmentInput]:
    if not files:
        raise ApiError(422, "validation.required", field="attachments")
    attachments = []
    try:
        if len(files) > MAX_ATTACHMENTS_PER_MESSAGE:
            raise ApiError(
                422,
                "validation.tooManyItems",
                field="attachments",
                params={"maxItems": MAX_ATTACHMENTS_PER_MESSAGE},
            )
        for upload in files:
            data = await upload.read(MAX_FILE_ATTACHMENT_BYTES + 1)
            mime_type = (upload.content_type or "").split(";", 1)[0].strip().lower()
            try:
                attachments.append(classify_attachment(data, mime_type, upload.filename))
            except AttachmentError as exc:
                raise ApiError(
                    413 if exc.code == "attachment.tooLarge" else 400, exc.code
                ) from exc
    finally:
        for upload in files:
            await upload.close()
    return attachments


def session_payload(
    item: ChatSession,
    *,
    effective_memory_mode: MemoryMode | None = None,
    memory_tools_available: bool | None = None,
    memory_unavailable_reason: str | None = None,
    last_message_at: datetime | None | object = _LAST_MESSAGE_AT_UNSET,
) -> dict[str, Any]:
    payload = {
        "id": str(item.id),
        "title": item.title,
        "titleGenerationStatus": item.title_generation_status,
        "pinned": bool(item.is_pinned),
        "modelId": str(item.model_config_id) if item.model_config_id else None,
        "memoryMode": item.memory_mode,
        "trustLevel": item.trust_level or TrustLevel.PUBLIC.value,
        "webSearchPolicy": item.web_search_policy,
        "workingDirectory": item.working_directory,
        "workspaceAccess": item.workspace_access,
        "createdAt": item.created_at.isoformat() if item.created_at else None,
        "updatedAt": item.updated_at.isoformat() if item.updated_at else None,
    }
    if last_message_at is not _LAST_MESSAGE_AT_UNSET:
        payload["lastMessageAt"] = (
            last_message_at.isoformat() if isinstance(last_message_at, datetime) else None
        )
    if effective_memory_mode is not None:
        payload["effectiveMemoryMode"] = effective_memory_mode.value
        payload["memoryToolsAvailable"] = memory_tools_available
        payload["memoryUnavailableReason"] = memory_unavailable_reason
    return payload


async def _session_payload(
    db: AsyncSession,
    item: ChatSession,
    *,
    last_message_at: datetime | None | object = _LAST_MESSAGE_AT_UNSET,
) -> dict[str, Any]:
    if item.memory_mode == MemoryMode.DISABLED.value:
        return session_payload(
            item,
            effective_memory_mode=MemoryMode.DISABLED,
            memory_tools_available=False,
            memory_unavailable_reason="memory.sessionDisabled",
            last_message_at=last_message_at,
        )
    ai = await SettingsStore.load(db, AiSettings)
    memory = await SettingsStore.load(db, MemorySettings, user_id=item.user_id)
    model_id = item.model_config_id or ai.default_model_id
    model = _model_for_session(ai, model_id) if _model_available(ai, model_id) else None
    state = effective_memory(item, memory, _provider_for_session(ai, model), model)
    return session_payload(
        item,
        effective_memory_mode=state.mode,
        memory_tools_available=state.tools_enabled,
        memory_unavailable_reason=state.unavailable_reason,
        last_message_at=last_message_at,
    )


async def _owned_session(
    db: AsyncSession,
    session_id: UUID,
    user_id: UUID,
    *,
    for_update: bool = False,
) -> ChatSession | None:
    if not for_update:
        item = await db.get(ChatSession, session_id)
        return item if item is not None and item.user_id == user_id else None
    statement = select(ChatSession).where(
        ChatSession.id == session_id, ChatSession.user_id == user_id
    )
    if for_update:
        statement = statement.with_for_update()
    return await db.scalar(statement)


def message_payload(item: Message) -> dict[str, Any]:
    error = (
        describe_error(item.error_code, params=item.error_params)
        if item.error_code
        else None
    )
    return {
        "id": str(item.id),
        "role": item.role,
        "kind": "runtime_error" if error else "message",
        "content": error.message if error else item.content,
        "modelName": item.model_name,
        "generationStatus": item.generation_status or "completed",
        "trustLevel": item.trust_level,
        "toolCallId": item.tool_call_id,
        "toolCalls": item.tool_calls,
        "reasoning": item.public_reasoning,
        "error": error.payload() if error else None,
        "attachments": [
            {
                "id": str(attachment.id),
                "kind": attachment.kind,
                "category": attachment.category,
                "processingStatus": attachment.processing_status,
                "mimeType": attachment.mime_type,
                "sizeBytes": attachment.size_bytes,
                "filename": attachment.filename,
                "width": attachment.width,
                "height": attachment.height,
                "durationMs": attachment.duration_ms,
                "metadata": attachment.metadata_json or {},
                "url": (
                    f"/api/sessions/{item.session_id}/messages/{item.id}"
                    f"/attachments/{attachment.id}"
                ),
            }
            for attachment in item.attachments
        ],
        "createdAt": item.created_at.isoformat() if item.created_at else None,
    }


async def _latest_user_message(db: AsyncSession, session_id: UUID) -> Message:
    message = await db.scalar(
        select(Message)
        .where(Message.session_id == session_id, Message.role == "user")
        .options(selectinload(Message.attachments))
        .order_by(Message.created_at.desc(), Message.id.desc())
        .limit(1)
    )
    if message is None:
        raise RuntimeError("Persistierte User-Nachricht fehlt nach Agent-Start")
    return message


def run_payload(run: AgentRun) -> dict[str, Any]:
    return {
        "id": str(run.id),
        "sessionId": str(run.session_id),
        "status": run.status,
        "modelName": run.model_name,
        "error": describe_error(run.error).payload() if run.error else None,
        "createdAt": run.created_at.isoformat() if run.created_at else None,
    }


@router.get("")
async def list_sessions(
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> list[dict]:
    latest_messages = (
        select(
            Message.session_id.label("session_id"),
            func.max(Message.created_at).label("last_message_at"),
        )
        .join(ChatSession, ChatSession.id == Message.session_id)
        .where(
            ChatSession.user_id == user.id,
            Message.role.in_(_VISIBLE_MESSAGE_ROLES),
        )
        .group_by(Message.session_id)
        .subquery()
    )
    rows = (
        await db.execute(
            select(ChatSession, latest_messages.c.last_message_at)
            .outerjoin(latest_messages, latest_messages.c.session_id == ChatSession.id)
            .where(ChatSession.user_id == user.id)
            .order_by(
                ChatSession.is_pinned.desc(),
                func.coalesce(
                    latest_messages.c.last_message_at, ChatSession.created_at
                ).desc(),
                ChatSession.id.desc(),
            )
        )
    ).all()
    return [
        await _session_payload(db, item, last_message_at=last_message_at)
        for item, last_message_at in rows
    ]


async def _create_session(
    payload: SessionCreateRequest,
    user: UserAccount,
    db: AsyncSession,
    attachments: list[AttachmentInput] | None = None,
) -> dict:
    content = payload.content.strip()
    if not content:
        raise ApiError(422, "validation.required", field="content")
    ai = await SettingsStore.load(db, AiSettings)
    model_id = payload.model_id or ai.default_model_id
    working_directory = None
    if payload.working_directory is not None:
        try:
            directory = WorkspaceService().resolve(payload.working_directory)
        except WorkspaceError as exc:
            raise ApiError(400, exc.code) from exc
        if not directory.is_dir() or directory.is_symlink():
            raise ApiError(400, "session.invalidWorkingDirectory")
        working_directory = WorkspaceService().relative(directory)
    initial_model = _model_for_session(ai, model_id)
    initial_provider = _provider_for_session(ai, initial_model)
    item = ChatSession(
        id=uuid4(),
        user_id=user.id,
        title="Neue Session",
        title_generation_status=TitleGenerationStatus.PENDING.value,
        model_config_id=model_id,
        memory_mode=payload.memory_mode.value,
        trust_level=(
            effective_model_trust(initial_provider, initial_model).value
            if _model_available(ai, model_id) and initial_model and initial_provider
            else TrustLevel.PUBLIC.value
        ),
        web_search_policy=payload.web_search_policy.value,
        working_directory=working_directory,
        workspace_access=payload.workspace_access,
    )
    db.add(item)
    await db.flush()
    session_storage = SessionStorage()
    try:
        await asyncio.to_thread(session_storage.provision, item.id)
        run = (
            await AgentRuntime.start(db, item, content, attachments=attachments)
            if attachments
            else await AgentRuntime.start(db, item, content)
        )
    except (OSError, SessionStorageError) as exc:
        await db.rollback()
        logger.exception("Session-Storage konnte nicht provisioniert werden")
        raise ApiError(500, "session.storageUnavailable") from exc
    except Exception:
        await db.rollback()
        try:
            await asyncio.to_thread(session_storage.remove_session, item.id)
        except Exception:
            logger.exception("Nicht persistierter Session-Storage konnte nicht entfernt werden")
        raise
    await db.refresh(item)
    user_message = await _latest_user_message(db, item.id)
    return {
        "session": await _session_payload(
            db, item, last_message_at=user_message.created_at
        ),
        "message": message_payload(user_message),
        "runId": str(run.id),
        "modelName": run.model_name,
    }


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_session(
    payload: SessionCreateRequest,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict:
    return await _create_session(payload, user, db)


@router.post("/with-attachments", status_code=status.HTTP_201_CREATED)
async def create_session_with_attachments(
    payload: Annotated[str, Form()],
    attachments: Annotated[list[UploadFile], File()],
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict:
    request = _multipart_payload(payload, SessionCreateRequest)
    assert isinstance(request, SessionCreateRequest)
    return await _create_session(request, user, db, await _attachment_uploads(attachments))


@router.get("/defaults")
async def session_defaults(
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
    model_id: Annotated[UUID | None, Query(alias="modelId")] = None,
    memory_mode: Annotated[MemoryMode, Query(alias="memoryMode")] = MemoryMode.FULL,
) -> dict:
    ai = await SettingsStore.load(db, AiSettings)
    memory = await SettingsStore.load(db, MemorySettings, user_id=user.id)
    selected_model_id = model_id or ai.default_model_id
    model = (
        _model_for_session(ai, selected_model_id)
        if _model_available(ai, selected_model_id)
        else None
    )
    default_session = ChatSession(user_id=user.id, memory_mode=memory_mode.value)
    state = effective_memory(
        default_session, memory, _provider_for_session(ai, model), model
    )
    return {
        "modelId": str(selected_model_id) if selected_model_id else None,
        "memoryMode": memory_mode.value,
        "effectiveMemoryMode": state.mode.value,
        "memoryToolsAvailable": state.tools_enabled,
        "memoryUnavailableReason": state.unavailable_reason,
        "trustLevel": (
            effective_model_trust(_provider_for_session(ai, model), model).value
            if model and _provider_for_session(ai, model)
            else TrustLevel.PUBLIC.value
        ),
        "webSearchPolicy": WebSearchPolicy.AVAILABLE.value,
        "workingDirectory": None,
        "workspaceAccess": "read-only",
    }


@router.get("/{session_id}")
async def get_session(
    session_id: UUID,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict:
    item = await _owned_session(db, session_id, user.id)
    if item is None:
        raise ApiError(404, "session.notFound")
    messages = list(
        (
            await db.scalars(
                select(Message)
                .where(Message.session_id == session_id)
                .options(selectinload(Message.attachments))
                .order_by(Message.created_at, Message.id)
            )
        ).all()
    )
    active_run = await db.scalar(
        select(AgentRun)
        .where(
            AgentRun.session_id == session_id,
            AgentRun.status.in_(["queued", "running", "waiting_approval"]),
        )
        .order_by(AgentRun.created_at.desc())
    )
    last_message_at = max(
        (
            message.created_at
            for message in messages
            if message.role in _VISIBLE_MESSAGE_ROLES and message.created_at
        ),
        default=None,
    )
    return {
        **(await _session_payload(db, item, last_message_at=last_message_at)),
        "messages": [message_payload(message) for message in messages],
        "activeRun": (
            {
                "id": str(active_run.id),
                "status": active_run.status,
                "modelName": active_run.model_name,
                "createdAt": active_run.created_at.isoformat() if active_run.created_at else None,
            }
            if active_run
            else None
        ),
    }


@router.patch("/{session_id}")
async def update_session(
    session_id: UUID,
    payload: SessionUpdateRequest,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict:
    item = await _owned_session(db, session_id, user.id)
    if item is None:
        raise ApiError(404, "session.notFound")
    fields = payload.model_fields_set
    requested_search_policy = (
        payload.web_search_policy.value
        if "web_search_policy" in fields and payload.web_search_policy is not None
        else item.web_search_policy
    )
    policy_fields = {
        "memory_mode",
        "model_id",
        "web_search_policy",
        "working_directory",
        "workspace_access",
    }
    if fields & policy_fields:
        active = await db.scalar(
            select(AgentRun.id).where(
                AgentRun.session_id == session_id,
                AgentRun.status.in_(["queued", "running", "waiting_approval"]),
            )
        )
        if active is not None:
            raise ApiError(409, "session.runAlreadyActive")
    if "title" in fields and payload.title is not None:
        item.title = payload.title
        item.title_generation_status = TitleGenerationStatus.SKIPPED.value
    if "pinned" in fields and payload.pinned is not None:
        item.is_pinned = payload.pinned
    if "model_id" in fields:
        ai = await SettingsStore.load(db, AiSettings)
        if payload.model_id is not None:
            if not _model_available(ai, payload.model_id):
                raise ApiError(400, "session.modelUnavailable")
        effective_model_id = payload.model_id or ai.default_model_id
        if (
            requested_search_policy == WebSearchPolicy.REQUIRED
            and not _model_supports_tools(ai, effective_model_id)
        ):
            raise ApiError(409, "session.modelToolsUnavailable")
        item.model_config_id = payload.model_id
    if "memory_mode" in fields and payload.memory_mode is not None:
        item.memory_mode = payload.memory_mode.value
    if "web_search_policy" in fields and payload.web_search_policy is not None:
        if payload.web_search_policy == WebSearchPolicy.REQUIRED:
            ai = await SettingsStore.load(db, AiSettings)
            model_id = item.model_config_id or ai.default_model_id
            if not _model_supports_tools(ai, model_id):
                raise ApiError(409, "session.modelToolsUnavailable")
        item.web_search_policy = payload.web_search_policy.value
    if "working_directory" in fields:
        if payload.working_directory is None:
            item.working_directory = None
        else:
            try:
                directory = WorkspaceService().resolve(payload.working_directory)
            except WorkspaceError as exc:
                raise ApiError(400, exc.code) from exc
            if not directory.is_dir() or directory.is_symlink():
                raise ApiError(400, "session.invalidWorkingDirectory")
            item.working_directory = WorkspaceService().relative(directory)
    if "workspace_access" in fields and payload.workspace_access is not None:
        item.workspace_access = payload.workspace_access
    await db.flush()
    await db.refresh(item)
    await db.commit()
    return await _session_payload(db, item)


@router.delete("/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_session(
    session_id: UUID,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> None:
    item = await _owned_session(db, session_id, user.id, for_update=True)
    if item is None:
        raise ApiError(404, "session.notFound")
    active_runs = list(
        (
            await db.scalars(
                select(AgentRun).where(
                    AgentRun.session_id == session_id,
                    AgentRun.status.in_(["queued", "running", "waiting_approval"]),
                )
            )
        ).all()
    )
    for run in active_runs:
        await AgentRuntime.cancel_and_wait(run.id)
    session_storage = SessionStorage()
    try:
        trashed = await asyncio.to_thread(session_storage.trash_session, session_id)
    except (OSError, SessionStorageError) as exc:
        logger.exception("Session-Storage konnte nicht in den Trash verschoben werden")
        raise ApiError(500, "session.storageUnavailable") from exc

    async def restore_tree() -> None:
        if trashed is None:
            return
        try:
            await asyncio.to_thread(session_storage.restore_session, session_id, trashed)
        except Exception:
            logger.exception("Session-Storage konnte nach DB-Fehler nicht wiederhergestellt werden")

    async def purge_tree() -> None:
        try:
            await asyncio.to_thread(session_storage.purge_trashed, trashed)
        except Exception:
            logger.exception("Geloeschter Session-Storage konnte nicht aus dem Trash entfernt werden")

    commit_task: asyncio.Task[None] | None = None
    try:
        await db.delete(item)
        commit_task = asyncio.create_task(db.commit())
        await asyncio.shield(commit_task)
    except asyncio.CancelledError:
        if commit_task is None:
            await db.rollback()
            await restore_tree()
            raise
        try:
            await commit_task
        except BaseException:
            await db.rollback()
            await restore_tree()
        else:
            await purge_tree()
        raise
    except BaseException:
        await db.rollback()
        await restore_tree()
        raise
    await purge_tree()


@router.get(
    "/{session_id}/messages/{message_id}/attachments/{attachment_id}",
    response_class=FileResponse,
)
async def download_attachment(
    session_id: UUID,
    message_id: UUID,
    attachment_id: UUID,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> FileResponse:
    attachment = await db.scalar(
        select(MessageAttachment)
        .join(Message, Message.id == MessageAttachment.message_id)
        .where(
            MessageAttachment.id == attachment_id,
            Message.id == message_id,
            Message.session_id == session_id,
            Message.chat_session.has(ChatSession.user_id == user.id),
        )
    )
    if attachment is None:
        raise ApiError(404, "attachment.notFound")
    try:
        path = await AttachmentService().verified_path(attachment, session_id)
    except AttachmentError as exc:
        logger.warning("Attachment-Datei fuer vorhandene Metadaten nicht gefunden")
        raise ApiError(404, "attachment.notFound") from exc
    filename = safe_filename(attachment.filename)
    inline = attachment.kind in {"image", "audio"}
    return FileResponse(
        path,
        media_type=attachment.mime_type,
        filename=filename,
        content_disposition_type="inline" if inline else "attachment",
        headers={
            "X-Content-Type-Options": "nosniff",
            "Cross-Origin-Resource-Policy": "same-origin",
            "Cache-Control": "private, no-store",
        },
    )


async def _send_message(
    session_id: UUID,
    payload: SendMessageRequest,
    user: UserAccount,
    db: AsyncSession,
    attachments: list[AttachmentInput] | None = None,
) -> dict[str, Any]:
    item = await _owned_session(db, session_id, user.id, for_update=True)
    if item is None:
        raise ApiError(404, "session.notFound")
    content = payload.content.strip()
    if not content:
        raise ApiError(422, "validation.required", field="content")
    active = await db.scalar(
        select(AgentRun).where(
            AgentRun.session_id == session_id,
            AgentRun.status.in_(["queued", "running", "waiting_approval"]),
        )
    )
    if active:
        raise ApiError(409, "session.runAlreadyActive")
    run = (
        await AgentRuntime.start(db, item, content, attachments=attachments)
        if attachments
        else await AgentRuntime.start(db, item, content)
    )
    user_message = await _latest_user_message(db, item.id)
    return {
        "runId": str(run.id),
        "modelName": run.model_name,
        "message": message_payload(user_message),
    }


@router.post("/{session_id}/messages", status_code=status.HTTP_202_ACCEPTED)
async def send_message(
    session_id: UUID,
    payload: SendMessageRequest,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict[str, Any]:
    return await _send_message(session_id, payload, user, db)


@router.post("/{session_id}/messages/with-attachments", status_code=status.HTTP_202_ACCEPTED)
async def send_message_with_attachments(
    session_id: UUID,
    payload: Annotated[str, Form()],
    attachments: Annotated[list[UploadFile], File()],
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict[str, Any]:
    request = _multipart_payload(payload, SendMessageRequest)
    assert isinstance(request, SendMessageRequest)
    return await _send_message(
        session_id, request, user, db, await _attachment_uploads(attachments)
    )


@router.get("/{session_id}/runs/{run_id}")
async def get_run(
    session_id: UUID,
    run_id: UUID,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict:
    if await _owned_session(db, session_id, user.id) is None:
        raise ApiError(404, "session.runNotFound")
    run = await db.get(AgentRun, run_id)
    if run is None or run.session_id != session_id:
        raise ApiError(404, "session.runNotFound")
    return run_payload(run)


@router.post("/{session_id}/runs/{run_id}/cancel")
async def cancel_run(
    session_id: UUID,
    run_id: UUID,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict:
    if await _owned_session(db, session_id, user.id) is None:
        raise ApiError(404, "session.runNotFound")
    run = await db.get(AgentRun, run_id)
    if run is None or run.session_id != session_id:
        raise ApiError(404, "session.runNotFound")
    if run.status in {"completed", "failed", "cancelled"}:
        return run_payload(run)

    await AgentRuntime.cancel_and_wait(run_id)
    await db.refresh(run)
    if run.status not in {"completed", "failed", "cancelled"}:
        raise ApiError(409, "session.runInactive")
    return run_payload(run)


@router.get("/{session_id}/runs/{run_id}/events")
async def stream_run_events(
    session_id: UUID,
    run_id: UUID,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> StreamingResponse:
    if await _owned_session(db, session_id, user.id) is None:
        raise ApiError(404, "session.runNotFound")
    run = await db.get(AgentRun, run_id)
    if run is None or run.session_id != session_id:
        raise ApiError(404, "session.runNotFound")
    run_status = run.status
    run_error = run.error
    error_message = (
        await db.scalar(select(Message).where(Message.error_run_id == run_id).limit(1))
        if run_status == "failed"
        else None
    )
    await db.rollback()

    async def events():
        if run_status in {"completed", "failed", "cancelled"}:
            if run_status == "failed":
                error = describe_error(
                    run_error or "agent.failed",
                    params=error_message.error_params if error_message is not None else None,
                )
                if error_message is not None:
                    event = {
                        "type": "message.completed",
                        "message": message_payload(error_message),
                    }
                    yield f"event: message.completed\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
                event = {"type": "run.failed", "error": error.payload()}
            else:
                event = {"type": f"run.{run_status}", "runId": str(run_id)}
            yield f"event: {event['type']}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
            return
        async for event in run_events.subscribe(run_id):
            event_type = event.get("type", "message")
            yield f"event: {event_type}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
            if event_type in {"run.completed", "run.failed", "run.cancelled"}:
                break

    return StreamingResponse(events(), media_type="text/event-stream")
