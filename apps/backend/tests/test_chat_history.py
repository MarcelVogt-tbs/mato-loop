from unittest.mock import AsyncMock, MagicMock, Mock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

import mato_loop_backend.agent.loop as mock_agent_loop
import mato_loop_backend.agent.runtime as mock_agent_runtime
import mato_loop_backend.agent.titles as mock_agent_titles
from mato_loop_backend.agent.run_contracts import AgentRunError
from mato_loop_backend.agent.runtime import AgentRuntime
from mato_loop_backend.chat_history import ChatHistoryError, ChatHistoryService
from mato_loop_backend.models import AgentRun, ChatSession, Message, UserAccount


@pytest.fixture(autouse=True)
def _no_live_descendant_tasks(monkeypatch):
    monkeypatch.setattr(AgentRuntime, "cancel_descendants", AsyncMock(), raising=False)


def _records(session_id):
    return [
        Message(id=uuid4(), session_id=session_id, role="user", content="Erster Auftrag"),
        Message(id=uuid4(), session_id=session_id, role="assistant", content="Erste Antwort"),
        Message(id=uuid4(), session_id=session_id, role="user", content="Wiederherstellen"),
        Message(id=uuid4(), session_id=session_id, role="assistant", content="Entfernen"),
    ]


async def test_undo_removes_user_message_suffix_and_returns_draft(monkeypatch) -> None:
    user = UserAccount(id=uuid4(), username="test", password_hash="hash")
    session = ChatSession(id=uuid4(), user_id=user.id, title="Test")
    messages = _records(session.id)
    db = AsyncMock(spec=AsyncSession)
    remove_suffix = AsyncMock()
    db.scalars.return_value = MagicMock(all=lambda: [])
    db.execute.return_value = MagicMock(all=lambda: [])
    commit = AsyncMock()
    monkeypatch.setattr(
        ChatHistoryService, "_lock_after_cancelling_active_run", AsyncMock(return_value=session)
    )
    monkeypatch.setattr(ChatHistoryService, "_messages", AsyncMock(return_value=messages))
    monkeypatch.setattr(ChatHistoryService, "_delete_message_suffix", remove_suffix)
    monkeypatch.setattr(ChatHistoryService, "_commit", commit)

    result = await ChatHistoryService.undo_user_message(
        db, session_id=session.id, user_id=user.id, message_id=messages[2].id
    )

    assert result.content == "Wiederherstellen"
    assert result.session_deleted is False
    remove_suffix.assert_awaited_once_with(db, messages, 2, include_boundary=True)
    commit.assert_awaited_once_with(db)


async def test_undo_cancels_active_run_before_locking_session(monkeypatch) -> None:
    user = UserAccount(id=uuid4(), username="test", password_hash="hash")
    session = ChatSession(id=uuid4(), user_id=user.id, title="Test")
    run_id = uuid4()
    events = []

    class ActiveRun:
        @property
        def id(self):
            events.append("run_id")
            return run_id

    run = ActiveRun()
    db = AsyncMock(spec=AsyncSession)
    db.rollback.side_effect = lambda: events.append("rollback")
    monkeypatch.setattr(ChatHistoryService, "_owned_session", AsyncMock(return_value=session))
    monkeypatch.setattr(
        ChatHistoryService,
        "_active_run",
        AsyncMock(side_effect=[run, None, None]),
    )
    cancel = AsyncMock(side_effect=lambda _run_id: events.append("cancel"))
    monkeypatch.setattr(AgentRuntime, "cancel_and_wait", cancel)

    result = await ChatHistoryService._lock_after_cancelling_active_run(
        db, session.id, user.id
    )

    assert result is session
    cancel.assert_awaited_once_with(run_id)
    assert events[:3] == ["run_id", "rollback", "cancel"]
    assert db.rollback.await_count == 2


async def test_undo_of_first_user_message_deletes_session(monkeypatch) -> None:
    user = UserAccount(id=uuid4(), username="test", password_hash="hash")
    session = ChatSession(id=uuid4(), user_id=user.id, title="Test")
    messages = _records(session.id)
    db = AsyncMock(spec=AsyncSession)
    db.execute.return_value = MagicMock(all=lambda: [])
    delete_session = AsyncMock()
    monkeypatch.setattr(
        ChatHistoryService, "_lock_after_cancelling_active_run", AsyncMock(return_value=session)
    )
    monkeypatch.setattr(ChatHistoryService, "_messages", AsyncMock(return_value=messages))
    monkeypatch.setattr(ChatHistoryService, "_delete_session", delete_session)

    result = await ChatHistoryService.undo_user_message(
        db, session_id=session.id, user_id=user.id, message_id=messages[0].id
    )

    assert result.session_deleted is True
    assert result.content == "Erster Auftrag"
    delete_session.assert_awaited_once_with(db, session)


async def test_retry_keeps_user_message_and_restarts_original_failed_run(monkeypatch) -> None:
    user = UserAccount(id=uuid4(), username="test", password_hash="hash")
    session = ChatSession(id=uuid4(), user_id=user.id, title="Test")
    failed_run = AgentRun(id=uuid4(), session_id=session.id, status="failed")
    messages = _records(session.id)[:3]
    error = Message(
        id=uuid4(),
        session_id=session.id,
        role="assistant",
        content="",
        error_code="provider.requestFailed",
        error_run_id=failed_run.id,
    )
    messages.append(error)
    replacement_run = AgentRun(id=uuid4(), session_id=session.id, status="queued")
    db = AsyncMock(spec=AsyncSession)
    db.get.return_value = failed_run
    db.execute.return_value = MagicMock(all=lambda: [])
    remove_suffix = AsyncMock()
    retry = AsyncMock(return_value=replacement_run)
    monkeypatch.setattr(ChatHistoryService, "_owned_session", AsyncMock(return_value=session))
    monkeypatch.setattr(ChatHistoryService, "_active_run", AsyncMock(return_value=None))
    monkeypatch.setattr(ChatHistoryService, "_messages", AsyncMock(return_value=messages))
    monkeypatch.setattr(ChatHistoryService, "_delete_message_suffix", remove_suffix)
    monkeypatch.setattr(AgentRuntime, "retry", retry)

    result = await ChatHistoryService.retry_failed_message(
        db, session_id=session.id, user_id=user.id, message_id=error.id
    )

    assert result.run is replacement_run
    remove_suffix.assert_awaited_once_with(db, messages, 2, include_boundary=False)
    retry.assert_awaited_once_with(db, session, failed_run)


async def test_retry_rejects_a_non_latest_error(monkeypatch) -> None:
    user = UserAccount(id=uuid4(), username="test", password_hash="hash")
    session = ChatSession(id=uuid4(), user_id=user.id, title="Test")
    messages = _records(session.id)
    old_error = Message(
        id=uuid4(),
        session_id=session.id,
        role="assistant",
        content="",
        error_code="provider.requestFailed",
        error_run_id=uuid4(),
    )
    messages.insert(2, old_error)
    db = AsyncMock(spec=AsyncSession)
    monkeypatch.setattr(ChatHistoryService, "_owned_session", AsyncMock(return_value=session))
    monkeypatch.setattr(ChatHistoryService, "_active_run", AsyncMock(return_value=None))
    monkeypatch.setattr(ChatHistoryService, "_messages", AsyncMock(return_value=messages))

    with pytest.raises(ChatHistoryError) as exc_info:
        await ChatHistoryService.retry_failed_message(
            db, session_id=session.id, user_id=user.id, message_id=old_error.id
        )

    assert exc_info.value.code == "session.retryUnavailable"


async def test_runtime_retry_reuses_failed_run_model(monkeypatch) -> None:
    pass
    from mato_loop_backend.settings import AiSettings, SettingsStore
    from mato_loop_backend.settings.models import AgentProfile, LlmProviderConfig, ModelConfig

    provider = LlmProviderConfig(name="Local", adapter_type="openai-compatible")
    model = ModelConfig(provider_id=provider.id, model_id="original")
    current_profile = AgentProfile(name="Current", instructions="Task")
    session = ChatSession(
        id=uuid4(),
        user_id=uuid4(),
        title="Test",
        agent_profile_id=current_profile.id,
    )
    failed_run = AgentRun(
        id=uuid4(),
        session_id=session.id,
        status="failed",
        model_config_id=model.id,
        model_name="Originalmodell",
        trust_level="private",
        pseudonymization_enabled=True,
        agent_profile_id=uuid4(),
    )
    db = AsyncMock(spec=AsyncSession)
    message = Message(id=uuid4(), session_id=session.id, role="user", content="Retry")
    failed_run.input_message_id = message.id
    db.get.return_value = message
    monkeypatch.setattr(
        SettingsStore,
        "load",
        AsyncMock(return_value=AiSettings(providers=[provider], models=[model])),
    )
    _mock_value = AsyncMock(return_value=current_profile)
    for _mock_target in (mock_agent_runtime, mock_agent_loop, mock_agent_titles,):
        monkeypatch.setattr(_mock_target, 'load_agent_profile', _mock_value)
    schedule = Mock()
    monkeypatch.setattr(AgentRuntime, "_schedule_run", schedule)

    run = await AgentRuntime.retry(db, session, failed_run)

    assert run.model_config_id == model.id
    assert run.model_name == "Originalmodell"
    assert run.trust_level == "private"
    assert run.pseudonymization_enabled is True
    assert run.agent_profile_id == current_profile.id
    db.commit.assert_awaited_once()
    schedule.assert_called_once_with(run)


async def test_runtime_retry_rejects_unowned_or_revoked_execution_agent(
    monkeypatch,
) -> None:
    from mato_loop_backend.settings import AiSettings, SettingsStore
    from mato_loop_backend.settings.models import LlmProviderConfig, ModelConfig

    provider = LlmProviderConfig(name="Local", adapter_type="openai-compatible")
    model = ModelConfig(provider_id=provider.id, model_id="original")
    session = ChatSession(id=uuid4(), user_id=uuid4(), title="Test")
    failed_run = AgentRun(
        id=uuid4(),
        session_id=session.id,
        status="failed",
        model_config_id=model.id,
        execution_agent_id=uuid4(),
    )
    db = AsyncMock(spec=AsyncSession)
    db.scalar.side_effect = [True, None]
    monkeypatch.setattr(
        SettingsStore,
        "load",
        AsyncMock(return_value=AiSettings(providers=[provider], models=[model])),
    )

    with pytest.raises(AgentRunError) as exc_info:
        await AgentRuntime.retry(db, session, failed_run)

    assert exc_info.value.code == "executionAgent.unavailable"
