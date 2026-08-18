from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

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

router = APIRouter(prefix="/automations", dependencies=[Depends(require_user)])


class ScheduleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    cron: str = Field(min_length=1, max_length=255)
    timezone: str = Field(min_length=1, max_length=100)
    enabled: bool = True

    @field_validator("cron", "timezone")
    @classmethod
    def normalize(cls, value: str) -> str:
        return value.strip()


class AutomationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, populate_by_name=True)

    name: str = Field(min_length=1, max_length=240)
    task: str = Field(min_length=1, max_length=40_000)
    skill_id: UUID | None = Field(default=None, alias="skillId")
    enabled: bool = True
    source_session_id: UUID | None = Field(default=None, alias="sourceSessionId")
    output_session_id: UUID | None = Field(default=None, alias="outputSessionId")
    output_session_required: bool = Field(default=False, alias="outputSessionRequired")
    schedules: list[ScheduleRequest] = Field(min_length=1, max_length=50)
    expected_revision: int | None = Field(default=None, alias="expectedRevision", ge=1)

    @field_validator("name", "task")
    @classmethod
    def normalize(cls, value: str) -> str:
        return value.strip()

    @model_validator(mode="after")
    def required_output_present(self) -> AutomationRequest:
        if self.output_session_required and self.output_session_id is None:
            raise ValueError("outputSessionId is required")
        return self


class AutomationRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, populate_by_name=True)

    expected_revision: int = Field(alias="expectedRevision", ge=1)


def _raise(exc: AutomationError) -> None:
    statuses = {
        "session.storageUnavailable": 500,
        "automation.notFound": 404,
        "automation.runNotFound": 404,
        "automation.sessionNotFound": 404,
        "skill.notFound": 404,
        "automation.alreadyRunning": 409,
        "automation.deliveryUnavailable": 409,
        "automation.scheduleDuplicate": 409,
        "automation.runtimeActive": 409,
        "automation.changed": 409,
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
    effective_enabled = item.enabled and any(schedule["enabled"] for schedule in schedules)
    payload = {
        "id": str(item.id),
        "name": item.name,
        "task": item.task,
        "skillId": str(item.skill_id) if item.skill_id else None,
        "enabled": item.enabled,
        "revision": item.revision,
        "outputSessionId": str(item.output_session_id) if item.output_session_id else None,
        "outputSessionRequired": item.output_session_required,
        "executionSessionId": str(item.execution_session_id),
        "runtime": runtime_snapshot(execution_session),
        "schedules": schedules,
        "effectiveState": {
            "enabled": effective_enabled,
            "activeRunStatus": active.status if active else None,
            "nextRunAt": min(
                (schedule["nextRunAt"] for schedule in schedules if schedule["enabled"]),
                default=None,
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
        "result": (
            {
                "messageId": str(result_message.id),
                "content": canonical_assistant_content(result_message.content),
            }
            if result_message is not None and not result_message.error_code
            else None
        ),
        "error": error,
        "delivery": {
            "status": run.delivery_status,
            "attempts": run.delivery_attempts,
            "nextAttemptAt": (
                run.delivery_next_attempt_at.isoformat()
                if run.delivery_next_attempt_at
                else None
            ),
            "outputSessionId": str(run.output_session_id) if run.output_session_id else None,
            "outputMessageId": str(run.output_message_id) if run.output_message_id else None,
        },
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
) -> dict[str, Any]:
    items = list(
        (
            await db.scalars(
                select(Automation)
                .options(selectinload(Automation.schedules))
                .where(Automation.user_id == user.id)
                .order_by(Automation.updated_at.desc(), Automation.id.desc())
            )
        ).all()
    )
    return {"items": [await _automation_payload(db, item) for item in items]}


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_automation(
    payload: AutomationRequest,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict[str, Any]:
    try:
        item = await AutomationService.create(
            db,
            user_id=user.id,
            name=payload.name,
            task=payload.task,
            skill_id=payload.skill_id,
            enabled=payload.enabled,
            source_session_id=payload.source_session_id,
            output_session_id=payload.output_session_id,
            output_session_required=payload.output_session_required,
            schedules=_schedule_drafts(payload),
        )
    except AutomationError as exc:
        _raise(exc)
    try:
        await AutomationService.provision_and_commit(db, item)
    except AutomationError as exc:
        _raise(exc)
    return await _automation_payload(db, item, detail=True)


@router.get("/{automation_id}")
async def get_automation(
    automation_id: UUID,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict[str, Any]:
    try:
        item = await AutomationService.owned(db, automation_id, user.id)
        return await _automation_payload(db, item, detail=True)
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
        item = await AutomationService.replace(
            db,
            item,
            expected_revision=payload.expected_revision,
            name=payload.name,
            task=payload.task,
            skill_id=payload.skill_id,
            enabled=payload.enabled,
            source_session_id=payload.source_session_id,
            output_session_id=payload.output_session_id,
            output_session_required=payload.output_session_required,
            schedules=_schedule_drafts(payload),
        )
        await db.commit()
        return await _automation_payload(db, item, detail=True)
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
) -> dict[str, Any]:
    try:
        await AutomationService.owned(db, automation_id, user.id)
    except AutomationError as exc:
        _raise(exc)
    rows = list(
        (
            await db.scalars(
                select(AutomationRun)
                .where(AutomationRun.automation_id == automation_id)
                .order_by(AutomationRun.created_at.desc(), AutomationRun.id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size + 1)
            )
        ).all()
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


@router.post("/{automation_id}/runs/{run_id}/delivery-retry")
async def retry_automation_delivery(
    automation_id: UUID,
    run_id: UUID,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict[str, Any]:
    try:
        run = await AutomationService.retry_delivery(
            db, automation_id, run_id, user.id
        )
        return await _run_payload(db, run)
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
