import json
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
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
from mato_loop_backend.api.errors import ApiError, register_error_handlers
from mato_loop_backend.api.routes import integrations as integrations_route_module
from mato_loop_backend.api.routes.integrations import (
    CalendarIntegrationRequest,
    IntegrationAccessRequest,
    SearchIntegrationRequest,
    create_calendar_integration,
    create_search_integration,
    delete_calendar_integration,
    delete_search_integration,
    get_integration_access,
    search_settings_payload,
    update_calendar_integration,
    update_integration_access,
    update_search_integration,
    update_search_selection,
)
from mato_loop_backend.db.session import get_db_session
from mato_loop_backend.integrations import (
    SearchIntegrationError,
    SearchIntegrationService,
    search_available,
    search_provider_options,
    search_web,
)
from mato_loop_backend.integrations import service as integration_service_module
from mato_loop_backend.integrations.caldav_client import CalDavError
from mato_loop_backend.integrations.contracts import (
    IntegrationContributions,
    IntegrationResource,
    ToolContribution,
)
from mato_loop_backend.integrations.service import BookStackIntegrationService
from mato_loop_backend.models import Credential, UserAccount
from mato_loop_backend.security import require_user
from mato_loop_backend.settings import (
    DefaultCalendarSettings,
    DefaultTaskListSettings,
    IntegrationInstance,
    IntegrationSettings,
    SettingsStore,
    ToolExecutionPolicy,
    ToolSettings,
    UserIntegrationSettings,
    UserToolSettings,
    WebSearchToolSettings,
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


def _catalog_app(db, user=None) -> FastAPI:
    app = FastAPI()
    register_error_handlers(app)
    app.include_router(integrations_route_module.router, prefix="/api")

    async def override_db():
        yield db

    app.dependency_overrides[get_db_session] = override_db
    if user is not None:
        app.dependency_overrides[require_user] = lambda: user
    return app


def test_integration_catalog_requires_authentication(monkeypatch) -> None:
    db = AsyncMock(spec=AsyncSession)
    db.scalar.return_value = None
    load = AsyncMock()
    monkeypatch.setattr(SettingsStore, "load", load)

    response = TestClient(_catalog_app(db)).get("/api/integrations/catalog")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "auth.notAuthenticated"
    load.assert_not_awaited()


def test_integration_catalog_read_and_admin_update(monkeypatch) -> None:
    db = AsyncMock(spec=AsyncSession)
    settings = IntegrationSettings(
        adapter_availability={"caldav": "instance_only"}
    )
    monkeypatch.setattr(SettingsStore, "load", AsyncMock(return_value=settings))
    save = AsyncMock()
    monkeypatch.setattr(SettingsStore, "save", save)
    user = UserAccount(
        id=uuid4(),
        username="test",
        password_hash="hash",
        is_admin=False,
        is_active=True,
    )
    app = _catalog_app(db, user)

    with TestClient(app) as client:
        listed = client.get("/api/integrations/catalog")
        forbidden = client.put(
            "/api/integrations/catalog/caldav", json={"availability": "full"}
        )
        user.is_admin = True
        invalid = client.put(
            "/api/integrations/catalog/caldav", json={"availability": "sometimes"}
        )
        invalid_agent = client.put(
            "/api/integrations/catalog/git", json={"availability": "instance_only"}
        )
        updated = client.put(
            "/api/integrations/catalog/caldav", json={"availability": "full"}
        )
        unknown = client.put(
            "/api/integrations/catalog/unknown", json={"availability": "disabled"}
        )

    adapters = listed.json()["adapters"]
    assert listed.status_code == 200
    assert [(item["integrationType"], item["adapterType"]) for item in adapters] == sorted(
        (item["integrationType"], item["adapterType"]) for item in adapters
    )
    assert next(item for item in adapters if item["adapterType"] == "caldav") == {
        "integrationType": "calendar",
        "adapterType": "caldav",
        "executionLocation": "backend",
        "availability": "instance_only",
    }
    assert next(item for item in adapters if item["adapterType"] == "opencode")[
        "personalOnly"
    ] is True
    assert forbidden.status_code == 403
    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "validation.invalidChoice"
    assert invalid_agent.status_code == 422
    assert invalid_agent.json()["error"] == {
        "code": "validation.invalidChoice",
        "message": "The selected value is invalid.",
        "field": "availability",
    }
    assert updated.json() == {
        "integrationType": "calendar",
        "adapterType": "caldav",
        "executionLocation": "backend",
        "availability": "full",
    }
    assert unknown.status_code == 409
    assert unknown.json()["error"]["code"] == "integration.providerUnsupported"
    assert settings.adapter_availability == {}
    save.assert_awaited_once_with(db, settings)
    db.commit.assert_awaited_once()


@pytest.mark.parametrize("scope", ["instance", "personal"])
async def test_disabled_adapter_blocks_create_but_allows_delete(
    monkeypatch, scope
) -> None:
    integration = _searxng()
    global_settings = IntegrationSettings(
        adapter_availability={"searxng": "disabled"},
        instances=[integration] if scope == "instance" else [],
    )
    personal_settings = UserIntegrationSettings(instances=[integration])

    async def load(_db, settings_type, **_kwargs):
        if settings_type is IntegrationSettings:
            return global_settings
        if settings_type is UserIntegrationSettings:
            return personal_settings
        return ToolSettings()

    monkeypatch.setattr(SettingsStore, "load", AsyncMock(side_effect=load))
    monkeypatch.setattr(SettingsStore, "load_optional", AsyncMock(return_value=None))
    create = AsyncMock()
    delete = AsyncMock()
    monkeypatch.setattr(SearchIntegrationService, "create", create)
    monkeypatch.setattr(SearchIntegrationService, "delete", delete)
    user = SimpleNamespace(id=uuid4(), is_admin=scope == "instance")
    db = AsyncMock(spec=AsyncSession)

    with pytest.raises(ApiError, match="integration.adapterDisabled"):
        await create_search_integration(
            _request(), db, scope=scope, user=user
        )
    listed = await integrations_route_module.get_search_integrations(
        db, scope=scope, user=user
    )
    await delete_search_integration(
        integration.id, db, scope=scope, user=user
    )

    assert listed["providers"][0]["id"] == str(integration.id)
    create.assert_not_awaited()
    delete.assert_awaited_once_with(
        db,
        integration.id,
        owner_id=None if scope == "instance" else user.id,
    )


async def test_bookstack_policy_cleanup_removes_resource_labels(monkeypatch) -> None:
    integration_id = uuid4()
    tools = ToolSettings(
        integration_policies={str(integration_id): {}},
        resource_policies={str(integration_id): {}},
        resource_labels={str(integration_id): {"book:7": "Handbuch"}},
    )
    monkeypatch.setattr(SettingsStore, "load", AsyncMock(return_value=tools))
    monkeypatch.setattr(SettingsStore, "save", AsyncMock())
    db = AsyncMock(spec=AsyncSession)

    await BookStackIntegrationService._save(
        db, IntegrationSettings(), clear_policies=integration_id
    )

    assert str(integration_id) not in tools.resource_labels


async def test_nonadmin_cannot_read_instance_integration_or_use_credential_test(
    monkeypatch,
) -> None:
    user = SimpleNamespace(id=uuid4(), is_admin=False)
    load = AsyncMock(return_value=IntegrationSettings())
    discover = AsyncMock()
    monkeypatch.setattr(SettingsStore, "load", load)
    monkeypatch.setattr(integrations_route_module, "load_caldav_resources", discover)
    db = AsyncMock(spec=AsyncSession)

    with pytest.raises(ApiError, match="auth.adminRequired"):
        await integrations_route_module.get_calendar_integrations(
            db, scope="instance", user=user
        )
    with pytest.raises(ApiError, match="auth.adminRequired"):
        await integrations_route_module.test_calendar_integration(
            uuid4(), db, scope="instance", user=user
        )

    load.assert_not_awaited()
    discover.assert_not_awaited()


async def test_personal_policy_read_exposes_no_integration_configuration(
    monkeypatch,
) -> None:
    settings = IntegrationSettings(
        adapter_availability={
            "searxng": "instance_only",
            "bookstack": "instance_only",
            "mealie": "instance_only",
            "openscad": "disabled",
        },
        instances=[_searxng()],
    )
    monkeypatch.setattr(SettingsStore, "load", AsyncMock(return_value=settings))

    result = await integrations_route_module.get_personal_integration_settings(
        SimpleNamespace(id=uuid4(), is_admin=False), AsyncMock(spec=AsyncSession)
    )

    assert result == {
        "enabled": True,
        "allowedAdapterTypes": ["caldav", "git", "imap_smtp", "opencode"],
    }


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
            IntegrationResource(
                "calendar:private",
                "Privat",
                applicable_capabilities=frozenset({"network.search"}),
            ),
            IntegrationResource(
                "calendar:work",
                "Arbeit",
                applicable_capabilities=frozenset({"network.search"}),
            ),
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


async def test_revoked_personal_adapters_block_external_clients(monkeypatch) -> None:
    _resource_adapter(monkeypatch)
    resource_loader = AsyncMock(return_value=())
    current = integration_registry._ADAPTERS[("search", "searxng")]
    monkeypatch.setitem(
        integration_registry._ADAPTERS,
        ("search", "searxng"),
        replace(current, resource_loader=resource_loader),
    )
    search = _searxng()
    calendar = IntegrationInstance(
        name="Calendar",
        integration_type="calendar",
        adapter_type="caldav",
    )
    knowledge = IntegrationInstance(
        name="Knowledge",
        integration_type="knowledge",
        adapter_type="bookstack",
    )
    openscad = IntegrationInstance(
        name="OpenSCAD",
        integration_type="openscad",
        adapter_type="openscad",
        execution_agent_id=uuid4(),
        config={"binary_path": "/usr/bin/openscad"},
    )
    instance = IntegrationSettings(
        adapter_availability={
            "searxng": "instance_only",
            "caldav": "instance_only",
            "bookstack": "instance_only",
            "openscad": "disabled",
        }
    )
    personal = UserIntegrationSettings(
        instances=[search, calendar, knowledge, openscad]
    )

    async def load(_db, settings_type, **_kwargs):
        return instance if settings_type is IntegrationSettings else personal

    search_client = AsyncMock()
    calendar_client = AsyncMock()
    knowledge_client = AsyncMock()
    execution_client = Mock()
    monkeypatch.setattr(SettingsStore, "load", AsyncMock(side_effect=load))
    monkeypatch.setattr(
        integrations_route_module, "search_with_provider", search_client
    )
    monkeypatch.setattr(
        integrations_route_module, "load_caldav_resources", calendar_client
    )
    monkeypatch.setattr(
        integrations_route_module, "load_bookstack_resources", knowledge_client
    )
    monkeypatch.setattr(
        integrations_route_module, "_connected_client", execution_client
    )
    user = SimpleNamespace(id=uuid4(), is_admin=False)
    db = AsyncMock(spec=AsyncSession)
    operations = (
        integrations_route_module.test_search_integration(
            search.id, db, scope="personal", user=user
        ),
        integrations_route_module.test_calendar_integration(
            calendar.id, db, scope="personal", user=user
        ),
        integrations_route_module.test_knowledge_integration(
            knowledge.id, db, scope="personal", user=user
        ),
        integrations_route_module.test_openscad_integration(
            openscad.id, user, db, scope="personal"
        ),
        get_integration_access(
            search.id, db, "public", scope="personal", user=user
        ),
    )

    for operation, code in zip(
        operations,
        [
            "integration.personalAdapterDisabled",
            "integration.personalAdapterDisabled",
            "integration.personalAdapterDisabled",
            "integration.adapterDisabled",
            "integration.personalAdapterDisabled",
        ],
        strict=True,
    ):
        with pytest.raises(ApiError, match=code):
            await operation

    search_client.assert_not_awaited()
    calendar_client.assert_not_awaited()
    knowledge_client.assert_not_awaited()
    execution_client.assert_not_called()
    resource_loader.assert_not_awaited()


def test_no_key_provider_makes_search_available_without_configuration() -> None:
    settings = IntegrationSettings()

    assert search_available(settings) is True
    schemas = available_tool_schemas(settings)
    search_schema = next(item for item in schemas if item["name"] == "web_search")
    properties = search_schema["parameters"]["properties"]
    assert set(properties) == {"query", "search_type"}
    assert properties["query"]["description"] == (
        "One compact search-engine query for one information need. Preserve user-supplied dates, "
        "versions, model numbers, and exact names; never add a date."
    )
    assert properties["search_type"]["enum"] == ["web", "images"]
    assert properties["search_type"]["default"] == "web"


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
            "publicPermissions": {
                "policies": {"network.search": "ask"},
                "resourcePolicies": {
                    str(integration.id): {
                        "calendar:private": {"network.search": "auto"}
                    }
                },
            },
        }
    )

    async def load_settings(_db, settings_type, **_kwargs):
        return integrations if settings_type is IntegrationSettings else tools

    monkeypatch.setattr(SettingsStore, "load", AsyncMock(side_effect=load_settings))

    result = await get_integration_access(
        integration.id, AsyncMock(spec=AsyncSession), "public"
    )

    resources = {item["key"]: item for item in result["resources"]}
    assert resources["calendar:private"]["policies"] == {"network.search": "auto"}
    assert resources["calendar:private"]["effectivePolicies"] == {
        "network.search": "auto"
    }
    assert resources["calendar:private"]["applicableCapabilities"] == [
        "network.search"
    ]
    assert resources["calendar:work"]["policies"] == {}
    assert resources["calendar:work"]["effectivePolicies"] == {
        "network.search": "ask"
    }


async def test_integration_access_reports_integration_policy_and_hard_limits(
    monkeypatch,
) -> None:
    _resource_adapter(monkeypatch)
    current = integration_registry._ADAPTERS[("search", "searxng")]

    async def load_resources(_instance, _credential):
        return (
            IntegrationResource(
                "project:archive",
                "Archiv",
                frozenset({"network.search"}),
            ),
        )

    monkeypatch.setitem(
        integration_registry._ADAPTERS,
        ("search", "searxng"),
        replace(current, resource_loader=load_resources),
    )
    integration = _searxng()
    integrations = IntegrationSettings(instances=[integration])
    tools = ToolSettings.model_validate(
        {
            "publicPermissions": {
                "policies": {"network.search": "ask"},
                "integrationPolicies": {
                    str(integration.id): {"network.search": "auto"}
                },
            },
        }
    )

    async def load_settings(_db, settings_type, **_kwargs):
        return integrations if settings_type is IntegrationSettings else tools

    monkeypatch.setattr(SettingsStore, "load", AsyncMock(side_effect=load_settings))

    result = await get_integration_access(
        integration.id, AsyncMock(spec=AsyncSession), "public"
    )

    action = result["actions"][0]
    assert action["allowedPolicies"] == ["disabled", "ask", "auto"]
    assert action["integrationPolicy"] == "auto"
    assert action["effectiveIntegrationPolicy"] == "auto"
    assert result["resources"][0]["hardUnavailableCapabilities"] == [
        "network.search"
    ]
    assert result["resources"][0]["effectivePolicies"] == {
        "network.search": "disabled"
    }


@pytest.mark.parametrize(
    "resource",
    [
        IntegrationResource(
            "project:main",
            "Hauptprojekt",
            hard_unavailable_capabilities=frozenset({"tasks.invented"}),
        ),
        IntegrationResource(
            "project:main",
            "Hauptprojekt",
            applicable_capabilities=frozenset({"tasks.invented"}),
        ),
        IntegrationResource(
            "project:main",
            "Hauptprojekt",
            group_key="invalid group key",
        ),
    ],
)
async def test_resource_discovery_cannot_invent_adapter_capabilities(
    monkeypatch, resource
) -> None:
    _resource_adapter(monkeypatch)
    current = integration_registry._ADAPTERS[("search", "searxng")]

    async def load_resources(_instance, _credential):
        return (resource,)

    monkeypatch.setitem(
        integration_registry._ADAPTERS,
        ("search", "searxng"),
        replace(current, resource_loader=load_resources),
    )
    integration = _searxng()
    integrations = IntegrationSettings(instances=[integration])
    monkeypatch.setattr(
        SettingsStore, "load", AsyncMock(return_value=integrations)
    )

    with pytest.raises(ApiError) as exc_info:
        await get_integration_access(
            integration.id, AsyncMock(spec=AsyncSession), "public"
        )

    assert exc_info.value.code == "integration.resourceDiscoveryFailed"


async def test_integration_access_replaces_only_its_resource_overrides(
    monkeypatch,
) -> None:
    _resource_adapter(monkeypatch)
    integration = _searxng()
    other_id = uuid4()
    integrations = IntegrationSettings(instances=[integration])
    tools = ToolSettings.model_validate(
        {
            "publicPermissions": {
                "integrationPolicies": {
                    str(integration.id): {"network.search": "ask"}
                },
                "resourcePolicies": {
                    str(other_id): {
                        "calendar:other": {"network.search": "disabled"}
                    }
                },
            },
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
            {"resources": {"calendar:work": {"network.search": "auto"}}}
        ),
        db,
        "public",
    )

    assert tools.permissions.resource_policies[str(integration.id)] == {
        "calendar:work": {"network.search": ToolExecutionPolicy.AUTO}
    }
    assert tools.resource_labels[str(integration.id)] == {
        "calendar:private": "Privat",
        "calendar:work": "Arbeit",
    }
    assert str(other_id) in tools.permissions.resource_policies
    assert tools.permissions.integration_policies[str(integration.id)] == {
        "network.search": ToolExecutionPolicy.ASK
    }
    assert result["resources"][1]["effectivePolicies"] == {
        "network.search": "auto"
    }
    assert load.await_args_list[-2].kwargs == {"for_update": True}
    assert load.await_args_list[-1].kwargs == {"for_update": True}
    save.assert_awaited_once_with(db, tools, user_id=None)
    db.commit.assert_awaited_once()


async def test_caldav_access_manages_independent_defaults(monkeypatch) -> None:
    key = f"calendar:{'a' * 32}"
    task_list_key = f"tasklist:{'a' * 32}"
    other_task_list_key = f"tasklist:{'b' * 32}"

    async def load_resources(_instance, _credential):
        return (
            IntegrationResource(
                key,
                "Privat",
                applicable_capabilities=frozenset(
                    {"calendar.read", "calendar.write", "calendar.delete"}
                ),
                group_key="a" * 32,
            ),
            IntegrationResource(
                task_list_key,
                "Aufgaben",
                applicable_capabilities=frozenset(
                    {"tasks.read", "tasks.write", "tasks.delete"}
                ),
                group_key="a" * 32,
            ),
            IntegrationResource(
                other_task_list_key,
                "Weitere Aufgaben",
                applicable_capabilities=frozenset(
                    {"tasks.read", "tasks.write", "tasks.delete"}
                ),
                group_key="b" * 32,
            ),
        )

    current_adapter = integration_registry._ADAPTERS[("calendar", "caldav")]
    monkeypatch.setitem(
        integration_registry._ADAPTERS,
        ("calendar", "caldav"),
        replace(current_adapter, resource_loader=load_resources),
    )
    integration = IntegrationInstance(
        name="Kalender",
        integration_type="calendar",
        adapter_type="caldav",
        config={"base_url": "https://calendar.example.com", "username": "user"},
    )
    integrations = IntegrationSettings(instances=[integration])
    other_id = uuid4()
    tools = ToolSettings.model_validate(
        {
            "defaultCalendar": {
                "integrationId": str(integration.id),
                "calendarKey": key,
            },
            "defaultTaskList": {
                "integrationId": str(integration.id),
                "taskListKey": task_list_key,
            },
            "publicPermissions": {
                "integrationPolicies": {
                    str(integration.id): {"calendar.read": "ask"}
                },
                "resourcePolicies": {
                    str(integration.id): {key: {"calendar.read": "auto"}}
                },
            },
        }
    )

    async def load_settings(_db, settings_type, **_kwargs):
        return integrations if settings_type is IntegrationSettings else tools

    monkeypatch.setattr(SettingsStore, "load", AsyncMock(side_effect=load_settings))
    monkeypatch.setattr(SettingsStore, "save", AsyncMock())
    db = AsyncMock(spec=AsyncSession)

    access = await get_integration_access(integration.id, db, "public")
    access_resources = {resource["key"]: resource for resource in access["resources"]}
    assert access["defaultResourceKey"] == key
    assert access["defaultTaskListKey"] == task_list_key
    assert access_resources[key]["groupKey"] == "a" * 32
    assert access_resources[task_list_key]["groupKey"] == "a" * 32

    preserved = await update_integration_access(
        integration.id,
        IntegrationAccessRequest(),
        db,
        "public",
    )
    assert tools.default_calendar.calendar_key == key
    assert tools.default_task_list.task_list_key == task_list_key
    assert preserved["defaultResourceKey"] == key
    assert preserved["defaultTaskListKey"] == task_list_key
    assert tools.permissions.integration_policies[str(integration.id)] == {
        "calendar.read": ToolExecutionPolicy.ASK
    }
    assert str(integration.id) not in tools.permissions.resource_policies

    tools.default_task_list = DefaultTaskListSettings(
        integration_id=other_id,
        task_list_key=other_task_list_key,
    )
    unchanged = await update_integration_access(
        integration.id,
        IntegrationAccessRequest.model_validate(
            {"defaultTaskListKey": None}
        ),
        db,
        "public",
    )
    assert tools.default_task_list.integration_id == other_id
    assert unchanged["defaultTaskListKey"] is None

    selected = await update_integration_access(
        integration.id,
        IntegrationAccessRequest.model_validate(
            {"defaultTaskListKey": task_list_key}
        ),
        db,
        "public",
    )
    assert tools.default_task_list.integration_id == integration.id
    assert tools.default_task_list.task_list_key == task_list_key
    assert selected["defaultResourceKey"] == key
    assert selected["defaultTaskListKey"] == task_list_key
    resources = {resource["key"]: resource for resource in selected["resources"]}
    assert resources[key]["applicableCapabilities"] == [
        "calendar.delete",
        "calendar.read",
        "calendar.write",
    ]
    assert resources[task_list_key]["applicableCapabilities"] == [
        "tasks.delete",
        "tasks.read",
        "tasks.write",
    ]
    assert set(resources[key]["effectivePolicies"]) == {
        "calendar.read",
        "calendar.write",
        "calendar.delete",
    }
    assert set(resources[task_list_key]["effectivePolicies"]) == {
        "tasks.read",
        "tasks.write",
        "tasks.delete",
    }

    replaced = await update_integration_access(
        integration.id,
        IntegrationAccessRequest.model_validate(
            {"defaultTaskListKey": other_task_list_key}
        ),
        db,
        "public",
    )
    assert tools.default_task_list.task_list_key == other_task_list_key
    assert replaced["defaultResourceKey"] == key
    assert replaced["defaultTaskListKey"] == other_task_list_key

    cleared = await update_integration_access(
        integration.id,
        IntegrationAccessRequest.model_validate(
            {"defaultTaskListKey": None}
        ),
        db,
        "public",
    )
    assert tools.default_task_list is None
    assert tools.default_calendar.calendar_key == key
    assert cleared["defaultResourceKey"] == key
    assert cleared["defaultTaskListKey"] is None

    with pytest.raises(ApiError) as task_default:
        await update_integration_access(
            integration.id,
            IntegrationAccessRequest.model_validate(
                {"defaultResourceKey": task_list_key}
            ),
            db,
            "public",
        )
    assert task_default.value.code == "validation.invalidChoice"
    assert task_default.value.field == "default_resource_key"

    for invalid_key in (key, f"tasklist:{'c' * 32}"):
        with pytest.raises(ApiError) as exc_info:
            await update_integration_access(
                integration.id,
                IntegrationAccessRequest.model_validate(
                    {"defaultTaskListKey": invalid_key}
                ),
                db,
                "public",
            )
        assert exc_info.value.code == "validation.invalidChoice"
        assert exc_info.value.field == "default_task_list_key"

    with pytest.raises(ApiError) as stale_calendar:
        await update_integration_access(
            integration.id,
            IntegrationAccessRequest.model_validate(
                {"defaultResourceKey": f"calendar:{'c' * 32}"}
            ),
            db,
            "public",
        )
    assert stale_calendar.value.code == "validation.invalidChoice"
    assert stale_calendar.value.field == "default_resource_key"


async def test_non_caldav_access_rejects_a_task_default(monkeypatch) -> None:
    _resource_adapter(monkeypatch)
    integration = _searxng()
    integrations = IntegrationSettings(instances=[integration])
    monkeypatch.setattr(SettingsStore, "load", AsyncMock(return_value=integrations))

    with pytest.raises(ApiError) as exc_info:
        await update_integration_access(
            integration.id,
            IntegrationAccessRequest.model_validate(
                {
                    "defaultTaskListKey": f"tasklist:{'a' * 32}",
                }
            ),
            AsyncMock(spec=AsyncSession),
            "public",
        )

    assert exc_info.value.code == "validation.invalidChoice"
    assert exc_info.value.field == "default_task_list_key"


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
    monkeypatch.setattr(SettingsStore, "load", AsyncMock(return_value=integrations))

    with pytest.raises(ApiError) as exc_info:
        await update_integration_access(
            integration.id,
            IntegrationAccessRequest.model_validate({"resources": resources}),
            AsyncMock(spec=AsyncSession),
            "public",
        )

    assert exc_info.value.code == "validation.invalidChoice"


async def test_personal_search_selection_uses_effective_provider_options(
    monkeypatch,
) -> None:
    owner_id = uuid4()
    personal_integration = _searxng()
    personal_integrations = UserIntegrationSettings(instances=[personal_integration])
    effective_integrations = IntegrationSettings(instances=[personal_integration])
    tools = UserToolSettings()
    save = AsyncMock()
    monkeypatch.setattr(
        integrations_route_module,
        "_load_scoped_integrations",
        AsyncMock(return_value=(personal_integrations, owner_id)),
    )
    monkeypatch.setattr(
        integrations_route_module,
        "load_effective_integrations",
        AsyncMock(return_value=effective_integrations),
    )
    monkeypatch.setattr(
        integrations_route_module,
        "_load_scoped_tools",
        AsyncMock(return_value=tools),
    )
    monkeypatch.setattr(SettingsStore, "save", save)
    db = AsyncMock(spec=AsyncSession)
    provider_key = f"searxng:{personal_integration.id}"

    result = await update_search_selection(
        WebSearchToolSettings(
            primary_provider=provider_key,
            fallback_provider="no_key",
        ),
        db,
        scope="personal",
        user=SimpleNamespace(id=owner_id, is_admin=False),
    )

    assert result["primaryProvider"] == provider_key
    assert result["fallbackProvider"] == "no_key"
    assert {option["value"] for option in result["providerOptions"]} == {
        "no_key",
        provider_key,
    }
    assert tools.web_search.primary_provider == provider_key
    save.assert_awaited_once_with(db, tools, user_id=owner_id)
    db.commit.assert_awaited_once()

    with pytest.raises(ApiError) as unavailable:
        await update_search_selection(
            WebSearchToolSettings(primary_provider=f"searxng:{uuid4()}"),
            db,
            scope="personal",
            user=SimpleNamespace(id=owner_id, is_admin=False),
        )
    assert unavailable.value.code == "integration.providerUnavailable"
    assert unavailable.value.field == "primary_provider"


def test_integration_payload_marks_policy_support(monkeypatch) -> None:
    integration = _searxng()
    provider = search_settings_payload(IntegrationSettings(instances=[integration]))[
        "providers"
    ][0]
    assert provider["supportsIntegrationPolicies"] is False
    assert provider["supportsResourcePolicies"] is False

    _resource_adapter(monkeypatch)

    provider = search_settings_payload(IntegrationSettings(instances=[integration]))[
        "providers"
    ][0]
    assert provider["supportsIntegrationPolicies"] is True
    assert provider["supportsResourcePolicies"] is True


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


async def test_no_key_provider_supports_image_search(monkeypatch) -> None:
    images = Mock(
        return_value=[
            {
                "title": "Official product image",
                "url": "https://example.com/product",
                "image": "https://cdn.example.com/product.jpg",
                "body": "Rear view",
            }
        ]
    )
    client = Mock(images=images)
    monkeypatch.setattr(search_module, "DDGS", Mock(return_value=client))

    result = await search_module.search_without_api_key(
        "product rear view", search_type="images"
    )

    images.assert_called_once_with(query="product rear view", max_results=8)
    assert json.loads(result) == [
        {
            "sourceId": search_module.evidence_source_id(
                "https://example.com/product"
            ),
            "title": "Official product image",
            "url": "https://example.com/product",
            "snippet": "Rear view",
            "publishedDate": None,
            "imageUrl": "https://cdn.example.com/product.jpg",
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


async def test_searxng_supports_image_search(monkeypatch) -> None:
    response = _StreamResponse(
        {
            "results": [
                {
                    "title": "Official product image",
                    "url": "https://example.com/product",
                    "img_src": "https://cdn.example.com/product.webp",
                    "content": "Rear view",
                }
            ],
            "unresponsive_engines": [],
        }
    )
    client = AsyncMock()
    client.stream = Mock(return_value=response)
    context = AsyncMock()
    context.__aenter__.return_value = client
    monkeypatch.setattr(
        search_module.httpx, "AsyncClient", Mock(return_value=context)
    )

    result = await search_module.search_with_provider(
        _searxng(), "product rear view", search_type="images"
    )

    assert client.stream.call_args.kwargs["params"] == {
        "q": "product rear view",
        "format": "json",
        "categories": "images",
    }
    assert json.loads(result)[0]["imageUrl"] == (
        "https://cdn.example.com/product.webp"
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
    async def execute(_settings, provider, _query, **_kwargs):
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
            "publicPermissions": {
                "resourcePolicies": {
                    str(integration.id): {
                        "calendar:work": {"network.search": "ask"}
                    }
                },
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
    assert str(integration.id) not in tools.permissions.resource_policies
    assert any(call.args[1] is tools for call in save.await_args_list)


async def test_changing_connection_clears_its_resource_policies(monkeypatch) -> None:
    integration = _searxng()
    settings = IntegrationSettings(instances=[integration])
    tools = ToolSettings.model_validate(
        {
            "publicPermissions": {
                "resourcePolicies": {
                    str(integration.id): {
                        "calendar:work": {"network.search": "auto"}
                    }
                },
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

    assert str(integration.id) not in tools.permissions.resource_policies


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
        search_type="images",
        provider="no_key",
        fallback_provider=f"searxng:{fallback.id}",
    )

    assert result == "[]"
    assert [call.args[1] for call in execute.await_args_list] == [
        "no_key",
        f"searxng:{fallback.id}",
    ]
    assert [call.kwargs["search_type"] for call in execute.await_args_list] == [
        "images",
        "images",
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


def _calendar_request(
    *,
    name: str = "Kalender",
    base_url: str = "https://calendar.example.com",
    username: str = "user",
    password: str | None = "secret",
    enabled: bool = True,
) -> CalendarIntegrationRequest:
    return CalendarIntegrationRequest(
        integration_type="calendar",
        provider_type="caldav",
        name=name,
        base_url=base_url,
        username=username,
        password=password,
        enabled=enabled,
    )


async def test_caldav_account_crud_rotates_secret_and_keeps_accounts_independent(
    monkeypatch,
) -> None:
    settings = IntegrationSettings()
    tools = ToolSettings()
    db = AsyncMock(spec=AsyncSession)
    db.get.return_value = Mock(user_id=None)
    credential_ids = [uuid4(), uuid4(), uuid4()]
    store_credential = AsyncMock(
        side_effect=[Mock(id=credential_id) for credential_id in credential_ids]
    )

    async def load_settings(_db, settings_type, **_kwargs):
        return settings if settings_type is IntegrationSettings else tools

    monkeypatch.setattr(SettingsStore, "load", AsyncMock(side_effect=load_settings))
    monkeypatch.setattr(SettingsStore, "save", AsyncMock())
    monkeypatch.setattr(
        integration_service_module, "store_credential", store_credential
    )

    first_payload = await create_calendar_integration(_calendar_request(), db)
    first = settings.instances[0]
    await create_calendar_integration(
        _calendar_request(
            name="Arbeit",
            base_url="https://work.example.com",
            username="worker",
            password="work-secret",
        ),
        db,
    )
    second = settings.instances[1]
    tools.permissions.integration_policies = {
        str(first.id): {"calendar.read": ToolExecutionPolicy.AUTO},
        str(second.id): {"calendar.read": ToolExecutionPolicy.ASK},
    }
    tools.permissions.resource_policies = {
        str(first.id): {
            f"calendar:{'a' * 32}": {"calendar.write": ToolExecutionPolicy.ASK}
        },
        str(second.id): {
            f"calendar:{'b' * 32}": {"calendar.read": ToolExecutionPolicy.AUTO}
        },
    }
    tools.resource_labels = {
        str(first.id): {f"calendar:{'a' * 32}": "Privat"},
        str(second.id): {f"calendar:{'b' * 32}": "Arbeit"},
    }
    tools.default_calendar = DefaultCalendarSettings(
        integration_id=first.id,
        calendar_key=f"calendar:{'a' * 32}",
    )
    tools.default_task_list = DefaultTaskListSettings(
        integration_id=first.id,
        task_list_key=f"tasklist:{'a' * 32}",
    )

    assert first.credential_id == credential_ids[0]
    assert first_payload["accounts"][0]["config"] == {
        "baseUrl": "https://calendar.example.com/",
        "username": "user",
    }
    assert first_payload["accounts"][0]["hasCredential"] is True
    assert first_payload["accounts"][0]["supportsIntegrationPolicies"] is True
    assert first_payload["accounts"][0]["supportsResourcePolicies"] is True
    assert "secret" not in json.dumps(first_payload)
    assert "credentialId" not in json.dumps(first_payload)

    await update_calendar_integration(
        first.id,
        _calendar_request(name="Umbenannt", password="new-secret", enabled=False),
        db,
    )

    assert first.credential_id == credential_ids[2]
    assert str(first.id) in tools.permissions.integration_policies
    assert str(first.id) in tools.permissions.resource_policies
    assert tools.default_calendar.integration_id == first.id
    assert tools.default_task_list.integration_id == first.id
    assert str(second.id) in tools.permissions.integration_policies
    assert str(second.id) in tools.permissions.resource_policies
    assert store_credential.await_args_list[-1].args == (db,)
    assert store_credential.await_args_list[-1].kwargs == {
        "owner_id": None,
        "kind": "caldav.password",
        "label": "Umbenannt",
        "payload": {"password": "new-secret"},
    }
    assert db.delete.await_count == 1

    await update_calendar_integration(
        first.id,
        _calendar_request(
            base_url="https://moved.example.com",
            password=None,
        ),
        db,
    )

    assert str(first.id) not in tools.permissions.integration_policies
    assert str(first.id) not in tools.permissions.resource_policies
    assert str(first.id) not in tools.resource_labels
    assert str(second.id) in tools.permissions.integration_policies
    assert str(second.id) in tools.permissions.resource_policies
    assert str(second.id) in tools.resource_labels
    assert tools.default_calendar is None
    assert tools.default_task_list is None
    assert first.credential_id == credential_ids[2]

    tools.default_calendar = DefaultCalendarSettings(
        integration_id=second.id,
        calendar_key=f"calendar:{'b' * 32}",
    )
    tools.default_task_list = DefaultTaskListSettings(
        integration_id=first.id,
        task_list_key=f"tasklist:{'a' * 32}",
    )
    await delete_calendar_integration(first.id, db)

    assert [instance.id for instance in settings.instances] == [second.id]
    assert tools.default_calendar.integration_id == second.id
    assert tools.default_task_list is None
    assert db.delete.await_count == 2


async def test_caldav_test_endpoint_maps_remote_failure_to_controlled_error(
    monkeypatch,
) -> None:
    account = IntegrationInstance(
        name="Kalender",
        integration_type="calendar",
        adapter_type="caldav",
        credential_id=uuid4(),
        config={"base_url": "https://calendar.example.com", "username": "user"},
    )
    settings = IntegrationSettings(instances=[account])
    record = Mock(spec=Credential)
    record.kind = "caldav.password"
    db = AsyncMock(spec=AsyncSession)
    db.get.return_value = record
    monkeypatch.setattr(SettingsStore, "load", AsyncMock(return_value=settings))
    monkeypatch.setattr(
        integrations_route_module,
        "read_credential",
        Mock(return_value={"password": "secret"}),
    )
    monkeypatch.setattr(
        integrations_route_module,
        "load_caldav_resources",
        AsyncMock(side_effect=CalDavError("caldav.authenticationFailed", status_code=401)),
    )

    with pytest.raises(ApiError) as exc_info:
        await integrations_route_module.test_calendar_integration(account.id, db)

    assert exc_info.value.status_code == 502
    assert exc_info.value.code == "integration.authenticationFailed"
    assert exc_info.value.error.message == "The integration credentials were rejected."
    assert "caldav" not in exc_info.value.error.message.lower()
