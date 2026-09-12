from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.agent.permissions import (
    PermissionDecision,
    PermissionPolicy,
    execution_directory_permission,
)
from mato_loop_backend.agent.runtime import AgentRuntime, _effective_execution_operations
from mato_loop_backend.api.errors import ApiError
from mato_loop_backend.api.routes.sessions import (
    SessionUpdateRequest,
    _session_payload,
    update_session,
)
from mato_loop_backend.execution import (
    DirectoryResult,
    ExecutionClient,
    execution_agent_connections,
)
from mato_loop_backend.memory.episode_indexing import EpisodeIndexService
from mato_loop_backend.models import AgentRun, ChatSession, ExecutionAgent, PermissionGrant
from mato_loop_backend.sessions.service import (
    SessionServiceError,
    add_directory_grant,
    validate_execution_preferences,
)
from mato_loop_backend.settings import AiSettings, SettingsStore, ToolExecutionPolicy, TrustLevel
from mato_loop_backend.settings.models import LlmProviderConfig, ModelConfig
from mato_loop_backend.workspace import RootFileService


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
            "home_shell_enabled": True,
            "external_shell_enabled": True,
            **values,
        },
    )


def test_execution_session_models_define_agent_foreign_keys_and_jsonb_operations() -> None:
    session_agent_fk = next(iter(ChatSession.__table__.c.preferred_execution_agent_id.foreign_keys))
    run_agent_fk = next(iter(AgentRun.__table__.c.execution_agent_id.foreign_keys))
    assert session_agent_fk.ondelete == run_agent_fk.ondelete == "SET NULL"
    assert AgentRun.__table__.c.execution_operations.type.compile(
        dialect=postgresql.dialect()
    ) == "JSONB"


async def test_preferences_require_an_executor_for_current_directory() -> None:
    db = AsyncMock(spec=AsyncSession)
    with pytest.raises(SessionServiceError) as error:
        await validate_execution_preferences(db, uuid4(), None, "/project")
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

    assert await validate_execution_preferences(
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
    with pytest.raises(SessionServiceError) as error:
        await validate_execution_preferences(db, user_id, agent.id, "/srv")
    assert error.value.code == "permission.denied"


@pytest.mark.parametrize("zone_policy", ["auto", "ask"])
@pytest.mark.parametrize("trust", list(TrustLevel))
async def test_directory_grant_is_bound_to_profile_and_model_trust(zone_policy, trust) -> None:
    db = AsyncMock(spec=AsyncSession)
    db.scalar.return_value = None
    profile_id = uuid4()
    agent_id = uuid4()

    await add_directory_grant(
        db,
        uuid4(),
        agent_id,
        "/srv/project",
        zone_policy,
        profile_id,
        trust.value,
    )

    grant = db.add.call_args.args[0]
    assert isinstance(grant, PermissionGrant)
    assert grant.agent_profile_id == profile_id
    assert grant.trust_level == trust.value

    session = ChatSession(id=grant.session_id, user_id=uuid4(), current_directory="/srv/project")
    agent = _agent(session.user_id, external_filesystem_policy=zone_policy)
    agent.id = agent_id
    db.scalar.return_value = agent
    db.scalars.return_value = Mock(all=Mock(return_value=[grant]))
    request = execution_directory_permission(agent_id, "/srv/project/src")
    roots = RootFileService(session.id)
    assert await PermissionPolicy.evaluate(
        db, session, request, roots, ToolExecutionPolicy.ASK,
        agent_profile_id=profile_id, trust_level=trust,
    ) == PermissionDecision.ALLOW
    db.scalars.return_value.all.return_value = []
    assert await PermissionPolicy.evaluate(
        db, session, request, roots, ToolExecutionPolicy.ASK,
        agent_profile_id=profile_id, trust_level=trust,
    ) == PermissionDecision.ASK

    db.reset_mock()
    await add_directory_grant(
        db,
        uuid4(),
        uuid4(),
        "/srv/project",
        "ask",
        profile_id,
        None,
    )
    db.add.assert_not_called()


@pytest.mark.parametrize("policy", [None, "deny"])
async def test_directory_grant_is_not_created_for_unavailable_zone(policy):
    db = AsyncMock(spec=AsyncSession)
    await add_directory_grant(db, uuid4(), uuid4(), "/srv/project", policy, uuid4(), "public")
    db.add.assert_not_called()
    db.scalar.assert_not_awaited()


def test_effective_operations_filter_directory_dependent_tools() -> None:
    run = AgentRun(
        execution_operations=[
            "filesystem.resolve",
            "filesystem.read",
            "integration.openscad.render",
            "shell.run",
        ]
    )
    assert _effective_execution_operations(run) == {
        "filesystem.resolve",
        "integration.openscad.render",
    }
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
