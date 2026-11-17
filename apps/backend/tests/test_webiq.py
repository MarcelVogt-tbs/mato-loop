import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import httpx
import pytest
from tool_helpers import _context

from mato_loop_backend.integrations.service import IntegrationSettingsError
from mato_loop_backend.integrations.webiq import client, service
from mato_loop_backend.integrations.webiq.adapter import WebIqConfig, WebIqCredentials
from mato_loop_backend.permissions.policy import PermissionDecision
from mato_loop_backend.providers.base import ToolCall
from mato_loop_backend.settings import (
    IntegrationInstance,
    IntegrationSettings,
    SettingsStore,
    ToolExecutionPolicy,
    ToolSettings,
)
from mato_loop_backend.tools import access
from mato_loop_backend.tools.catalog import tool_registry
from mato_loop_backend.web.fetch import WebFetchError


def _transport(monkeypatch, handler):
    real_client = httpx.AsyncClient
    monkeypatch.setattr(client.httpx, "AsyncClient", lambda **kwargs: real_client(
        transport=httpx.MockTransport(handler), **kwargs,
    ))


@pytest.mark.parametrize("operation", ["web", "images", "browse"])
async def test_requests_use_fixed_authenticated_endpoints_and_normalize_content(monkeypatch, operation):
    from mato_loop_backend.integrations.search import _compact_results

    def respond(request):
        body = json.loads(request.content)
        assert (str(request.url), request.headers["x-apikey"], body["query"]) == (
            f"https://api.microsoft.ai/v3/{'browse' if operation == 'browse' else f'search/{operation}'}",
            "test-key", "information",
        )
        if operation != "images":
            assert (body["contentFormat"], body["maxLength"]) == ("passage", 3000)
        page = {"url": "https://target.test/page", "title": "Article", "content": "Relevant passage"}
        data = page if operation == "browse" else {"webResults": [page]} if operation == "web" else {
            "imageResults": [{"url": "https://target.test/photo.jpg", "hostPageUrl": page["url"], "title": "Photo"}],
        }
        return httpx.Response(200, stream=httpx.ByteStream(json.dumps(data).encode()))

    _transport(monkeypatch, respond)
    if operation == "browse":
        result = await client.fetch(WebIqConfig(), "https://target.test/page", query="information", api_key="test-key")
        assert (result.content, result.output_format) == ("Relevant passage", "text")
    else:
        results = json.loads(_compact_results(
            await client.search(WebIqConfig(), "information", api_key="test-key", search_type=operation),
            search_type=operation,
        ))
        assert results[0]["url"] == "https://target.test/page"
        assert results[0].get("imageUrl") == ("https://target.test/photo.jpg" if operation == "images" else None)


@pytest.mark.parametrize(("status", "body", "headers", "code"), [
    (202, b"private upstream details", {}, "webfetch.crawlPending"),
    (401, b"private upstream details", {}, "integration.authenticationFailed"),
    (403, b"private upstream details", {}, "integration.requestFailed"),
    (429, b"private upstream details", {}, "integration.rateLimited"),
    (302, b"", {"Location": "https://other.test/"}, "integration.requestFailed"),
    (200, b"{}", {}, "integration.invalidResponse"),
    (200, b"not json", {}, "integration.invalidResponse"),
    (200, b"x" * 1001, {}, "webfetch.responseTooLarge"),
    (200, b"{}", {"Content-Encoding": "gzip"}, "integration.invalidResponse"),
])
async def test_failures_never_become_content_or_leak_upstream_details(monkeypatch, status, body, headers, code):
    _transport(monkeypatch, lambda _request: httpx.Response(
        status, headers=headers, stream=httpx.ByteStream(body),
    ))
    with pytest.raises(WebFetchError, match=code):
        await client.fetch(WebIqConfig(), "https://target.test/", query="information",
                           api_key="test-key", max_response_bytes=1000)


async def test_request_has_a_total_deadline(monkeypatch):
    real_timeout = asyncio.timeout
    monkeypatch.setattr(client.asyncio, "timeout", lambda _seconds: real_timeout(0.01))

    async def slow(_request):
        await asyncio.sleep(1)

    _transport(monkeypatch, slow)
    with pytest.raises(WebFetchError, match="webfetch.timeout"):
        await client.search(WebIqConfig(), "information", api_key="test-key")


@pytest.mark.parametrize("integration_type", ["search", "webfetch"])
async def test_key_is_required_kept_and_replaced_only_in_credentials(monkeypatch, integration_type):
    settings = IntegrationSettings()
    db = AsyncMock()
    monkeypatch.setattr(SettingsStore, "load", AsyncMock(side_effect=lambda _db, model, **_kwargs: settings if model is IntegrationSettings else ToolSettings()))
    monkeypatch.setattr(SettingsStore, "save", AsyncMock())
    store = AsyncMock(side_effect=[SimpleNamespace(id=uuid4()), SimpleNamespace(id=uuid4())])
    delete = AsyncMock()
    monkeypatch.setattr(service, "store_credential", store)
    monkeypatch.setattr(service, "delete_credential", delete)
    values = {"name": "Web IQ", "enabled": True, "integration_type": integration_type}
    with pytest.raises(IntegrationSettingsError, match="validation.required"):
        await service.WebIqIntegrationService.save(db, None, api_key=None, **values)
    await service.WebIqIntegrationService.save(db, None, api_key="first-key", **values)
    instance = settings.instances[0]
    original = instance.credential_id
    await service.WebIqIntegrationService.save(db, instance.id, api_key=None, **values)
    assert instance.credential_id == original
    await service.WebIqIntegrationService.save(db, instance.id, api_key="new-key", **values)
    assert (store.await_args.kwargs["kind"], instance.config) == ("webiq.api_key", {"timeout_seconds": 30})
    delete.assert_awaited_once_with(db, original, owner_id=None)
    with pytest.raises(IntegrationSettingsError, match="validation.invalidChoice"):
        await service.WebIqIntegrationService.save(
            db, instance.id, api_key=None,
            **{**values, "integration_type": "webfetch" if integration_type == "search" else "search"},
        )


@pytest.mark.parametrize("integration_type", ["search", "webfetch"])
async def test_connection_test_uses_only_its_own_capability(monkeypatch, integration_type):
    from mato_loop_backend.api.routes import integrations as routes

    instance = IntegrationInstance(
        name="Web IQ", integration_type=integration_type,
        adapter_type="webiq_search" if integration_type == "search" else "webiq",
        credential_id=uuid4(),
    )
    monkeypatch.setattr(SettingsStore, "load", AsyncMock(return_value=IntegrationSettings(instances=[instance])))
    monkeypatch.setattr(service, "credentials", AsyncMock(return_value=WebIqCredentials(api_key="test-key")))
    search, fetch = AsyncMock(), AsyncMock()
    monkeypatch.setattr(client, "search", search)
    monkeypatch.setattr(client, "fetch", fetch)
    await routes.test_webiq_integration(instance.id, AsyncMock(), SimpleNamespace(is_admin=True))
    assert (search.await_count, fetch.await_count) == ((1, 0) if integration_type == "search" else (0, 1))


@pytest.mark.parametrize("tool_name", ["web_search", "webfetch"])
@pytest.mark.parametrize("state", ["allowed", "denied", "changed", "excluded", "disabled"])
async def test_both_native_tools_recheck_binding_and_permissions(tmp_path, monkeypatch, tool_name, state):
    from mato_loop_backend.integrations.search import search_provider_options
    from mato_loop_backend.web.providers import provider_options

    call = ToolCall("web", tool_name, {"query": "information", **({"url": "https://target.test/page"} if tool_name == "webfetch" else {})})
    context = _context(tmp_path, call, web_fetch_policy=ToolExecutionPolicy.ASK)
    context.tool_settings.private_permissions.policies["network.search"] = ToolExecutionPolicy.ASK
    instance = IntegrationInstance(
        name="Web IQ", integration_type="search" if tool_name == "web_search" else "webfetch",
        adapter_type="webiq_search" if tool_name == "web_search" else "webiq",
        credential_id=uuid4(), config={"timeout_seconds": 30},
    )
    settings = IntegrationSettings(instances=[instance], webfetch_integration_id=instance.id if tool_name == "webfetch" else None)
    key = f"{instance.adapter_type}:{instance.id}"
    if tool_name == "web_search":
        context.tool_settings.web_search.primary_provider = key
    tools = tool_registry.effective(settings, webfetch_enabled=True, web_search_enabled=True, tool_settings=context.tool_settings)
    search_options = {item["value"] for item in search_provider_options(settings)}
    fetch_options = {item["value"] for item in provider_options(settings)}
    assert (key in search_options, key in fetch_options) == (tool_name == "web_search", tool_name == "webfetch")
    current = settings.model_copy(deep=True)
    if state == "changed":
        current.instances[0].credential_id = uuid4()
    elif state == "excluded":
        current.instances = []
    elif state == "disabled":
        current.instances[0].enabled = False
    monkeypatch.setattr(type(context), "current_integrations", AsyncMock(side_effect=[settings, current]))
    monkeypatch.setattr(access, "profile_tool_settings", AsyncMock(return_value=context.tool_settings))
    context.ask_handler.return_value = PermissionDecision.DENY if state == "denied" else PermissionDecision.ALLOW
    monkeypatch.setattr(service, "credentials", AsyncMock(return_value=WebIqCredentials(api_key="test-key")))
    requests = Mock()

    def respond(request):
        requests()
        page = {"url": "https://target.test/page", "content": "Relevant passage"}
        return httpx.Response(200, stream=httpx.ByteStream(json.dumps(
            {"webResults": [page]} if tool_name == "web_search" else page,
        ).encode()))

    _transport(monkeypatch, respond)
    result = await tools.execute(call, context)
    if state == "allowed":
        assert "Relevant passage" in result.content
    else:
        requests.assert_not_called()
        assert result.metadata["errorCode"] == ("permission.denied" if state == "denied" else "tool.unavailable")
