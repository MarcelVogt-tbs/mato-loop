from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

import mato_loop_backend.app as app_module
from mato_loop_backend.agent import AgentRuntime
from mato_loop_backend.api.errors import ApiError
from mato_loop_backend.api.routes.approvals import ApprovalDecisionRequest, decide_approval
from mato_loop_backend.models import (
    AgentRun,
    ApprovalRequest,
    TitleGenerationStatus,
    UserAccount,
)


class _SessionContext:
    def __init__(self, session: AsyncMock) -> None:
        self.session = session

    async def __aenter__(self) -> AsyncMock:
        return self.session

    async def __aexit__(self, *_args: object) -> None:
        return None


async def test_startup_expires_in_memory_run_state_instead_of_resuming(monkeypatch) -> None:
    db = AsyncMock(spec=AsyncSession)
    attachments = Mock()
    attachments.all.return_value = []
    db.scalars.return_value = attachments
    rows = Mock()
    rows.all.return_value = []
    db.execute.return_value = rows
    engine = Mock(dispose=AsyncMock())
    monkeypatch.setattr(app_module, "AsyncSessionFactory", lambda: _SessionContext(db))
    recover_runs = AsyncMock()
    monkeypatch.setattr(AgentRuntime, "recover", recover_runs)
    monkeypatch.setattr(app_module.AutomationService, "recover", AsyncMock())
    monkeypatch.setattr(app_module, "engine", engine)
    monkeypatch.setattr(app_module.MemoryIndexService, "recover", AsyncMock())
    monkeypatch.setattr(app_module.MemoryIndexService, "reconcile", AsyncMock())
    episode_recover = AsyncMock()
    episode_reconcile = AsyncMock()
    monkeypatch.setattr(app_module.EpisodeIndexService, "recover", episode_recover)
    monkeypatch.setattr(app_module.EpisodeIndexService, "reconcile", episode_reconcile)
    monkeypatch.setattr(app_module.runtime_config, "embedding_enabled", False)
    monkeypatch.setattr(app_module.MemoryIndexScheduler, "start", Mock())
    monkeypatch.setattr(app_module.MemoryIndexScheduler, "stop", AsyncMock())
    monkeypatch.setattr(app_module.EpisodeIndexScheduler, "start", Mock())
    monkeypatch.setattr(app_module.EpisodeIndexScheduler, "stop", AsyncMock())
    monkeypatch.setattr(app_module.AutomationScheduler, "start", Mock())
    monkeypatch.setattr(app_module.AutomationScheduler, "stop", AsyncMock())
    monkeypatch.setattr(app_module.embedding_client, "close", AsyncMock())
    session_storage = Mock()
    session_storage.reconcile.return_value = Mock(provisioned=(), restored=(), trashed=())
    monkeypatch.setattr(app_module, "SessionStorage", Mock(return_value=session_storage))

    async with app_module.lifespan(None):
        pass

    statements = [call.args[0] for call in db.execute.await_args_list]
    approval_update = next(
        statement for statement in statements if statement.table.name == "approval_requests"
    )
    title_update = next(
        statement
        for statement in statements
        if statement.table.name == "chat_sessions"
    )
    approval_params = list(approval_update.compile().params.values())

    recover_runs.assert_awaited_once_with(db)
    episode_recover.assert_awaited_once_with(db)
    episode_reconcile.assert_awaited_once_with(db, retry_failed=True)
    assert {"expired", "pending", "agent_action"}.issubset(approval_params)
    assert "resolved_at=" in str(approval_update)
    title_params = list(title_update.compile().params.values())
    assert TitleGenerationStatus.RUNNING.value in title_params
    assert TitleGenerationStatus.FAILED.value in title_params
    assert db.commit.await_count == 2
    engine.dispose.assert_awaited_once()


async def test_startup_recovery_persists_one_error_message_per_active_run(monkeypatch) -> None:
    run = AgentRun(
        id=uuid4(),
        session_id=uuid4(),
        status="running",
        model_name="Test model",
        trust_level="private",
    )
    rows = Mock()
    rows.all.return_value = [run]
    db = AsyncMock(spec=AsyncSession)
    db.scalars.return_value = rows
    db.scalar.return_value = None
    db.add.side_effect = lambda message: setattr(message, "id", uuid4())
    terminalize = AsyncMock()
    monkeypatch.setattr(AgentRuntime, "_terminalize_incomplete_tool_calls", terminalize)

    recovered = await AgentRuntime.recover(db)

    message = db.add.call_args.args[0]
    assert recovered == 1
    assert run.status == "failed"
    assert run.error == "agent.backendRestarted"
    assert message.error_run_id == run.id
    assert message.error_code == "agent.backendRestarted"
    assert message.error_params is None
    assert message.content == ""
    terminalize.assert_awaited_once_with(db, run.session_id)
    db.flush.assert_awaited()
    db.commit.assert_not_awaited()


async def test_persisted_approval_without_live_waiter_cannot_resume_run(monkeypatch) -> None:
    approval = ApprovalRequest(
        id=uuid4(),
        request_type="agent_action",
        user_id=uuid4(),
        session_id=uuid4(),
        run_id=uuid4(),
        action_kind="read_session",
        title="Session lesen?",
        summary="Der Agent möchte eine Session lesen.",
        capability="session.read",
        resource_type="session",
        resource_scope=str(uuid4()),
        available_decisions=["allow_once", "allow_session", "deny"],
        status="pending",
    )
    user = UserAccount(id=approval.user_id, username="test", password_hash="hash")
    db = AsyncMock(spec=AsyncSession)
    db.get.return_value = approval
    monkeypatch.setattr(AgentRuntime, "resolve_approval", lambda *_args: False)

    with pytest.raises(ApiError) as exc_info:
        await decide_approval(
            approval.id,
            ApprovalDecisionRequest(decision="allow_session"),
            user,
            db,
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.code == "approval.runInactive"
    assert approval.status == "expired"
    assert approval.decision is None
    db.commit.assert_awaited_once()
