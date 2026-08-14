import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.agent.tools import tool_registry
from mato_loop_backend.api.errors import ApiError
from mato_loop_backend.api.routes.integrations import (
    GitIntegrationRequest,
    create_git_integration,
    delete_git_integration,
    get_git_integrations,
    git_settings_payload,
    update_git_integration,
)
from mato_loop_backend.error_contract import describe_error
from mato_loop_backend.integrations.git import GIT_ADAPTER, GitConfig
from mato_loop_backend.integrations.registry import (
    effective_integration_contributions,
    integration_adapter,
)
from mato_loop_backend.integrations.service import (
    GitIntegrationService,
    IntegrationSettingsError,
)
from mato_loop_backend.settings import (
    IntegrationInstance,
    IntegrationSettings,
    SettingsStore,
    ToolExecutionPolicy,
    ToolSettings,
)


def _instance(agent_id=None, *, allow_network=False) -> IntegrationInstance:
    return IntegrationInstance(
        name="Git",
        integration_type="git",
        adapter_type="git",
        execution_agent_id=agent_id or uuid4(),
        config={"allow_network": allow_network},
    )


def test_git_adapter_contract_and_tool_schemas() -> None:
    assert integration_adapter("git", "git") is GIT_ADAPTER
    assert GIT_ADAPTER.execution_location == "agent"
    assert GIT_ADAPTER.execution_kind == "git"
    assert GitConfig().allow_network is False
    assert GitConfig.model_validate({"allowNetwork": True}).allow_network is True

    tools = {tool.name: tool for tool in GIT_ADAPTER.execution_tools}
    assert set(tools) == {
        "git_clone",
        "git_status",
        "git_diff",
        "git_log",
        "git_add",
        "git_commit",
    }
    assert {tool.execution_operation for tool in tools.values()} == {
        "integration.git.clone",
        "integration.git.status",
        "integration.git.diff",
        "integration.git.log",
        "integration.git.add",
        "integration.git.commit",
    }
    assert {name for name, tool in tools.items() if tool.write} == {
        "git_clone",
        "git_add",
        "git_commit",
    }
    assert {
        action.capability: action.default_policy
        for tool in tools.values()
        for action in tool.actions
    } == {
        "git.read": ToolExecutionPolicy.AUTO,
        "git.stage": ToolExecutionPolicy.ASK,
        "git.clone": ToolExecutionPolicy.ASK,
        "git.commit": ToolExecutionPolicy.ASK,
    }
    assert all(tool.result_model is not None for tool in tools.values())
    assert tools["git_clone"].execution_config_arguments == ("allowNetwork",)
    assert all(
        not tool.execution_config_arguments
        for name, tool in tools.items()
        if name != "git_clone"
    )
    with pytest.raises(ValidationError):
        tools["git_add"].arguments_model.model_validate(
            {"path": ".", "paths": ["README.md"], "unknown": True}
        )


def test_git_write_tools_are_hidden_in_read_only_mode() -> None:
    instance = _instance()
    instance._execution_operations = frozenset(
        tool.execution_operation for tool in GIT_ADAPTER.execution_tools
    )
    settings = IntegrationSettings(instances=[instance])
    contributions = effective_integration_contributions(settings)
    schemas = {schema["name"] for schema in tool_registry.schemas(settings, "read-only")}

    assert {tool.name for tool in contributions.tools} == {
        "git_clone",
        "git_status",
        "git_diff",
        "git_log",
        "git_add",
        "git_commit",
    }
    assert {"git_status", "git_diff", "git_log"} <= schemas
    assert {"git_clone", "git_add", "git_commit"}.isdisjoint(schemas)
    catalog = {
        tool["name"]: tool
        for group in tool_registry.catalog(ToolSettings(), settings)
        for tool in group["tools"]
    }
    assert catalog["git_status"]["toolType"] == "computer"


def test_git_approval_decisions_are_narrow_for_clone_and_commit() -> None:
    instance = _instance()
    context = SimpleNamespace(
        bound_options={
            "integration_id": str(instance.id),
            "binding_revision": json.dumps(
                instance.model_dump(mode="json"),
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            ),
        }
    )
    tools = {tool.name: tool for tool in GIT_ADAPTER.execution_tools}

    clone = tools["git_clone"].authorization_factory(
        tools["git_clone"].arguments_model(
            source="https://example.com/repository.git",
            destination="repository",
        ),
        context,
    )[0]
    commit = tools["git_commit"].authorization_factory(
        tools["git_commit"].arguments_model(
            message="Initial commit", expectedIndexTree="0" * 64
        ),
        context,
    )[0]

    assert clone.available_decisions == ("allow_once", "deny")
    assert commit.available_decisions == ("allow_once", "deny")
    assert clone.approval_details == {
        "git": {
            "source": "https://example.com/repository.git",
            "destination": "repository",
        }
    }


def test_git_payload_has_agent_config_and_no_executable() -> None:
    agent_id = uuid4()
    payload = git_settings_payload(
        IntegrationSettings(instances=[_instance(agent_id, allow_network=True)])
    )

    assert payload["configured"] is True
    item = payload["instances"][0]
    assert item["executionAgentId"] == str(agent_id)
    assert item["config"] == {"allowNetwork": True}
    assert item["hasCredential"] is False
    assert "executable" not in item


def test_git_errors_are_controlled() -> None:
    assert describe_error("git.networkDisabled").message == (
        "Network access is disabled for this Git integration."
    )


async def test_git_crud_defaults_network_off_and_allows_offline_agent(monkeypatch) -> None:
    settings = IntegrationSettings()
    tools = ToolSettings()
    agent_id = uuid4()
    user = SimpleNamespace(id=uuid4())
    db = AsyncMock(spec=AsyncSession)
    db.scalar.return_value = agent_id

    async def load(_db, settings_type, **_kwargs):
        return settings if settings_type is IntegrationSettings else tools

    monkeypatch.setattr(SettingsStore, "load", AsyncMock(side_effect=load))
    monkeypatch.setattr(SettingsStore, "save", AsyncMock())

    created = await create_git_integration(
        GitIntegrationRequest(executionAgentId=agent_id), user, db
    )
    integration_id = settings.instances[0].id
    assert created["instances"][0]["config"] == {"allowNetwork": False}
    assert settings.instances[0].credential_id is None

    updated = await update_git_integration(
        integration_id,
        GitIntegrationRequest.model_validate(
            {"executionAgentId": str(agent_id), "config": {"allowNetwork": True}}
        ),
        user,
        db,
    )
    assert updated["instances"][0]["config"] == {"allowNetwork": True}

    await delete_git_integration(integration_id, user, db)
    assert settings.instances == []
    assert db.commit.await_count == 3


async def test_git_crud_rejects_unowned_or_revoked_agent(monkeypatch) -> None:
    monkeypatch.setattr(SettingsStore, "load", AsyncMock(return_value=IntegrationSettings()))
    db = AsyncMock(spec=AsyncSession)
    db.scalar.return_value = None

    with pytest.raises(ApiError) as exc_info:
        await create_git_integration(
            GitIntegrationRequest(executionAgentId=uuid4()),
            SimpleNamespace(id=uuid4()),
            db,
        )

    assert exc_info.value.code == "executionAgent.notFound"


async def test_get_git_rejects_an_unowned_binding(monkeypatch) -> None:
    monkeypatch.setattr(
        SettingsStore,
        "load",
        AsyncMock(return_value=IntegrationSettings(instances=[_instance()])),
    )
    db = AsyncMock(spec=AsyncSession)
    db.scalar.return_value = None

    with pytest.raises(ApiError) as exc_info:
        await get_git_integrations(SimpleNamespace(id=uuid4()), db)

    assert exc_info.value.code == "executionAgent.notFound"


async def test_git_crud_rejects_duplicate_per_computer(monkeypatch) -> None:
    agent_id = uuid4()
    settings = IntegrationSettings(instances=[_instance(agent_id)])
    monkeypatch.setattr(SettingsStore, "load", AsyncMock(return_value=settings))
    db = AsyncMock(spec=AsyncSession)
    db.scalar.return_value = agent_id

    with pytest.raises(ApiError) as exc_info:
        await create_git_integration(
            GitIntegrationRequest(executionAgentId=agent_id),
            SimpleNamespace(id=uuid4()),
            db,
        )

    assert exc_info.value.code == "git.alreadyConfigured"
    assert exc_info.value.status_code == 409


async def test_git_service_delete_checks_bound_agent_ownership(monkeypatch) -> None:
    integration = _instance()
    monkeypatch.setattr(
        SettingsStore,
        "load",
        AsyncMock(return_value=IntegrationSettings(instances=[integration])),
    )
    db = AsyncMock(spec=AsyncSession)
    db.scalar.return_value = None

    with pytest.raises(IntegrationSettingsError) as exc_info:
        await GitIntegrationService.delete(db, integration.id, user_id=uuid4())

    assert getattr(exc_info.value, "code", None) == "executionAgent.notFound"
