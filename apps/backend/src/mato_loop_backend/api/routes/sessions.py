from __future__ import annotations

import asyncio
import base64
import binascii
import json
import logging
from datetime import datetime
from typing import Annotated, Any, Literal
from urllib.parse import urlencode
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile, status
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from mato_loop_backend.agent import AgentRuntime
from mato_loop_backend.agent.runtime import AgentRunError
from mato_loop_backend.api.errors import ApiError
from mato_loop_backend.attachments import (
    MAX_ATTACHMENTS_PER_MESSAGE,
    MAX_FILE_ATTACHMENT_BYTES,
    AttachmentError,
    AttachmentInput,
    AttachmentService,
    classify_attachment,
    image_mime_type,
    safe_filename,
)
from mato_loop_backend.chat_history import ChatHistoryError, ChatHistoryService
from mato_loop_backend.db.session import get_db_session
from mato_loop_backend.error_contract import describe_error
from mato_loop_backend.events import run_events
from mato_loop_backend.integrations import WebFetchError, canonicalize_url
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
)
from mato_loop_backend.settings.models import (
    ModelAvailability,
    ModelCapability,
)
from mato_loop_backend.workspace import (
    RootFileError,
    RootFileService,
    WorkspaceError,
    WorkspaceService,
)

router = APIRouter(prefix="/sessions", dependencies=[Depends(require_user)])
logger = logging.getLogger(__name__)
_LAST_MESSAGE_AT_UNSET = object()
_VISIBLE_MESSAGE_ROLES = ("user", "assistant")


def _encode_session_cursor(
    user_id: UUID, pinned: bool, activity_at: datetime, session_id: UUID
) -> str:
    value = json.dumps(
        [str(user_id), pinned, activity_at.isoformat(), str(session_id)],
        separators=(",", ":"),
    ).encode()
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()


def _decode_session_cursor(
    value: str, user_id: UUID
) -> tuple[bool, datetime, UUID]:
    try:
        decoded = base64.b64decode(
            value + "=" * (-len(value) % 4), altchars=b"-_", validate=True
        )
        payload = json.loads(decoded)
        if not (
            isinstance(payload, list)
            and len(payload) == 4
            and all(isinstance(item, str) for item in (payload[0], payload[2], payload[3]))
            and type(payload[1]) is bool
            and UUID(payload[0]) == user_id
        ):
            raise ValueError
        activity_at = datetime.fromisoformat(payload[2])
        if activity_at.tzinfo is None:
            raise ValueError
        return payload[1], activity_at, UUID(payload[3])
    except (binascii.Error, UnicodeDecodeError, ValueError, TypeError) as exc:
        raise ApiError(400, "validation.invalid", field="cursor") from exc


def _public_research_payload(item: Message) -> dict[str, Any] | None:
    provider_data = item.provider_data if isinstance(item.provider_data, dict) else {}
    runtime_data = provider_data.get("_matoloop")
    if not isinstance(runtime_data, dict):
        return None
    research = runtime_data.get("research")
    if not isinstance(research, dict):
        return None
    policy = research.get("policy")
    used = research.get("used")
    if policy not in {item.value for item in WebSearchPolicy} or not isinstance(used, bool):
        return None
    linked_sources = []
    raw_sources = research.get("linkedSources")
    if isinstance(raw_sources, list):
        for source in raw_sources[:100]:
            if not isinstance(source, dict):
                continue
            title = source.get("title")
            url = source.get("url")
            if not isinstance(title, str) or not title.strip() or not isinstance(url, str):
                continue
            try:
                safe_url = canonicalize_url(url)
            except WebFetchError:
                continue
            linked_sources.append(
                {"title": title.strip()[:500], "url": safe_url}
            )
    return {"policy": policy, "used": used, "linkedSources": linked_sources}


class _StoredFileAction(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    operation: Literal["write", "copy", "move"]
    root: Literal["session", "workspace"]
    root_identity: str = Field(alias="rootIdentity", min_length=1, max_length=1_000)
    path: str = Field(min_length=1, max_length=1_000)
    filename: str = Field(min_length=1, max_length=1_000)
    size_bytes: int = Field(alias="sizeBytes", ge=0)

    @model_validator(mode="after")
    def validate_paths(self) -> _StoredFileAction:
        service = RootFileService(UUID(int=0))
        if service.normalize_relative_path(self.path) != self.path or self.path == ".":
            raise ValueError("path must be a normalized file path")
        if self.filename != self.path.rsplit("/", 1)[-1]:
            raise ValueError("filename must match path")
        if self.root == "workspace" and (
            service.normalize_relative_path(self.root_identity) != self.root_identity
        ):
            raise ValueError("root identity must be normalized")
        return self


def _public_file_actions(item: Message) -> list[dict[str, Any]]:
    if item.role != "tool" or not isinstance(item.provider_data, dict):
        return []
    runtime_data = item.provider_data.get("_matoloop")
    raw_actions = runtime_data.get("fileActions") if isinstance(runtime_data, dict) else None
    if not isinstance(raw_actions, list) or len(raw_actions) > 1:
        return []
    actions = []
    for raw_action in raw_actions:
        try:
            action = _StoredFileAction.model_validate(raw_action)
        except (RootFileError, ValidationError, ValueError):
            continue
        if action.root == "session" and action.root_identity != str(item.session_id):
            continue
        scope = "session" if action.root == "session" else "filesystem"
        actions.append(
            {
                **action.model_dump(mode="json", by_alias=True),
                "downloadUrl": "/api/workspace/files/download?"
                + urlencode(
                    {
                        "path": action.path,
                        "scope": scope,
                        "session_id": str(item.session_id),
                        "root_identity": action.root_identity,
                    }
                ),
            }
        )
    return actions


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


class SessionReadRequest(BaseModel):
    through_message_id: UUID = Field(alias="throughMessageId")


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
        for index, upload in enumerate(files):
            data = await upload.read(MAX_FILE_ATTACHMENT_BYTES + 1)
            mime_type = (upload.content_type or "").split(";", 1)[0].strip().lower()
            try:
                attachments.append(classify_attachment(data, mime_type, upload.filename))
            except AttachmentError as exc:
                if exc.code == "attachment.invalidImage":
                    filename = safe_filename(upload.filename)
                    suffix = (
                        f".{filename.rsplit('.', 1)[1].casefold()}" if "." in filename else ""
                    )
                    logger.warning(
                        "Attachment image validation failed index=%d size_bytes=%d "
                        "content_type=%r detected_type=%r suffix=%r diagnostic=%r",
                        index,
                        len(data),
                        mime_type,
                        image_mime_type(data),
                        suffix,
                        exc.diagnostic,
                    )
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
    has_unread_messages: bool | None = None,
) -> dict[str, Any]:
    payload = {
        "id": str(item.id),
        "title": item.title,
        "titleGenerationStatus": item.title_generation_status,
        "pinned": bool(item.is_pinned),
        "modelId": str(item.model_config_id) if item.model_config_id else None,
        "memoryMode": item.memory_mode,
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
    if has_unread_messages is not None:
        payload["hasUnreadMessages"] = has_unread_messages
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
    has_unread_messages: bool | None = None,
) -> dict[str, Any]:
    if item.memory_mode == MemoryMode.DISABLED.value:
        return session_payload(
            item,
            effective_memory_mode=MemoryMode.DISABLED,
            memory_tools_available=False,
            memory_unavailable_reason="memory.sessionDisabled",
            last_message_at=last_message_at,
            has_unread_messages=has_unread_messages,
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
        has_unread_messages=has_unread_messages,
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
        "research": _public_research_payload(item),
        "fileActions": _public_file_actions(item),
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


def _has_unread_messages(
    item: ChatSession, last_assistant_message_at: datetime | None
) -> bool:
    return bool(
        last_assistant_message_at
        and (
            item.last_read_at is None
            or last_assistant_message_at > item.last_read_at
        )
    )


async def _session_detail_payload(db: AsyncSession, item: ChatSession) -> dict[str, Any]:
    messages = list(
        (
            await db.scalars(
                select(Message)
                .where(Message.session_id == item.id)
                .options(selectinload(Message.attachments))
                .order_by(Message.created_at, Message.id)
            )
        ).all()
    )
    active_run = await db.scalar(
        select(AgentRun)
        .where(
            AgentRun.session_id == item.id,
            AgentRun.status.in_(["queued", "running", "waiting_approval", "waiting_input"]),
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
    last_assistant_message_at = max(
        (
            message.created_at
            for message in messages
            if message.role == "assistant" and message.created_at
        ),
        default=None,
    )
    return {
        **(
            await _session_payload(
                db,
                item,
                last_message_at=last_message_at,
                has_unread_messages=_has_unread_messages(
                    item, last_assistant_message_at
                ),
            )
        ),
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


async def _latest_user_message(db: AsyncSession, session_id: UUID) -> Message:
    message = await db.scalar(
        select(Message)
        .where(Message.session_id == session_id, Message.role == "user")
        .options(selectinload(Message.attachments))
        .order_by(Message.created_at.desc(), Message.id.desc())
        .limit(1)
    )
    if message is None:
        raise RuntimeError("Persisted user message is missing after agent start")
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
    page_size: Annotated[int, Query(alias="pageSize", ge=1, le=100)] = 20,
    cursor: Annotated[str | None, Query()] = None,
) -> dict[str, Any]:
    active_run_status = (
        select(AgentRun.status)
        .where(
            AgentRun.session_id == ChatSession.id,
            AgentRun.status.in_(["queued", "running", "waiting_approval", "waiting_input"]),
        )
        .order_by(AgentRun.created_at.desc(), AgentRun.id.desc())
        .limit(1)
        .correlate(ChatSession)
        .scalar_subquery()
    )
    latest_messages = (
        select(
            Message.session_id.label("session_id"),
            func.max(Message.created_at).label("last_message_at"),
            func.max(Message.created_at)
            .filter(Message.role == "assistant")
            .label("last_assistant_message_at"),
        )
        .join(ChatSession, ChatSession.id == Message.session_id)
        .where(
            ChatSession.user_id == user.id,
            Message.role.in_(_VISIBLE_MESSAGE_ROLES),
        )
        .group_by(Message.session_id)
        .subquery()
    )
    activity_at = func.coalesce(latest_messages.c.last_message_at, ChatSession.created_at)
    statement = (
        select(
            ChatSession,
            latest_messages.c.last_message_at,
            latest_messages.c.last_assistant_message_at,
            active_run_status.label("active_run_status"),
            activity_at.label("activity_at"),
        )
        .outerjoin(latest_messages, latest_messages.c.session_id == ChatSession.id)
        .where(ChatSession.user_id == user.id)
    )
    if cursor is not None:
        cursor_pinned, cursor_activity_at, cursor_id = _decode_session_cursor(
            cursor, user.id
        )
        activity_tail = or_(
            activity_at < cursor_activity_at,
            and_(activity_at == cursor_activity_at, ChatSession.id < cursor_id),
        )
        statement = statement.where(
            or_(
                ChatSession.is_pinned.is_(False),
                and_(ChatSession.is_pinned.is_(True), activity_tail),
            )
            if cursor_pinned
            else and_(ChatSession.is_pinned.is_(False), activity_tail)
        )
    rows = (
        await db.execute(
            statement.order_by(
                ChatSession.is_pinned.desc(),
                activity_at.desc(),
                ChatSession.id.desc(),
            )
            .limit(page_size + 1)
        )
    ).all()
    page = rows[:page_size]
    items = [
        {
            **(
                await _session_payload(
                    db,
                    item,
                    last_message_at=last_message_at,
                    has_unread_messages=_has_unread_messages(
                        item, last_assistant_message_at
                    ),
                )
            ),
            "activeRunStatus": active_status,
        }
        for item, last_message_at, last_assistant_message_at, active_status, _activity_at in page
    ]
    next_cursor = None
    if len(rows) > page_size:
        item, _last_message_at, _last_assistant_message_at, _active_status, item_activity_at = page[-1]
        next_cursor = _encode_session_cursor(
            user.id, bool(item.is_pinned), item_activity_at, item.id
        )
    return {"items": items, "nextCursor": next_cursor}


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
    if not _model_available(ai, model_id):
        raise ApiError(409, "session.modelUnavailable", field="modelId")
    if not _model_supports_tools(ai, model_id):
        raise ApiError(409, "session.modelToolsUnavailable", field="modelId")
    working_directory = None
    if payload.working_directory is not None:
        await db.scalar(
            select(UserAccount.id)
            .where(UserAccount.id == user.id)
            .with_for_update()
        )
        try:
            directory = WorkspaceService().resolve(payload.working_directory)
        except WorkspaceError as exc:
            raise ApiError(400, exc.code) from exc
        if not directory.is_dir() or directory.is_symlink():
            raise ApiError(400, "session.invalidWorkingDirectory")
        working_directory = WorkspaceService().relative(directory)
    item = ChatSession(
        id=uuid4(),
        user_id=user.id,
        title="Neue Session",
        title_generation_status=TitleGenerationStatus.PENDING.value,
        model_config_id=model_id,
        memory_mode=payload.memory_mode.value,
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
        logger.exception("Session storage could not be provisioned")
        raise ApiError(500, "session.storageUnavailable") from exc
    except Exception:
        await db.rollback()
        try:
            await asyncio.to_thread(session_storage.remove_session, item.id)
        except Exception:
            logger.exception("Unpersisted session storage could not be removed")
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
        "modelAvailable": bool(model and model.supports_tools),
        "modelUnavailableReason": (
            None
            if model and model.supports_tools
            else (
                "session.modelToolsUnavailable"
                if model is not None
                else "session.modelUnavailable"
            )
        ),
        "memoryMode": memory_mode.value,
        "effectiveMemoryMode": state.mode.value,
        "memoryToolsAvailable": state.tools_enabled,
        "memoryUnavailableReason": state.unavailable_reason,
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
    return await _session_detail_payload(db, item)


@router.post("/{session_id}/read")
async def mark_session_read(
    session_id: UUID,
    payload: SessionReadRequest,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict[str, bool]:
    item = await _owned_session(db, session_id, user.id, for_update=True)
    if item is None:
        raise ApiError(404, "session.notFound")
    read_at = await db.scalar(
        select(Message.created_at).where(
            Message.id == payload.through_message_id,
            Message.session_id == session_id,
            Message.role == "assistant",
        )
    )
    if read_at is None:
        raise ApiError(400, "validation.invalid", field="throughMessageId")
    await db.execute(
        update(ChatSession)
        .where(
            ChatSession.id == session_id,
            ChatSession.last_read_at < read_at,
        )
        .values(last_read_at=read_at, updated_at=ChatSession.updated_at)
    )
    await db.commit()
    return {"read": True}


@router.patch("/{session_id}")
async def update_session(
    session_id: UUID,
    payload: SessionUpdateRequest,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict:
    fields = payload.model_fields_set
    if "working_directory" in fields:
        await db.scalar(
            select(UserAccount.id)
            .where(UserAccount.id == user.id)
            .with_for_update()
        )
    item = await _owned_session(db, session_id, user.id)
    if item is None:
        raise ApiError(404, "session.notFound")
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
                AgentRun.status.in_(["queued", "running", "waiting_approval", "waiting_input"]),
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
        if not _model_available(ai, effective_model_id):
            raise ApiError(409, "session.modelUnavailable")
        if not _model_supports_tools(ai, effective_model_id):
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
                    AgentRun.status.in_(["queued", "running", "waiting_approval", "waiting_input"]),
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
        logger.exception("Session storage could not be moved to trash")
        raise ApiError(500, "session.storageUnavailable") from exc

    async def restore_tree() -> None:
        if trashed is None:
            return
        try:
            await asyncio.to_thread(session_storage.restore_session, session_id, trashed)
        except Exception:
            logger.exception("Session storage could not be restored after database failure")

    async def purge_tree() -> None:
        try:
            await asyncio.to_thread(session_storage.purge_trashed, trashed)
        except Exception:
            logger.exception("Deleted session storage could not be removed from trash")

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


@router.post("/{session_id}/messages/{message_id}/undo")
async def undo_user_message(
    session_id: UUID,
    message_id: UUID,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict[str, Any]:
    try:
        result = await ChatHistoryService.undo_user_message(
            db,
            session_id=session_id,
            user_id=user.id,
            message_id=message_id,
        )
    except ChatHistoryError as exc:
        raise ApiError(exc.status_code, exc.code) from exc
    return {
        "sessionDeleted": result.session_deleted,
        "draft": {"content": result.content},
    }


@router.post(
    "/{session_id}/messages/{message_id}/retry",
    status_code=status.HTTP_202_ACCEPTED,
)
async def retry_failed_message(
    session_id: UUID,
    message_id: UUID,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict[str, Any]:
    try:
        result = await ChatHistoryService.retry_failed_message(
            db,
            session_id=session_id,
            user_id=user.id,
            message_id=message_id,
        )
    except ChatHistoryError as exc:
        raise ApiError(exc.status_code, exc.code) from exc
    except AgentRunError as exc:
        raise ApiError(409, exc.code, params=exc.params) from exc
    return {
        "runId": str(result.run.id),
        "modelName": result.run.model_name,
        "createdAt": result.run.created_at.isoformat() if result.run.created_at else None,
    }


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
        logger.warning("Attachment file for existing metadata was not found")
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
            AgentRun.status.in_(["queued", "running", "waiting_approval", "waiting_input"]),
        )
    )
    if active:
        raise ApiError(409, "session.runAlreadyActive")
    ai = await SettingsStore.load(db, AiSettings)
    model_id = item.model_config_id or ai.default_model_id
    if not _model_available(ai, model_id):
        raise ApiError(409, "session.modelUnavailable")
    if not _model_supports_tools(ai, model_id):
        raise ApiError(409, "session.modelToolsUnavailable")
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
        await db.scalar(
            select(Message)
            .where(Message.error_run_id == run_id)
            .options(selectinload(Message.attachments))
            .limit(1)
        )
        if run_status == "failed"
        else None
    )
    error_params = error_message.error_params if error_message is not None else None
    error_message_payload = (
        message_payload(error_message) if error_message is not None else None
    )
    await db.rollback()

    async def events():
        if run_status in {"completed", "failed", "cancelled"}:
            if run_status == "failed":
                error = describe_error(
                    run_error or "agent.failed",
                    params=error_params,
                )
                if error_message_payload is not None:
                    event = {
                        "type": "message.completed",
                        "message": error_message_payload,
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
