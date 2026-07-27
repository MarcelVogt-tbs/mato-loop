import json
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

import mato_loop_backend.integrations.search as search_module
from mato_loop_backend.agent.tools import available_tool_schemas
from mato_loop_backend.api.routes.integrations import (
    SearchIntegrationRequest,
    create_search_integration,
    delete_search_integration,
    update_search_integration,
)
from mato_loop_backend.integrations import (
    SearchIntegrationError,
    search_available,
    search_provider_options,
    search_web,
)
from mato_loop_backend.settings import (
    IntegrationInstance,
    IntegrationSettings,
    SettingsStore,
    ToolSettings,
)


class _StreamResponse:
    def __init__(
        self,
        payload,
        *,
        status_code: int = 200,
        raw: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status_code = status_code
        self.headers = headers or {}
        self.body = raw if raw is not None else json.dumps(payload).encode()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def aiter_raw(self):
        yield self.body


def _request(*, name: str = "SearXNG", enabled: bool = True) -> SearchIntegrationRequest:
    return SearchIntegrationRequest(
        integration_type="search",
        provider_type="searxng",
        name=name,
        base_url="https://search.example.com",
        enabled=enabled,
    )


def _searxng(*, name: str = "SearXNG", enabled: bool = True) -> IntegrationInstance:
    return IntegrationInstance(
        name=name,
        integration_type="search",
        adapter_type="searxng",
        enabled=enabled,
        config={"base_url": "https://search.example.com"},
    )


def test_no_key_provider_makes_search_available_without_configuration() -> None:
    settings = IntegrationSettings()

    assert search_available(settings) is True
    schemas = available_tool_schemas(settings, "read-only")
    search_schema = next(item for item in schemas if item["name"] == "web_search")
    properties = search_schema["parameters"]["properties"]
    assert set(properties) == {"query"}
    assert properties["query"]["description"] == (
        "Compact search-engine query for exactly one information need."
    )


def test_active_integrations_extend_the_host_search_provider_catalog() -> None:
    first = _searxng(name="Intern")
    second = _searxng(name="Extern")
    disabled = _searxng(name="Aus", enabled=False)
    settings = IntegrationSettings(instances=[first, second, disabled])

    expected = ["no_key", f"searxng:{first.id}", f"searxng:{second.id}"]
    options = search_provider_options(settings)
    assert [item["value"] for item in options] == expected
    assert str(disabled.id) not in str(options)


def test_search_snippet_preserves_escaped_technical_syntax() -> None:
    assert search_module._normalize_snippet("Use std::vector&lt;T&gt; safely.") == (
        "Use std::vector<T> safely."
    )
    assert search_module._normalize_snippet("<b>Result</b><img src=x>") == "Result"


async def test_no_key_provider_uses_ddgs_auto_search_and_normalizes_results(
    monkeypatch,
) -> None:
    text = Mock(
        return_value=[
            {
                "title": "MatoLoop",
                "href": "https://example.com/result",
                "body": "&lt;b&gt;Beschreibung&lt;/b&gt;   mit Abstand",
            },
            {
                "title": "Duplikat",
                "href": "https://example.com/result",
                "body": "Andere Beschreibung",
            },
        ]
    )
    client = Mock(text=text)
    constructor = Mock(return_value=client)
    monkeypatch.setattr(search_module, "DDGS", constructor)

    result = await search_module.search_without_api_key("MatoLoop")

    constructor.assert_called_once_with(timeout=30)
    text.assert_called_once_with(query="MatoLoop", max_results=8)
    assert json.loads(result) == [
        {
            "sourceId": "src-87009d3e3bbb",
            "title": "MatoLoop",
            "url": "https://example.com/result",
            "snippet": "<b>Beschreibung</b> mit Abstand",
            "publishedDate": None,
        }
    ]


async def test_searxng_reports_unresponsive_engines_as_transient_failure(
    monkeypatch,
) -> None:
    response = _StreamResponse(
        {
            "results": [],
            "unresponsive_engines": [["duckduckgo", "CAPTCHA"]],
        }
    )
    client = AsyncMock()
    client.stream = Mock(return_value=response)
    context = AsyncMock()
    context.__aenter__.return_value = client
    monkeypatch.setattr(
        search_module.httpx, "AsyncClient", Mock(return_value=context)
    )

    with pytest.raises(SearchIntegrationError) as exc_info:
        await search_module.search_with_provider(_searxng(), "MatoLoop")

    assert exc_info.value.code == "integration.unavailable"
    assert exc_info.value.transient is True


async def test_searxng_retries_unresponsive_default_engines(monkeypatch) -> None:
    unavailable = _StreamResponse(
        {
            "results": [],
            "unresponsive_engines": [["duckduckgo", "CAPTCHA"]],
        }
    )
    recovered = _StreamResponse(
        {
            "results": [
                {
                    "title": "MatoLoop",
                    "url": "https://example.com/result",
                    "content": "Beschreibung",
                }
            ],
            "unresponsive_engines": [],
        }
    )
    client = AsyncMock()
    client.stream = Mock(side_effect=[unavailable, recovered])
    context = AsyncMock()
    context.__aenter__.return_value = client
    monkeypatch.setattr(
        search_module.httpx, "AsyncClient", Mock(return_value=context)
    )

    result = await search_module.search_with_provider(_searxng(), "MatoLoop")

    assert json.loads(result)[0]["url"] == "https://example.com/result"
    assert client.stream.call_args_list[1].kwargs["params"]["engines"] == (
        "bing,mojeek,presearch"
    )


async def test_searxng_rejects_oversized_streamed_responses(monkeypatch) -> None:
    response = _StreamResponse(
        None,
        raw=b"x" * (search_module.MAX_SEARCH_RESPONSE_BYTES + 1),
    )
    client = AsyncMock()
    client.stream = Mock(return_value=response)
    context = AsyncMock()
    context.__aenter__.return_value = client
    monkeypatch.setattr(
        search_module.httpx, "AsyncClient", Mock(return_value=context)
    )

    with pytest.raises(SearchIntegrationError) as exc_info:
        await search_module.search_with_provider(_searxng(), "MatoLoop")

    assert exc_info.value.code == "integration.invalidResponse"


async def test_searxng_rejects_compressed_responses(monkeypatch) -> None:
    response = _StreamResponse(
        {"results": []},
        headers={"Content-Encoding": "gzip"},
    )
    client = AsyncMock()
    client.stream = Mock(return_value=response)
    context = AsyncMock()
    context.__aenter__.return_value = client
    monkeypatch.setattr(
        search_module.httpx, "AsyncClient", Mock(return_value=context)
    )

    with pytest.raises(SearchIntegrationError) as exc_info:
        await search_module.search_with_provider(_searxng(), "MatoLoop")

    assert exc_info.value.code == "integration.invalidResponse"
    assert client.stream.call_args.kwargs["headers"] == {"Accept-Encoding": "identity"}


async def test_search_deadline_includes_fallback(monkeypatch) -> None:
    async def execute(_settings, provider, _query):
        if provider == "no_key":
            raise SearchIntegrationError("integration.unavailable", transient=True)
        await search_module.asyncio.sleep(1)
        return "[]"

    monkeypatch.setattr(search_module, "search_with_key", execute)
    monkeypatch.setattr(search_module, "SEARCH_DEADLINE_SECONDS", 0.01)

    with pytest.raises(SearchIntegrationError) as exc_info:
        await search_web(
            IntegrationSettings(),
            "query",
            provider="no_key",
            fallback_provider="fallback",
        )

    assert exc_info.value.code == "integration.unavailable"
    assert exc_info.value.transient is True


async def test_search_integration_crud_restores_no_key_default(monkeypatch) -> None:
    settings = IntegrationSettings()
    tools = ToolSettings()
    db = AsyncMock(spec=AsyncSession)

    async def load_settings(_db, settings_type, **_kwargs):
        return settings if settings_type is IntegrationSettings else tools

    monkeypatch.setattr(SettingsStore, "load", AsyncMock(side_effect=load_settings))
    monkeypatch.setattr(SettingsStore, "save", AsyncMock())

    created = await create_search_integration(_request(), db)
    integration_id = settings.instances[0].id

    assert created["effectiveEnabled"] is True
    assert created["providers"][0]["id"] == str(integration_id)
    tools.web_search.primary_provider = f"searxng:{integration_id}"

    updated = await update_search_integration(
        integration_id, _request(name="Privat", enabled=False), db
    )
    assert updated["providers"][0]["name"] == "Privat"
    assert tools.web_search.primary_provider == "no_key"
    assert tools.web_search.fallback_provider is None

    await delete_search_integration(integration_id, db)
    assert settings.instances == []
    assert db.commit.await_count == 3


async def test_deleting_selected_fallback_removes_it_from_tool_settings(monkeypatch) -> None:
    integration = _searxng(name="Fallback")
    settings = IntegrationSettings(instances=[integration])
    tools = ToolSettings.model_validate(
        {
            "webSearch": {
                "primaryProvider": "no_key",
                "fallbackProvider": f"searxng:{integration.id}",
            }
        }
    )
    db = AsyncMock(spec=AsyncSession)

    async def load_settings(_db, settings_type, **_kwargs):
        return settings if settings_type is IntegrationSettings else tools

    monkeypatch.setattr(SettingsStore, "load", AsyncMock(side_effect=load_settings))
    save = AsyncMock()
    monkeypatch.setattr(SettingsStore, "save", save)

    await delete_search_integration(integration.id, db)

    assert tools.web_search.primary_provider == "no_key"
    assert tools.web_search.fallback_provider is None
    assert any(call.args[1] is tools for call in save.await_args_list)


async def test_search_uses_fallback_only_for_temporary_errors(monkeypatch) -> None:
    fallback = _searxng(name="Fallback")
    settings = IntegrationSettings(instances=[fallback])
    execute = AsyncMock(
        side_effect=[
            SearchIntegrationError("integration.rateLimited", transient=True),
            "[]",
        ]
    )
    monkeypatch.setattr(search_module, "search_with_key", execute)

    result = await search_web(
        settings,
        "query",
        provider="no_key",
        fallback_provider=f"searxng:{fallback.id}",
    )

    assert result == "[]"
    assert [call.args[1] for call in execute.await_args_list] == [
        "no_key",
        f"searxng:{fallback.id}",
    ]


async def test_search_does_not_fallback_for_permanent_errors(monkeypatch) -> None:
    execute = AsyncMock(
        side_effect=SearchIntegrationError("integration.authenticationFailed")
    )
    monkeypatch.setattr(search_module, "search_with_key", execute)

    with pytest.raises(SearchIntegrationError) as exc_info:
        await search_web(
            IntegrationSettings(),
            "query",
            provider="no_key",
            fallback_provider=f"searxng:{uuid4()}",
        )

    assert exc_info.value.code == "integration.authenticationFailed"
    execute.assert_awaited_once()


async def test_search_rejects_identical_fallback() -> None:
    with pytest.raises(SearchIntegrationError) as exc_info:
        await search_web(
            IntegrationSettings(),
            "query",
            provider="no_key",
            fallback_provider="no_key",
        )

    assert exc_info.value.code == "tool.invalidArguments"
