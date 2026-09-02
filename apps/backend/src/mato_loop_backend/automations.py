from __future__ import annotations

import asyncio
import hashlib
import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from cronsim import CronSim, CronSimError
from pydantic import ValidationError
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from mato_loop_backend.db.session import AsyncSessionFactory
from mato_loop_backend.message_content import canonical_assistant_content
from mato_loop_backend.messaging.policy import channel_provider_allowed
from mato_loop_backend.models import (
    AgentRun,
    Automation,
    AutomationRun,
    AutomationSchedule,
    ChatSession,
    Message,
    MessagingEndpoint,
    MessagingOutbox,
    UserAccount,
)
from mato_loop_backend.notifications import NotificationDraft, NotificationService
from mato_loop_backend.session_storage import SessionStorage, SessionStorageError
from mato_loop_backend.settings import (
    DEFAULT_AGENT_PROFILE_ID,
    SettingsStore,
    SkillDefinition,
    SkillsSettings,
    load_effective_ai,
)
from mato_loop_backend.web_push import (
    WebPushAutomationRunTarget,
    WebPushPayload,
    WebPushService,
    web_push_payload,
)

logger = logging.getLogger(__name__)
ACTIVE_STATUSES = ("queued", "running", "waiting_approval", "waiting_input")


class AutomationError(ValueError):
    def __init__(
        self,
        code: str,
        *,
        field: str | None = None,
        params: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(code)
        self.code = code
        self.field = field
        self.params = params


def automation_impact_token(automations: list[Automation]) -> str:
    value = "|".join(
        f"{item.id}:{item.revision}:{int(item.output_session_required)}"
        for item in sorted(automations, key=lambda item: item.id)
    )
    return hashlib.sha256(value.encode()).hexdigest()


def next_run_at(cron_expression: str, timezone: str, after: datetime) -> datetime:
    if len(cron_expression.split()) != 5:
        raise AutomationError("automation.scheduleInvalid", field="cron")
    try:
        zone = ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise AutomationError("automation.timezoneInvalid", field="timezone") from exc
    if after.tzinfo is None:
        raise ValueError("after must be timezone-aware")
    try:
        value = next(CronSim(cron_expression, after.astimezone(zone)))
    except (CronSimError, StopIteration, ValueError) as exc:
        raise AutomationError("automation.scheduleInvalid", field="cron") from exc
    return value.astimezone(UTC)


def advance_schedule(schedule: AutomationSchedule, after: datetime) -> None:
    if schedule.cron_expression is None:
        schedule.enabled = False
        return
    assert schedule.timezone is not None
    schedule.next_run_at = next_run_at(
        schedule.cron_expression, schedule.timezone, after
    )
    if (
        schedule.valid_until is not None
        and schedule.next_run_at > schedule.valid_until
    ):
        schedule.enabled = False


def runtime_snapshot(session: ChatSession) -> dict[str, Any]:
    return {
        "modelId": str(session.model_config_id) if session.model_config_id else None,
        "agentProfileId": str(session.agent_profile_id),
        "reasoningEffort": session.reasoning_effort,
        "memoryMode": session.memory_mode,
        "webSearchPolicy": session.web_search_policy,
        "preferredExecutionAgentId": (
            str(session.preferred_execution_agent_id)
            if session.preferred_execution_agent_id
            else None
        ),
        "currentDirectory": session.current_directory,
    }


def copy_runtime(source: ChatSession, target: ChatSession) -> None:
    for name in (
        "model_config_id",
        "agent_profile_id",
        "reasoning_effort",
        "memory_mode",
        "web_search_policy",
        "preferred_execution_agent_id",
        "current_directory",
    ):
        setattr(target, name, getattr(source, name))


class AutomationService:
    @staticmethod
    def validate_archive_policy(
        schedules: list[dict[str, Any]], archive_after_success: bool
    ) -> None:
        if archive_after_success and (
            len(schedules) != 1 or schedules[0].get("run_at") is None
        ):
            raise AutomationError(
                "automation.archiveRequiresOneTimeSchedule",
                field="archiveAfterSuccess",
            )

    @staticmethod
    async def owned(
        db: AsyncSession,
        automation_id: UUID,
        user_id: UUID,
        *,
        for_update: bool = False,
    ) -> Automation:
        statement = (
            select(Automation)
            .options(selectinload(Automation.schedules))
            .execution_options(populate_existing=True)
            .where(Automation.id == automation_id, Automation.user_id == user_id)
        )
        if for_update:
            statement = statement.with_for_update()
        automation = await db.scalar(statement)
        if automation is None:
            raise AutomationError("automation.notFound")
        return automation

    @staticmethod
    async def normal_session(
        db: AsyncSession, session_id: UUID, user_id: UUID, *, field: str
    ) -> ChatSession:
        session = await db.scalar(
            select(ChatSession)
            .where(
                ChatSession.id == session_id,
                ChatSession.user_id == user_id,
                ChatSession.session_type == "chat",
            )
            .with_for_update()
        )
        if session is None:
            raise AutomationError("automation.sessionNotFound", field=field)
        return session

    @staticmethod
    async def skill(
        db: AsyncSession, user_id: UUID, skill_id: UUID | None
    ) -> SkillDefinition | None:
        if skill_id is None:
            return None
        settings = await SettingsStore.load(
            db, SkillsSettings, user_id=user_id, for_update=True
        )
        skill = next((item for item in settings.skills if item.id == skill_id), None)
        if skill is None:
            raise AutomationError("skill.notFound", field="skillId")
        return skill

    @staticmethod
    async def delivery_endpoint(
        db: AsyncSession, user_id: UUID, endpoint_id: UUID, *, field: str
    ) -> MessagingEndpoint:
        endpoint = await db.scalar(
            select(MessagingEndpoint).where(
                MessagingEndpoint.id == endpoint_id,
                MessagingEndpoint.user_id == user_id,
                MessagingEndpoint.enabled.is_(True),
                MessagingEndpoint.deleted_at.is_(None),
                MessagingEndpoint.paired_chat_id.is_not(None),
            )
        )
        if endpoint is None:
            raise AutomationError("automation.deliveryUnavailable", field=field)
        if not await channel_provider_allowed(db, endpoint.provider):
            raise AutomationError("automation.deliveryUnavailable", field=field)
        return endpoint

    @staticmethod
    async def resolved_delivery_channel(
        db: AsyncSession, automation: Automation
    ) -> dict[str, Any]:
        if automation.delivery_channel_type == "app":
            return {
                "delivery_channel_type": "app",
                "delivery_endpoint_id": None,
                "delivery_destination": None,
            }
        endpoint_id = automation.delivery_endpoint_id
        if endpoint_id is None and automation.output_session_id is not None:
            first = await db.scalar(
                select(Message)
                .where(
                    Message.session_id == automation.output_session_id,
                    Message.role == "user",
                )
                .order_by(Message.created_at, Message.id)
                .limit(1)
            )
            endpoint_id = (
                first.source_endpoint_id
                if first is not None and first.source_type == "endpoint"
                else None
            )
        if endpoint_id is not None:
            endpoint = await db.scalar(
                select(MessagingEndpoint).where(
                    MessagingEndpoint.id == endpoint_id,
                    MessagingEndpoint.user_id == automation.user_id,
                    MessagingEndpoint.enabled.is_(True),
                    MessagingEndpoint.deleted_at.is_(None),
                    MessagingEndpoint.paired_chat_id.is_not(None),
                )
            )
            if endpoint is not None and await channel_provider_allowed(
                db, endpoint.provider, lock=True
            ):
                return {
                    "delivery_channel_type": "endpoint",
                    "delivery_endpoint_id": endpoint.id,
                    "delivery_destination": endpoint.paired_chat_id,
                }
        return {
            "delivery_channel_type": "app",
            "delivery_endpoint_id": None,
            "delivery_destination": None,
        }

    @staticmethod
    def set_schedules(
        automation: Automation,
        schedules: list[dict[str, Any]],
        *,
        now: datetime,
    ) -> None:
        seen: set[tuple[Any, ...]] = set()
        replacement = []
        for draft in schedules:
            cron = draft.get("cron")
            timezone = draft.get("timezone")
            run_at = draft.get("run_at")
            valid_until = draft.get("valid_until")
            if valid_until is not None:
                if (
                    not isinstance(valid_until, datetime)
                    or valid_until.tzinfo is None
                    or valid_until.utcoffset() is None
                ):
                    raise AutomationError("automation.scheduleInvalid", field="validUntil")
                valid_until = valid_until.astimezone(UTC)
            if run_at is None:
                if not isinstance(cron, str) or not isinstance(timezone, str):
                    raise AutomationError("automation.scheduleInvalid", field="schedules")
                key = ("cron", cron, timezone)
                next_at = next_run_at(cron, timezone, now)
            else:
                if (
                    cron is not None
                    or timezone is not None
                    or valid_until is not None
                    or not isinstance(run_at, datetime)
                    or run_at.tzinfo is None
                    or run_at.utcoffset() is None
                ):
                    raise AutomationError("automation.scheduleInvalid", field="runAt")
                next_at = run_at.astimezone(UTC)
                key = ("once", next_at)
            if key in seen:
                raise AutomationError("automation.scheduleDuplicate", field="schedules")
            seen.add(key)
            replacement.append(
                AutomationSchedule(
                    cron_expression=cron,
                    timezone=timezone,
                    enabled=draft["enabled"]
                    and (valid_until is None or next_at <= valid_until),
                    next_run_at=next_at,
                    valid_until=valid_until,
                )
            )
        automation.schedules = replacement

    @staticmethod
    async def create(
        db: AsyncSession,
        *,
        user_id: UUID,
        name: str,
        task: str,
        skill_id: UUID | None,
        enabled: bool,
        source_session_id: UUID | None,
        output_session_id: UUID | None,
        output_session_required: bool,
        schedules: list[dict[str, Any]],
        delivery_channel_type: str | None = None,
        delivery_endpoint_id: UUID | None = None,
        archive_after_success: bool = False,
        now: datetime | None = None,
    ) -> Automation:
        await AutomationService.skill(db, user_id, skill_id)
        source = (
            await AutomationService.normal_session(
                db, source_session_id, user_id, field="sourceSessionId"
            )
            if source_session_id
            else None
        )
        if output_session_required and output_session_id is None:
            raise AutomationError("validation.required", field="outputSessionId")
        if output_session_id:
            await AutomationService.normal_session(
                db, output_session_id, user_id, field="outputSessionId"
            )
        if delivery_channel_type == "endpoint" and delivery_endpoint_id is not None:
            await AutomationService.delivery_endpoint(
                db, user_id, delivery_endpoint_id, field="deliveryEndpointId"
            )
        AutomationService.validate_archive_policy(schedules, archive_after_success)
        execution_session = ChatSession(
            id=uuid4(),
            user_id=user_id,
            title=name,
            session_type="automation",
            title_generation_status="skipped",
            memory_mode="full",
            web_search_policy="available",
            agent_profile_id=DEFAULT_AGENT_PROFILE_ID,
        )
        if source is not None:
            copy_runtime(source, execution_session)
        automation = Automation(
            user_id=user_id,
            execution_session_id=execution_session.id,
            output_session_id=output_session_id,
            output_session_required=output_session_required,
            delivery_channel_type=delivery_channel_type,
            delivery_endpoint_id=delivery_endpoint_id,
            name=name,
            task=task,
            skill_id=skill_id,
            enabled=enabled,
            archive_after_success=archive_after_success,
        )
        AutomationService.set_schedules(
            automation, schedules, now=now or datetime.now(UTC)
        )
        db.add(execution_session)
        await db.flush()
        db.add(automation)
        await db.flush()
        return automation

    @staticmethod
    async def provision_and_commit(
        db: AsyncSession, automation: Automation
    ) -> Automation:
        storage = SessionStorage()
        try:
            await asyncio.to_thread(storage.provision, automation.execution_session_id)
            await db.commit()
        except (OSError, SessionStorageError) as exc:
            await db.rollback()
            logger.exception("Automation session storage could not be provisioned")
            raise AutomationError("session.storageUnavailable") from exc
        except BaseException:
            await db.rollback()
            await asyncio.to_thread(
                storage.remove_session, automation.execution_session_id
            )
            raise
        return automation

    @staticmethod
    async def replace(
        db: AsyncSession,
        automation: Automation,
        *,
        expected_revision: int,
        name: str,
        task: str,
        skill_id: UUID | None,
        enabled: bool,
        source_session_id: UUID | None,
        output_session_id: UUID | None,
        output_session_required: bool,
        schedules: list[dict[str, Any]],
        delivery_channel_type: str | None = None,
        delivery_endpoint_id: UUID | None = None,
        archive_after_success: bool = False,
        now: datetime | None = None,
    ) -> Automation:
        if automation.archived_at is not None:
            raise AutomationError("automation.archived")
        if automation.revision != expected_revision:
            raise AutomationError(
                "automation.changed", params={"currentRevision": automation.revision}
            )
        await AutomationService.skill(db, automation.user_id, skill_id)
        if output_session_required and output_session_id is None:
            raise AutomationError("validation.required", field="outputSessionId")
        if output_session_id:
            await AutomationService.normal_session(
                db, output_session_id, automation.user_id, field="outputSessionId"
            )
        if (
            delivery_channel_type == "endpoint"
            and delivery_endpoint_id is not None
            and delivery_endpoint_id != automation.delivery_endpoint_id
        ):
            await AutomationService.delivery_endpoint(
                db,
                automation.user_id,
                delivery_endpoint_id,
                field="deliveryEndpointId",
            )
        AutomationService.validate_archive_policy(schedules, archive_after_success)
        execution_session = await db.get(ChatSession, automation.execution_session_id)
        if execution_session is None:
            raise AutomationError("automation.executionSessionMissing")
        if source_session_id:
            active = await db.scalar(
                select(AutomationRun.id).where(
                    AutomationRun.automation_id == automation.id,
                    AutomationRun.status.in_(ACTIVE_STATUSES),
                )
            )
            if active is not None:
                raise AutomationError("automation.runtimeActive")
            source = await AutomationService.normal_session(
                db, source_session_id, automation.user_id, field="sourceSessionId"
            )
            copy_runtime(source, execution_session)
        execution_session.title = name
        automation.name = name
        automation.task = task
        automation.skill_id = skill_id
        automation.enabled = enabled
        automation.archive_after_success = archive_after_success
        automation.output_session_id = output_session_id
        automation.output_session_required = output_session_required
        automation.delivery_channel_type = delivery_channel_type
        automation.delivery_endpoint_id = delivery_endpoint_id
        automation.revision += 1
        automation.schedules = []
        await db.flush()
        AutomationService.set_schedules(
            automation, schedules, now=now or datetime.now(UTC)
        )
        await db.flush()
        await db.refresh(automation, attribute_names=["updated_at"])
        return automation

    @staticmethod
    async def _snapshot(
        db: AsyncSession, automation: Automation
    ) -> dict[str, Any]:
        execution_session = await db.get(ChatSession, automation.execution_session_id)
        if execution_session is None:
            raise AutomationError("automation.executionSessionMissing")
        skill = await AutomationService.skill(db, automation.user_id, automation.skill_id)
        runtime = runtime_snapshot(execution_session)
        if runtime["modelId"] is None:
            ai_settings = await load_effective_ai(db, automation.user_id)
            runtime["modelId"] = (
                str(ai_settings.default_model_id) if ai_settings.default_model_id else None
            )
        return {
            "task": automation.task,
            "skill": skill.model_dump(mode="json") if skill else None,
            "runtime": runtime,
        }

    @staticmethod
    async def enqueue_manual(
        db: AsyncSession,
        automation_id: UUID,
        user_id: UUID,
        *,
        expected_revision: int | None = None,
    ) -> AutomationRun:
        automation = await AutomationService.owned(
            db, automation_id, user_id, for_update=True
        )
        if automation.archived_at is not None:
            raise AutomationError("automation.archived")
        if expected_revision is not None and automation.revision != expected_revision:
            raise AutomationError(
                "automation.changed", params={"currentRevision": automation.revision}
            )
        active = await db.scalar(
            select(AutomationRun.id).where(
                AutomationRun.automation_id == automation.id,
                AutomationRun.status.in_(ACTIVE_STATUSES),
            )
        )
        if active is not None:
            raise AutomationError("automation.alreadyRunning")
        run = AutomationRun(
            automation_id=automation.id,
            automation_revision=automation.revision,
            trigger="manual",
            status="queued",
            definition_snapshot=await AutomationService._snapshot(db, automation),
            output_session_id=automation.output_session_id,
            delivery_status=("pending" if automation.output_session_id else "not_requested"),
            **await AutomationService.resolved_delivery_channel(db, automation),
        )
        db.add(run)
        await db.commit()
        return run

    @staticmethod
    async def delete(
        db: AsyncSession,
        automation_id: UUID,
        user_id: UUID,
        *,
        expected_revision: int,
    ) -> None:
        automation = await AutomationService.owned(db, automation_id, user_id)
        if automation.revision != expected_revision:
            raise AutomationError(
                "automation.changed", params={"currentRevision": automation.revision}
            )
        agent_run_ids = list(
            (
                await db.scalars(
                    select(AgentRun.id)
                    .join(AutomationRun, AutomationRun.agent_run_id == AgentRun.id)
                    .where(
                        AutomationRun.automation_id == automation.id,
                        AgentRun.status.in_(ACTIVE_STATUSES),
                    )
                )
            ).all()
        )
        await db.rollback()
        from mato_loop_backend.agent.runtime import AgentRuntime

        for agent_run_id in agent_run_ids:
            await AgentRuntime.cancel_and_wait(agent_run_id)

        automation = await AutomationService.owned(
            db, automation_id, user_id, for_update=True
        )
        if automation.revision != expected_revision:
            raise AutomationError(
                "automation.changed", params={"currentRevision": automation.revision}
            )
        late_agent_run_ids = list(
            (
                await db.scalars(
                    select(AgentRun.id)
                    .join(AutomationRun, AutomationRun.agent_run_id == AgentRun.id)
                    .where(
                        AutomationRun.automation_id == automation.id,
                        AgentRun.status.in_(ACTIVE_STATUSES),
                    )
                )
            ).all()
        )
        if late_agent_run_ids:
            await db.rollback()
            for agent_run_id in late_agent_run_ids:
                await AgentRuntime.cancel_and_wait(agent_run_id)
            return await AutomationService.delete(
                db,
                automation_id,
                user_id,
                expected_revision=expected_revision,
            )
        execution_session = await db.get(
            ChatSession, automation.execution_session_id
        )
        storage = SessionStorage()
        try:
            trashed = await asyncio.to_thread(
                storage.trash_session, automation.execution_session_id
            )
        except (OSError, SessionStorageError) as exc:
            await db.rollback()
            logger.exception("Automation session storage could not be moved to trash")
            raise AutomationError("session.storageUnavailable") from exc
        try:
            await db.delete(automation)
            await db.flush()
            if execution_session is not None:
                await db.delete(execution_session)
            await db.commit()
        except BaseException:
            await db.rollback()
            if trashed is not None:
                await asyncio.to_thread(
                    storage.restore_session,
                    automation.execution_session_id,
                    trashed,
                )
            raise
        await asyncio.to_thread(storage.purge_trashed, trashed)

    @staticmethod
    async def enqueue_due(db: AsyncSession, *, now: datetime | None = None) -> int:
        now = now or datetime.now(UTC)
        automations = list(
            (
                await db.scalars(
                    select(Automation)
                    .join(UserAccount, UserAccount.id == Automation.user_id)
                    .options(selectinload(Automation.schedules))
                    .where(
                        Automation.enabled.is_(True),
                        Automation.archived_at.is_(None),
                        UserAccount.is_active.is_(True),
                        Automation.schedules.any(
                            and_(
                                AutomationSchedule.enabled.is_(True),
                                AutomationSchedule.next_run_at <= now,
                            )
                        ),
                    )
                    .order_by(Automation.id)
                    .with_for_update(skip_locked=True)
                )
            ).all()
        )
        count = 0
        for automation in automations:
            due = [
                item
                for item in automation.schedules
                if item.enabled and item.next_run_at <= now
            ]
            if not due:
                continue
            snapshots = [
                {
                    "id": str(item.id),
                    "cron": item.cron_expression,
                    "timezone": item.timezone,
                    "runAt": (
                        item.next_run_at.isoformat()
                        if item.cron_expression is None
                        else None
                    ),
                    **(
                        {"validUntil": item.valid_until.isoformat()}
                        if item.valid_until
                        else {}
                    ),
                    "scheduledFor": item.next_run_at.isoformat(),
                }
                for item in due
            ]
            scheduled_for = min(item.next_run_at for item in due)
            for item in due:
                advance_schedule(item, now)
            active = await db.scalar(
                select(AutomationRun.id).where(
                    AutomationRun.automation_id == automation.id,
                    AutomationRun.status.in_(ACTIVE_STATUSES),
                )
            )
            run = AutomationRun(
                automation_id=automation.id,
                automation_revision=automation.revision,
                trigger="scheduled",
                status="skipped" if active else "queued",
                scheduled_for=scheduled_for,
                schedule_snapshots=snapshots,
                definition_snapshot=await AutomationService._snapshot(db, automation),
                output_session_id=automation.output_session_id,
                delivery_status=(
                    "not_requested"
                    if active or automation.output_session_id is None
                    else "pending"
                ),
                error_code="automation.alreadyRunning" if active else None,
                completed_at=now if active else None,
                **await AutomationService.resolved_delivery_channel(db, automation),
            )
            db.add(run)
            count += 1
        await db.commit()
        return count

    @staticmethod
    async def _fail_start(db: AsyncSession, run_id: UUID, code: str) -> None:
        await db.rollback()
        run = await db.get(AutomationRun, run_id)
        if run is None or run.status != "queued":
            return
        run.status = "failed"
        run.error_code = code
        run.completed_at = datetime.now(UTC)
        if run.delivery_status == "pending":
            run.delivery_status = "cancelled"
            run.delivery_next_attempt_at = None
        automation = await db.get(Automation, run.automation_id)
        push = None
        if automation is not None:
            push = await AutomationService._notify_terminal(db, automation, run)
        await db.commit()
        if push is not None:
            await WebPushService.deliver_after_commit(automation.user_id, push)

    @staticmethod
    async def process_next(db: AsyncSession) -> bool:
        from mato_loop_backend.agent.runtime import AgentRunError, AgentRuntime

        run = await db.scalar(
            select(AutomationRun)
            .join(Automation, Automation.id == AutomationRun.automation_id)
            .join(UserAccount, UserAccount.id == Automation.user_id)
            .where(
                AutomationRun.status == "queued",
                UserAccount.is_active.is_(True),
            )
            .order_by(AutomationRun.created_at, AutomationRun.id)
            .with_for_update(of=(AutomationRun, Automation), skip_locked=True)
            .limit(1)
        )
        if run is None:
            await db.rollback()
            return False
        run_id = run.id
        automation = await db.get(Automation, run.automation_id)
        session = (
            await db.get(ChatSession, automation.execution_session_id)
            if automation is not None
            else None
        )
        if automation is None or session is None:
            await AutomationService._fail_start(
                db, run_id, "automation.executionSessionMissing"
            )
            return True
        try:
            raw_skill = run.definition_snapshot.get("skill")
            skill = SkillDefinition.model_validate(raw_skill) if raw_skill else None
            raw_model_id = run.definition_snapshot.get("runtime", {}).get("modelId")
            model_id = UUID(raw_model_id) if raw_model_id else None
            await AgentRuntime.start(
                db,
                session,
                str(run.definition_snapshot["task"]),
                automation_run=run,
                model_id_override=model_id,
                required_skill=skill,
            )
        except (
            AgentRunError,
            ValidationError,
            AttributeError,
            KeyError,
            TypeError,
            ValueError,
        ) as exc:
            code = exc.code if isinstance(exc, AgentRunError) else "automation.snapshotInvalid"
            await AutomationService._fail_start(db, run_id, code)
        return True

    @staticmethod
    async def _notify_terminal(
        db: AsyncSession, automation: Automation, run: AutomationRun
    ) -> WebPushPayload | None:
        if run.status not in {"completed", "failed"}:
            return None
        completed = run.status == "completed"
        body = f'„{automation.name[:120]}“ konnte nicht abgeschlossen werden.'
        if completed:
            agent_run = await db.get(AgentRun, run.agent_run_id) if run.agent_run_id else None
            result_message = (
                await db.get(Message, agent_run.result_message_id)
                if agent_run is not None and agent_run.result_message_id is not None
                else None
            )
            result = (
                canonical_assistant_content(result_message.content).strip()
                if result_message is not None and not result_message.error_code
                else ""
            )
            body = result[:1_000] or f'„{automation.name[:120]}“ wurde abgeschlossen.'
        notification_id = await NotificationService.create(
            db,
            NotificationDraft(
                user_id=automation.user_id,
                automation_run_id=run.id,
                kind="automation.completed" if completed else "automation.failed",
                title="Automation abgeschlossen" if completed else "Automation fehlgeschlagen",
                body=body,
                deduplication_key=f"automation-run:{run.id}:{run.status}",
            ),
        )
        if notification_id is None:
            return None
        return web_push_payload(
            title="Automation abgeschlossen" if completed else "Automation fehlgeschlagen",
            body=body,
            tag=f"notification:{notification_id}",
            target=WebPushAutomationRunTarget(
                type="automationRun", automationId=automation.id, runId=run.id
            ),
        )

    @staticmethod
    async def mirror_agent_runs(db: AsyncSession) -> int:
        rows = list(
            (
                await db.execute(
                    select(AutomationRun, Automation, AgentRun)
                    .join(Automation, Automation.id == AutomationRun.automation_id)
                    .join(AgentRun, AgentRun.id == AutomationRun.agent_run_id)
                    .where(AutomationRun.status.in_(ACTIVE_STATUSES))
                    .with_for_update(of=AutomationRun, skip_locked=True)
                )
            ).all()
        )
        changed = 0
        pushes: list[tuple[UUID, WebPushPayload]] = []
        now = datetime.now(UTC)
        for run, automation, agent_run in rows:
            status = agent_run.status
            if status not in (*ACTIVE_STATUSES, "completed", "failed", "cancelled"):
                continue
            if run.status == status:
                continue
            run.status = status
            changed += 1
            if status in {"completed", "failed", "cancelled"}:
                run.completed_at = agent_run.completed_at or now
                if status == "failed":
                    run.error_code = agent_run.error or "agent.failed"
                    error_message = await db.scalar(
                        select(Message).where(Message.error_run_id == agent_run.id)
                    )
                    run.error_params = error_message.error_params if error_message else None
                if status == "completed" and run.output_session_id is not None:
                    run.delivery_status = "pending"
                    run.delivery_next_attempt_at = now
                elif status in {"failed", "cancelled"} and run.delivery_status == "pending":
                    run.delivery_status = "cancelled"
                    run.delivery_next_attempt_at = None
                push = await AutomationService._notify_terminal(db, automation, run)
                if push is not None:
                    pushes.append((automation.user_id, push))
                if (
                    status == "completed"
                    and automation.archive_after_success
                    and automation.archived_at is None
                    and run.trigger == "scheduled"
                    and run.automation_revision == automation.revision
                    and len(run.schedule_snapshots) == 1
                    and run.schedule_snapshots[0].get("runAt") is not None
                    and run.schedule_snapshots[0].get("cron") is None
                ):
                    automation.archived_at = run.completed_at
        await db.commit()
        await asyncio.gather(
            *(WebPushService.deliver_after_commit(user_id, push) for user_id, push in pushes)
        )
        return changed

    @staticmethod
    async def _delivery_failure(
        db: AsyncSession, run: AutomationRun, automation: Automation
    ) -> WebPushPayload | None:
        run.delivery_attempts += 1
        if run.delivery_attempts < 3:
            delay = timedelta(seconds=30) if run.delivery_attempts == 1 else timedelta(minutes=5)
            run.delivery_next_attempt_at = datetime.now(UTC) + delay
            return None
        run.delivery_status = "failed"
        run.delivery_next_attempt_at = None
        notification_id = await NotificationService.create(
            db,
            NotificationDraft(
                user_id=automation.user_id,
                automation_run_id=run.id,
                kind="automation.deliveryFailed",
                title="Automation-Ergebnis nicht zugestellt",
                body=f'Das Ergebnis von „{automation.name[:120]}“ konnte nicht zugestellt werden.',
                deduplication_key=f"automation-run:{run.id}:delivery-failed",
            ),
        )
        if notification_id is None:
            return None
        return web_push_payload(
            title="Automation-Ergebnis nicht zugestellt",
            body=f'Das Ergebnis von „{automation.name[:120]}“ konnte nicht zugestellt werden.',
            tag=f"notification:{notification_id}",
            target=WebPushAutomationRunTarget(
                type="automationRun", automationId=automation.id, runId=run.id
            ),
        )

    @staticmethod
    async def process_delivery(db: AsyncSession) -> bool:
        now = datetime.now(UTC)
        candidate = (
            await db.execute(
                select(AutomationRun.id, AutomationRun.output_session_id)
                .join(Automation, Automation.id == AutomationRun.automation_id)
                .join(UserAccount, UserAccount.id == Automation.user_id)
                .where(
                    AutomationRun.status == "completed",
                    AutomationRun.delivery_status == "pending",
                    UserAccount.is_active.is_(True),
                    or_(
                        AutomationRun.delivery_next_attempt_at.is_(None),
                        AutomationRun.delivery_next_attempt_at <= now,
                    ),
                )
                .order_by(AutomationRun.created_at, AutomationRun.id)
                .limit(1)
            )
        ).first()
        if candidate is None:
            await db.rollback()
            return False
        candidate_id, output_session_id = candidate
        output_session = None
        if output_session_id is not None:
            output_session = await db.scalar(
                select(ChatSession)
                .where(
                    ChatSession.id == output_session_id,
                    ChatSession.session_type == "chat",
                )
                .with_for_update()
            )
        row = (
            await db.execute(
                select(AutomationRun, Automation)
                .join(Automation, Automation.id == AutomationRun.automation_id)
                .where(
                    AutomationRun.id == candidate_id,
                    AutomationRun.status == "completed",
                    AutomationRun.delivery_status == "pending",
                )
                .with_for_update(of=AutomationRun, skip_locked=True)
            )
        ).first()
        if row is None:
            await db.rollback()
            return False
        run, automation = row
        if run.delivery_channel_type == "endpoint":
            endpoint = await db.get(MessagingEndpoint, run.delivery_endpoint_id)
            if (
                endpoint is None
                or not endpoint.enabled
                or endpoint.deleted_at is not None
                or endpoint.paired_chat_id is None
                or not await channel_provider_allowed(db, endpoint.provider)
            ):
                run.delivery_channel_type = "app"
                run.delivery_endpoint_id = None
                run.delivery_destination = None
        if run.output_message_id is not None:
            if run.delivery_channel_type == "endpoint":
                outbox = await db.scalar(
                    select(MessagingOutbox).where(
                        MessagingOutbox.endpoint_id == run.delivery_endpoint_id,
                        MessagingOutbox.deduplication_key == f"automation:{run.id}",
                    )
                )
                if outbox is None:
                    output_message = await db.get(Message, run.output_message_id)
                    if output_message is not None and run.delivery_endpoint_id and run.delivery_destination:
                        db.add(
                            MessagingOutbox(
                                endpoint_id=run.delivery_endpoint_id,
                                automation_run_id=run.id,
                                source_message_id=output_message.id,
                                destination=run.delivery_destination,
                                deduplication_key=f"automation:{run.id}",
                                 payload={
                                     "text": canonical_assistant_content(output_message.content),
                                     "contentFormat": "markdown",
                                     "includeAttachments": True,
                                 },
                            )
                        )
                run.delivery_next_attempt_at = now + timedelta(seconds=5)
            else:
                run.delivery_status = "delivered"
                run.delivery_next_attempt_at = None
            await db.commit()
            return True
        if run.output_session_id is None:
            run.delivery_status = "cancelled"
            run.delivery_next_attempt_at = None
            await db.commit()
            return True
        if output_session is None or output_session.user_id != automation.user_id:
            run.output_session_id = None
            run.delivery_status = "cancelled"
            run.delivery_next_attempt_at = None
            await db.commit()
            return True
        active = await db.scalar(
            select(AgentRun.id).where(
                AgentRun.session_id == output_session.id,
                AgentRun.status.in_(ACTIVE_STATUSES),
            )
        )
        if active is not None:
            run.delivery_next_attempt_at = now + timedelta(seconds=30)
            await db.commit()
            return True
        agent_run = await db.get(AgentRun, run.agent_run_id) if run.agent_run_id else None
        source = (
            await db.get(Message, agent_run.result_message_id)
            if agent_run is not None and agent_run.result_message_id is not None
            else None
        )
        if source is None or source.role != "assistant" or not source.content.strip():
            push = await AutomationService._delivery_failure(db, run, automation)
            await db.commit()
            if push is not None:
                await WebPushService.deliver_after_commit(automation.user_id, push)
            return True
        execution_session = await db.get(ChatSession, automation.execution_session_id)
        if execution_session is not None and execution_session.private_integration_data_used:
            output_session.private_integration_data_used = True
        message = Message(
            session_id=output_session.id,
            role="assistant",
            content=source.content,
            model_name=source.model_name,
            trust_level=source.trust_level,
            provider_data={
                "_matoloop": {
                    "source": {
                        "type": "automationRun",
                        "automationId": str(automation.id),
                        "runId": str(run.id),
                    }
                }
            },
            created_at=now,
        )
        db.add(message)
        try:
            await db.flush()
        except Exception:
            logger.exception("Automation result delivery failed for run %s", run.id)
            run_id = run.id
            await db.rollback()
            run = await db.get(AutomationRun, run_id)
            automation = await db.get(Automation, run.automation_id) if run else None
            if run is not None and automation is not None and run.delivery_status == "pending":
                push = await AutomationService._delivery_failure(db, run, automation)
                await db.commit()
                if push is not None:
                    await WebPushService.deliver_after_commit(automation.user_id, push)
            return True
        run.output_message_id = message.id
        if (
            run.delivery_channel_type == "endpoint"
            and run.delivery_endpoint_id is not None
            and run.delivery_destination is not None
        ):
            db.add(
                MessagingOutbox(
                    endpoint_id=run.delivery_endpoint_id,
                    automation_run_id=run.id,
                    source_message_id=message.id,
                    destination=run.delivery_destination,
                    deduplication_key=f"automation:{run.id}",
                     payload={
                         "text": canonical_assistant_content(message.content),
                         "contentFormat": "markdown",
                         "includeAttachments": True,
                     },
                )
            )
            run.delivery_next_attempt_at = now + timedelta(seconds=5)
        else:
            run.delivery_status = "delivered"
            run.delivery_next_attempt_at = None
        await db.commit()
        return True

    @staticmethod
    async def retry_delivery(
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
            .with_for_update()
        )
        if run is None:
            raise AutomationError("automation.runNotFound")
        if (
            run.status != "completed"
            or run.output_session_id is None
            or run.delivery_status != "failed"
        ):
            raise AutomationError("automation.deliveryUnavailable")
        if run.output_message_id is not None:
            if run.delivery_channel_type == "app":
                run.delivery_status = "delivered"
                return run
            endpoint = await db.get(MessagingEndpoint, run.delivery_endpoint_id)
            if (
                endpoint is None
                or not endpoint.enabled
                or endpoint.deleted_at is not None
                or endpoint.paired_chat_id != run.delivery_destination
                or not await channel_provider_allowed(db, endpoint.provider)
            ):
                run.delivery_channel_type = "app"
                run.delivery_endpoint_id = None
                run.delivery_destination = None
                run.delivery_status = "delivered"
                run.delivery_next_attempt_at = None
                await db.commit()
                return run
            outbox = await db.scalar(
                select(MessagingOutbox).where(
                    MessagingOutbox.endpoint_id == run.delivery_endpoint_id,
                    MessagingOutbox.deduplication_key == f"automation:{run.id}",
                )
            )
            if outbox is not None:
                outbox.status = "pending"
                outbox.attempts = 0
                outbox.next_attempt_at = datetime.now(UTC)
                outbox.error_code = None
            run.delivery_status = "pending"
            run.delivery_attempts = 0
            run.delivery_next_attempt_at = datetime.now(UTC)
            await db.commit()
            return run
        run.delivery_status = "pending"
        run.delivery_attempts = 0
        run.delivery_next_attempt_at = datetime.now(UTC)
        await db.commit()
        return run

    @staticmethod
    async def recover(db: AsyncSession) -> None:
        await AutomationService.mirror_agent_runs(db)
        now = datetime.now(UTC)
        schedules = list(
            (
                await db.scalars(
                    select(AutomationSchedule)
                    .where(AutomationSchedule.next_run_at <= now)
                    .with_for_update(skip_locked=True)
                )
            ).all()
        )
        for schedule in schedules:
            advance_schedule(schedule, now)
        await db.commit()


class AutomationScheduler:
    _task: asyncio.Task[None] | None = None

    @classmethod
    def start(cls) -> None:
        if cls._task is None or cls._task.done():
            cls._task = asyncio.create_task(cls._loop())

    @classmethod
    async def stop(cls) -> None:
        if cls._task is None:
            return
        cls._task.cancel()
        await asyncio.gather(cls._task, return_exceptions=True)
        cls._task = None

    @classmethod
    async def _loop(cls) -> None:
        while True:
            try:
                async with AsyncSessionFactory() as db:
                    await AutomationService.mirror_agent_runs(db)
                    await AutomationService.enqueue_due(db)
                    while await AutomationService.process_next(db):
                        pass
                    while await AutomationService.process_delivery(db):
                        pass
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Automation scheduler iteration failed")
            await asyncio.sleep(5)
