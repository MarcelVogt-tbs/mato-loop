import json
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from pydantic import BaseModel, ConfigDict, ValidationError

import mato_loop_backend.integrations.registry as integration_registry
from mato_loop_backend.execution import manager as execution_manager
from mato_loop_backend.execution.client import ExecutionClientError
from mato_loop_backend.integrations.contracts import (
    IntegrationAdapterDefinition,
    ToolContribution,
)
from mato_loop_backend.integrations.registry import (
    effective_execution_integration_settings,
    effective_integration_contributions,
    register_integration_adapter,
    validate_integration,
)
from mato_loop_backend.settings import IntegrationInstance, IntegrationSettings


class _Config(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _Arguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: int


class _Result(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answer: int


def _adapter() -> IntegrationAdapterDefinition:
    return IntegrationAdapterDefinition(
        integration_type="local",
        adapter_type="example",
        config_model=_Config,
        execution_location="agent",
        execution_kind="example",
        execution_tools=(
            ToolContribution(
                name="example_run",
                description="Run the configured example integration.",
                arguments_model=_Arguments,
                handler=None,
                execution_operation="integration.example.run",
                result_model=_Result,
            ),
        ),
    )


def _instance(agent_id=None) -> IntegrationInstance:
    return IntegrationInstance(
        name="Example",
        integration_type="local",
        adapter_type="example",
        execution_agent_id=agent_id or uuid4(),
    )


def test_agent_integration_settings_are_additive_and_unique_per_agent_kind() -> None:
    assert IntegrationInstance(
        name="Search",
        integration_type="search",
        adapter_type="searxng",
    ).execution_agent_id is None

    instance = _instance()
    assert len(IntegrationSettings(instances=[instance, _instance()]).instances) == 2
    with pytest.raises(ValidationError):
        IntegrationSettings(instances=[instance, instance.model_copy(update={"id": uuid4()})])


def test_agent_tools_require_the_run_agent_and_advertised_operation(monkeypatch) -> None:
    adapter = _adapter()
    monkeypatch.setitem(
        integration_registry._ADAPTERS,
        (adapter.integration_type, adapter.adapter_type),
        adapter,
    )
    agent_id = uuid4()
    settings = IntegrationSettings(instances=[_instance(agent_id)])
    disabled = IntegrationSettings(
        instances=[_instance(agent_id)],
        adapter_availability={"example": "disabled"},
    )

    wrong_agent = effective_execution_integration_settings(
        settings, uuid4(), {"integration.example.run"}
    )
    missing_operation = effective_execution_integration_settings(settings, agent_id, set())
    effective = effective_execution_integration_settings(
        settings, agent_id, {"integration.example.run"}
    )
    unavailable = effective_execution_integration_settings(
        disabled, agent_id, {"integration.example.run"}
    )

    assert effective_integration_contributions(wrong_agent).tools == ()
    assert effective_integration_contributions(missing_operation).tools == ()
    assert [tool.name for tool in effective_integration_contributions(effective).tools] == [
        "example_run"
    ]
    assert effective_integration_contributions(unavailable).tools == ()


async def test_agent_tool_delegates_validated_arguments_and_builds_tool_result() -> None:
    adapter = _adapter()
    instance = _instance()
    client = SimpleNamespace(
        agent_id=instance.execution_agent_id,
        request=AsyncMock(return_value=_Result(answer=4)),
    )
    context = SimpleNamespace(db=AsyncMock(), run_id=uuid4(), execution_client=client)
    context.db.get.return_value = SimpleNamespace(
        execution_agent_id=instance.execution_agent_id,
        execution_operations=["integration.example.run"],
    )

    contribution = adapter.contributions(instance).tools[0]
    assert contribution.handler is not None
    result = await contribution.handler(_Arguments(value=3), context)

    client.request.assert_awaited_once_with(
        "integration.example.run",
        {"value": 3},
        _Result,
    )
    assert json.loads(result.content) == {"answer": 4}
    assert result.is_error is False


async def test_agent_tool_maps_execution_client_errors() -> None:
    instance = _instance()
    client = SimpleNamespace(
        agent_id=instance.execution_agent_id,
        request=AsyncMock(side_effect=ExecutionClientError("executionAgent.protocolError")),
    )
    context = SimpleNamespace(db=AsyncMock(), run_id=uuid4(), execution_client=client)
    context.db.get.return_value = SimpleNamespace(
        execution_agent_id=instance.execution_agent_id,
        execution_operations=["integration.example.run"],
    )
    handler = _adapter().contributions(instance).tools[0].handler
    assert handler is not None

    result = await handler(_Arguments(value=3), context)

    assert result.metadata == {"errorCode": "executionAgent.protocolError"}


@pytest.mark.parametrize("matching_agent,operations", [(False, ["integration.example.run"]), (True, [])])
async def test_agent_tool_rechecks_the_persisted_run_snapshot(
    monkeypatch, matching_agent, operations
) -> None:
    instance = _instance()
    request = AsyncMock()
    context = SimpleNamespace(db=AsyncMock(), run_id=uuid4(), execution_client=None)
    context.db.get.return_value = SimpleNamespace(
        execution_agent_id=instance.execution_agent_id if matching_agent else uuid4(),
        execution_operations=operations,
    )
    handler = _adapter().contributions(instance).tools[0].handler
    assert handler is not None

    result = await handler(_Arguments(value=3), context)

    assert result.metadata == {"errorCode": "tool.unavailable"}
    request.assert_not_awaited()


def test_agent_tool_definition_requires_a_result_model() -> None:
    with pytest.raises(ValueError, match="result models"):
        IntegrationAdapterDefinition(
            integration_type="local",
            adapter_type="invalid",
            config_model=_Config,
            execution_location="agent",
            execution_kind="invalid",
            execution_tools=(
                ToolContribution(
                    name="invalid_run",
                    description="Invalid static tool.",
                    arguments_model=_Arguments,
                    handler=None,
                    execution_operation="integration.invalid.run",
                ),
            ),
        )


def test_agent_tools_may_share_an_operation_but_keep_names_and_result_contracts():
    adapter = _adapter()
    tool = adapter.execution_tools[0]
    second = replace(tool, name="example_other")
    assert replace(adapter, execution_tools=(tool, second)).execution_tools == (tool, second)
    with pytest.raises(ValueError, match="tool names must be unique"):
        replace(adapter, execution_tools=(tool, tool))
    for operation in ("integration.other.run", "integration.example.run_code", "integration.example."):
        with pytest.raises(ValueError, match="operations must match"):
            replace(adapter, execution_tools=(tool, replace(second, execution_operation=operation)))
    with pytest.raises(ValueError, match="result models"):
        replace(adapter, execution_tools=(tool, replace(second, result_model=None)))


def test_static_agent_adapter_registration_admits_only_declared_operations(
    monkeypatch,
) -> None:
    adapter = _adapter()
    monkeypatch.setattr(integration_registry, "_ADAPTERS", dict(integration_registry._ADAPTERS))
    monkeypatch.setattr(
        execution_manager, "SUPPORTED_OPERATIONS", execution_manager.SUPPORTED_OPERATIONS
    )

    register_integration_adapter(adapter)

    assert "integration.example.run" in execution_manager.SUPPORTED_OPERATIONS


def test_integration_location_binding_is_validated(monkeypatch) -> None:
    adapter = _adapter()
    monkeypatch.setitem(
        integration_registry._ADAPTERS,
        (adapter.integration_type, adapter.adapter_type),
        adapter,
    )

    with pytest.raises(ValueError):
        validate_integration(
            IntegrationInstance(
                name="Example",
                integration_type="local",
                adapter_type="example",
            )
        )
    with pytest.raises(ValueError):
        validate_integration(
            IntegrationInstance(
                name="Search",
                integration_type="search",
                adapter_type="searxng",
                execution_agent_id=uuid4(),
                config={"base_url": "https://search.example.com"},
            )
        )
