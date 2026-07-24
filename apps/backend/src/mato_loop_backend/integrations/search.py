from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Iterable

import httpx
from ddgs import DDGS

from mato_loop_backend.integrations.evidence import evidence_source_id
from mato_loop_backend.integrations.registry import (
    effective_integration_contributions,
    search_provider_key,
)
from mato_loop_backend.integrations.webfetch import WebFetchError, canonicalize_url
from mato_loop_backend.settings.models import (
    IntegrationInstance,
    IntegrationSettings,
    ToolSettings,
)

logger = logging.getLogger(__name__)
_SEARXNG_RECOVERY_ENGINES = "bing,mojeek,presearch"


class SearchIntegrationError(RuntimeError):
    def __init__(self, code: str, *, transient: bool = False) -> None:
        super().__init__(code)
        self.code = code
        self.transient = transient


def search_available(_settings: IntegrationSettings) -> bool:
    return True


def search_provider_options(settings: IntegrationSettings) -> list[dict[str, str | None]]:
    options: list[dict[str, str | None]] = [
        {
            "value": "no_key",
            "name": "Websuche ohne API-Key",
            "adapterType": "ddgs",
            "integrationId": None,
        }
    ]
    options.extend(
        {
            "value": contribution.value,
            "name": contribution.label,
            "adapterType": contribution.value.split(":", 1)[0],
            "integrationId": contribution.integration_id,
        }
        for contribution in effective_integration_contributions(settings).tool_options
        if contribution.tool_name == "web_search" and contribution.option_name == "provider"
    )
    return options


def effective_search_providers(
    tool_settings: ToolSettings, integration_settings: IntegrationSettings
) -> tuple[str, str | None]:
    available = {
        str(option["value"]) for option in search_provider_options(integration_settings)
    }
    primary = tool_settings.web_search.primary_provider
    if primary not in available:
        primary = "no_key"
    fallback = tool_settings.web_search.fallback_provider
    if fallback not in available or fallback == primary:
        fallback = None
    return primary, fallback


def normalize_search_tool_settings(
    tool_settings: ToolSettings, integration_settings: IntegrationSettings
) -> bool:
    primary, fallback = effective_search_providers(tool_settings, integration_settings)
    changed = (
        tool_settings.web_search.primary_provider != primary
        or tool_settings.web_search.fallback_provider != fallback
    )
    tool_settings.web_search.primary_provider = primary
    tool_settings.web_search.fallback_provider = fallback
    return changed


def search_provider(
    settings: IntegrationSettings, provider_key: str
) -> IntegrationInstance | None:
    if provider_key == "no_key":
        return None
    return next(
        (
            instance
            for instance in settings.instances
            if instance.enabled
            and instance.integration_type == "search"
            and search_provider_key(instance) == provider_key
            and instance.config.get("base_url")
        ),
        None,
    )


def _http_error_code(status_code: int) -> tuple[str, bool]:
    if status_code in {401, 403}:
        return "integration.authenticationFailed", False
    if status_code == 404:
        return "integration.endpointNotFound", False
    if status_code == 408:
        return "integration.unavailable", True
    if status_code == 429:
        return "integration.rateLimited", True
    if status_code >= 500:
        return "integration.unavailable", True
    return "integration.requestFailed", False


def _public_result_url(value: object) -> str | None:
    try:
        url = httpx.URL(str(value))
    except (TypeError, ValueError):
        return None
    if url.scheme not in {"http", "https"} or not url.host or url.userinfo:
        return None
    return str(url)


def _compact_results(results: Iterable[object]) -> str:
    compact = []
    seen_urls: set[str] = set()
    for item in results:
        if not isinstance(item, dict):
            continue
        url = _public_result_url(item.get("url") or item.get("href"))
        if url is None:
            continue
        try:
            url = canonicalize_url(url)
        except WebFetchError:
            continue
        if url in seen_urls:
            continue
        seen_urls.add(url)
        compact.append(
            {
                "sourceId": evidence_source_id(url),
                "title": item.get("title"),
                "url": url,
                "snippet": item.get("content") or item.get("body"),
                "publishedDate": item.get("publishedDate")
                or item.get("pubdate")
                or item.get("date"),
            }
        )
        if len(compact) == 8:
            break
    return json.dumps(compact, ensure_ascii=False)


async def search_without_api_key(query: str) -> str:
    def execute() -> list[dict[str, str]]:
        return DDGS(timeout=30).text(
            query=query,
            max_results=8,
        )

    try:
        async with asyncio.timeout(35):
            results = await asyncio.to_thread(execute)
    except Exception as exc:
        logger.warning("No-key web search failed: %s", type(exc).__name__)
        raise SearchIntegrationError("integration.unavailable", transient=True) from exc
    return _compact_results(results)


async def search_with_provider(provider: IntegrationInstance, query: str) -> str:
    if provider.integration_type != "search" or provider.adapter_type != "searxng":
        raise SearchIntegrationError("integration.providerUnsupported")
    base_url = str(provider.config.get("base_url") or "").rstrip("/")
    if not base_url:
        raise SearchIntegrationError("integration.configurationInvalid")

    def parse_response(response: httpx.Response) -> tuple[list[object], list[object]]:
        if response.status_code >= 400:
            code, transient = _http_error_code(response.status_code)
            raise SearchIntegrationError(code, transient=transient)
        try:
            payload = response.json()
            results = payload.get("results") if isinstance(payload, dict) else None
            if not isinstance(results, list):
                raise TypeError
            unresponsive = payload.get("unresponsive_engines", [])
            if not isinstance(unresponsive, list):
                unresponsive = []
            return results, list(unresponsive)
        except (TypeError, ValueError) as exc:
            raise SearchIntegrationError("integration.requestFailed") from exc

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(
                f"{base_url}/search", params={"q": query, "format": "json"}
            )
            results, unresponsive_engines = parse_response(response)
            if not results and unresponsive_engines:
                recovery_response = await client.get(
                    f"{base_url}/search",
                    params={
                        "q": query,
                        "format": "json",
                        "engines": _SEARXNG_RECOVERY_ENGINES,
                    },
                )
                results, recovery_unresponsive = parse_response(recovery_response)
                unresponsive_engines.extend(recovery_unresponsive)
    except httpx.HTTPError as exc:
        raise SearchIntegrationError("integration.unavailable", transient=True) from exc
    if not results and unresponsive_engines:
        raise SearchIntegrationError("integration.unavailable", transient=True)
    return _compact_results(results)


async def search_with_key(
    settings: IntegrationSettings, provider_key: str, query: str
) -> str:
    if provider_key == "no_key":
        return await search_without_api_key(query)
    provider = search_provider(settings, provider_key)
    if provider is None:
        raise SearchIntegrationError("integration.providerUnavailable")
    return await search_with_provider(provider, query)


async def search_web(
    settings: IntegrationSettings,
    query: str,
    *,
    provider: str = "no_key",
    fallback_provider: str | None = None,
) -> str:
    if fallback_provider == provider:
        raise SearchIntegrationError("tool.invalidArguments")
    try:
        return await search_with_key(settings, provider, query)
    except SearchIntegrationError as exc:
        if fallback_provider is None or not exc.transient:
            raise
    return await search_with_key(settings, fallback_provider, query)
