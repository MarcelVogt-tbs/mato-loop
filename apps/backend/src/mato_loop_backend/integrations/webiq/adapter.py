from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from mato_loop_backend.integrations.contracts import (
    IntegrationAdapterDefinition,
    IntegrationContributions,
    ToolOptionContribution,
)
from mato_loop_backend.settings import IntegrationInstance

WEBIQ_CREDENTIAL_KIND = "webiq.api_key"
WEBIQ_PASSAGE_LENGTH = 3_000
WEBIQ_ADAPTER_TYPES = {"search": "webiq_search", "webfetch": "webiq"}


class WebIqConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    timeout_seconds: int = Field(default=30, ge=1, le=120)


class WebIqCredentials(BaseModel):
    model_config = ConfigDict(extra="forbid")

    api_key: str = Field(min_length=1, max_length=10_000, pattern=r"^[\x21-\x7e]+$")


def _contributions(instance: IntegrationInstance) -> IntegrationContributions:
    if instance.owner_id is not None or instance.credential_id is None:
        return IntegrationContributions()
    return IntegrationContributions(tool_options=(
        ToolOptionContribution(
            tool_name="web_search", option_name="provider",
            value=f"{instance.adapter_type}:{instance.id}", label=instance.name,
            integration_id=str(instance.id),
        ),
    ))


WEBIQ_FETCH_ADAPTER = IntegrationAdapterDefinition(
    integration_type="webfetch",
    adapter_type="webiq",
    config_model=WebIqConfig,
    instance_only=True,
    native_tool="webfetch",
)

WEBIQ_SEARCH_ADAPTER = IntegrationAdapterDefinition(
    integration_type="search",
    adapter_type="webiq_search",
    config_model=WebIqConfig,
    contribution_factory=_contributions,
    instance_only=True,
    native_tool="web_search",
)
