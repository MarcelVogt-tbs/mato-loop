from __future__ import annotations

from dataclasses import replace

from pydantic import BaseModel, HttpUrl

from mato_loop_backend.integrations.contracts import (
    IntegrationAdapterDefinition,
    IntegrationContributions,
    ToolOptionContribution,
)
from mato_loop_backend.settings.models import IntegrationInstance, IntegrationSettings


class SearxngConfig(BaseModel):
    base_url: HttpUrl


def search_provider_key(instance: IntegrationInstance) -> str:
    return f"{instance.adapter_type}:{instance.id}"


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
    )
}


def integration_adapter(
    integration_type: str, adapter_type: str
) -> IntegrationAdapterDefinition | None:
    return _ADAPTERS.get((integration_type, adapter_type))


def validate_integration(instance: IntegrationInstance) -> None:
    adapter = integration_adapter(instance.integration_type, instance.adapter_type)
    if adapter is None:
        raise ValueError("unsupported integration adapter")
    adapter.config_model.model_validate(instance.config)


def effective_integration_contributions(
    settings: IntegrationSettings,
) -> IntegrationContributions:
    tools = []
    options = []
    prompt_fragments = []
    for instance in settings.instances:
        if not instance.enabled:
            continue
        adapter = integration_adapter(instance.integration_type, instance.adapter_type)
        if adapter is None:
            continue
        try:
            contribution = adapter.contributions(instance)
        except ValueError:
            continue
        tools.extend(
            replace(tool, integration_id=str(instance.id))
            for tool in contribution.tools
        )
        options.extend(contribution.tool_options)
        prompt_fragments.extend(contribution.prompt_fragments)
    return IntegrationContributions(
        tools=tuple(tools),
        tool_options=tuple(options),
        prompt_fragments=tuple(prompt_fragments),
    )
