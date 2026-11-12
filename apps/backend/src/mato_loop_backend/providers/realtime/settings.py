from __future__ import annotations

from mato_loop_backend.providers.base import ProviderError
from mato_loop_backend.providers.realtime.transport import RealtimeEndpoint
from mato_loop_backend.settings.models import (
    AiSettingsBase,
    LlmProviderConfig,
    ModelAvailability,
    ModelCapability,
    ModelConfig,
)

ADAPTER_TYPES = frozenset({"azure-openai-realtime", "openai-realtime"})


def endpoint_for(adapter_type: str, base_url: str) -> RealtimeEndpoint:
    if adapter_type == "azure-openai-realtime":
        base_url = base_url.rstrip("/")
        if not base_url.removeprefix("https://").partition("/")[1]:
            base_url += "/openai/v1"
        return RealtimeEndpoint(base_url, "azure-openai-realtime")
    if adapter_type == "openai-realtime":
        return RealtimeEndpoint(base_url, "openai-realtime")
    raise ProviderError("Unsupported Realtime adapter", code="provider.configurationInvalid")


def realtime_model_available(
    settings: AiSettingsBase, model: ModelConfig, provider: LlmProviderConfig,
) -> bool:
    return bool(
        model in settings.models
        and provider in settings.providers
        and model.provider_id == provider.id
        and model.enabled and provider.enabled
        and model.availability != ModelAvailability.MISSING
        and provider.adapter_type in ADAPTER_TYPES
        and provider.credential_id is not None
        and ModelCapability.REALTIME_AUDIO in model.capabilities
        and model.supports_tools
    )
