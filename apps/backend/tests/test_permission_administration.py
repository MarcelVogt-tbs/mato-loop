import importlib.util
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import make_transient_to_detached

from mato_loop_backend.agent.run_support import _tool_policy
from mato_loop_backend.api.errors import ApiError
from mato_loop_backend.api.routes.agent_profiles import (
    AgentProfileRequest,
    DefaultProfileRequest,
    EnabledRequest,
    _profile_from_request,
    set_agent_profile_enabled,
    set_default_agent_profile,
)
from mato_loop_backend.api.routes.integrations import (
    ProfileIntegrationAvailabilityRequest,
    get_integration_permissions,
    get_profile_integration_availability,
    update_integration_permissions,
    update_profile_integration_availability,
)
from mato_loop_backend.api.routes.tool_settings import (
    ProfileToolPermissionsRequest,
    get_profile_tool_permissions,
    update_profile_tool_permissions,
)
from mato_loop_backend.integrations.contracts import IntegrationResource
from mato_loop_backend.integrations.tbs_desk.adapter import profile_id as integration_profile_id
from mato_loop_backend.models import UserAccount
from mato_loop_backend.permissions.policy import integration_operation_permission
from mato_loop_backend.permissions.resolution import tool_execution_policy
from mato_loop_backend.settings import (
    BUILD_PROFILE_ID,
    GENERAL_PROFILE_ID,
    PLAN_PROFILE_ID,
    AgentProfilesSettings,
    CustomAgentProfile,
    IntegrationInstance,
    IntegrationSettings,
    SettingsStore,
    ToolSettings,
    TrustLevel,
    UserAgentProfilesSettings,
    UserIntegrationSettings,
    load_agent_profile_tools,
)
from mato_loop_backend.settings.effective import (
    apply_integration_permissions,
    integration_profile_available,
)
from mato_loop_backend.settings.models import AgentProfileAccessOverrides, IntegrationPermissions
from mato_loop_backend.tools.catalog import tool_registry


def settings_db(monkeypatch, documents):
    db = AsyncMock(spec=AsyncSession)

    async def load(_db, model, *, user_id=None, **_kwargs):
        return documents.get((model, user_id), model()).model_copy(deep=True)

    async def optional(_db, model, *, user_id=None, **_kwargs):
        return await load(_db, model, user_id=user_id) if (model, user_id) in documents else None

    async def save(_db, value, *, user_id=None):
        documents[type(value), user_id] = value.model_copy(deep=True)

    monkeypatch.setattr(SettingsStore, "load", load)
    monkeypatch.setattr(SettingsStore, "load_optional", optional)
    monkeypatch.setattr(SettingsStore, "save", save)
    return db


async def test_personal_integration_profile_permissions_are_independent_of_global_tools(monkeypatch):
    user = UserAccount(id=uuid4(), username="owner", password_hash="hash", is_admin=False)
    own = IntegrationInstance(name="Own", integration_type="calendar", adapter_type="caldav", config={"base_url": "https://calendar.example.com", "username": "owner"})
    shared = IntegrationInstance(name="Shared", integration_type="calendar", adapter_type="caldav", config={"base_url": "https://calendar.example.com", "username": "shared"})
    documents = {
        (AgentProfilesSettings, None): AgentProfilesSettings(
            personal_agents_enabled=False,
            builtin_access_overrides={GENERAL_PROFILE_ID: AgentProfileAccessOverrides(
                publicPermissions={"policies": {"calendar.write": "disabled"}}
            )},
        ),
        (ToolSettings, None): ToolSettings(public_permissions={"policies": {"calendar.write": "disabled"}}),
        (IntegrationSettings, None): IntegrationSettings(instances=[shared]),
        (UserIntegrationSettings, user.id): UserIntegrationSettings(instances=[own]),
    }
    db = settings_db(monkeypatch, documents)
    monkeypatch.setattr("mato_loop_backend.api.integration_resources._integration_resources", AsyncMock(return_value=()))
    await update_integration_permissions(
        own.id, IntegrationPermissions(publicPermissions={"policies": {"calendar.write": "auto"}}),
        user, db, "personal", GENERAL_PROFILE_ID,
    )
    tools, _ = await load_agent_profile_tools(db, user.id, GENERAL_PROFILE_ID, TrustLevel.PUBLIC)
    assert (
        tool_execution_policy(tools, "calendar.write", integration_id=own.id),
        tool_execution_policy(tools, "calendar.write", integration_id=shared.id),
    ) == ("auto", "ask")
    assert "calendar_create_event" in tool_registry.effective(
        IntegrationSettings(instances=[own, shared]), tool_settings=tools,
    ).tools


async def test_integration_permissions_cannot_target_another_owners_connection(monkeypatch):
    user = UserAccount(id=uuid4(), username="owner", password_hash="hash", is_admin=False)
    db = settings_db(monkeypatch, {})
    with pytest.raises(ApiError, match="integration.notFound"):
        await update_integration_permissions(uuid4(), IntegrationPermissions(), user, db, "personal", GENERAL_PROFILE_ID)


async def test_integration_permission_page_lists_configured_rights_and_resources(monkeypatch):
    user = UserAccount(id=uuid4(), username="owner", password_hash="hash", is_admin=False)
    integration = IntegrationInstance(name="Own", integration_type="calendar", adapter_type="caldav",
                                      config={"base_url": "https://calendar.example.com", "username": "owner"},
                                      permissions={"privatePermissions": {"policies": {"calendar.read": "ask"}}})
    db = settings_db(monkeypatch, {(UserIntegrationSettings, user.id): UserIntegrationSettings(instances=[integration])})
    monkeypatch.setattr("mato_loop_backend.api.integration_resources._integration_resources", AsyncMock(return_value=(IntegrationResource("calendar:work", "Arbeit"),)))
    response = await get_integration_permissions(integration.id, user, db, "personal")
    assert (response["permissions"]["privatePermissions"]["policies"],
               {resource["key"] for resource in response["inherited"]["private"]["resources"]}) == ({"calendar.read": "ask"}, {"calendar:work"})


@pytest.mark.parametrize("profile_id,expected", [(None, "ask"), (GENERAL_PROFILE_ID, "ask"), (PLAN_PROFILE_ID, "ask"), (BUILD_PROFILE_ID, "auto")])
async def test_browser_permission_page_displays_the_selected_profile_defaults(monkeypatch, profile_id, expected):
    user = UserAccount(id=uuid4(), is_admin=False)
    integration = IntegrationInstance(name="Browser", integration_type="browser", adapter_type="browser", execution_agent_id=uuid4())
    db = settings_db(monkeypatch, {(UserIntegrationSettings, user.id): UserIntegrationSettings(instances=[integration])})
    monkeypatch.setattr("mato_loop_backend.api.integration_resources._integration_resources", AsyncMock(return_value=()))
    response = await get_integration_permissions(integration.id, user, db, "personal", profile_id)
    assert [(action["label"], action["policy"]) for action in response["inherited"]["public"]["actions"] if action["capability"].startswith("browser.")] == [
        ("Browser verwenden", expected), ("Browser-Python ausführen", "ask"),
    ]


@pytest.mark.parametrize("trust", list(TrustLevel))
@pytest.mark.parametrize("preset, expected", [
    ("ask_all", ["ask", "ask", "ask"]),
    ("balanced", ["ask", "ask", "ask"]),
    ("ask_changes", ["auto", "ask", "ask"]),
    ("ask_delete", ["auto", "auto", "ask"]),
    ("auto_all", ["auto", "auto", "auto"]),
])
async def test_integration_page_and_runtime_share_base_preset(monkeypatch, trust, preset, expected):
    user = UserAccount(id=uuid4(), is_admin=False)
    instance = IntegrationInstance(name="Calendar", integration_type="calendar", adapter_type="caldav",
                                   config={"base_url": "https://calendar.test", "username": "owner"})
    tools = ToolSettings(**{f"{trust.value}_permission_preset": preset}).select_trust(trust)
    integrations = IntegrationSettings(instances=[instance])
    db = settings_db(monkeypatch, {(ToolSettings, None): tools,
                                  (UserIntegrationSettings, user.id): UserIntegrationSettings(instances=[instance])})
    monkeypatch.setattr("mato_loop_backend.api.integration_resources._integration_resources", AsyncMock(return_value=(IntegrationResource("calendar:work", "Work"),)))
    response = await get_integration_permissions(instance.id, user, db, "personal")
    displayed = {action["capability"]: action["policy"] for action in response["inherited"][trust.value]["actions"]}
    capabilities = ["calendar.read", "calendar.write", "calendar.delete"]
    runtime = [_tool_policy(tools, integrations, integration_operation_permission(
        capability=capability, integration_id=instance.id, resource_key="calendar:work", title="Calendar", summary="Calendar",
        binding_revision="{}", operation_key="test",
    )) for capability in capabilities]
    assert ([displayed[key] for key in capabilities], runtime) == (expected, expected)


async def test_global_profile_permissions_override_default_availability(monkeypatch):
    user = UserAccount(id=uuid4(), is_admin=True)
    integration = IntegrationInstance(name="Shared", integration_type="calendar", adapter_type="caldav", config={"base_url": "https://calendar.test", "username": "owner"})
    own = CustomAgentProfile(name="Own", description="", base_profile_id=GENERAL_PROFILE_ID, overrides={})
    documents = {
        (IntegrationSettings, None): IntegrationSettings(instances=[integration]),
        (AgentProfilesSettings, None): AgentProfilesSettings(profiles=[own]),
    }
    db = settings_db(monkeypatch, documents)
    monkeypatch.setattr("mato_loop_backend.api.integration_resources._integration_resources", AsyncMock(return_value=()))
    await update_profile_integration_availability(own.id, ProfileIntegrationAvailabilityRequest(integrationsEnabledByDefault=False), user, db, "instance")
    page = await get_integration_permissions(integration.id, user, db, "instance", own.id)
    assert page["inheritedAvailable"] is False
    await update_integration_permissions(integration.id, IntegrationPermissions(available=True, toolLoading="all", publicPermissions={"policies": {"calendar.write": "auto"}}), user, db, "instance", own.id)
    tools, profile = await load_agent_profile_tools(db, user.id, own.id, TrustLevel.PUBLIC)
    stored = documents[IntegrationSettings, None].instances[0]
    assert stored.profile_permissions[own.id].tool_loading == "all"
    assert (integration_profile_available(stored, profile), tool_execution_policy(tools, "calendar.write", integration_id=stored.id)) == (True, "auto")


@pytest.mark.parametrize("admin", [False, True])
async def test_global_connection_rejects_non_admin_or_personal_profile(monkeypatch, admin):
    user = UserAccount(id=uuid4(), is_admin=admin)
    integration = IntegrationInstance(name="Shared", integration_type="calendar", adapter_type="caldav")
    personal = CustomAgentProfile(name="Own", description="", base_profile_id=GENERAL_PROFILE_ID, overrides={})
    db = settings_db(monkeypatch, {(IntegrationSettings, None): IntegrationSettings(instances=[integration]), (UserAgentProfilesSettings, user.id): UserAgentProfilesSettings(profiles=[personal])})
    with pytest.raises(ApiError, match="agentProfile.notFound" if admin else "auth.adminRequired"):
        await update_integration_permissions(integration.id, IntegrationPermissions(), user, db, "instance", personal.id)


def test_resource_activation_retains_rules_and_delegated_denial():
    from mato_loop_backend.agent.subagents import apply_delegation_ceiling
    instance = IntegrationInstance(name="Shared", integration_type="calendar", adapter_type="caldav",
        permissions=IntegrationPermissions(privatePermissions={"resourcePolicies": {"calendar:work": {"calendar.read": "auto"}}}, resourceAvailability={"calendar:work": False}))
    tools = ToolSettings()
    apply_integration_permissions(tools, IntegrationSettings(instances=[instance]))
    snapshot = {"permissions": tools.permissions.model_dump(mode="json"), "agentPermissions": None,
                "shellPrefixes": {"read": [], "write": []}, "integrationDefaults": {str(instance.id): {"calendar.read": "ask"}}, "actionDefaults": {}}
    assert tool_execution_policy(tools, "calendar.read", integration_id=instance.id, resource_key="calendar:work") == "disabled"
    instance.permissions.resource_availability["calendar:work"] = True
    apply_integration_permissions(tools, IntegrationSettings(instances=[instance]))
    assert tool_execution_policy(tools, "calendar.read", integration_id=instance.id, resource_key="calendar:work") == "auto"
    apply_delegation_ceiling(tools, snapshot)
    assert tool_execution_policy(tools, "calendar.read", integration_id=instance.id, resource_key="calendar:work") == "disabled"


@pytest.mark.parametrize("save", [False, True])
async def test_sonobin_permissions_preserve_loaded_user(monkeypatch, save):
    import mato_loop_backend.api.routes.integrations as routes

    user = UserAccount(id=uuid4(), username="owner", password_hash="hash", is_admin=False)
    make_transient_to_detached(user)
    integration = IntegrationInstance(name="SonoBin", integration_type="music", adapter_type="sonobin",
                                     config={"endpoint": "https://music.example/mcp/", "tools": [
                                         {"name": "list_recordings", "input_schema": {"type": "object"}},
                                     ]})
    settings = UserIntegrationSettings(instances=[integration])
    monkeypatch.setattr(routes, "_permission_instance", AsyncMock(return_value=(settings, user.id, integration)))
    monkeypatch.setattr(routes, "load_effective_agent_profiles", AsyncMock(return_value=AgentProfilesSettings()))
    monkeypatch.setattr(SettingsStore, "load", AsyncMock(return_value=ToolSettings()))
    monkeypatch.setattr(SettingsStore, "save", AsyncMock())
    async with AsyncSession(expire_on_commit=False) as db:
        db.add(user)
        if save:
            result = await update_integration_permissions(integration.id, IntegrationPermissions(), user, db, "personal")
        else:
            result = await get_integration_permissions(integration.id, user, db, "personal")
        assert result["inherited"]["public"]["resources"][0]["label"] == "Aufnahmen auflisten"


async def test_builtin_access_overrides_can_be_changed_and_removed(monkeypatch):
    user = UserAccount(id=uuid4(), username="admin", password_hash="hash", is_admin=True)
    documents = {(AgentProfilesSettings, None): AgentProfilesSettings(builtin_access_overrides={
        PLAN_PROFILE_ID: AgentProfileAccessOverrides(integrations_enabled_by_default=True, temporary_files_enabled=False,
                                                     public_permissions={"policies": {"shell.write": "auto"}}),
    })}
    db = settings_db(monkeypatch, documents)
    response = await update_profile_tool_permissions(PLAN_PROFILE_ID, ProfileToolPermissionsRequest(
        publicPermissions={"policies": {"shell.write": "ask"}}, toolAvailability={"read": False}, shellPrefixes={"read": [["git", "status"]]},
    ), user, db, "instance")
    actions = [action for group in response["groupsByTrust"]["public"] for tool in group["tools"] for action in tool["actions"]]
    assert (response["publicPermissions"]["policies"]["shell.write"], next(action["policy"] for action in actions if action["capability"] == "shell.write"), response["inheritedToolAvailability"].get("read", True), response["inheritedShellPrefixes"]) == ("ask", "disabled", True, {"read": [], "write": []})
    await update_profile_tool_permissions(PLAN_PROFILE_ID, ProfileToolPermissionsRequest(
        publicPermissions={}, toolAvailability={}, shellPrefixes=None,
    ), user, db, "instance")
    await update_profile_integration_availability(PLAN_PROFILE_ID, ProfileIntegrationAvailabilityRequest(integrationsEnabledByDefault=None), user, db, "instance")
    tools, profile = await load_agent_profile_tools(db, user.id, PLAN_PROFILE_ID, TrustLevel.PUBLIC)
    assert tools._temporary_files_enabled is False
    assert (tool_execution_policy(tools, "shell.write"), tools.tool_availability.get("read", True), tools.shell_prefixes.read, profile.integrations_enabled_by_default) == ("disabled", True, [], False)


@pytest.mark.parametrize("scope", ["instance", "personal"])
@pytest.mark.parametrize("loading", ["on_demand", "native", "all"])
async def test_profile_integration_default_preserves_tool_overrides(monkeypatch, scope, loading):
    user = UserAccount(id=uuid4(), username="owner", password_hash="hash", is_admin=scope == "instance")
    integration = IntegrationInstance(name="Calendar", integration_type="calendar", adapter_type="caldav")
    profile = CustomAgentProfile(name="Own", description="", base_profile_id=GENERAL_PROFILE_ID,
                                 overrides={"publicPermissions": {"policies": {"shell.write": "auto"}}})
    documents = {
        (IntegrationSettings, None): IntegrationSettings(instances=[integration]),
        (ToolSettings, None): ToolSettings(personal_settings_enabled=True),
        (UserAgentProfilesSettings, user.id): UserAgentProfilesSettings(profiles=[profile]),
        (AgentProfilesSettings, None): AgentProfilesSettings(profiles=[profile]),
    }
    db = settings_db(monkeypatch, documents)
    profile_id = profile.id
    saved = await update_profile_integration_availability(profile_id, ProfileIntegrationAvailabilityRequest(
        integrationsEnabledByDefault=False, integrationToolLoading=loading,
    ), user, db, scope)
    assert saved["integrationToolLoading"] == loading
    await update_profile_integration_availability(profile_id, ProfileIntegrationAvailabilityRequest(
        integrationToolLoading=None,
    ), user, db, scope)
    response = await get_profile_integration_availability(profile_id, user, db, scope)
    tools, _ = await load_agent_profile_tools(db, user.id, profile_id, TrustLevel.PUBLIC)
    assert (response["integrationsEnabledByDefault"], response["integrationToolLoading"],
            response["inheritedIntegrationToolLoading"], tool_execution_policy(tools, "shell.write")) == (False, None, "native", "auto")


def test_profile_integration_default_rejects_removed_connection_assignments():
    with pytest.raises(ValidationError):
        ProfileIntegrationAvailabilityRequest(integrationAvailability={str(uuid4()): True})


@pytest.mark.parametrize("overrides", [{"publicPermissions": {"policies": {"shell.write": "auto"}}}, {"toolAvailability": {"read": True}}])
async def test_profile_content_cannot_bypass_tool_permission_gate(overrides):
    user = UserAccount(id=uuid4(), username="owner", password_hash="hash", is_admin=False)
    with pytest.raises(ApiError, match="tools.usePermissionSettings"):
        await _profile_from_request(AsyncMock(), user, user.id, AgentProfileRequest(
            name="Own", description="", baseProfileId=GENERAL_PROFILE_ID,
            overrides=overrides,
        ))


async def test_personal_tool_permission_gate_precedes_profile_lookup(monkeypatch):
    user = UserAccount(id=uuid4(), username="owner", password_hash="hash", is_admin=False)
    db = settings_db(monkeypatch, {(ToolSettings, None): ToolSettings(personal_settings_enabled=False)})
    with pytest.raises(ApiError, match="tools.personalDisabled"):
        await update_profile_tool_permissions(uuid4(), ProfileToolPermissionsRequest(), user, db, "personal")


@pytest.mark.parametrize("global_enabled,base_enabled,own,gate,expected", [
    (True, True, {"read": False}, True, False),
    (False, True, {"read": True}, True, False),
    (True, False, {}, True, False),
    (True, False, {"read": True}, True, True),
    (True, True, {"read": False}, False, True),
])
async def test_profile_tool_availability_inherits_and_respects_global_caps(monkeypatch, global_enabled, base_enabled, own, gate, expected):
    from mato_loop_backend.tools.catalog import tool_registry

    user = UserAccount(id=uuid4(), is_admin=False)
    profile = CustomAgentProfile(name="Own", description="", base_profile_id=GENERAL_PROFILE_ID, overrides={"toolAvailability": own})
    db = settings_db(monkeypatch, {
        (AgentProfilesSettings, None): AgentProfilesSettings(builtin_access_overrides={GENERAL_PROFILE_ID: AgentProfileAccessOverrides(tool_availability={"read": base_enabled})}),
        (UserAgentProfilesSettings, user.id): UserAgentProfilesSettings(profiles=[profile]),
        (ToolSettings, None): ToolSettings(personal_settings_enabled=gate, tool_availability={"read": global_enabled}),
    })
    db.get.return_value = user
    tools, _ = await load_agent_profile_tools(db, user.id, profile.id, TrustLevel.PRIVATE)
    available = tool_registry.effective(IntegrationSettings(), tool_settings=tools).tools
    assert ("read" in available, "glob" in available) == (expected, True)


async def test_profile_settings_survive_rule_updates_and_can_be_removed(monkeypatch):
    user = UserAccount(id=uuid4(), is_admin=True)
    own = CustomAgentProfile(name="Own", description="", base_profile_id=GENERAL_PROFILE_ID, overrides={})
    db = settings_db(monkeypatch, {(AgentProfilesSettings, None): AgentProfilesSettings(profiles=[own])})
    await update_profile_tool_permissions(own.id, ProfileToolPermissionsRequest(toolAvailability={"read": False}, shellPrefixes={"read": [], "write": []}), user, db, "instance")
    response = await update_profile_tool_permissions(own.id, ProfileToolPermissionsRequest(privatePermissions={"policies": {"filesystem.read": "auto"}}), user, db, "instance")
    assert (response["toolAvailability"], response["shellPrefixes"]) == ({"read": False}, {"read": [], "write": []})
    response = await update_profile_tool_permissions(own.id, ProfileToolPermissionsRequest(toolAvailability={}, shellPrefixes=None), user, db, "instance")
    assert (response["toolAvailability"], response["shellPrefixes"], response["privatePermissions"]["policies"]) == ({}, None, {"filesystem.read": "auto"})
    with pytest.raises(ApiError, match="validation.invalidChoice"):
        await update_profile_tool_permissions(own.id, ProfileToolPermissionsRequest(toolAvailability={"unknown": False}), user, db, "instance")


@pytest.mark.parametrize(("admin", "expected"), [(False, "ask"), (True, "auto")])
async def test_disabled_personal_tool_gate_uses_base_rules_except_for_admins(monkeypatch, admin, expected):
    user = UserAccount(id=uuid4(), username="owner", password_hash="hash", is_admin=admin)
    profile = CustomAgentProfile(name="Own", description="", base_profile_id=GENERAL_PROFILE_ID,
                                 overrides={"publicPermissions": {"policies": {"shell.write": "auto"}}, "temporaryFilesEnabled": True})
    db = settings_db(monkeypatch, {
        (UserAgentProfilesSettings, user.id): UserAgentProfilesSettings(profiles=[profile]),
        (ToolSettings, None): ToolSettings(personal_settings_enabled=False),
    })
    db.get.return_value = user
    tools, _ = await load_agent_profile_tools(db, user.id, profile.id, TrustLevel.PUBLIC)
    assert (tool_execution_policy(tools, "shell.write"), tools._temporary_files_enabled) == (expected, True)


async def test_tool_permission_display_uses_the_same_profile_inheritance_as_runtime(monkeypatch):
    user = UserAccount(id=uuid4(), username="admin", password_hash="hash", is_admin=True)
    db = settings_db(monkeypatch, {
        (ToolSettings, None): ToolSettings(public_permissions={"policies": {"session.files.read": "ask"}}),
    })
    response = await get_profile_tool_permissions(PLAN_PROFILE_ID, user, db, "instance")
    actions = [action for group in response["groupsByTrust"]["public"] for tool in group["tools"] for action in tool["actions"]]
    assert next(action["policy"] for action in actions if action["capability"] == "session.files.read") == "auto"


async def test_provided_integration_profiles_work_when_personal_tool_overrides_are_disabled(monkeypatch):
    user = UserAccount(id=uuid4(), username="owner", password_hash="hash", is_admin=False)
    integration = IntegrationInstance(name="Support", integration_type="support", adapter_type="tbs-desk",
                                      config={"base_url": "https://desk.example", "username": "agent"})
    db = settings_db(monkeypatch, {
        (AgentProfilesSettings, None): AgentProfilesSettings(personal_agents_enabled=False),
        (ToolSettings, None): ToolSettings(personal_settings_enabled=False),
        (UserIntegrationSettings, user.id): UserIntegrationSettings(instances=[integration]),
    })
    db.get.return_value = user
    _, profile = await load_agent_profile_tools(db, user.id, integration_profile_id(integration.id), TrustLevel.PUBLIC)
    assert profile.integration_id == integration.id


async def test_default_profile_remains_active_when_builtin_enablement_changes(monkeypatch):
    user = UserAccount(id=uuid4(), username="admin", password_hash="hash", is_admin=True)
    documents = {(AgentProfilesSettings, None): AgentProfilesSettings()}
    db = settings_db(monkeypatch, documents)
    await set_default_agent_profile(DefaultProfileRequest(profileId=PLAN_PROFILE_ID), user, db)
    await set_agent_profile_enabled(GENERAL_PROFILE_ID, EnabledRequest(enabled=False), user, db, "instance")
    with pytest.raises(ApiError, match="agentProfile.defaultMustBeActive"):
        await set_agent_profile_enabled(PLAN_PROFILE_ID, EnabledRequest(enabled=False), user, db, "instance")
    assert documents[AgentProfilesSettings, None].default_profile_id == PLAN_PROFILE_ID


def test_migration_preserves_personal_integration_rules_without_reviving_tool_snapshots():
    path = Path(__file__).parents[1] / "alembic/versions/7494cba04f61_reorganize_scoped_settings.py"
    spec = importlib.util.spec_from_file_location("permission_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    owner, integration_id, profile_id = uuid4(), str(uuid4()), str(uuid4())
    migrated = migration.migrate_documents({
        ("tools", None): {"public_permissions": {"policies": {"shell.write": "auto"}}},
        ("agent-profiles", None): {"builtin_access_overrides": {str(GENERAL_PROFILE_ID): {"public_permissions": {"policies": {"shell.write": "ask"}}}}},
        ("integrations", owner): {"instances": [{"id": integration_id}]},
        ("agent-profiles", owner): {"profiles": [{"id": profile_id, "overrides": {"public_permissions": {"integration_policies": {integration_id: {"calendar.write": "auto"}}}}}]},
    })
    assert (
        migrated["tools", None]["public_permissions"]["policies"],
        migrated["integrations", owner]["instances"][0]["profile_permissions"][profile_id]["public_permissions"]["policies"],
    ) == ({"shell.write": "ask"}, {"calendar.write": "auto"})
