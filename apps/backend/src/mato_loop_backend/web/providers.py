from __future__ import annotations

from mato_loop_backend.settings import IntegrationInstance, IntegrationSettings

_READER_ADAPTERS = {("webfetch", "jina"), ("webfetch", "webiq")}


def provider_key(settings: IntegrationSettings) -> str:
    if settings.webfetch_integration_id is None:
        return "builtin"
    instance = next((
        item for item in settings.instances
        if item.id == settings.webfetch_integration_id
        and (item.integration_type, item.adapter_type) in _READER_ADAPTERS
    ), None)
    return f"{instance.adapter_type if instance else 'unavailable'}:{settings.webfetch_integration_id}"


def selected_provider(settings: IntegrationSettings, key: str | None = None) -> IntegrationInstance | None:
    key = key or provider_key(settings)
    return next((
        item for item in settings.instances
        if f"{item.adapter_type}:{item.id}" == key
        and (item.integration_type, item.adapter_type) in _READER_ADAPTERS
        and item.enabled and item.owner_id is None
        and (item.adapter_type != "webiq" or item.credential_id is not None)
        and settings.availability_for(item.adapter_type) != "disabled"
    ), None)


def provider_available(settings: IntegrationSettings) -> bool:
    return settings.webfetch_integration_id is None or selected_provider(settings) is not None


def provider_options(settings: IntegrationSettings) -> list[dict]:
    return [{"value": "builtin", "name": "Eingebaut"}, *(
        {"value": f"{item.adapter_type}:{item.id}", "name": item.name}
        for item in settings.instances
        if selected_provider(settings, f"{item.adapter_type}:{item.id}") is not None
    )]
