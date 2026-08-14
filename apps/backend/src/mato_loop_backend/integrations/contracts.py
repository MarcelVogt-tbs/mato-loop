from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from typing import Any, Literal, Protocol

from pydantic import BaseModel

from mato_loop_backend.agent.tool_contracts import ToolAction
from mato_loop_backend.settings.models import IntegrationInstance

ToolHandler = Callable[[Any, Any], Awaitable[Any]]


@dataclass(frozen=True, slots=True)
class IntegrationResource:
    key: str
    label: str
    hard_unavailable_capabilities: frozenset[str] = frozenset()
    binding: object | None = field(default=None, repr=False, compare=False)
    applicable_capabilities: frozenset[str] | None = None
    group_key: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "hard_unavailable_capabilities",
            frozenset(self.hard_unavailable_capabilities),
        )
        if self.applicable_capabilities is not None:
            object.__setattr__(
                self,
                "applicable_capabilities",
                frozenset(self.applicable_capabilities),
            )


ResourceLoader = Callable[
    [IntegrationInstance, dict[str, Any] | None],
    Awaitable[tuple[IntegrationResource, ...]],
]


@dataclass(frozen=True, slots=True)
class ToolContribution:
    name: str
    description: str
    arguments_model: type[BaseModel]
    handler: ToolHandler | None
    write: bool = False
    requires_search: bool = False
    requires_webfetch: bool = False
    requires_memory: bool = False
    requires_memory_write: bool = False
    required_capability: str | None = None
    group: str = "integrations"
    label: str | None = None
    icon: str = "extension"
    actions: tuple[ToolAction, ...] = ()
    integration_id: str | None = None
    integration_ids: tuple[str, ...] = ()
    instance_argument: str | None = None
    resource_argument: str | None = None
    releases_private_data: bool = False
    authorization_factory: Callable[[Any, Any], tuple[Any, ...]] | None = None
    execution_operation: str | None = None
    execution_config_arguments: tuple[str, ...] = ()
    result_model: type[BaseModel] | None = None


@dataclass(frozen=True, slots=True)
class ToolOptionContribution:
    tool_name: str
    option_name: str
    value: str
    label: str
    integration_id: str | None = None


@dataclass(frozen=True, slots=True)
class IntegrationContributions:
    tools: tuple[ToolContribution, ...] = ()
    tool_options: tuple[ToolOptionContribution, ...] = ()
    prompt_fragments: tuple[str, ...] = ()


class IntegrationAdapter(Protocol):
    integration_type: str
    adapter_type: str
    config_model: type[BaseModel]

    def contributions(self, instance: IntegrationInstance) -> IntegrationContributions: ...


@dataclass(frozen=True, slots=True)
class IntegrationAdapterDefinition:
    integration_type: str
    adapter_type: str
    config_model: type[BaseModel]
    contribution_factory: Callable[[IntegrationInstance], IntegrationContributions] = field(
        default=lambda _instance: IntegrationContributions()
    )
    resource_actions: tuple[ToolAction, ...] = ()
    resource_loader: ResourceLoader | None = None
    shared_tools: tuple[ToolContribution, ...] = ()
    execution_location: Literal["backend", "agent"] = "backend"
    execution_kind: str | None = None
    execution_tools: tuple[ToolContribution, ...] = ()

    def __post_init__(self) -> None:
        if self.execution_location == "backend":
            if self.execution_kind is not None or self.execution_tools:
                raise ValueError("backend adapters cannot declare agent execution tools")
            return
        if self.execution_kind is None or re.fullmatch(
            r"[a-z][a-z0-9]*", self.execution_kind
        ) is None:
            raise ValueError("agent adapters require a valid execution kind")
        if self.shared_tools:
            raise ValueError("agent integration tools must be instance-bound")
        if any(
            tool.result_model is None
            or not isinstance(tool.result_model, type)
            or not issubclass(tool.result_model, BaseModel)
            for tool in self.execution_tools
        ):
            raise ValueError("agent integration tools require Pydantic result models")
        prefix = f"integration.{self.execution_kind}."
        operations = [tool.execution_operation for tool in self.execution_tools]
        if (
            not operations
            or any(
                operation is None
                or len(operation) > 64
                or not operation.startswith(prefix)
                or re.fullmatch(r"[a-z][a-z0-9]*", operation.removeprefix(prefix))
                is None
                for operation in operations
            )
            or len(operations) != len(set(operations))
        ):
            raise ValueError("agent integration operations must be unique and match their kind")

    def contributions(self, instance: IntegrationInstance) -> IntegrationContributions:
        config = self.config_model.model_validate(instance.config)
        contributions = self.contribution_factory(instance)
        if self.execution_location == "backend":
            return contributions
        if instance.execution_agent_id is None:
            raise ValueError("agent integrations require an execution agent")
        if contributions.tools:
            raise ValueError("agent integration tools must use static execution tools")
        from mato_loop_backend.integrations.execution import execution_tool_handler

        tools = tuple(
            replace(
                tool,
                handler=execution_tool_handler(
                    instance,
                    tool.execution_operation,
                    tool.result_model,
                    {
                        name: value
                        for name, value in config.model_dump(mode="json", by_alias=True).items()
                        if name in tool.execution_config_arguments
                    },
                ),
            )
            for tool in self.execution_tools
            if instance._execution_operations is None
            or tool.execution_operation in instance._execution_operations
        )
        return IntegrationContributions(
            tools=(*contributions.tools, *tools),
            tool_options=contributions.tool_options,
            prompt_fragments=contributions.prompt_fragments,
        )

    async def resources(
        self, instance: IntegrationInstance, credential: dict[str, Any] | None
    ) -> tuple[IntegrationResource, ...]:
        self.config_model.model_validate(instance.config)
        if self.resource_loader is None:
            return ()
        resources = await self.resource_loader(instance, credential)
        capabilities = {action.capability for action in self.resource_actions}
        if any(
            not resource.hard_unavailable_capabilities <= capabilities
            or (
                resource.applicable_capabilities is not None
                and not resource.applicable_capabilities <= capabilities
            )
            for resource in resources
        ):
            raise ValueError("resource capabilities must be declared by the adapter")
        return resources
