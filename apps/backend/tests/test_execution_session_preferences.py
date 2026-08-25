from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.agent.runtime import AgentRuntime, _effective_execution_operations
from mato_loop_backend.api.errors import ApiError
from mato_loop_backend.api.routes.sessions import (
    SessionUpdateRequest,
    _session_payload,
    _validate_execution_preferences,
    update_session,
)
from mato_loop_backend.execution import (
    DirectoryResult,
    ExecutionClient,
    execution_agent_connections,
)
from mato_loop_backend.memory.episode_indexing import EpisodeIndexService
from mato_loop_backend.models import AgentRun, ChatSession, ExecutionAgent
from mato_loop_backend.settings import AiSettings, SettingsStore
from mato_loop_backend.settings.models import LlmProviderConfig, ModelConfig


def _agent(user_id=None, **values):
    return ExecutionAgent(
        id=uuid4(),
        user_id=user_id or uuid4(),
        alias="Desktop",
        token_hash="a" * 64,
        revision=7,
        operations=["filesystem.list", "filesystem.read", "filesystem.write"],
        home_directory="/home/user",
        **{
            "home_filesystem_policy": "auto",
            "external_filesystem_policy": "ask",
            "home_shell_policy": "ask",
            "external_shell_policy": "ask",
            **values,
        },
    )


def test_execution_session_models_keep_only_absolute_directory_snapshots() -> None:
    session_agent_fk = next(iter(ChatSession.__table__.c.preferred_execution_agent_id.foreign_keys))
    run_agent_fk = next(iter(AgentRun.__table__.c.execution_agent_id.foreign_keys))
    assert session_agent_fk.ondelete == run_agent_fk.ondelete == "SET NULL"
    assert AgentRun.__table__.c.execution_operations.type.compile(
        dialect=postgresql.dialect()
    ) == "JSONB"
    assert "preferred_root_id" not in ChatSession.__table__.c
    assert "working_directory" not in ChatSession.__table__.c
    assert "execution_root_id" not in AgentRun.__table__.c
    assert "working_directory_snapshot" not in AgentRun.__table__.c


async def test_preferences_require_an_executor_for_current_directory() -> None:
    db = AsyncMock(spec=AsyncSession)
    with pytest.raises(ApiError) as error:
        await _validate_execution_preferences(db, uuid4(), None, "/project")
    assert error.value.field == "currentDirectory"
    db.scalar.assert_not_awaited()


async def test_preferences_resolve_absolute_directory_and_return_policy(monkeypatch) -> None:
    user_id = uuid4()
    agent = _agent(user_id)
    db = AsyncMock(spec=AsyncSession)
    db.scalar.return_value = agent
    request = AsyncMock(return_value=DirectoryResult(path="/home/user/project"))
    monkeypatch.setattr(ExecutionClient, "request", request)
    monkeypatch.setattr(
        execution_agent_connections,
        "snapshot",
        lambda _agent_id: frozenset({"filesystem.resolve"}),
    )

    assert await _validate_execution_preferences(
        db, user_id, agent.id, "/home/user/project"
    ) == ("/home/user/project", "auto")
    request.assert_awaited_once_with(
        "filesystem.resolve", {"path": "/home/user/project"}, DirectoryResult
    )


async def test_preferences_reject_denied_external_directory(monkeypatch) -> None:
    user_id = uuid4()
    agent = _agent(user_id, external_filesystem_policy="deny")
    db = AsyncMock(spec=AsyncSession)
    db.scalar.return_value = agent
    monkeypatch.setattr(ExecutionClient, "request", AsyncMock(return_value=DirectoryResult(path="/srv")))
    monkeypatch.setattr(
        execution_agent_connections,
        "snapshot",
        lambda _agent_id: frozenset({"filesystem.resolve"}),
    )
    with pytest.raises(ApiError) as error:
        await _validate_execution_preferences(db, user_id, agent.id, "/srv")
    assert error.value.code == "permission.denied"


def test_effective_operations_require_a_current_directory() -> None:
    run = AgentRun(
        execution_operations=[
            "filesystem.resolve",
            "filesystem.read",
            "integration.git.status",
            "process.exec",
        ]
    )
    assert _effective_execution_operations(run) == {"filesystem.resolve"}
    run.current_directory_snapshot = "/home/user"
    assert _effective_execution_operations(run) == set(run.execution_operations)


async def test_executor_preferences_cannot_change_during_active_run() -> None:
    user_id = uuid4()
    agent = _agent(user_id)
    session = ChatSession(
        id=uuid4(),
        user_id=user_id,
        title="Test",
        memory_mode="disabled",
        preferred_execution_agent_id=agent.id,
        current_directory="/home/user",
    )
    db = AsyncMock(spec=AsyncSession)
    db.get.return_value = session
    db.scalar.side_effect = [session.id, uuid4()]
    with pytest.raises(ApiError) as error:
        await update_session(
            session.id,
            SessionUpdateRequest(current_directory="/home/user/project"),
            SimpleNamespace(id=user_id),
            db,
        )
    assert error.value.code == "session.runAlreadyActive"


async def test_session_payload_reports_connected_operations(monkeypatch) -> None:
    user_id = uuid4()
    agent = _agent(user_id)
    session = ChatSession(
        id=uuid4(),
        user_id=user_id,
        title="Test",
        memory_mode="disabled",
        preferred_execution_agent_id=agent.id,
        current_directory="/home/user",
    )
    db = AsyncMock(spec=AsyncSession)
    db.scalar.return_value = agent
    monkeypatch.setattr(
        execution_agent_connections,
        "snapshot",
        lambda _agent_id: frozenset({"filesystem.list", "filesystem.read"}),
    )
    payload = await _session_payload(db, session)
    assert payload["preferredExecutionAgentId"] == str(agent.id)
    assert "preferredRootId" not in payload
    assert "workingDirectory" not in payload
    assert payload["workspaceContext"] == {
        "executionAgentId": str(agent.id),
        "connected": True,
        "operations": ["filesystem.list", "filesystem.read"],
    }


async def test_start_persists_rootless_executor_snapshot(monkeypatch) -> None:
    user_id = uuid4()
    agent = _agent(user_id)
    provider = LlmProviderConfig(name="Local", adapter_type="openai-compatible")
    model = ModelConfig(provider_id=provider.id, model_id="agent-model")
    settings = AiSettings(providers=[provider], models=[model], default_model_id=model.id)
    session = ChatSession(
        id=uuid4(),
        user_id=user_id,
        title="Test",
        model_config_id=model.id,
        preferred_execution_agent_id=agent.id,
        current_directory="/home/user/project",
    )
    db = AsyncMock(spec=AsyncSession)
    db.scalar.side_effect = [session, None, True, agent, True]
    monkeypatch.setattr(SettingsStore, "load", AsyncMock(return_value=settings))
    monkeypatch.setattr(EpisodeIndexService, "enqueue", AsyncMock())
    monkeypatch.setattr(AgentRuntime, "_schedule_run", lambda _run: None)
    monkeypatch.setattr(
        ExecutionClient,
        "request",
        AsyncMock(return_value=DirectoryResult(path="/home/user/project")),
    )
    monkeypatch.setattr(
        execution_agent_connections,
        "snapshot",
        lambda _agent_id: frozenset({"filesystem.resolve", "filesystem.read"}),
    )
    run = await AgentRuntime.start(db, session, "Work")
    assert run.execution_agent_id == agent.id
    assert run.current_directory_snapshot == "/home/user/project"
    assert run.execution_operations == ["filesystem.read", "filesystem.resolve"]
