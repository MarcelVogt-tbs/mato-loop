import json
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from mato_loop_backend.agent.runtime import AgentRuntime
from mato_loop_backend.integrations.mcp.adapter import mcp_model_tool_name
from mato_loop_backend.models import ContextSummary, Message, WebSearchPolicy
from mato_loop_backend.providers.base import ProviderResult, ToolCall
from mato_loop_backend.settings.effective import integration_tool_loading
from mato_loop_backend.settings.models import (
    GENERAL_PROFILE_ID,
    AgentProfile,
    IntegrationInstance,
    IntegrationPermissions,
    IntegrationSettings,
    ToolSettings,
)
from mato_loop_backend.tools.catalog import tool_registry
from mato_loop_backend.tools.contracts import ToolResult
from mato_loop_backend.tools.discovery import (
    SearchToolsArguments,
    expire_discoveries,
    offer_integration_tools,
)


def calendar(name="Calendar"):
    return IntegrationInstance(name=name, integration_type="calendar", adapter_type="caldav",
                               config={"base_url": "https://calendar.example.test", "username": "owner"})


def offer(instances, profile, records=()):
    settings = IntegrationSettings(instances=instances)
    tools = ToolSettings()
    tools._agent_profile = profile
    effective = tool_registry.effective(settings, tool_settings=tools)
    return offer_integration_tools(effective, settings, profile, list(records))


def discovery(result):
    return Message(id=uuid4(), role="tool", provider_data={"_matoloop": {
        "toolName": "search_tools", **result.metadata,
    }})


@pytest.mark.parametrize("profile_mode,connection_mode,base_mode,own_mode,expected", [
    ("on_demand", None, None, None, "on_demand"),
    ("all", None, None, None, "all"),
    ("all", "on_demand", None, None, "on_demand"),
    ("on_demand", "all", "on_demand", None, "on_demand"),
    ("all", "all", "all", "on_demand", "on_demand"),
    ("on_demand", None, None, "all", "all"),
])
def test_loading_uses_existing_connection_and_profile_override_precedence(profile_mode, connection_mode, base_mode, own_mode, expected):
    instance = calendar()
    profile = AgentProfile(name="Agent", instructions="Task", base_profile_id=GENERAL_PROFILE_ID,
                           integration_tool_loading=profile_mode)
    instance.permissions.tool_loading = connection_mode
    instance.profile_permissions = {
        GENERAL_PROFILE_ID: IntegrationPermissions(tool_loading=base_mode),
        profile.id: IntegrationPermissions(tool_loading=own_mode),
    }
    assert integration_tool_loading(instance, profile) == expected


async def test_search_reloads_persisted_tools_without_changing_initial_prefix():
    instance = calendar()
    profile = AgentProfile(name="Agent", instructions="Task")
    initial = offer([instance], profile)
    assert "read" in initial.tools and "tasks_create" not in initial.tools
    context = SimpleNamespace(current_integrations=AsyncMock(return_value=IntegrationSettings(instances=[instance])))
    result = await initial.tools["search_tools"].definition.handler(SearchToolsArguments(query="tasks_create"), context)
    record = discovery(result)
    reloaded = offer([instance], profile, [record])
    assert "tasks_create" in reloaded.tools
    assert reloaded.schemas()[:len(initial.tools)] == initial.schemas()
    record.provider_data = json.loads(json.dumps(record.provider_data, sort_keys=True))
    assert offer([instance], profile, [record]).schemas() == reloaded.schemas()
    assert "tasks_create" not in offer([instance], AgentProfile(name="Other", instructions="Task"), [record]).tools


async def test_shared_tool_schema_loads_only_pinned_accounts_and_search_rechecks_availability():
    pinned, deferred, disabled = calendar("Pinned"), calendar("Deferred"), calendar("Disabled")
    profile = AgentProfile(name="Agent", instructions="Task")
    pinned.profile_permissions[profile.id] = IntegrationPermissions(tool_loading="all")
    disabled.profile_permissions[profile.id] = IntegrationPermissions(available=False, tool_loading="all")
    initial = offer([pinned, deferred, disabled], profile)
    schema = initial.tools["tasks_create"].schema()
    assert schema["parameters"]["properties"]["integration_id"]["enum"] == [str(pinned.id)]
    assert str(disabled.id) not in json.dumps(initial.schemas())
    context = SimpleNamespace(current_integrations=AsyncMock(return_value=IntegrationSettings(instances=[])))
    result = await initial.tools["search_tools"].definition.handler(SearchToolsArguments(query="tasks_create"), context)
    assert json.loads(result.content)["tools"] == []


def test_context_pressure_expires_older_discoveries_not_the_latest():
    instance = calendar()
    profile = AgentProfile(name="Agent", instructions="Task")
    records = [Message(id=uuid4(), role="tool", provider_data={"_matoloop": {"toolName": "search_tools", "toolDiscovery": {
        "profileId": str(profile.id), "tools": {name: [str(instance.id)]},
    }}}) for name in ("calendar_query_events", "tasks_create")]
    assert expire_discoveries(records, profile)
    offered = offer([instance], profile, records)
    assert "calendar_query_events" not in offered.tools and "tasks_create" in offered.tools


def test_all_mode_can_defer_one_connection_and_disabled_connections_stay_excluded():
    instance = calendar()
    profile = AgentProfile(name="Agent", instructions="Task", integration_tool_loading="all")
    assert "tasks_create" in offer([instance], profile).tools
    instance.profile_permissions[profile.id] = IntegrationPermissions(tool_loading="on_demand")
    assert "tasks_create" not in offer([instance], profile).tools
    instance.profile_permissions[profile.id].available = False
    assert "search_tools" not in offer([instance], profile).tools


async def test_search_prioritizes_original_mcp_names_over_verbose_neighbor_descriptions():
    instance = IntegrationInstance(name="hass", integration_type="mcp", adapter_type="mcp_streamable_http", config={
        "endpoint": "https://mcp.example.test/", "tools": [
            {"name": name, "title": "Entity tool", "description": description, "input_schema": {"type": "object"}}
            for name, description in [("ha_get_state", "Read current state."),
                *[(f"ha_other_{index}", "Home Assistant entity state attributes metadata search registry read get status") for index in range(6)]]
        ],
    })
    profile = AgentProfile(name="Agent", instructions="Task")
    offered = offer([instance], profile)
    context = SimpleNamespace(current_integrations=AsyncMock(return_value=IntegrationSettings(instances=[instance])))
    result = await offered.tools["search_tools"].definition.handler(SearchToolsArguments(query="ha_get_state read entity state"), context)
    assert json.loads(result.content)["tools"][0]["name"] == mcp_model_tool_name(instance.id, "ha_get_state")


async def test_runtime_persists_search_results_before_offering_discovered_tools(monkeypatch):
    from runtime_helpers import _runtime_setup

    instance = IntegrationInstance(name="Desk", integration_type="mcp", adapter_type="mcp_streamable_http", config={
        "endpoint": "https://mcp.example.test/", "tools": [{"name": "get_ticket", "description": "Read ticket details.",
        "input_schema": {"type": "object", "properties": {"ticket_id": {"type": "integer"}}, "required": ["ticket_id"]}}],
    })
    name = mcp_model_tool_name(instance.id, "get_ticket")
    execute = tool_registry._execute_definition
    db, run, requests, _, _ = await _runtime_setup(monkeypatch, [
        ProviderResult(tool_calls=[ToolCall("find", "search_tools", {"query": "get_ticket"})]),
        ProviderResult(tool_calls=[ToolCall("read", name, {"ticket_id": 4711})]),
        ProviderResult(content="Done", finish_reason="stop"),
    ], integration_settings=IntegrationSettings(instances=[instance]), integration_tool_loading="on_demand", search_policy=WebSearchPolicy.AVAILABLE)
    records = await AgentRuntime._records(db, run.session_id)

    def add(item):
        if isinstance(item, Message):
            item.id, item.created_at = uuid4(), datetime.now(UTC)
            records.append(item)

    async def execute_call(call, context, tool):
        return await execute(call, context, tool) if call.name == "search_tools" else ToolResult.json({"ticket": 4711})

    db.add.side_effect = add
    db.scalar.side_effect = lambda statement: None if statement.column_descriptions[0]["entity"] is ContextSummary else True
    monkeypatch.setattr(tool_registry, "_execute_definition", execute_call)
    await AgentRuntime._execute_loop(db, run)
    assert [name in {tool["name"] for tool in request["tools"]} for request in requests] == [False, True, True]
