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
from mato_loop_backend.models import (
    AgentRun,
    ChatSession,
    ExecutionAgent,
    ExecutionAgentRoot,
)
from mato_loop_backend.settings import AiSettings, SettingsStore
from mato_loop_backend.settings.models import LlmProviderConfig, ModelConfig


def _agent_and_root(user_id=None):
    agent = ExecutionAgent(
        id=uuid4(),
        user_id=user_id or uuid4(),
        name="Desktop",
        token_hash="a" * 64,
        revision=7,
        protocol_version=3,
        operations=["workspace.list", "workspace.read", "workspace.write"],
    )
    root = ExecutionAgentRoot(
        execution_agent_id=agent.id,
        id=uuid4(),
        label="Projects",
        revision="b" * 64,
    )
    agent.roots = [root]
    return agent, root


def test_execution_session_models_define_preferences_and_run_snapshots() -> None:
    session_agent_fk = next(iter(ChatSession.__table__.c.preferred_execution_agent_id.foreign_keys))
    run_agent_fk = next(iter(AgentRun.__table__.c.execution_agent_id.foreign_keys))

    assert session_agent_fk.ondelete == "SET NULL"
    assert run_agent_fk.ondelete == "SET NULL"
    assert AgentRun.__table__.c.execution_operations.type.compile(
        dialect=postgresql.dialect()
    ) == "JSONB"
    assert ChatSession.__table__.c.preferred_root_id.nullable is True
    assert AgentRun.__table__.c.working_directory_snapshot.nullable is True


async def test_preferences_require_an_executor_for_root_and_workdir() -> None:
    db = AsyncMock(spec=AsyncSession)

    with pytest.raises(ApiError) as root_error:
        await _validate_execution_preferences(db, uuid4(), None, uuid4(), None)
    with pytest.raises(ApiError) as directory_error:
        await _validate_execution_preferences(db, uuid4(), None, None, "project")

    assert root_error.value.field == "preferredRootId"
    assert directory_error.value.code == "session.invalidWorkingDirectory"
    db.scalar.assert_not_awaited()


async def test_preferences_validate_owned_root_and_normalized_relative_workdir() -> None:
    user_id = uuid4()
    agent, root = _agent_and_root(user_id)
    db = AsyncMock(spec=AsyncSession)
    db.scalar.return_value = agent

    assert (
        await _validate_execution_preferences(
            db, user_id, agent.id, root.id, "projects/mato-loop"
        )
        == ("projects/mato-loop", None, root.id, root.revision)
    )
    with pytest.raises(ApiError) as exc_info:
        await _validate_execution_preferences(
            db, user_id, agent.id, root.id, "projects/../outside"
        )

    assert exc_info.value.code == "session.invalidWorkingDirectory"
    assert exc_info.value.field == "workingDirectory"


async def test_preferences_resolve_a_connected_workspace_directory(monkeypatch) -> None:
    user_id = uuid4()
    agent, root = _agent_and_root(user_id)
    db = AsyncMock(spec=AsyncSession)
    db.scalar.return_value = agent
    request = AsyncMock(
        return_value=DirectoryResult(
            path="/srv/projects/mato-loop",
            root_id=root.id,
            root_revision=root.revision,
            relative_path="projects/mato-loop",
        )
    )
    monkeypatch.setattr(ExecutionClient, "request", request)
    monkeypatch.setattr(
        execution_agent_connections,
        "snapshot",
        lambda _agent_id: (frozenset({"workspace.resolve"}), (root,)),
    )

    assert await _validate_execution_preferences(
        db, user_id, agent.id, root.id, "projects/mato-loop"
    ) == ("projects/mato-loop", "/srv/projects/mato-loop", root.id, root.revision)
    request.assert_awaited_once_with(
        "workspace.resolve", {"path": "projects/mato-loop"}, DirectoryResult
    )


def test_effective_operations_follow_the_current_directory() -> None:
    run = AgentRun(
        execution_operations=[
            "filesystem.resolve",
            "integration.git.status",
            "process.exec",
            "workspace.read",
            "workspace.resolve",
        ]
    )

    assert _effective_execution_operations(run) == {"filesystem.resolve"}
    run.execution_root_id = uuid4()
    assert _effective_execution_operations(run) == {
        "filesystem.resolve",
        "workspace.read",
        "workspace.resolve",
    }
    run.current_directory_snapshot = "/srv/project"
    assert _effective_execution_operations(run) == set(run.execution_operations)


async def test_executor_preferences_cannot_change_during_active_run() -> None:
    user_id = uuid4()
    agent, root = _agent_and_root(user_id)
    session = ChatSession(
        id=uuid4(),
        user_id=user_id,
        title="Test",
        memory_mode="disabled",
        preferred_execution_agent_id=agent.id,
        preferred_root_id=root.id,
    )
    db = AsyncMock(spec=AsyncSession)
    db.get.return_value = session
    db.scalar.side_effect = [session.id, uuid4()]

    with pytest.raises(ApiError) as exc_info:
        await update_session(
            session.id,
            SessionUpdateRequest(preferred_root_id=uuid4()),
            SimpleNamespace(id=user_id),
            db,
        )

    assert exc_info.value.code == "session.runAlreadyActive"
    assert session.preferred_root_id == root.id


async def test_session_payload_reports_connected_effective_workspace_operations(
    monkeypatch,
) -> None:
    user_id = uuid4()
    agent, root = _agent_and_root(user_id)
    session = ChatSession(
        id=uuid4(),
        user_id=user_id,
        title="Test",
        memory_mode="disabled",
        preferred_execution_agent_id=agent.id,
        preferred_root_id=root.id,
        working_directory="project",
        workspace_access="read-only",
    )
    db = AsyncMock(spec=AsyncSession)
    db.scalar.return_value = agent
    live_root = SimpleNamespace(id=root.id, revision=root.revision)
    monkeypatch.setattr(
        execution_agent_connections,
        "snapshot",
        lambda _agent_id: (
            frozenset({"workspace.list", "workspace.read", "workspace.write"}),
            (live_root,),
        ),
    )

    payload = await _session_payload(db, session)

    assert payload["preferredExecutionAgentId"] == str(agent.id)
    assert payload["preferredRootId"] == str(root.id)
    assert payload["workspaceContext"] == {
        "executionAgentId": str(agent.id),
        "rootId": str(root.id),
        "connected": True,
        "operations": ["workspace.list", "workspace.read"],
    }


async def test_start_persists_live_executor_snapshot(monkeypatch) -> None:
    user_id = uuid4()
    agent, root = _agent_and_root(user_id)
    provider = LlmProviderConfig(name="Local", adapter_type="openai-compatible")
    model = ModelConfig(provider_id=provider.id, model_id="agent-model")
    settings = AiSettings(providers=[provider], models=[model], default_model_id=model.id)
    session = ChatSession(
        id=uuid4(),
        user_id=user_id,
        title="Test",
        model_config_id=model.id,
        preferred_execution_agent_id=agent.id,
        preferred_root_id=root.id,
        working_directory="project/subdir",
    )
    db = AsyncMock(spec=AsyncSession)
    db.scalar.return_value = agent
    monkeypatch.setattr(SettingsStore, "load", AsyncMock(return_value=settings))
    monkeypatch.setattr(EpisodeIndexService, "enqueue", AsyncMock())
    monkeypatch.setattr(AgentRuntime, "_schedule_run", lambda _run: None)
    resolve = AsyncMock(
        return_value=DirectoryResult(
            path="/srv/project/subdir",
            root_id=root.id,
            root_revision=root.revision,
            relative_path="project/subdir",
        )
    )
    monkeypatch.setattr(ExecutionClient, "request", resolve)
    monkeypatch.setattr(
        execution_agent_connections,
        "snapshot",
        lambda _agent_id: (
            frozenset({"workspace.resolve", "workspace.write", "workspace.read"}),
            (SimpleNamespace(id=root.id, revision=root.revision),),
        ),
    )

    run = await AgentRuntime.start(db, session, "Work")
    session.preferred_root_id = uuid4()
    session.working_directory = "changed"

    assert run.execution_agent_id == agent.id
    assert run.execution_agent_revision == 7
    assert run.execution_root_id == root.id
    assert run.execution_root_revision == root.revision
    assert run.working_directory_snapshot == "project/subdir"
    assert run.current_directory_snapshot == "/srv/project/subdir"
    assert run.execution_operations == [
        "workspace.read",
        "workspace.resolve",
        "workspace.write",
    ]
    resolve.assert_awaited_once_with(
        "workspace.resolve", {"path": "project/subdir"}, DirectoryResult
    )


async def test_start_preserves_live_operations_without_a_workspace(monkeypatch) -> None:
    user_id = uuid4()
    agent, _root = _agent_and_root(user_id)
    provider = LlmProviderConfig(name="Local", adapter_type="openai-compatible")
    model = ModelConfig(provider_id=provider.id, model_id="agent-model")
    session = ChatSession(
        id=uuid4(),
        user_id=user_id,
        title="Test",
        model_config_id=model.id,
        preferred_execution_agent_id=agent.id,
    )
    db = AsyncMock(spec=AsyncSession)
    db.scalar.return_value = agent
    monkeypatch.setattr(
        SettingsStore,
        "load",
        AsyncMock(
            return_value=AiSettings(
                providers=[provider], models=[model], default_model_id=model.id
            )
        ),
    )
    monkeypatch.setattr(EpisodeIndexService, "enqueue", AsyncMock())
    monkeypatch.setattr(AgentRuntime, "_schedule_run", lambda _run: None)
    monkeypatch.setattr(
        execution_agent_connections,
        "snapshot",
        lambda _agent_id: (
            frozenset(
                {
                    "workspace.read",
                    "system.info",
                    "filesystem.resolve",
                    "integration.openscad.render",
                    "cache.export",
                }
            ),
            (),
        ),
    )

    run = await AgentRuntime.start(db, session, "Create a cube")

    assert run.execution_root_id is None
    assert run.execution_operations == [
        "cache.export",
        "filesystem.resolve",
        "integration.openscad.render",
        "system.info",
        "workspace.read",
    ]


async def test_start_keeps_disconnected_executor_without_live_operations(monkeypatch) -> None:
    user_id = uuid4()
    agent, root = _agent_and_root(user_id)
    provider = LlmProviderConfig(name="Local", adapter_type="openai-compatible")
    model = ModelConfig(provider_id=provider.id, model_id="agent-model")
    session = ChatSession(
        id=uuid4(),
        user_id=user_id,
        title="Test",
        model_config_id=model.id,
        preferred_execution_agent_id=agent.id,
        preferred_root_id=root.id,
    )
    db = AsyncMock(spec=AsyncSession)
    db.scalar.return_value = agent
    monkeypatch.setattr(
        SettingsStore,
        "load",
        AsyncMock(
            return_value=AiSettings(
                providers=[provider], models=[model], default_model_id=model.id
            )
        ),
    )
    monkeypatch.setattr(execution_agent_connections, "snapshot", lambda _agent_id: None)

    monkeypatch.setattr(EpisodeIndexService, "enqueue", AsyncMock())
    monkeypatch.setattr(AgentRuntime, "_schedule_run", lambda _run: None)

    run = await AgentRuntime.start(db, session, "Work")

    assert run.execution_agent_id == agent.id
    assert run.execution_operations == []
