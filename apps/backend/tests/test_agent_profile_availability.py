import json
from dataclasses import replace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from pydantic import ValidationError

from mato_loop_backend.agent.permissions import PermissionDecision
from mato_loop_backend.agent.tool_registry import ToolContext, ToolResult
from mato_loop_backend.agent.tools import _coding_analyze, _web_search, tool_registry
from mato_loop_backend.api.errors import ApiError
from mato_loop_backend.api.routes.agent_profiles import (
    AgentProfileRequest,
    _profile_from_request,
    _response,
)
from mato_loop_backend.integrations.registry import effective_integration_contributions
from mato_loop_backend.models import ChatSession, Project, UserAccount
from mato_loop_backend.providers.base import ToolCall
from mato_loop_backend.settings.effective import (
    filter_agent_profile_integrations,
    load_agent_profile_tools,
)
from mato_loop_backend.settings.models import (
    BUILD_AGENT_PROFILE_ID,
    DEFAULT_AGENT_PROFILE_ID,
    PLAN_AGENT_PROFILE_ID,
    AgentProfile,
    AgentProfilesSettings,
    IntegrationInstance,
    IntegrationSettings,
    ToolSettings,
    TrustLevel,
    UserIntegrationSettings,
)
from mato_loop_backend.settings.store import SettingsStore


@pytest.mark.parametrize("by_alias", [False, True])
@pytest.mark.parametrize(
    "profile_id,limit,enabled,answer_display,todo_enabled",
    [
        (DEFAULT_AGENT_PROFILE_ID, 25, True, "compact", False),
        (PLAN_AGENT_PROFILE_ID, None, False, "timeline", True),
        (BUILD_AGENT_PROFILE_ID, None, False, "timeline", True),
        (uuid4(), 25, True, "compact", False),
    ],
)
def test_profile_defaults_backfill_only_missing_fields(
    by_alias, profile_id, limit, enabled, answer_display, todo_enabled
):
    old = AgentProfile(id=profile_id, name="Renamed", instructions="Custom task").model_dump(
        mode="json", by_alias=by_alias
    )
    keys = (
        ("answerDisplay", "maxToolCalls", "todoEnabled", "integrationsEnabledByDefault")
        if by_alias
        else ("answer_display", "max_tool_calls", "todo_enabled", "integrations_enabled_by_default")
    )
    for key in keys:
        old.pop(key)
    loaded = AgentProfile.model_validate(old)
    assert loaded.answer_display == answer_display
    assert loaded.max_tool_calls == limit
    assert loaded.todo_enabled is todo_enabled
    assert loaded.integrations_enabled_by_default is enabled
    dumped = loaded.model_dump(mode="json", by_alias=by_alias)
    assert {key: value for key, value in dumped.items() if key not in keys} == old
    old.update(dict(zip(keys, ["compact", None, False, False], strict=True)))
    assert AgentProfile.model_validate(old).model_dump(mode="json", by_alias=by_alias) == old
    old.update(dict(zip(keys, ["timeline", 9, True, True], strict=True)))
    assert AgentProfile.model_validate(old).model_dump(mode="json", by_alias=by_alias) == old


@pytest.mark.parametrize("profile,enabled", [
    (AgentProfile(id=DEFAULT_AGENT_PROFILE_ID, name="Allgemein", instructions="Task"), False),
    (AgentProfile(id=PLAN_AGENT_PROFILE_ID, name="Plan", instructions="Task"), True),
    (AgentProfile(id=BUILD_AGENT_PROFILE_ID, name="Build", instructions="Task"), True),
    (AgentProfile(name="Custom", instructions="Task"), False),
])
def test_todo_tool_follows_profile_availability(profile, enabled):
    tools = ToolSettings()
    tools._agent_profile = profile

    effective = tool_registry.effective(IntegrationSettings(), tool_settings=tools)

    assert ("todo_write" in effective.tools) is enabled


@pytest.mark.parametrize("model", [AgentProfile, AgentProfileRequest])
@pytest.mark.parametrize("value", [None, "cards", 1])
def test_answer_display_rejects_invalid_explicit_values(model, value):
    with pytest.raises(ValidationError):
        model(name="Agent", instructions="Task", answerDisplay=value)


@pytest.mark.parametrize("model", [AgentProfile, AgentProfileRequest])
@pytest.mark.parametrize("value", [0, -1, True, False, 1.0, "25"])
def test_profile_limit_requires_a_positive_strict_integer(model, value):
    with pytest.raises(ValidationError):
        model(name="Agent", instructions="Task", maxToolCalls=value)


@pytest.mark.parametrize("model", [AgentProfile, AgentProfileRequest])
@pytest.mark.parametrize(
    "overrides",
    [
        {"not-a-uuid": True},
        {str(uuid4()).upper(): False},
        {str(uuid4()): 1},
        {str(uuid4()): "false"},
        {str(uuid4()): None},
    ],
)
def test_profile_availability_requires_canonical_ids_and_strict_booleans(model, overrides):
    with pytest.raises(ValidationError):
        model(name="Agent", instructions="Task", integrationAvailability=overrides)


def account(kind="calendar", adapter="caldav", **values):
    config = {"base_url": "https://example.com", "username": "user"}
    if kind == "mail":
        config = {
            "imap_host": "mail.example.com",
            "imap_port": 993,
            "imap_security": "tls",
            "smtp_host": "mail.example.com",
            "smtp_port": 465,
            "smtp_security": "tls",
            "username": "user",
            "from_address": "user@example.com",
        }
    return IntegrationInstance(
        name=adapter,
        integration_type=kind,
        adapter_type=adapter,
        config=config,
        **values,
    )


def mock_settings(monkeypatch, integrations, profile, *, personal=None):
    async def load(_db, model, **_kwargs):
        if model is IntegrationSettings:
            return integrations.model_copy(deep=True)
        if model is AgentProfilesSettings:
            return AgentProfilesSettings(profiles=[profile.model_copy(deep=True)])
        return model()

    async def optional(_db, model, **_kwargs):
        return personal if model is UserIntegrationSettings else None

    monkeypatch.setattr(SettingsStore, "load", AsyncMock(side_effect=load))
    monkeypatch.setattr(SettingsStore, "load_optional", AsyncMock(side_effect=optional))


async def test_profile_update_preserves_omitted_new_fields_and_unchanged_stale_ids(monkeypatch):
    stale_id = str(uuid4())
    profile = AgentProfile(
        name="Agent",
        instructions="Task",
        maxToolCalls=None,
        integrationsEnabledByDefault=False,
        integrationAvailability={stale_id: False},
    )
    mock_settings(monkeypatch, IntegrationSettings(), profile)
    user = UserAccount(id=uuid4(), username="admin", password_hash="hash", is_admin=True)
    for data in ({}, {"integrationAvailability": {stale_id: False}}):
        result = await _profile_from_request(
            AsyncMock(),
            user,
            None,
            AgentProfileRequest(name="Agent", instructions="Task", **data),
            profile_id=profile.id,
            existing=profile,
        )
        assert result.model_dump() == profile.model_dump()
    for overrides in ({stale_id: True}, {str(uuid4()): False}):
        with pytest.raises(ApiError, match="validation.invalidChoice"):
            await _profile_from_request(
                AsyncMock(),
                user,
                None,
                AgentProfileRequest(
                    name="Agent", instructions="Task", integrationAvailability=overrides
                ),
                existing=profile,
            )
    result = await _profile_from_request(
        AsyncMock(),
        user,
        None,
        AgentProfileRequest(
            name="Agent",
            instructions="Task",
            maxToolCalls=None,
            integrationsEnabledByDefault=True,
            integrationAvailability={},
        ),
        existing=profile.model_copy(update={"max_tool_calls": 8}),
    )
    assert result.max_tool_calls is None
    assert result.integrations_enabled_by_default is True
    assert result.integration_availability == {}


async def test_profile_scope_validation_and_listing_include_option_only_integrations(monkeypatch):
    search = account("search", "searxng")
    coding = account("coding", "opencode")
    profile = AgentProfile(name="Agent", instructions="Task")
    mock_settings(
        monkeypatch,
        IntegrationSettings(instances=[search]),
        profile,
        personal=UserIntegrationSettings(instances=[coding]),
    )
    user = UserAccount(id=uuid4(), username="admin", password_hash="hash", is_admin=True)
    request = AgentProfileRequest(
        name="Agent", instructions="Task", integrationAvailability={str(coding.id): True}
    )
    with pytest.raises(ApiError, match="validation.invalidChoice"):
        await _profile_from_request(AsyncMock(), user, None, request)
    result = await _profile_from_request(AsyncMock(), user, user.id, request)
    assert result.integration_availability == {str(coding.id): True}
    instance_result = await _response(AsyncMock(), user, scope="instance")
    personal_result = await _response(AsyncMock(), user, scope="personal")
    assert [item["id"] for item in instance_result["integrations"]] == [str(search.id)]
    assert {item["id"] for item in personal_result["integrations"]} == {
        str(search.id),
        str(coding.id),
    }
    assert all(not item["supportsPolicies"] for item in personal_result["integrations"])


def test_shared_accounts_opt_in_is_separate_from_permissions_and_does_not_mutate_source():
    first, hidden = account(), account()
    settings = IntegrationSettings(instances=[first, hidden])
    profile = AgentProfile(
        name="Agent",
        instructions="Task",
        integrationsEnabledByDefault=False,
        integrationAvailability={str(first.id): True, str(hidden.id): False},
        privatePermissions={"policies": {"calendar.read": "ask"}},
    )
    tools = ToolSettings()
    tools._agent_profile = profile
    before = settings.model_dump_json(), profile.model_dump_json(), tools.model_dump_json()
    effective = tool_registry.effective(settings, tool_settings=tools)
    bound = effective.tools["calendar_list_calendars"]
    assert set(dict(bound.integration_revisions)) == {str(first.id)}
    assert bound.schema()["parameters"]["properties"]["integration_id"]["enum"] == [str(first.id)]
    assert str(hidden.id) not in json.dumps(effective.schemas())
    filtered = filter_agent_profile_integrations(settings, profile)
    assert [item.id for item in filtered.instances] == [first.id]
    assert filter_agent_profile_integrations(settings, profile, allowed_ids=[]).instances == []
    assert before == (
        settings.model_dump_json(),
        profile.model_dump_json(),
        tools.model_dump_json(),
    )
    profile.integrations_enabled_by_default = True
    future = account()
    settings.instances.append(future)
    assert [item.id for item in filter_agent_profile_integrations(settings, profile).instances] == [
        first.id,
        future.id,
    ]
    assert profile.integration_availability == {str(first.id): True, str(hidden.id): False}


@pytest.mark.parametrize(
    "kind,adapter,tool_name",
    [
        ("openscad", "openscad", "render_openscad"),
        ("coding", "opencode", "coding_analyze"),
        ("search", "searxng", "web_search"),
    ],
)
def test_profile_selection_covers_execution_builtin_and_option_tools(kind, adapter, tool_name):
    integration = account(
        kind, adapter, execution_agent_id=uuid4() if kind == "openscad" else None
    )
    if kind == "openscad":
        integration.config = {"binary_path": "/usr/bin/openscad"}
    profile = AgentProfile(name="Agent", instructions="Task", integrationsEnabledByDefault=False)
    settings = IntegrationSettings(instances=[integration])
    tools = ToolSettings()
    tools._agent_profile = profile
    if kind == "search":
        tools.web_search.primary_provider = f"searxng:{integration.id}"
    assert (
        tool_name
        not in tool_registry.effective(
            settings, tool_settings=tools, tool_capabilities={"project.coding"}
        ).tools
    )
    profile.integration_availability[str(integration.id)] = True
    effective = tool_registry.effective(
        settings, tool_settings=tools, tool_capabilities={"project.coding"}
    )
    assert tool_name in effective.tools
    if kind == "coding":
        assert (
            tool_name
            not in tool_registry.effective(
                settings,
                tool_settings=tools,
                tool_capabilities={"project.coding"},
                coding_integration_id=uuid4(),
            ).tools
        )
    if kind == "search":
        profile.integration_availability[str(integration.id)] = False
        filtered = filter_agent_profile_integrations(settings, profile)
        assert not effective_integration_contributions(filtered).tool_options
        tools.web_search.primary_provider = "no_key"
        tools.web_search.fallback_provider = f"searxng:{integration.id}"
        available = tool_registry.effective(settings, tool_settings=tools).tools["web_search"]
        assert available.selected_options["provider"] == "no_key"
        assert available.selected_options["fallback_provider"] is None


async def tool_context(monkeypatch, settings, profile, call):
    mock_settings(monkeypatch, settings, profile)
    session = ChatSession(id=uuid4(), user_id=uuid4(), agent_profile_id=profile.id)
    tools, loaded_profile = await load_agent_profile_tools(
        AsyncMock(), session.user_id, profile.id, TrustLevel.PRIVATE
    )
    assert tools._agent_profile == loaded_profile
    return ToolContext(
        db=AsyncMock(),
        session=session,
        run_id=uuid4(),
        tool_call=call,
        root_files=None,
        integration_settings=settings,
        tool_settings=tools,
        ask_handler=AsyncMock(return_value=PermissionDecision.ALLOW),
    )


@pytest.mark.parametrize("revoke", ["before", "approval", "tamper", "none"])
async def test_shared_binding_guards_selection_before_and_after_approval(monkeypatch, revoke):
    first, hidden = account(), account()
    settings = IntegrationSettings(instances=[first, hidden])
    profile = AgentProfile(
        name="Agent",
        instructions="Task",
        integrationsEnabledByDefault=False,
        integrationAvailability={str(first.id): True},
    )
    call = ToolCall(
        id="call",
        name="calendar_list_calendars",
        arguments={"integration_id": str(hidden.id if revoke == "tamper" else first.id)},
    )
    context = await tool_context(monkeypatch, settings, profile, call)
    effective = tool_registry.effective(settings, tool_settings=context.tool_settings)
    bound = effective.tools[call.name]
    handler = AsyncMock(return_value=ToolResult.json({"ok": True}))
    effective.tools[call.name] = replace(
        bound, definition=replace(bound.definition, handler=handler)
    )
    if revoke == "before":
        profile.integration_availability[str(first.id)] = False
    if revoke == "approval":

        async def approve(_request):
            profile.integration_availability[str(first.id)] = False
            return PermissionDecision.ALLOW

        context.ask_handler = approve
    result = await effective.execute(call, context)
    assert result.is_error is (revoke != "none")
    if revoke != "none":
        assert result.metadata["errorCode"] == (
            "permission.denied" if revoke == "approval" else "tool.unavailable"
        )
        handler.assert_not_awaited()
    else:
        assert [item.id for item in context.integration_settings.instances] == [first.id]
        handler.assert_awaited_once()


@pytest.mark.parametrize("kind,adapter", [("search", "searxng"), ("coding", "opencode")])
async def test_search_and_coding_recheck_selection_after_approval(monkeypatch, kind, adapter):
    from mato_loop_backend.agent import tools as tools_module
    from mato_loop_backend.agent.tool_arguments import CodingAnalyzeArguments, WebSearchArguments

    integration = account(kind, adapter)
    profile = AgentProfile(name="Agent", instructions="Task")
    settings = IntegrationSettings(instances=[integration])
    call = ToolCall(id="call", name="web_search", arguments={"query": "test"})
    context = await tool_context(monkeypatch, settings, profile, call)

    async def approve(_request):
        profile.integration_availability[str(integration.id)] = False
        return PermissionDecision.ALLOW

    context.ask_handler = approve
    if integration.adapter_type == "searxng":
        tools = ToolSettings(web_search={"primary_provider": f"searxng:{integration.id}"})
        monkeypatch.setattr(tools_module, "_profile_tool_settings", AsyncMock(return_value=tools))
        execute = AsyncMock()
        monkeypatch.setattr(tools_module, "search_web", execute)
        result = await _web_search(WebSearchArguments(query="test"), context)
    else:
        context.session.project_id = uuid4()
        context.db.scalar.return_value = Project(
            id=context.session.project_id,
            user_id=context.session.user_id,
            name="Project",
            coding_integration_id=integration.id,
        )
        execute = AsyncMock()
        monkeypatch.setattr(tools_module.ProjectCodingService, "analyze", execute)
        result = await _coding_analyze(
            CodingAnalyzeArguments(title="Task", brief="Inspect"), context
        )
    assert result.is_error
    execute.assert_not_awaited()


@pytest.mark.parametrize("delegated", [False, True])
@pytest.mark.parametrize("kind,adapter", [("calendar", "caldav"), ("mail", "imap_smtp")])
async def test_account_discovery_and_helper_reloads_never_expand_the_selection(
    monkeypatch, delegated, kind, adapter
):
    from mato_loop_backend.agent.subagents import permission_snapshot

    first, hidden = account(kind, adapter), account(kind, adapter)
    settings = IntegrationSettings(instances=[first, hidden])
    profile = AgentProfile(
        name="Agent",
        instructions="Task",
        integrationsEnabledByDefault=False,
        integrationAvailability={str(first.id): True},
    )
    call = ToolCall(id="call", name=f"{kind}_list_accounts", arguments={})
    context = await tool_context(monkeypatch, settings, profile, call)
    if delegated:
        context.tool_settings._delegation_ceiling = permission_snapshot(
            context.tool_settings, set(), IntegrationSettings(instances=[first])
        )
        profile.integrations_enabled_by_default = True
        context.tool_settings._agent_profile = profile.model_copy(deep=True)
    effective = tool_registry.effective(settings, tool_settings=context.tool_settings)
    result = await effective.execute(call, context)
    assert not result.is_error
    assert json.loads(result.content)["accounts"] == [
        {"integrationId": str(first.id), "name": first.name}
    ]
    assert [item.id for item in (await context.current_integrations()).instances] == [first.id]
