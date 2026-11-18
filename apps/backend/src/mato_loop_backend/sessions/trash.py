"""Recoverable deletion; optional worktree removal precedes the trash barrier."""
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update

from mato_loop_backend.models import (
    AgentRun,
    ApprovalRequest,
    Automation,
    AutomationRun,
    AutomationSchedule,
    ChatSession,
    Project,
    ProjectPlanExecution,
    QuestionRequest,
    QueuedMessage,
    UserAccount,
)

from .cleanup import descendant_sessions
from .state import require_session_available

ACTIVE = ("queued", "running", "waiting_approval", "waiting_input")


async def _lock(db, item):
    if isinstance(item, ChatSession) and item.project_id:
        await db.execute(select(Project.id).where(Project.id == item.project_id).with_for_update(key_share=True))
    return await db.scalar(select(type(item)).where(type(item).id == item.id)
                           .execution_options(populate_existing=True)
                           # NO KEY UPDATE still excludes project mutation, but permits a
                           # source-locked fork to insert its project foreign key.
                           .with_for_update(key_share=isinstance(item, Project)))


async def _sessions(db, item):
    roots = list(await db.scalars(select(ChatSession.id).where(
        ChatSession.project_id == item.id,
    ))) if isinstance(item, Project) else [item.id]
    return list(dict.fromkeys([*roots, *(child for child, _ in await descendant_sessions(db, roots))]))


async def _trash_scope(db, item):
    from mato_loop_backend.settings import SettingsStore, UserPreferences
    from mato_loop_backend.voice.admission import active_connections

    from .service import SessionServiceError

    item = await _lock(db, item)
    if item is None:
        raise SessionServiceError(404, "trash.notFound")
    if isinstance(item, ChatSession):
        if item.parent_session_id is not None:
            raise SessionServiceError(403, "session.readOnly")
        await require_session_available(db, item)
    elif item.delete_at is not None:
        raise SessionServiceError(409, "project.inTrash")
    preferences = await SettingsStore.load(db, UserPreferences, user_id=item.user_id)
    if not preferences.trash_enabled:
        raise SessionServiceError(409, "trash.disabled")
    project_id = item.id if isinstance(item, Project) else item.project_id
    if project_id and await db.scalar(select(ProjectPlanExecution.id).where(
        ProjectPlanExecution.project_id == project_id,
        ProjectPlanExecution.status.in_(("queued", "running", "waiting_permission")),
    ).limit(1)) is not None:
        raise SessionServiceError(409, "project.codingExecutionsActive")
    ids = await _sessions(db, item)
    session_ids = select(ChatSession.id).where(ChatSession.id.in_(ids))
    await db.execute(session_ids.with_for_update())
    if await active_connections(db, session_ids):
        raise SessionServiceError(409, "voice.busy")
    return item, ids, preferences


async def move_to_trash(db, item, *, worktree_impact=None):
    from mato_loop_backend.agent.runtime import AgentRuntime
    from mato_loop_backend.agent.session_secrets import session_secrets

    from .service import SessionServiceError, remove_session_worktree

    if worktree_impact is not None:
        if not isinstance(item, ChatSession):
            raise SessionServiceError(400, "validation.invalid", field="worktreeImpact")
        item, ids, _ = await _trash_scope(db, item)
        item_id, user_id = item.id, item.user_id
        runs = list(await db.scalars(select(AgentRun.id).where(
            AgentRun.session_id.in_(ids), AgentRun.status.in_(ACTIVE),
        )))
        # Cancellation uses independent transactions: release locks, then revalidate.
        await db.rollback()
        for run_id in runs:
            await AgentRuntime.cancel_and_wait(run_id)
        for session_id in ids:
            await AgentRuntime.cancel_descendants(session_id)
        await db.execute(select(UserAccount.id).where(UserAccount.id == user_id).with_for_update(key_share=True))
        item = await db.get(ChatSession, item_id)
        if item is None:
            raise SessionServiceError(404, "trash.notFound")
    item, ids, preferences = await _trash_scope(db, item)
    runs = list(await db.scalars(select(AgentRun.id).where(
        AgentRun.session_id.in_(ids), AgentRun.status.in_(ACTIVE),
    )))
    if worktree_impact is not None:
        if runs:
            raise SessionServiceError(409, "session.runAlreadyActive")
        try:
            await remove_session_worktree(db, item, worktree_impact)
        except BaseException:
            await db.rollback()
            raise
    now = datetime.now(UTC)
    item.delete_at = now + timedelta(days=preferences.trash_retention_days)
    await db.execute(update(QueuedMessage).where(QueuedMessage.session_id.in_(ids)).values(paused=True))
    await db.execute(update(AutomationRun).where(
        AutomationRun.automation_id.in_(select(Automation.id).where(Automation.execution_session_id.in_(ids))),
        AutomationRun.status == "queued",
    ).values(status="cancelled", completed_at=now))
    await db.execute(update(ApprovalRequest).where(
        ApprovalRequest.session_id.in_(ids), ApprovalRequest.status == "pending",
    ).values(status="expired", resolved_at=now))
    await db.execute(update(QuestionRequest).where(
        QuestionRequest.session_id.in_(ids), QuestionRequest.status == "pending",
    ).values(status="expired"))
    # Publish the barrier before joining tasks which need their own transactions.
    await db.commit()
    session_secrets.clear_sessions(ids)
    for run_id in runs:
        await AgentRuntime.cancel_and_wait(run_id)
    for session_id in ids:
        await AgentRuntime.cancel_descendants(session_id)


async def restore_from_trash(db, item):
    from mato_loop_backend.automations import advance_schedule

    from .service import SessionServiceError

    item = await _lock(db, item)
    if item is None or item.delete_at is None:
        raise SessionServiceError(404, "trash.notFound")
    now = datetime.now(UTC)
    if item.delete_at <= now:
        raise SessionServiceError(409, "trash.expired")
    if isinstance(item, ChatSession) and item.project_id:
        project = await db.get(Project, item.project_id)
        if project is not None and project.delete_at is not None:
            raise SessionServiceError(409, "trash.parentDeleted")
    ids = await _sessions(db, item)
    if await db.scalar(select(AgentRun.id).where(
        AgentRun.session_id.in_(ids), AgentRun.status.in_(ACTIVE),
    ).limit(1)) is not None:
        raise SessionServiceError(409, "session.runAlreadyActive")
    item.delete_at = None
    schedules = await db.scalars(select(AutomationSchedule).where(
        AutomationSchedule.automation_id.in_(select(Automation.id).where(Automation.execution_session_id.in_(ids))),
        AutomationSchedule.enabled.is_(True), AutomationSchedule.next_run_at <= now,
    ).with_for_update())
    for schedule in schedules:
        advance_schedule(schedule, now)
    await db.commit()
