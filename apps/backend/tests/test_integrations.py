import json
from dataclasses import replace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

import mato_loop_backend.integrations.registry as integration_registry
import mato_loop_backend.integrations.search as search_module
from mato_loop_backend.agent.permissions import PermissionRequest
from mato_loop_backend.agent.tools import (
    ToolAction,
    ToolResult,
    available_tool_schemas,
)
from mato_loop_backend.api.errors import ApiError
from mato_loop_backend.api.routes.integrations import (
    IntegrationAccessRequest,
    SearchIntegrationRequest,
    create_search_integration,
    delete_search_integration,
    get_integration_access,
    search_settings_payload,
    update_integration_access,
    update_search_integration,
)
from mato_loop_backend.integrations import (
    SearchIntegrationError,
    search_available,
    search_provider_options,
    search_web,
)
from mato_loop_backend.integrations.contracts import (
    IntegrationContributions,
    IntegrationResource,
    ToolContribution,
)
from mato_loop_backend.settings import (
    IntegrationInstance,
    IntegrationSettings,
    SettingsStore,
    ToolExecutionPolicy,
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


class _ResourceArguments(BaseModel):
    resource_key: str


async def _resource_handler(_arguments, _context) -> ToolResult:
    return ToolResult.json({"ok": True})


def _resource_adapter(monkeypatch) -> None:
    async def load_resources(_instance, _credential):
        return (
            IntegrationResource("calendar:private", "Privat"),
            IntegrationResource("calendar:work", "Arbeit"),
        )

    current = integration_registry._ADAPTERS[("search", "searxng")]
    action = ToolAction("network.search", "Suchen", ToolExecutionPolicy.AUTO)

    def contributions(instance):
        base = current.contributions(instance)
        return IntegrationContributions(
            tools=(
                ToolContribution(
                    name="integration_resource_search",
                    description="Search one integration resource.",
                    arguments_model=_ResourceArguments,
                    handler=_resource_handler,
                    actions=(action,),
                    authorization_factory=lambda arguments, _context: (
                        PermissionRequest(
                            capability="network.search",
                            resource_type="network_service",
                            resource_scope=arguments.resource_key,
                            action_kind="integration_resource_search",
                            risk_level="medium",
                            title="Ressource durchsuchen?",
                            summary="Der Agent möchte eine Integrationsressource durchsuchen.",
                            policy_resource_key=arguments.resource_key,
                        ),
                    ),
                ),
            ),
            tool_options=base.tool_options,
            prompt_fragments=base.prompt_fragments,
        )

    monkeypatch.setitem(
        integration_registry._ADAPTERS,
        ("search", "searxng"),
        replace(
            current,
            contribution_factory=contributions,
            resource_actions=(action,),
            resource_loader=load_resources,
        ),
    )


def test_no_key_provider_makes_search_available_without_configuration() -> None:
    settings = IntegrationSettings()

    assert search_available(settings) is True
    schemas = available_tool_schemas(settings, "read-only")
    search_schema = next(item for item in schemas if item["name"] == "web_search")
    properties = search_schema["parameters"]["properties"]
    assert set(properties) == {"query"}
    assert properties["query"]["description"] == (
        "Compact search-engine query for exactly one information need. If the user supplies a "
        "year, copy it exactly. If the user asks for current, latest, or today without a year, "
        "the query must contain no year or date. Copy versions, model numbers, and exact names "
        "needed by the requested scope. Examples: 'latest Python version' becomes 'latest stable "
        "Python version'; 'Python 3.12 release notes from 2023' becomes 'Python 3.12 release notes "
        "2023'."
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


async def test_integration_access_inherits_and_overrides_global_policy(
    monkeypatch,
) -> None:
    _resource_adapter(monkeypatch)
    integration = _searxng()
    integrations = IntegrationSettings(instances=[integration])
    tools = ToolSettings.model_validate(
        {
            "policies": {"network.search": "ask"},
            "resourcePolicies": {
                str(integration.id): {
                    "calendar:private": {"network.search": "auto"}
                }
            },
        }
    )

    async def load_settings(_db, settings_type, **_kwargs):
        return integrations if settings_type is IntegrationSettings else tools

    monkeypatch.setattr(SettingsStore, "load", AsyncMock(side_effect=load_settings))

    result = await get_integration_access(integration.id, AsyncMock(spec=AsyncSession))

    resources = {item["key"]: item for item in result["resources"]}
    assert resources["calendar:private"]["policies"] == {"network.search": "auto"}
    assert resources["calendar:private"]["effectivePolicies"] == {
        "network.search": "auto"
    }
    assert resources["calendar:work"]["policies"] == {}
    assert resources["calendar:work"]["effectivePolicies"] == {
        "network.search": "ask"
    }


async def test_global_disabled_is_a_hard_resource_policy_limit(monkeypatch) -> None:
    _resource_adapter(monkeypatch)
    integration = _searxng()
    integrations = IntegrationSettings(instances=[integration])
    tools = ToolSettings.model_validate(
        {
            "policies": {"network.search": "disabled"},
            "resourcePolicies": {
                str(integration.id): {
                    "calendar:private": {"network.search": "auto"}
                }
            },
        }
    )

    async def load_settings(_db, settings_type, **_kwargs):
        return integrations if settings_type is IntegrationSettings else tools

    monkeypatch.setattr(SettingsStore, "load", AsyncMock(side_effect=load_settings))

    result = await get_integration_access(integration.id, AsyncMock(spec=AsyncSession))

    private = next(item for item in result["resources"] if item["key"] == "calendar:private")
    assert private["effectivePolicies"] == {"network.search": "disabled"}


async def test_integration_access_replaces_only_its_resource_overrides(
    monkeypatch,
) -> None:
    _resource_adapter(monkeypatch)
    integration = _searxng()
    other_id = uuid4()
    integrations = IntegrationSettings(instances=[integration])
    tools = ToolSettings.model_validate(
        {
            "resourcePolicies": {
                str(other_id): {
                    "calendar:other": {"network.search": "disabled"}
                }
            }
        }
    )

    async def load_settings(_db, settings_type, **_kwargs):
        return integrations if settings_type is IntegrationSettings else tools

    load = AsyncMock(side_effect=load_settings)
    save = AsyncMock()
    monkeypatch.setattr(SettingsStore, "load", load)
    monkeypatch.setattr(SettingsStore, "save", save)
    db = AsyncMock(spec=AsyncSession)

    result = await update_integration_access(
        integration.id,
        IntegrationAccessRequest.model_validate(
            {
                "resources": {
                    "calendar:work": {"network.search": "auto"}
                }
            }
        ),
        db,
    )

    assert tools.resource_policies[str(integration.id)] == {
        "calendar:work": {"network.search": ToolExecutionPolicy.AUTO}
    }
    assert str(other_id) in tools.resource_policies
    assert result["resources"][1]["effectivePolicies"] == {
        "network.search": "auto"
    }
    assert load.await_args_list[-2].kwargs == {"for_update": True}
    assert load.await_args_list[-1].kwargs == {"for_update": True}
    save.assert_awaited_once_with(db, tools)
    db.commit.assert_awaited_once()


@pytest.mark.parametrize(
    "resources",
    [
        {"calendar:unknown": {"network.search": "auto"}},
        {"calendar:work": {"calendar.unknown": "auto"}},
    ],
)
async def test_integration_access_rejects_unknown_resources(
    monkeypatch, resources
) -> None:
    _resource_adapter(monkeypatch)
    integration = _searxng()
    integrations = IntegrationSettings(instances=[integration])
    monkeypatch.setattr(
        SettingsStore, "load", AsyncMock(return_value=integrations)
    )

    with pytest.raises(ApiError) as exc_info:
        await update_integration_access(
            integration.id,
            IntegrationAccessRequest.model_validate({"resources": resources}),
            AsyncMock(spec=AsyncSession),
        )

    assert getattr(exc_info.value, "code", None) == "validation.invalidChoice"


def test_integration_payload_marks_resource_policy_support(monkeypatch) -> None:
    integration = _searxng()
    assert search_settings_payload(IntegrationSettings(instances=[integration]))[
        "providers"
    ][0]["supportsResourcePolicies"] is False

    _resource_adapter(monkeypatch)

    assert search_settings_payload(IntegrationSettings(instances=[integration]))[
        "providers"
    ][0]["supportsResourcePolicies"] is True


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
            },
            "resourcePolicies": {
                str(integration.id): {
                    "calendar:work": {"network.search": "ask"}
                }
            },
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
    assert str(integration.id) not in tools.resource_policies
    assert any(call.args[1] is tools for call in save.await_args_list)


async def test_changing_connection_clears_its_resource_policies(monkeypatch) -> None:
    integration = _searxng()
    settings = IntegrationSettings(instances=[integration])
    tools = ToolSettings.model_validate(
        {
            "resourcePolicies": {
                str(integration.id): {
                    "calendar:work": {"network.search": "auto"}
                }
            }
        }
    )

    async def load_settings(_db, settings_type, **_kwargs):
        return settings if settings_type is IntegrationSettings else tools

    monkeypatch.setattr(SettingsStore, "load", AsyncMock(side_effect=load_settings))
    monkeypatch.setattr(SettingsStore, "save", AsyncMock())

    await update_search_integration(
        integration.id,
        SearchIntegrationRequest(
            name="SearXNG",
            base_url="https://other.example.com",
            enabled=True,
        ),
        AsyncMock(spec=AsyncSession),
    )

    assert str(integration.id) not in tools.resource_policies


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
