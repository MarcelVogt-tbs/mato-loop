from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import load_only, selectinload

from mato_loop_backend.api.errors import ApiError
from mato_loop_backend.automations import (
    ACTIVE_STATUSES,
    AutomationError,
    AutomationService,
    runtime_snapshot,
)
from mato_loop_backend.db.session import get_db_session
from mato_loop_backend.error_contract import describe_error
from mato_loop_backend.message_content import canonical_assistant_content
from mato_loop_backend.models import (
    AgentRun,
    Automation,
    AutomationRun,
    ChatSession,
    Message,
    UserAccount,
)
from mato_loop_backend.security import require_user
from mato_loop_backend.sessions.archive import active_session_clause, is_session_archived

router = APIRouter(prefix="/automations", dependencies=[Depends(require_user)])


class ScheduleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, populate_by_name=True)

    cron: str | None = Field(default=None, min_length=1, max_length=255)
    timezone: str | None = Field(default=None, min_length=1, max_length=100)
    run_at: datetime | None = Field(default=None, alias="runAt", strict=False)
    valid_until: datetime | None = Field(default=None, alias="validUntil", strict=False)
    enabled: bool = True

    @field_validator("cron", "timezone")
    @classmethod
    def normalize(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not (cleaned := value.strip()):
            raise ValueError("schedule value must not be blank")
        return cleaned

    @field_validator("run_at", "valid_until")
    @classmethod
    def schedule_datetime_is_aware(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("schedule datetime must include a timezone offset")
        return value

    @model_validator(mode="after")
    def schedule_kind_is_valid(self) -> ScheduleRequest:
        if self.run_at is None:
            if self.cron is None or self.timezone is None:
                raise ValueError("cron and timezone are required together")
        elif (
            self.cron is not None
            or self.timezone is not None
            or self.valid_until is not None
        ):
            raise ValueError("runAt cannot be combined with recurrence fields")
        return self


class AutomationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, populate_by_name=True)

    name: str = Field(min_length=1, max_length=240)
    task: str = Field(min_length=1, max_length=40_000)
    session_id: UUID | None = Field(default=None, alias="sessionId", strict=False)
    enabled: bool = True
    triggers: list[Literal["agent_response", "agent_error"]] = Field(default_factory=list, max_length=2)
    schedules: list[ScheduleRequest] = Field(max_length=50)
    expected_revision: int | None = Field(default=None, alias="expectedRevision", ge=1)

    @field_validator("name", "task")
    @classmethod
    def normalize(cls, value: str) -> str:
        return value.strip()

class AutomationRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, populate_by_name=True)

    expected_revision: int = Field(alias="expectedRevision", ge=1)


class AutomationEffectiveState(BaseModel):
    enabled: bool
    active_run_status: str | None = Field(alias="activeRunStatus")
    next_run_at: datetime | None = Field(alias="nextRunAt")


class AutomationStatusResponse(BaseModel):
    id: UUID
    revision: int
    enabled: bool
    triggers: list[str] = Field(default_factory=list)
    schedule_count: int = Field(alias="scheduleCount")
    effective_state: AutomationEffectiveState = Field(alias="effectiveState")


class AutomationSummaryResponse(AutomationStatusResponse):
    name: str
    execution_session_id: UUID = Field(alias="executionSessionId")
    task_excerpt: str = Field(alias="taskExcerpt", max_length=240)
    last_activity_at: datetime | None = Field(alias="lastActivityAt")


class AutomationSummaryListResponse(BaseModel):
    items: list[AutomationSummaryResponse]


class AutomationRunSummaryResponse(BaseModel):
    id: UUID
    automation_id: UUID = Field(alias="automationId")
    status: str
    trigger: Literal["scheduled", "manual", "agent_response", "agent_error"]
    automation_revision: int = Field(alias="automationRevision")
    scheduled_for: datetime | None = Field(alias="scheduledFor")
    created_at: datetime | None = Field(alias="createdAt")
    started_at: datetime | None = Field(alias="startedAt")
    completed_at: datetime | None = Field(alias="completedAt")


class AutomationRunSummaryListResponse(BaseModel):
    items: list[AutomationRunSummaryResponse]
    page: int
    has_more: bool = Field(alias="hasMore")


async def _session_activity_times(
    db: AsyncSession, session_ids: list[UUID]
) -> dict[UUID, datetime]:
    if not session_ids:
        return {}
    rows = await db.execute(
        select(ChatSession.id, func.coalesce(func.max(Message.created_at), ChatSession.created_at))
        .outerjoin(Message, (Message.session_id == ChatSession.id) & Message.role.in_(("user", "assistant")))
        .where(ChatSession.id.in_(session_ids))
        .group_by(ChatSession.id, ChatSession.created_at)
    )
    return dict(rows.all())


async def _active_run_statuses(
    db: AsyncSession, automation_ids: list[UUID]
) -> dict[UUID, str]:
    if not automation_ids:
        return {}
    rows = await db.execute(
        select(AutomationRun.automation_id, AutomationRun.status)
        .where(
            AutomationRun.automation_id.in_(automation_ids),
            AutomationRun.status.in_(ACTIVE_STATUSES),
        )
        .order_by(AutomationRun.created_at, AutomationRun.id)
    )
    return dict(rows.all())


def _automation_status(
    item: Automation, active_run_status: str | None, *, archived: bool = False,
) -> AutomationStatusResponse:
    next_at = min(
        (schedule.next_run_at for schedule in item.schedules if schedule.enabled),
        default=None,
    ) if item.enabled and not archived else None
    return AutomationStatusResponse(
        id=item.id,
        revision=item.revision,
        enabled=item.enabled,
        triggers=item.triggers or [],
        scheduleCount=len(item.schedules),
        effectiveState=AutomationEffectiveState(
            enabled=bool(item.enabled and not archived and (next_at is not None or item.triggers)),
            activeRunStatus=active_run_status,
            nextRunAt=next_at,
        ),
    )


def _raise(exc: AutomationError) -> None:
    statuses = {
        "session.readOnly": 403,
        "session.storageUnavailable": 500,
        "automation.notFound": 404,
        "automation.runNotFound": 404,
        "automation.sessionNotFound": 404,
        "skill.notFound": 404,
        "automation.alreadyRunning": 409,
        "session.archived": 409,
        "automation.deliveryUnavailable": 409,
        "automation.scheduleDuplicate": 409,
        "automation.runtimeActive": 409,
        "automation.changed": 409,
        "session.controlCenterFull": 409,
    }
    raise ApiError(
        statuses.get(exc.code, 422),
        exc.code,
        field=exc.field,
        params=exc.params,
    ) from exc


def _schedule_payload(item) -> dict[str, Any]:
    return {
        "id": str(item.id),
        "cron": item.cron_expression,
        "timezone": item.timezone,
        "runAt": item.next_run_at.isoformat() if item.cron_expression is None else None,
        "validUntil": item.valid_until.isoformat() if item.valid_until else None,
        "enabled": item.enabled,
        "nextRunAt": item.next_run_at.isoformat(),
    }


async def _automation_payload(
    db: AsyncSession, item: Automation, *, detail: bool = False
) -> dict[str, Any]:
    execution_session = await db.get(ChatSession, item.execution_session_id)
    if execution_session is None:
        raise AutomationError("automation.executionSessionMissing")
    active = await db.scalar(
        select(AutomationRun)
        .where(
            AutomationRun.automation_id == item.id,
            AutomationRun.status.in_(ACTIVE_STATUSES),
        )
        .order_by(AutomationRun.created_at.desc(), AutomationRun.id.desc())
    )
    schedules = [_schedule_payload(schedule) for schedule in item.schedules]
    effective_enabled = (
        item.enabled and not await is_session_archived(db, execution_session)
        and (bool(item.triggers) or any(schedule["enabled"] for schedule in schedules))
    )
    payload = {
        "id": str(item.id),
        "name": item.name,
        "task": item.task,
        "enabled": item.enabled,
        "triggers": item.triggers or [],
        "revision": item.revision,
        "executionSessionId": str(item.execution_session_id),
        "lastActivityAt": (await _session_activity_times(db, [item.execution_session_id])).get(item.execution_session_id),
        "runtime": runtime_snapshot(execution_session),
        "schedules": schedules,
        "effectiveState": {
            "enabled": effective_enabled,
            "activeRunStatus": active.status if active else None,
            "nextRunAt": (
                min(
                    (
                        schedule["nextRunAt"]
                        for schedule in schedules
                        if schedule["enabled"]
                    ),
                    default=None,
                )
                if effective_enabled
                else None
            ),
        },
        "createdAt": item.created_at.isoformat() if item.created_at else None,
        "updatedAt": item.updated_at.isoformat() if item.updated_at else None,
    }
    if detail:
        messages = list(
            (
                await db.scalars(
                    select(Message)
                    .where(
                        Message.session_id == item.execution_session_id,
                        Message.role.in_(("user", "assistant")),
                    )
                    .order_by(Message.created_at, Message.id)
                )
            ).all()
        )
        payload["executionHistory"] = [
            {
                "id": str(message.id),
                "role": message.role,
                "content": (
                    describe_error(message.error_code, params=message.error_params).message
                    if message.error_code
                    else canonical_assistant_content(message.content)
                    if message.role == "assistant"
                    else message.content
                ),
                "createdAt": message.created_at.isoformat() if message.created_at else None,
            }
            for message in messages
        ]
    return payload


async def _run_payload(db: AsyncSession, run: AutomationRun) -> dict[str, Any]:
    agent_run = await db.get(AgentRun, run.agent_run_id) if run.agent_run_id else None
    result_message = (
        await db.get(Message, agent_run.result_message_id)
        if agent_run is not None and agent_run.result_message_id is not None
        else None
    )
    error = (
        describe_error(run.error_code, params=run.error_params).payload()
        if run.error_code
        else None
    )
    return {
        "id": str(run.id),
        "automationId": str(run.automation_id),
        "automationRevision": run.automation_revision,
        "trigger": run.trigger,
        "status": run.status,
        "scheduledFor": run.scheduled_for.isoformat() if run.scheduled_for else None,
        "scheduleSnapshots": run.schedule_snapshots,
        "definition": run.definition_snapshot,
        "agentRunId": str(run.agent_run_id) if run.agent_run_id else None,
        "messageId": str(result_message.id) if result_message is not None else (
            str(agent_run.input_message_id) if agent_run is not None and agent_run.input_message_id else None
        ),
        "agentProfileId": (
            str(agent_run.agent_profile_id)
            if agent_run is not None and agent_run.agent_profile_id
            else None
        ),
        "result": (
            {
                "messageId": str(result_message.id),
                "content": canonical_assistant_content(result_message.content),
            }
            if result_message is not None and not result_message.error_code
            else None
        ),
        "error": error,
        "createdAt": run.created_at.isoformat() if run.created_at else None,
        "startedAt": run.started_at.isoformat() if run.started_at else None,
        "completedAt": run.completed_at.isoformat() if run.completed_at else None,
    }


def _schedule_drafts(payload: AutomationRequest) -> list[dict[str, Any]]:
    return [item.model_dump() for item in payload.schedules]


@router.get("")
async def list_automations(
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
    summary: bool = False,
    session_id: Annotated[UUID | None, Query(alias="sessionId")] = None,
) -> AutomationSummaryListResponse | dict[str, Any]:
    items = list(
        (
            await db.scalars(
                select(Automation)
                .join(ChatSession, ChatSession.id == Automation.execution_session_id)
                .options(selectinload(Automation.schedules))
                .where(
                    Automation.user_id == user.id,
                    Automation.execution_session_id == session_id
                    if session_id is not None
                    else active_session_clause(),
                )
                .order_by(Automation.updated_at.desc(), Automation.id.desc())
            )
        ).all()
    )
    if summary:
        active_statuses = await _active_run_statuses(db, [item.id for item in items])
        activity_times = await _session_activity_times(db, [item.execution_session_id for item in items])
        archived = session_id is not None and await db.scalar(select(ChatSession.id).where(
            ChatSession.id == session_id, ChatSession.user_id == user.id, active_session_clause(),
        )) is None
        return AutomationSummaryListResponse(items=[
            AutomationSummaryResponse(
                **_automation_status(item, active_statuses.get(item.id), archived=archived).model_dump(by_alias=True),
                name=item.name,
                executionSessionId=item.execution_session_id,
                taskExcerpt=item.task[:240],
                lastActivityAt=activity_times.get(item.execution_session_id),
            )
            for item in items
        ])
    return {"items": [await _automation_payload(db, item) for item in items]}


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_automation(
    payload: AutomationRequest,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict[str, Any]:
    if payload.session_id is None:
        raise ApiError(422, "validation.required", field="sessionId")
    try:
        item = await AutomationService.create(
            db,
            user_id=user.id,
            session_id=payload.session_id,
            name=payload.name,
            task=payload.task,
            enabled=payload.enabled,
            schedules=_schedule_drafts(payload),
            triggers=payload.triggers,
        )
    except AutomationError as exc:
        _raise(exc)
    await db.commit()
    return await _automation_payload(db, item)


@router.get("/{automation_id}")
async def get_automation(
    automation_id: UUID,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
    include_history: Annotated[bool, Query(alias="includeHistory")] = True,
) -> dict[str, Any]:
    try:
        item = await AutomationService.owned(db, automation_id, user.id)
        return await _automation_payload(db, item, detail=include_history)
    except AutomationError as exc:
        _raise(exc)


@router.get("/{automation_id}/status")
async def get_automation_status(
    automation_id: UUID,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> AutomationStatusResponse:
    try:
        item = await AutomationService.owned(db, automation_id, user.id)
        active_statuses = await _active_run_statuses(db, [item.id])
        active_session = await db.scalar(select(ChatSession.id).where(
            ChatSession.id == item.execution_session_id, active_session_clause(),
        ))
        return _automation_status(item, active_statuses.get(item.id), archived=active_session is None)
    except AutomationError as exc:
        _raise(exc)


@router.put("/{automation_id}")
async def replace_automation(
    automation_id: UUID,
    payload: AutomationRequest,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict[str, Any]:
    if payload.expected_revision is None:
        raise ApiError(422, "validation.required", field="expectedRevision")
    try:
        item = await AutomationService.owned(db, automation_id, user.id, for_update=True)
        if payload.session_id is not None and payload.session_id != item.execution_session_id:
            raise ApiError(422, "validation.invalid", field="sessionId")
        item = await AutomationService.replace(
            db,
            item,
            expected_revision=payload.expected_revision,
            name=payload.name,
            task=payload.task,
            enabled=payload.enabled,
            schedules=_schedule_drafts(payload),
            triggers=payload.triggers,
        )
        await db.commit()
        return await _automation_payload(db, item)
    except AutomationError as exc:
        await db.rollback()
        _raise(exc)


@router.post("/{automation_id}/runs", status_code=status.HTTP_202_ACCEPTED)
async def start_automation(
    automation_id: UUID,
    payload: AutomationRunRequest,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict[str, Any]:
    try:
        await AutomationService.mirror_agent_runs(db)
        run = await AutomationService.enqueue_manual(
            db,
            automation_id,
            user.id,
            expected_revision=payload.expected_revision,
        )
        return await _run_payload(db, run)
    except AutomationError as exc:
        _raise(exc)


@router.get("/{automation_id}/runs")
async def list_automation_runs(
    automation_id: UUID,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(alias="pageSize", ge=1, le=100)] = 20,
    summary: bool = False,
) -> AutomationRunSummaryListResponse | dict[str, Any]:
    try:
        await AutomationService.owned(db, automation_id, user.id)
    except AutomationError as exc:
        _raise(exc)
    statement = (
        select(AutomationRun)
        .where(AutomationRun.automation_id == automation_id)
        .order_by(AutomationRun.created_at.desc(), AutomationRun.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size + 1)
    )
    if summary:
        statement = statement.options(load_only(
            AutomationRun.id, AutomationRun.automation_id, AutomationRun.status,
            AutomationRun.trigger, AutomationRun.automation_revision,
            AutomationRun.scheduled_for, AutomationRun.created_at,
            AutomationRun.started_at, AutomationRun.completed_at,
            raiseload=True,
        ))
    rows = list((await db.scalars(statement)).all())
    if summary:
        return AutomationRunSummaryListResponse(
            items=[
                AutomationRunSummaryResponse(
                    id=run.id,
                    automationId=run.automation_id,
                    status=run.status,
                    trigger=run.trigger,
                    automationRevision=run.automation_revision,
                    scheduledFor=run.scheduled_for,
                    createdAt=run.created_at,
                    startedAt=run.started_at,
                    completedAt=run.completed_at,
                )
                for run in rows[:page_size]
            ],
            page=page,
            hasMore=len(rows) > page_size,
        )
    return {
        "items": [await _run_payload(db, run) for run in rows[:page_size]],
        "page": page,
        "hasMore": len(rows) > page_size,
    }


async def _owned_run(
    db: AsyncSession, automation_id: UUID, run_id: UUID, user_id: UUID
) -> AutomationRun:
    run = await db.scalar(
        select(AutomationRun)
        .join(Automation, Automation.id == AutomationRun.automation_id)
        .where(
            AutomationRun.id == run_id,
            AutomationRun.automation_id == automation_id,
            Automation.user_id == user_id,
        )
    )
    if run is None:
        raise AutomationError("automation.runNotFound")
    return run


@router.get("/{automation_id}/runs/{run_id}")
async def get_automation_run(
    automation_id: UUID,
    run_id: UUID,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict[str, Any]:
    try:
        return await _run_payload(
            db, await _owned_run(db, automation_id, run_id, user.id)
        )
    except AutomationError as exc:
        _raise(exc)


@router.delete("/{automation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_automation(
    automation_id: UUID,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
    expected_revision: Annotated[int, Query(alias="expectedRevision", ge=1)],
) -> None:
    try:
        await AutomationService.delete(
            db,
            automation_id,
            user.id,
            expected_revision=expected_revision,
        )
    except AutomationError as exc:
        _raise(exc)
