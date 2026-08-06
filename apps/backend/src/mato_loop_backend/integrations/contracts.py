from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

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

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "hard_unavailable_capabilities",
            frozenset(self.hard_unavailable_capabilities),
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
    handler: ToolHandler
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

    def contributions(self, instance: IntegrationInstance) -> IntegrationContributions:
        self.config_model.model_validate(instance.config)
        return self.contribution_factory(instance)

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
            for resource in resources
        ):
            raise ValueError("resource capabilities must be declared by the adapter")
        return resources
