from contextlib import nullcontext
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

import mato_loop_backend.app as app_module
from mato_loop_backend.agent.runtime import AgentRuntime
from mato_loop_backend.api.errors import ApiError
from mato_loop_backend.api.routes.approvals import ApprovalDecisionRequest, decide_approval
from mato_loop_backend.error_contract import describe_error
from mato_loop_backend.models import (
    AgentRun,
    ApprovalRequest,
    Message,
    TitleGenerationStatus,
    UserAccount,
)


@pytest.mark.parametrize("restored", [False, True])
async def test_startup_expires_in_memory_run_state_instead_of_resuming(monkeypatch, restored) -> None:
    db = AsyncMock(spec=AsyncSession)
    monkeypatch.setattr(app_module, "initialize_skill_library", AsyncMock())
    order = []
    backfill = AsyncMock(side_effect=lambda _db: order.append("backfill"))
    monkeypatch.setattr(app_module.AttachmentService, "backfill_derived_files", backfill)
    monkeypatch.setattr(app_module.SettingsStore, "load", AsyncMock(return_value=app_module.InstanceLimits()))
    attachments = Mock()
    attachments.all.return_value = []
    db.scalars.return_value = attachments
    rows = Mock()
    rows.all.return_value = []
    db.execute.return_value = rows
    engine = Mock(dispose=AsyncMock())
    monkeypatch.setattr(app_module, "AsyncSessionFactory", lambda: nullcontext(db))
    recover_runs = AsyncMock(side_effect=lambda _db: order.append("recover"))
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
    def reconcile(*_args, **_kwargs):
        order.append("reconcile")
        return Mock(provisioned=(), restored=(uuid4(),) if restored else (), trashed=())

    session_storage.reconcile.side_effect = reconcile
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
    assert order == ["backfill", "recover", "reconcile", *(["backfill"] if restored else [])]
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
    monkeypatch.setattr(AgentRuntime, "_finalize_run_files", AsyncMock())
    run = AgentRun(
        id=uuid4(),
        session_id=uuid4(),
        status="running",
        model_name="Test model",
        trust_level="private",
        agent_profile_id=uuid4(),
    )
    rows = Mock()
    rows.all.return_value = [run]
    db = AsyncMock(spec=AsyncSession)
    db.scalars.side_effect = [rows, Mock(all=Mock(return_value=[]))]
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
    assert message.provider_data == {
        "_matoloop": {
            "runId": str(run.id),
            "agentProfileId": str(run.agent_profile_id),
        }
    }
    terminalize.assert_awaited_once_with(db, run.session_id, run_id=run.id)
    db.flush.assert_awaited()
    db.commit.assert_not_awaited()


async def test_startup_recovery_keeps_child_failure_internal(monkeypatch) -> None:
    monkeypatch.setattr(AgentRuntime, "_finalize_run_files", AsyncMock())
    child = AgentRun(
        id=uuid4(),
        session_id=uuid4(),
        parent_run_id=uuid4(),
        status="running",
        trust_level="private",
        internal_request="Inspect",
        internal_transcript=[],
    )
    rows = Mock()
    rows.all.return_value = [child]
    db = AsyncMock(spec=AsyncSession)
    db.scalars.side_effect = [rows, Mock(all=Mock(return_value=[]))]
    terminalize = AsyncMock()
    monkeypatch.setattr(AgentRuntime, "_terminalize_incomplete_tool_calls", terminalize)

    recovered = await AgentRuntime.recover(db)

    assert recovered == 1
    assert child.status == "failed"
    assert child.error == "agent.backendRestarted"
    assert "agent.backendRestarted" in child.internal_result
    assert child.result_message_id is None
    db.add.assert_not_called()
    terminalize.assert_not_awaited()


async def test_startup_recovery_keeps_duration_completed_before_user_wait(monkeypatch) -> None:
    monkeypatch.setattr(AgentRuntime, "_finalize_run_files", AsyncMock())
    run = AgentRun(
        id=uuid4(),
        session_id=uuid4(),
        status="waiting_approval",
        model_name="Test model",
        trust_level="private",
        active_duration_ms=2_500,
        agent_profile_id=uuid4(),
    )
    rows = Mock()
    rows.all.return_value = [run]
    messages = []
    db = AsyncMock(spec=AsyncSession)
    db.scalars.side_effect = [rows, Mock(all=Mock(return_value=[]))]
    db.scalar.return_value = None
    db.add.side_effect = lambda message: (
        setattr(message, "id", uuid4()),
        messages.append(message),
    )
    db.get.side_effect = lambda model, _item_id: messages[0] if model is Message else None
    terminalize = AsyncMock()

    monkeypatch.setattr(AgentRuntime, "_terminalize_incomplete_tool_calls", terminalize)

    await AgentRuntime.recover(db)

    assert messages[0].provider_data == {
        "_matoloop": {
            "runId": str(run.id),
            "agentProfileId": str(run.agent_profile_id),
            "performance": {"activeDurationMs": 2_500},
        }
    }


async def test_existing_recovery_error_is_restamped_without_losing_diagnostics() -> None:
    run = AgentRun(id=uuid4(), session_id=uuid4(), agent_profile_id=uuid4())
    existing = Message(
        id=uuid4(), session_id=run.session_id, role="assistant", content="",
        error_code="agent.backendRestarted", error_run_id=run.id,
        provider_data={
            "usage": {"input_tokens": 10},
            "_matoloop": {
                "runId": str(uuid4()),
                "agentProfileId": str(uuid4()),
                "performance": {"providerDurationMs": 20},
            },
        },
    )
    db = AsyncMock(spec=AsyncSession)
    db.scalar.return_value = existing

    result = await AgentRuntime._persist_error_message(
        db, run, describe_error("agent.backendRestarted")
    )

    assert result is existing
    assert run.result_message_id == existing.id
    assert existing.provider_data == {
        "usage": {"input_tokens": 10},
        "_matoloop": {
            "runId": str(run.id),
            "agentProfileId": str(run.agent_profile_id),
            "performance": {"providerDurationMs": 20},
        },
    }
    db.flush.assert_awaited_once()


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
