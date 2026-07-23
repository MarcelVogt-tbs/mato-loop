from __future__ import annotations

import json
import logging
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from mato_loop_backend.agent import AgentRuntime
from mato_loop_backend.api.errors import ApiError
from mato_loop_backend.attachments import (
    AttachmentService,
    AttachmentStorage,
    AttachmentStorageError,
)
from mato_loop_backend.db.session import get_db_session
from mato_loop_backend.events import run_events
from mato_loop_backend.integrations import search_available
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
from mato_loop_backend.settings import (
    AiSettings,
    MemorySettings,
    SearchSettings,
    SettingsStore,
    effective_model_trust,
)
from mato_loop_backend.settings.models import ModelAvailability, TrustLevel
from mato_loop_backend.workspace import WorkspaceError, WorkspaceService

router = APIRouter(prefix="/sessions", dependencies=[Depends(require_user)])
logger = logging.getLogger(__name__)


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
    return any(item.id == model_id and item.supports_tools for item in ai.models)


def _model_for_session(ai: AiSettings, model_id: UUID | None):
    return next((item for item in ai.models if item.id == model_id), None)


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


def session_payload(
    item: ChatSession,
    *,
    effective_memory_mode: MemoryMode | None = None,
    memory_tools_available: bool | None = None,
    memory_unavailable_reason: str | None = None,
) -> dict[str, Any]:
    payload = {
        "id": str(item.id),
        "title": item.title,
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
    if effective_memory_mode is not None:
        payload["effectiveMemoryMode"] = effective_memory_mode.value
        payload["memoryToolsAvailable"] = memory_tools_available
        payload["memoryUnavailableReason"] = memory_unavailable_reason
    return payload


async def _session_payload(db: AsyncSession, item: ChatSession) -> dict[str, Any]:
    if item.memory_mode == MemoryMode.DISABLED.value:
        return session_payload(
            item,
            effective_memory_mode=MemoryMode.DISABLED,
            memory_tools_available=False,
            memory_unavailable_reason="memory.sessionDisabled",
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
    return {
        "id": str(item.id),
        "role": item.role,
        "content": item.content,
        "modelName": item.model_name,
        "generationStatus": item.generation_status or "completed",
        "trustLevel": item.trust_level,
        "toolCallId": item.tool_call_id,
        "toolCalls": item.tool_calls,
        "reasoning": item.public_reasoning,
        "attachments": [
            {
                "id": str(attachment.id),
                "kind": attachment.kind,
                "mimeType": attachment.mime_type,
                "sizeBytes": attachment.size_bytes,
                "filename": attachment.filename,
                "width": attachment.width,
                "height": attachment.height,
                "url": (
                    f"/api/sessions/{item.session_id}/messages/{item.id}"
                    f"/attachments/{attachment.id}"
                ),
            }
            for attachment in item.attachments
        ],
        "createdAt": item.created_at.isoformat() if item.created_at else None,
    }


def run_payload(run: AgentRun) -> dict[str, Any]:
    return {
        "id": str(run.id),
        "sessionId": str(run.session_id),
        "status": run.status,
        "modelName": run.model_name,
        "error": {"code": run.error} if run.error else None,
        "createdAt": run.created_at.isoformat() if run.created_at else None,
    }


@router.get("")
async def list_sessions(
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> list[dict]:
    items = list(
        (
            await db.scalars(
                select(ChatSession).where(ChatSession.user_id == user.id).order_by(
                    ChatSession.is_pinned.desc(),
                    ChatSession.updated_at.desc(),
                )
            )
        ).all()
    )
    return [await _session_payload(db, item) for item in items]


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_session(
    payload: SessionCreateRequest,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict:
    content = payload.content.strip()
    if not content:
        raise ApiError(422, "validation.required", field="content")
    ai = await SettingsStore.load(db, AiSettings)
    model_id = payload.model_id or ai.default_model_id
    if not _model_available(ai, model_id):
        raise ApiError(400, "session.modelUnavailable")
    if payload.web_search_policy == WebSearchPolicy.REQUIRED:
        if not _model_supports_tools(ai, model_id):
            raise ApiError(409, "session.modelToolsUnavailable")
        search = await SettingsStore.load(db, SearchSettings)
        if not search_available(search):
            raise ApiError(409, "integration.searchUnavailable")
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
    if initial_model is None or initial_provider is None:
        raise ApiError(400, "session.modelUnavailable")
    item = ChatSession(
        user_id=user.id,
        title="Neue Session",
        title_generation_status=TitleGenerationStatus.PENDING.value,
        model_config_id=model_id,
        memory_mode=payload.memory_mode.value,
        trust_level=effective_model_trust(initial_provider, initial_model).value,
        web_search_policy=payload.web_search_policy.value,
        working_directory=working_directory,
        workspace_access=payload.workspace_access,
    )
    db.add(item)
    await db.flush()
    run = await AgentRuntime.start(db, item, content)
    await db.refresh(item)
    return {
        "session": await _session_payload(db, item),
        "runId": str(run.id),
        "modelName": run.model_name,
    }


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
    return {
        **(await _session_payload(db, item)),
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
            search = await SettingsStore.load(db, SearchSettings)
            if not search_available(search):
                raise ApiError(409, "integration.searchUnavailable")
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
    item = await _owned_session(db, session_id, user.id)
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
    storage_keys = list(
        (
            await db.scalars(
                select(MessageAttachment.storage_key)
                .join(Message, Message.id == MessageAttachment.message_id)
                .where(Message.session_id == session_id)
            )
        ).all()
    )
    await db.delete(item)
    await db.commit()
    await AttachmentService().delete_files(storage_keys)


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
        path = AttachmentStorage().path(attachment.storage_key)
    except AttachmentStorageError as exc:
        logger.warning("Attachment-Datei fuer vorhandene Metadaten nicht gefunden")
        raise ApiError(404, "attachment.notFound") from exc
    return FileResponse(
        path,
        media_type=attachment.mime_type,
        headers={
            "X-Content-Type-Options": "nosniff",
            "Cross-Origin-Resource-Policy": "same-origin",
            "Cache-Control": "private",
        },
    )


@router.post("/{session_id}/messages", status_code=status.HTTP_202_ACCEPTED)
async def send_message(
    session_id: UUID,
    payload: SendMessageRequest,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict[str, str]:
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
    if item.web_search_policy == WebSearchPolicy.REQUIRED:
        search = await SettingsStore.load(db, SearchSettings)
        if not search_available(search):
            raise ApiError(409, "integration.searchUnavailable")
    ai = await SettingsStore.load(db, AiSettings)
    model_id = item.model_config_id or ai.default_model_id
    if not _model_available(ai, model_id):
        raise ApiError(409, "session.modelUnavailable")
    if (
        item.web_search_policy == WebSearchPolicy.REQUIRED
        and not _model_supports_tools(ai, model_id)
    ):
        raise ApiError(409, "session.modelToolsUnavailable")
    run = await AgentRuntime.start(db, item, content)
    return {"runId": str(run.id), "modelName": run.model_name}


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
    await db.rollback()

    async def events():
        if run_status in {"completed", "failed", "cancelled"}:
            event = (
                {"type": "run.failed", "error": {"code": run_error or "agent.failed"}}
                if run_status == "failed"
                else {"type": f"run.{run_status}", "runId": str(run_id)}
            )
            yield f"event: {event['type']}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
            return
        async for event in run_events.subscribe(run_id):
            event_type = event.get("type", "message")
            yield f"event: {event_type}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
            if event_type in {"run.completed", "run.failed", "run.cancelled"}:
                break

    return StreamingResponse(events(), media_type="text/event-stream")
