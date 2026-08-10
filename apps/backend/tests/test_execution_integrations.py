import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from pydantic import BaseModel, ConfigDict, ValidationError

import mato_loop_backend.integrations.registry as integration_registry
from mato_loop_backend.execution import manager as execution_manager
from mato_loop_backend.execution.protocol import ResponseMessage
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

    wrong_agent = effective_execution_integration_settings(
        settings, uuid4(), {"integration.example.run"}
    )
    missing_operation = effective_execution_integration_settings(settings, agent_id, set())
    effective = effective_execution_integration_settings(
        settings, agent_id, {"integration.example.run"}
    )

    assert effective_integration_contributions(wrong_agent).tools == ()
    assert effective_integration_contributions(missing_operation).tools == ()
    assert [tool.name for tool in effective_integration_contributions(effective).tools] == [
        "example_run"
    ]


async def test_agent_tool_delegates_validated_arguments_and_builds_tool_result(
    monkeypatch,
) -> None:
    adapter = _adapter()
    instance = _instance()
    response = ResponseMessage.model_validate(
        {"type": "response", "id": uuid4(), "ok": True, "result": {"answer": 4}}
    )
    request = AsyncMock(return_value=response)
    monkeypatch.setattr(execution_manager.execution_agent_connections, "request", request)
    context = SimpleNamespace(db=AsyncMock(), run_id=uuid4())
    context.db.get.return_value = SimpleNamespace(
        execution_agent_id=instance.execution_agent_id,
        execution_operations=["integration.example.run"],
    )

    contribution = adapter.contributions(instance).tools[0]
    assert contribution.handler is not None
    result = await contribution.handler(_Arguments(value=3), context)

    request.assert_awaited_once_with(
        instance.execution_agent_id,
        "integration.example.run",
        {"value": 3},
    )
    assert json.loads(result.content) == {"answer": 4}
    assert result.is_error is False


@pytest.mark.parametrize("result_payload", [None, {}, {"answer": "invalid"}])
async def test_agent_tool_rejects_invalid_success_results(
    monkeypatch, result_payload
) -> None:
    instance = _instance()
    request = AsyncMock(
        return_value=SimpleNamespace(ok=True, result=result_payload, error=None)
    )
    monkeypatch.setattr(execution_manager.execution_agent_connections, "request", request)
    context = SimpleNamespace(db=AsyncMock(), run_id=uuid4())
    context.db.get.return_value = SimpleNamespace(
        execution_agent_id=instance.execution_agent_id,
        execution_operations=["integration.example.run"],
    )
    handler = _adapter().contributions(instance).tools[0].handler
    assert handler is not None

    result = await handler(_Arguments(value=3), context)

    assert result.metadata == {"errorCode": "tool.executionFailed"}


@pytest.mark.parametrize("matching_agent,operations", [(False, ["integration.example.run"]), (True, [])])
async def test_agent_tool_rechecks_the_persisted_run_snapshot(
    monkeypatch, matching_agent, operations
) -> None:
    instance = _instance()
    request = AsyncMock()
    monkeypatch.setattr(execution_manager.execution_agent_connections, "request", request)
    context = SimpleNamespace(db=AsyncMock(), run_id=uuid4())
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
