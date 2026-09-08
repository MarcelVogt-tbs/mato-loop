from __future__ import annotations

from dataclasses import replace
from uuid import UUID

from pydantic import BaseModel, HttpUrl

from mato_loop_backend.integrations.bookstack import BOOKSTACK_ADAPTER
from mato_loop_backend.integrations.caldav import CALDAV_ADAPTER
from mato_loop_backend.integrations.contracts import (
    IntegrationAdapterDefinition,
    IntegrationContributions,
    ToolContribution,
    ToolOptionContribution,
)
from mato_loop_backend.integrations.mail import MAIL_ADAPTER
from mato_loop_backend.integrations.mealie import MEALIE_ADAPTER
from mato_loop_backend.integrations.opencode import OPENCODE_ADAPTER
from mato_loop_backend.integrations.openscad import OPENSCAD_ADAPTER
from mato_loop_backend.settings.models import (
    IntegrationAdapterAvailability,
    IntegrationInstance,
    IntegrationSettings,
    ToolSettings,
)


class SearxngConfig(BaseModel):
    base_url: HttpUrl


def search_provider_key(instance: IntegrationInstance) -> str:
    return f"{instance.adapter_type}:{instance.id}"


def agent_profile_search_available(
    settings: IntegrationSettings, tools: ToolSettings
) -> bool:
    """Do not replace a profile-excluded primary search provider with no-key search."""
    primary = tools.web_search.primary_provider
    return primary.split(":", 1)[-1] not in settings._agent_profile_excluded_ids


def _searxng_contributions(instance: IntegrationInstance) -> IntegrationContributions:
    return IntegrationContributions(
        tool_options=(
            ToolOptionContribution(
                tool_name="web_search",
                option_name="provider",
                value=search_provider_key(instance),
                label=instance.name,
                integration_id=str(instance.id),
            ),
        )
    )


_ADAPTERS = {
    ("search", "searxng"): IntegrationAdapterDefinition(
        integration_type="search",
        adapter_type="searxng",
        config_model=SearxngConfig,
        contribution_factory=_searxng_contributions,
    ),
    ("calendar", "caldav"): CALDAV_ADAPTER,
    ("mail", "imap_smtp"): MAIL_ADAPTER,
    ("knowledge", "bookstack"): BOOKSTACK_ADAPTER,
    ("recipes", "mealie"): MEALIE_ADAPTER,
    ("openscad", "openscad"): OPENSCAD_ADAPTER,
    ("coding", "opencode"): OPENCODE_ADAPTER,
}


def integration_adapter(
    integration_type: str, adapter_type: str
) -> IntegrationAdapterDefinition | None:
    return _ADAPTERS.get((integration_type, adapter_type))


def integration_adapters() -> tuple[IntegrationAdapterDefinition, ...]:
    return tuple(_ADAPTERS[key] for key in sorted(_ADAPTERS))


def integration_adapter_type_registered(adapter_type: str) -> bool:
    return any(adapter.adapter_type == adapter_type for adapter in _ADAPTERS.values())


def register_integration_adapter(adapter: IntegrationAdapterDefinition) -> None:
    key = (adapter.integration_type, adapter.adapter_type)
    if key in _ADAPTERS:
        raise ValueError("duplicate integration adapter")
    _ADAPTERS[key] = adapter
    if adapter.execution_location == "agent":
        from mato_loop_backend.execution import manager as execution_manager

        execution_manager.SUPPORTED_OPERATIONS |= frozenset(
            tool.execution_operation
            for tool in adapter.execution_tools
            if tool.execution_operation is not None
        )


def installed_integration_tools(
    settings: IntegrationSettings | None = None,
) -> tuple[ToolContribution, ...]:
    tools: dict[str, ToolContribution] = {}
    for adapter in _ADAPTERS.values():
        if settings is not None and settings.availability_for(
            adapter.adapter_type
        ) == IntegrationAdapterAvailability.DISABLED:
            continue
        for tool in adapter.shared_tools:
            tools.setdefault(tool.name, tool)
    return tuple(tools.values())


def validate_integration(instance: IntegrationInstance) -> None:
    adapter = integration_adapter(instance.integration_type, instance.adapter_type)
    if adapter is None:
        raise ValueError("unsupported integration adapter")
    if (adapter.execution_location == "agent") != (
        instance.execution_agent_id is not None
    ):
        raise ValueError("integration execution location does not match its agent binding")
    adapter.config_model.model_validate(instance.config)


def effective_execution_integration_settings(
    settings: IntegrationSettings,
    execution_agent_id: UUID | None,
    execution_operations: set[str] | frozenset[str],
) -> IntegrationSettings:
    effective = settings.model_copy(deep=True)
    for instance in effective.instances:
        if (
            effective.availability_for(instance.adapter_type)
            == IntegrationAdapterAvailability.DISABLED
        ):
            instance.enabled = False
            continue
        adapter = integration_adapter(instance.integration_type, instance.adapter_type)
        if adapter is None:
            continue
        if adapter.execution_location == "backend":
            if instance.execution_agent_id is not None:
                instance.enabled = False
            continue
        if instance.execution_agent_id != execution_agent_id:
            instance.enabled = False
        instance._execution_operations = frozenset(execution_operations)
    return effective


def effective_integration_contributions(
    settings: IntegrationSettings,
) -> IntegrationContributions:
    tools = []
    options = []
    prompt_fragments = []
    shared_instances: dict[tuple[str, str], list[IntegrationInstance]] = {}
    for instance in settings.instances:
        if (
            not instance.enabled
            or settings.availability_for(instance.adapter_type)
            == IntegrationAdapterAvailability.DISABLED
        ):
            continue
        adapter = integration_adapter(instance.integration_type, instance.adapter_type)
        if adapter is None:
            continue
        try:
            contribution = adapter.contributions(instance)
        except ValueError:
            continue
        shared_instances.setdefault(
            (instance.integration_type, instance.adapter_type), []
        ).append(instance)
        tools.extend(
            replace(
                tool,
                integration_id=str(instance.id),
                prompt_fragments=(
                    *tool.prompt_fragments,
                    *contribution.prompt_fragments,
                ),
            )
            for tool in contribution.tools
        )
        options.extend(contribution.tool_options)
        prompt_fragments.extend(contribution.prompt_fragments)
    for adapter_key, instances in shared_instances.items():
        adapter = _ADAPTERS[adapter_key]
        integration_ids = tuple(str(instance.id) for instance in instances)
        tools.extend(
            replace(tool, integration_ids=integration_ids)
            for tool in adapter.shared_tools
        )
    return IntegrationContributions(
        tools=tuple(tools),
        tool_options=tuple(options),
        prompt_fragments=tuple(prompt_fragments),
    )
