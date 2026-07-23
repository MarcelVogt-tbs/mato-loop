from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from mato_loop_backend.api.errors import ApiError
from mato_loop_backend.db.session import get_db_session
from mato_loop_backend.error_contract import describe_error
from mato_loop_backend.memory.dreaming import (
    DreamingService,
    dreaming_availability,
)
from mato_loop_backend.memory.service import MemoryDuplicateError, MemoryService
from mato_loop_backend.models import (
    ChatSession,
    MemoryCategory,
    MemoryDreamRun,
    MemoryDreamTrigger,
    MemoryRecord,
    MemoryRetrievalLevel,
    MemorySourceType,
    UserAccount,
)
from mato_loop_backend.security import require_user
from mato_loop_backend.settings import AiSettings, MemorySettings, SettingsStore, TrustLevel

router = APIRouter(prefix="/memories", dependencies=[Depends(require_user)])


def _camel(value: str) -> str:
    first, *rest = value.split("_")
    return first + "".join(part.capitalize() for part in rest)


class ApiRequest(BaseModel):
    model_config = ConfigDict(alias_generator=_camel, populate_by_name=True, extra="forbid")


class MemoryRequest(ApiRequest):
    content: str = Field(min_length=1, max_length=20_000)
    category: MemoryCategory
    trust_level: TrustLevel
    retrieval_level: MemoryRetrievalLevel


class MemorySettingsRequest(ApiRequest):
    enabled: bool | None = None
    max_context_items: int | None = Field(default=None, ge=0, le=100)
    max_context_characters: int | None = Field(default=None, ge=0, le=200_000)
    automatic_dreaming_enabled: bool | None = None
    dreaming_instructions: str | None = Field(default=None, max_length=8_000)
    dreaming_interval_minutes: int | None = Field(default=None, ge=1, le=10_080)
    dreaming_idle_minutes: int | None = Field(default=None, ge=1, le=10_080)
    dreaming_max_batch_messages: int | None = Field(default=None, ge=2, le=500)
    dreaming_max_candidates: int | None = Field(default=None, ge=1, le=100)

    @model_validator(mode="after")
    def require_value(self) -> MemorySettingsRequest:
        if not self.model_fields_set:
            raise ValueError("at least one memory setting is required")
        if any(getattr(self, field) is None for field in self.model_fields_set):
            raise ValueError("memory settings cannot be null")
        return self


class DreamRunRequest(ApiRequest):
    session_id: UUID | None = None


class MemoryAcknowledgementRequest(ApiRequest):
    memory_ids: list[UUID] = Field(min_length=1, max_length=100)


def memory_payload(item: MemoryRecord) -> dict[str, Any]:
    message_ids = list(
        dict.fromkeys(
            [
                *([item.source_message_id] if item.source_message_id else []),
                *(source.message_id for source in item.source_messages),
            ]
        )
    )
    return {
        "id": str(item.id),
        "content": item.content,
        "category": item.category,
        "revision": item.revision,
        "sourceType": item.source_type,
        "retrievalLevel": item.retrieval_level,
        "trustLevel": item.trust_level,
        "sourceSession": (
            {"id": str(item.source_session.id), "title": item.source_session.title}
            if item.source_session
            else None
        ),
        "sourceMessageIds": [str(message_id) for message_id in message_ids],
        "sourceRunId": str(item.source_run_id or item.source_dream_run_id)
        if item.source_run_id or item.source_dream_run_id
        else None,
        "createdAt": item.created_at.isoformat() if item.created_at else None,
        "updatedAt": item.updated_at.isoformat() if item.updated_at else None,
        "seenAt": item.seen_at.isoformat() if item.seen_at else None,
    }


def dream_run_payload(item: MemoryDreamRun) -> dict[str, Any]:
    return {
        "id": str(item.id),
        "sessionId": str(item.session_id),
        "sourceSession": (
            {"id": str(item.source_session.id), "title": item.source_session.title}
            if item.source_session
            else None
        ),
        "status": item.status,
        "trigger": item.trigger,
        "sourceFromMessageId": str(item.source_from_message_id),
        "sourceToMessageId": str(item.source_to_message_id),
        "sourceMessageCount": item.source_message_count,
        "attempts": item.attempts,
        "error": describe_error(item.error).payload() if item.error else None,
        "candidates": item.candidate_count,
        "createdMemories": item.created_memory_count,
        "updatedMemories": item.updated_memory_count,
        "createdAt": item.created_at.isoformat() if item.created_at else None,
        "startedAt": item.started_at.isoformat() if item.started_at else None,
        "completedAt": item.completed_at.isoformat() if item.completed_at else None,
        "nextAttemptAt": item.next_attempt_at.isoformat() if item.next_attempt_at else None,
    }


async def settings_payload(
    db: AsyncSession, settings: MemorySettings
) -> dict[str, Any]:
    ai = await SettingsStore.load(db, AiSettings)
    automatic_enabled, automatic_reason = dreaming_availability(
        settings, ai, automatic=True
    )
    manual_enabled, manual_reason = dreaming_availability(
        settings, ai, automatic=False
    )
    return {
        "enabled": settings.enabled,
        "maxContextItems": settings.max_context_items,
        "maxContextCharacters": settings.max_context_characters,
        "automaticDreamingEnabled": settings.automatic_dreaming_enabled,
        "dreamingInstructions": settings.dreaming_instructions,
        "dreamingIntervalMinutes": settings.dreaming_interval_minutes,
        "dreamingIdleMinutes": settings.dreaming_idle_minutes,
        "dreamingMaxBatchMessages": settings.dreaming_max_batch_messages,
        "dreamingMaxCandidates": settings.dreaming_max_candidates,
        "effectiveAutomaticDreamingEnabled": automatic_enabled,
        "automaticDreamingUnavailableReason": automatic_reason,
        "manualDreamingAvailable": manual_enabled,
        "manualDreamingUnavailableReason": manual_reason,
    }


@router.get("/settings")
async def get_memory_settings(
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict[str, Any]:
    settings = await SettingsStore.load(db, MemorySettings, user_id=user.id)
    return await settings_payload(db, settings)


@router.patch("/settings")
async def put_memory_settings(
    payload: MemorySettingsRequest,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict[str, Any]:
    settings = await SettingsStore.load(
        db, MemorySettings, user_id=user.id, for_update=True
    )
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(settings, field, value)
    await SettingsStore.save(db, settings, user_id=user.id)
    await db.commit()
    return await settings_payload(db, settings)


@router.get("/dream-runs")
async def list_dream_runs(
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict[str, list[dict[str, Any]]]:
    items = list(
        (
            await db.scalars(
                select(MemoryDreamRun)
                .join(ChatSession, ChatSession.id == MemoryDreamRun.session_id)
                .where(ChatSession.user_id == user.id)
                .options(selectinload(MemoryDreamRun.source_session))
                .order_by(MemoryDreamRun.created_at.desc(), MemoryDreamRun.id.desc())
                .limit(100)
            )
        ).all()
    )
    return {"items": [dream_run_payload(item) for item in items]}


@router.get("/dream-runs/{run_id}")
async def get_dream_run(
    run_id: UUID,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict[str, Any]:
    item = await db.scalar(
        select(MemoryDreamRun)
        .join(ChatSession, ChatSession.id == MemoryDreamRun.session_id)
        .where(MemoryDreamRun.id == run_id)
        .where(ChatSession.user_id == user.id)
        .options(selectinload(MemoryDreamRun.source_session))
    )
    if item is None:
        raise ApiError(404, "memory.dreamRunNotFound")
    return dream_run_payload(item)


@router.post("/dream-runs", status_code=status.HTTP_202_ACCEPTED)
async def start_dream_runs(
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
    payload: DreamRunRequest | None = None,
) -> dict[str, Any]:
    memory_settings = await SettingsStore.load(
        db, MemorySettings, user_id=user.id
    )
    ai_settings = await SettingsStore.load(db, AiSettings)
    available, reason = dreaming_availability(
        memory_settings, ai_settings, automatic=False
    )
    if not available:
        raise ApiError(409, reason or "memory.dreamingUnavailable")
    if payload and payload.session_id is not None:
        target = await db.scalar(
            select(ChatSession).where(
                ChatSession.id == payload.session_id,
                ChatSession.user_id == user.id,
            )
        )
        if target is None:
            raise ApiError(404, "session.notFound")
        if target.memory_mode != "full":
            raise ApiError(409, "memory.sessionDisabled")
    runs = await DreamingService.enqueue(
        db,
        trigger=MemoryDreamTrigger.MANUAL,
        session_id=payload.session_id if payload else None,
        user_id=user.id,
    )
    run_ids = [item.id for item in runs]
    loaded = (
        list(
            (
                await db.scalars(
                    select(MemoryDreamRun)
                    .where(MemoryDreamRun.id.in_(run_ids))
                    .options(selectinload(MemoryDreamRun.source_session))
                    .order_by(MemoryDreamRun.created_at, MemoryDreamRun.id)
                )
            ).all()
        )
        if run_ids
        else []
    )
    return {"items": [dream_run_payload(item) for item in loaded], "queued": len(loaded)}


@router.get("")
async def list_memories(
    db: Annotated[AsyncSession, Depends(get_db_session)],
    query: str | None = Query(default=None, max_length=2_000),
    source_type: Annotated[
        MemorySourceType | None, Query(alias="sourceType")
    ] = None,
    category: MemoryCategory | None = None,
    trust_level: Annotated[TrustLevel | None, Query(alias="trustLevel")] = None,
    retrieval_level: Annotated[
        MemoryRetrievalLevel | None, Query(alias="retrievalLevel")
    ] = None,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
) -> dict[str, Any]:
    result = await MemoryService.list(
        db,
        query=query,
        source_type=source_type,
        category=category,
        trust_level=trust_level,
        retrieval_level=retrieval_level,
        offset=offset,
        limit=limit,
    )
    return {
        "items": [memory_payload(item) for item in result.items],
        "total": result.total,
        "offset": result.offset,
        "limit": result.limit,
    }


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_memory(
    payload: MemoryRequest,
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict[str, Any]:
    content = payload.content.strip()
    if not content:
        raise ApiError(422, "validation.required", field="content")
    try:
        item = await MemoryService.create_manual(
            db,
            content=content,
            category=payload.category,
            trust_level=payload.trust_level,
            retrieval_level=payload.retrieval_level,
        )
    except MemoryDuplicateError as exc:
        raise ApiError(409, "memory.duplicate") from exc
    await db.commit()
    item = await MemoryService.get(db, item.id)
    if item is None:
        raise ApiError(500, "memory.persistFailed")
    return memory_payload(item)


@router.post("/acknowledgements")
async def acknowledge_memories(
    payload: MemoryAcknowledgementRequest,
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict[str, int]:
    acknowledged = await MemoryService.mark_seen(db, payload.memory_ids)
    await db.commit()
    return {"acknowledged": acknowledged}


@router.get("/{memory_id}")
async def get_memory(
    memory_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict[str, Any]:
    item = await MemoryService.get(db, memory_id)
    if item is None:
        raise ApiError(404, "memory.notFound")
    return memory_payload(item)


@router.patch("/{memory_id}")
async def update_memory(
    memory_id: UUID,
    payload: MemoryRequest,
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict[str, Any]:
    item = await MemoryService.get(db, memory_id, for_update=True)
    if item is None:
        raise ApiError(404, "memory.notFound")
    content = payload.content.strip()
    if not content:
        raise ApiError(422, "validation.required", field="content")
    try:
        await MemoryService.update(
            db,
            item,
            content=content,
            category=payload.category,
            trust_level=payload.trust_level,
            retrieval_level=payload.retrieval_level,
        )
    except MemoryDuplicateError as exc:
        raise ApiError(409, "memory.duplicate") from exc
    await db.commit()
    refreshed = await MemoryService.get(db, item.id)
    if refreshed is None:
        raise ApiError(404, "memory.notFound")
    return memory_payload(refreshed)


@router.delete("/{memory_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_memory(
    memory_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> None:
    item = await MemoryService.get(db, memory_id)
    if item is None:
        raise ApiError(404, "memory.notFound")
    await MemoryService.delete(db, item)
    await db.commit()
