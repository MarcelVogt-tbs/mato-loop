from __future__ import annotations

import json

import httpx

from mato_loop_backend.settings.models import SearchProviderConfig, SearchSettings


class SearchIntegrationError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def default_search_provider(settings: SearchSettings) -> SearchProviderConfig | None:
    if not settings.enabled or settings.default_provider_id is None:
        return None
    return next(
        (
            provider
            for provider in settings.providers
            if provider.id == settings.default_provider_id
            and provider.enabled
            and provider.config.get("base_url")
        ),
        None,
    )


def search_available(settings: SearchSettings) -> bool:
    return default_search_provider(settings) is not None


def search_unavailable_reason(settings: SearchSettings) -> str | None:
    if not settings.providers:
        return "integration.notConfigured"
    if not settings.enabled or not any(provider.enabled for provider in settings.providers):
        return "integration.disabled"
    if default_search_provider(settings) is None:
        return "integration.defaultMissing"
    return None


def _http_error_code(status_code: int) -> str:
    if status_code in {401, 403}:
        return "integration.authenticationFailed"
    if status_code == 404:
        return "integration.endpointNotFound"
    if status_code == 429:
        return "integration.rateLimited"
    if status_code >= 500:
        return "integration.unavailable"
    return "integration.requestFailed"


def _public_result_url(value: object) -> str | None:
    try:
        url = httpx.URL(str(value))
    except (TypeError, ValueError):
        return None
    if url.scheme not in {"http", "https"} or not url.host or url.userinfo:
        return None
    return str(url)


async def search_with_provider(provider: SearchProviderConfig, query: str) -> str:
    if provider.adapter_type != "searxng":
        raise SearchIntegrationError("integration.providerUnsupported")
    base_url = str(provider.config.get("base_url") or "").rstrip("/")
    if not base_url:
        raise SearchIntegrationError("integration.configurationInvalid")
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(
                f"{base_url}/search", params={"q": query, "format": "json"}
            )
    except httpx.HTTPError as exc:
        raise SearchIntegrationError("integration.unavailable") from exc
    if response.status_code >= 400:
        raise SearchIntegrationError(_http_error_code(response.status_code))
    try:
        results = response.json().get("results") or []
    except (TypeError, ValueError) as exc:
        raise SearchIntegrationError("integration.requestFailed") from exc
    compact = []
    for item in results:
        if not isinstance(item, dict):
            continue
        url = _public_result_url(item.get("url"))
        if url is None:
            continue
        compact.append(
            {
                "title": item.get("title"),
                "url": url,
                "content": item.get("content"),
                "publishedDate": item.get("publishedDate") or item.get("pubdate"),
            }
        )
        if len(compact) == 8:
            break
    return json.dumps(compact, ensure_ascii=False)


async def search_web(settings: SearchSettings, query: str) -> str:
    provider = default_search_provider(settings)
    if provider is None:
        raise SearchIntegrationError("integration.searchUnavailable")
    return await search_with_provider(provider, query)
