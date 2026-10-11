import importlib.util
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import make_transient_to_detached

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
from mato_loop_backend.permissions.resolution import tool_execution_policy
from mato_loop_backend.settings import (
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
        (AgentProfilesSettings, None): AgentProfilesSettings(personal_agents_enabled=False),
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


async def test_global_profile_permissions_and_legacy_assignment_transfer(monkeypatch):
    user = UserAccount(id=uuid4(), is_admin=True)
    integration = IntegrationInstance(name="Shared", integration_type="calendar", adapter_type="caldav", config={"base_url": "https://calendar.test", "username": "owner"})
    documents = {
        (IntegrationSettings, None): IntegrationSettings(instances=[integration]),
        (AgentProfilesSettings, None): AgentProfilesSettings(builtin_access_overrides={GENERAL_PROFILE_ID: AgentProfileAccessOverrides(integration_availability={str(integration.id): False})}),
    }
    db = settings_db(monkeypatch, documents)
    monkeypatch.setattr("mato_loop_backend.api.integration_resources._integration_resources", AsyncMock(return_value=()))
    await update_profile_integration_availability(GENERAL_PROFILE_ID, ProfileIntegrationAvailabilityRequest(integrationsEnabledByDefault=True), user, db, "instance")
    page = await get_integration_permissions(integration.id, user, db, "instance", GENERAL_PROFILE_ID)
    assert page["permissions"]["available"] is False
    await update_integration_permissions(integration.id, IntegrationPermissions(available=True, publicPermissions={"policies": {"calendar.write": "auto"}}), user, db, "instance", GENERAL_PROFILE_ID)
    tools, profile = await load_agent_profile_tools(db, user.id, GENERAL_PROFILE_ID, TrustLevel.PUBLIC)
    stored = documents[IntegrationSettings, None].instances[0]
    assert (integration_profile_available(stored, profile), tool_execution_policy(tools, "calendar.write", integration_id=stored.id), profile.integration_availability) == (True, "auto", {})


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
    snapshot = {"permissionVersion": 2, "permissions": tools.permissions.model_dump(mode="json"), "agentPermissions": None,
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
    monkeypatch.setattr(SettingsStore, "save", AsyncMock())
    async with AsyncSession(expire_on_commit=False) as db:
        db.add(user)
        if save:
            result = await update_integration_permissions(integration.id, IntegrationPermissions(), user, db, "personal")
        else:
            result = await get_integration_permissions(integration.id, user, db, "personal")
        assert result["inherited"]["public"]["resources"][0]["label"] == "Aufnahmen auflisten"


async def test_reset_builtin_tool_permissions_preserves_integration_selection(monkeypatch):
    user = UserAccount(id=uuid4(), username="admin", password_hash="hash", is_admin=True)
    documents = {(AgentProfilesSettings, None): AgentProfilesSettings(builtin_access_overrides={
        PLAN_PROFILE_ID: AgentProfileAccessOverrides(integrations_enabled_by_default=True,
                                                     public_permissions={"policies": {"shell.write": "auto"}}),
    })}
    db = settings_db(monkeypatch, documents)
    await update_profile_tool_permissions(PLAN_PROFILE_ID, ProfileToolPermissionsRequest(), user, db, "instance")
    tools, profile = await load_agent_profile_tools(db, user.id, PLAN_PROFILE_ID, TrustLevel.PUBLIC)
    assert (tool_execution_policy(tools, "shell.write"), profile.integrations_enabled_by_default) == ("disabled", True)


@pytest.mark.parametrize("scope", ["instance", "personal"])
async def test_profile_integration_assignment_preserves_tool_overrides(monkeypatch, scope):
    user = UserAccount(id=uuid4(), username="owner", password_hash="hash", is_admin=scope == "instance")
    integration = IntegrationInstance(name="Calendar", integration_type="calendar", adapter_type="caldav")
    profile = CustomAgentProfile(name="Own", description="", base_profile_id=GENERAL_PROFILE_ID,
                                 overrides={"publicPermissions": {"policies": {"shell.write": "auto"}}})
    documents = {
        (IntegrationSettings, None): IntegrationSettings(instances=[integration]),
        (ToolSettings, None): ToolSettings(personal_settings_enabled=True),
        (UserAgentProfilesSettings, user.id): UserAgentProfilesSettings(profiles=[profile]),
        (AgentProfilesSettings, None): AgentProfilesSettings(builtin_access_overrides={
            GENERAL_PROFILE_ID: AgentProfileAccessOverrides(public_permissions={"policies": {"shell.write": "auto"}}),
        }),
    }
    db = settings_db(monkeypatch, documents)
    profile_id = GENERAL_PROFILE_ID if scope == "instance" else profile.id
    await update_profile_integration_availability(profile_id, ProfileIntegrationAvailabilityRequest(
        integrationsEnabledByDefault=False, integrationAvailability={str(integration.id): True},
    ), user, db, scope)
    response = await get_profile_integration_availability(profile_id, user, db, scope)
    tools, _ = await load_agent_profile_tools(db, user.id, profile_id, TrustLevel.PUBLIC)
    assert (response["integrationsEnabledByDefault"], response["integrationAvailability"],
            tool_execution_policy(tools, "shell.write")) == (False, {str(integration.id): True}, "auto")


async def test_profile_integration_assignment_rejects_unknown_connections(monkeypatch):
    user = UserAccount(id=uuid4(), username="admin", password_hash="hash", is_admin=True)
    db = settings_db(monkeypatch, {})
    with pytest.raises(ApiError, match="validation.invalidChoice"):
        await update_profile_integration_availability(GENERAL_PROFILE_ID, ProfileIntegrationAvailabilityRequest(
            integrationAvailability={str(uuid4()): True},
        ), user, db, "instance")


async def test_profile_content_cannot_bypass_tool_permission_gate():
    user = UserAccount(id=uuid4(), username="owner", password_hash="hash", is_admin=False)
    with pytest.raises(ApiError, match="tools.usePermissionSettings"):
        await _profile_from_request(AsyncMock(), user, user.id, AgentProfileRequest(
            name="Own", description="", baseProfileId=GENERAL_PROFILE_ID,
            overrides={"publicPermissions": {"policies": {"shell.write": "auto"}}},
        ))


async def test_personal_tool_permission_gate_precedes_profile_lookup(monkeypatch):
    user = UserAccount(id=uuid4(), username="owner", password_hash="hash", is_admin=False)
    db = settings_db(monkeypatch, {(ToolSettings, None): ToolSettings(personal_settings_enabled=False)})
    with pytest.raises(ApiError, match="tools.personalDisabled"):
        await update_profile_tool_permissions(uuid4(), ProfileToolPermissionsRequest(), user, db, "personal")


@pytest.mark.parametrize(("admin", "expected"), [(False, "ask"), (True, "auto")])
async def test_disabled_personal_tool_gate_uses_base_rules_except_for_admins(monkeypatch, admin, expected):
    user = UserAccount(id=uuid4(), username="owner", password_hash="hash", is_admin=admin)
    profile = CustomAgentProfile(name="Own", description="", base_profile_id=GENERAL_PROFILE_ID,
                                 overrides={"publicPermissions": {"policies": {"shell.write": "auto"}}})
    db = settings_db(monkeypatch, {
        (UserAgentProfilesSettings, user.id): UserAgentProfilesSettings(profiles=[profile]),
        (ToolSettings, None): ToolSettings(personal_settings_enabled=False),
    })
    db.get.return_value = user
    tools, _ = await load_agent_profile_tools(db, user.id, profile.id, TrustLevel.PUBLIC)
    assert tool_execution_policy(tools, "shell.write") == expected


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
