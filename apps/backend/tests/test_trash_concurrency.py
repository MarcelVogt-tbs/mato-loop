"""PostgreSQL lock-order regressions using committed rows and independent connections."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from mato_loop_backend.agent.runtime import AgentRuntime
from mato_loop_backend.automations import AutomationService
from mato_loop_backend.config import get_runtime_config
from mato_loop_backend.files.storage import SessionStorage
from mato_loop_backend.models import Automation, AutomationRun, ChatSession, Project, UserAccount
from mato_loop_backend.sessions import fork, trash
from mato_loop_backend.sessions.state import session_work_blocked
from mato_loop_backend.settings import SettingsStore, UserPreferences
from mato_loop_backend.voice import admission


@pytest.fixture
async def committed_trash(request, monkeypatch, tmp_path):
    if not request.config.getoption("--subagent-db"):
        pytest.skip("requires the migrated development PostgreSQL database")
    engine = create_async_engine(get_runtime_config().database_url, poolclass=NullPool,
        connect_args={"server_settings": {"statement_timeout": "8000", "lock_timeout": "5000"}})
    factory = async_sessionmaker(engine, expire_on_commit=False)
    user_id = uuid4()
    storage = SessionStorage(tmp_path / "sessions")
    monkeypatch.setattr(fork, "SessionStorage", lambda: storage)
    monkeypatch.setattr(AgentRuntime, "cancel_descendants", AsyncMock())
    try:
        async with factory() as db:
            db.add(UserAccount(id=user_id, username=f"trash-lock-{user_id}", password_hash="unusable-test-hash"))
            await db.flush()
            project = Project(user_id=user_id, name="Lock regression")
            db.add(project)
            await db.flush()
            session = ChatSession(user_id=user_id, project_id=project.id, title="Source", memory_mode="disabled")
            db.add(session)
            await db.flush()
            automation = Automation(user_id=user_id, execution_session_id=session.id, name="Queued", task="Task")
            db.add(automation)
            await db.flush()
            run = AutomationRun(automation_id=automation.id, automation_revision=1,
                trigger="manual", status="queued", definition_snapshot={"task": "Task"}, delivery_status="not_requested")
            db.add(run)
            await SettingsStore.save(db, UserPreferences(trash_enabled=True), user_id=user_id)
            await db.commit()
            storage.provision(session.id, project.id, user_id=user_id)
            yield SimpleNamespace(factory=factory, user_id=user_id, project_id=project.id,
                                  session_id=session.id, run_id=run.id)
    finally:
        async with factory() as db:
            await db.execute(delete(UserAccount).where(UserAccount.id == user_id))
            await db.commit()
        await engine.dispose()


async def test_automation_claim_skips_session_locked_by_trash(committed_trash, monkeypatch):
    state = committed_trash
    locked, claimed = asyncio.Event(), asyncio.Event()
    active_connections = admission.active_connections
    start = AsyncMock()
    monkeypatch.setattr(AgentRuntime, "start", start)

    async def hold_barrier(db, session_ids):
        result = await active_connections(db, session_ids)
        locked.set()
        await claimed.wait()
        return result

    monkeypatch.setattr(admission, "active_connections", hold_barrier)

    async def trash_session():
        async with state.factory() as db:
            await trash.move_to_trash(db, await db.get(ChatSession, state.session_id))

    async def claim():
        await locked.wait()
        async with state.factory() as db:
            assert not await AutomationService.process_next(db)
        claimed.set()

    async with asyncio.timeout(10), asyncio.TaskGroup() as tasks:
        tasks.create_task(trash_session())
        tasks.create_task(claim())
    async with state.factory() as db:
        assert not await AutomationService.process_next(db)
        assert await db.scalar(select(AutomationRun.status).where(AutomationRun.id == state.run_id)) == "cancelled"
    start.assert_not_awaited()


async def test_project_trash_allows_source_locked_fork_foreign_key(committed_trash, monkeypatch):
    state = committed_trash
    source_locked, project_locked = asyncio.Event(), asyncio.Event()
    get_owned_session, get_sessions = fork.get_owned_session, trash._sessions

    async def hold_source(*args, **kwargs):
        source = await get_owned_session(*args, **kwargs)
        source_locked.set()
        await project_locked.wait()
        return source

    async def hold_project(db, item):
        project_locked.set()
        return await get_sessions(db, item)

    monkeypatch.setattr(fork, "get_owned_session", hold_source)
    monkeypatch.setattr(trash, "_sessions", hold_project)

    async def fork_source():
        async with state.factory() as db:
            return await fork._fork_session(db, state.session_id, state.user_id)

    async def trash_project():
        await source_locked.wait()
        async with state.factory() as db:
            await trash.move_to_trash(db, await db.get(Project, state.project_id))

    async with asyncio.timeout(10), asyncio.TaskGroup() as tasks:
        copied = tasks.create_task(fork_source())
        tasks.create_task(trash_project())
    async with state.factory() as db:
        assert await session_work_blocked(db, await db.get(ChatSession, copied.result().id)) == "session.inTrash"


async def test_start_failure_preserves_concurrent_trash_cancellation(committed_trash, monkeypatch):
    state = committed_trash
    released, trashed = asyncio.Event(), asyncio.Event()
    async with state.factory() as worker:
        rollback = worker.rollback

        async def release_claim():
            await rollback()
            released.set()
            await trashed.wait()

        monkeypatch.setattr(worker, "rollback", release_claim)

        async def trash_session():
            await released.wait()
            async with state.factory() as db:
                await trash.move_to_trash(db, await db.get(ChatSession, state.session_id))
            trashed.set()

        async with asyncio.timeout(10), asyncio.TaskGroup() as tasks:
            tasks.create_task(AutomationService._fail_start(worker, state.run_id, "automation.snapshotInvalid"))
            tasks.create_task(trash_session())
    async with state.factory() as db:
        assert await db.scalar(select(AutomationRun.status).where(AutomationRun.id == state.run_id)) == "cancelled"
