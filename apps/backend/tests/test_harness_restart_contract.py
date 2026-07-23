from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

import mato_loop_backend.app as app_module
from mato_loop_backend.agent import AgentRuntime
from mato_loop_backend.api.errors import ApiError
from mato_loop_backend.api.routes.approvals import ApprovalDecisionRequest, decide_approval
from mato_loop_backend.models import (
    ApprovalRequest,
    ChatSession,
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
    engine = Mock(dispose=AsyncMock())
    monkeypatch.setattr(app_module, "AsyncSessionFactory", lambda: _SessionContext(db))
    monkeypatch.setattr(app_module, "engine", engine)
    monkeypatch.setattr(app_module.MemoryIndexService, "recover", AsyncMock())
    monkeypatch.setattr(app_module.MemoryIndexService, "reconcile", AsyncMock())
    monkeypatch.setattr(app_module.MemoryIndexScheduler, "start", Mock())
    monkeypatch.setattr(app_module.MemoryIndexScheduler, "stop", AsyncMock())
    monkeypatch.setattr(app_module.embedding_client, "close", AsyncMock())

    async with app_module.lifespan(None):
        pass

    statements = [call.args[0] for call in db.execute.await_args_list]
    run_update = next(statement for statement in statements if statement.table.name == "agent_runs")
    approval_update = next(
        statement for statement in statements if statement.table.name == "approval_requests"
    )
    title_update = next(
        statement
        for statement in statements
        if statement.table.name == "chat_sessions"
    )
    run_params = list(run_update.compile().params.values())
    approval_params = list(approval_update.compile().params.values())

    assert "failed" in run_params
    assert "agent.backendRestarted" in run_params
    assert ["queued", "running", "waiting_approval"] in run_params
    assert "expired" in approval_params
    assert "pending" in approval_params
    title_params = list(title_update.compile().params.values())
    assert TitleGenerationStatus.RUNNING.value in title_params
    assert TitleGenerationStatus.FAILED.value in title_params
    db.commit.assert_awaited_once()
    engine.dispose.assert_awaited_once()


async def test_persisted_approval_without_live_waiter_cannot_resume_run(monkeypatch) -> None:
    approval = ApprovalRequest(
        id=uuid4(),
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
    user = UserAccount(id=uuid4(), username="test", password_hash="hash")
    session = ChatSession(id=approval.session_id, user_id=user.id, title="Test")
    db = AsyncMock(spec=AsyncSession)
    db.get.side_effect = [approval, session]
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
