import importlib.util
from pathlib import Path
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from pydantic import ValidationError

from mato_loop_backend.agent.tools import ToolAction, tool_registry
from mato_loop_backend.api.errors import ApiError
from mato_loop_backend.api.routes import agent_profiles as agent_profiles_module
from mato_loop_backend.api.routes.agent_profiles import (
    AgentProfileRequest,
    _catalog_integration_policies,
    _ensure_unique_name,
    _profile_from_request,
    _profile_payload,
    _validate_permissions,
    agent_profile_integration_metadata,
    delete_agent_profile,
    update_agent_profile,
)
from mato_loop_backend.integrations.contracts import IntegrationResource
from mato_loop_backend.integrations.tbs_desk import profile_id as tbs_profile_id
from mato_loop_backend.models import ChatSession, UserAccount
from mato_loop_backend.settings import (
    BUILD_PROFILE_ID,
    GENERAL_PROFILE_ID,
    PLAN_PROFILE_ID,
    AgentIdentitySettings,
    AgentProfileOverrides,
    AgentProfilePermissions,
    AgentProfilesSettings,
    CustomAgentProfile,
    IntegrationInstance,
    IntegrationSettings,
    SettingsStore,
    ToolExecutionPolicy,
    ToolSettings,
    UserAgentProfilesSettings,
    UserIntegrationSettings,
    core_profiles,
    load_effective_agent_profiles,
    resolve_agent_profile,
)


def test_builtin_agent_profile_defaults() -> None:
    profiles = core_profiles()
    default, plan, build = profiles

    assert [profile.id for profile in profiles] == [
        GENERAL_PROFILE_ID,
        PLAN_PROFILE_ID,
        BUILD_PROFILE_ID,
    ]
    assert [profile.name for profile in profiles] == ["Allgemein", "Plan", "Build"]
    assert AgentProfilesSettings().profiles == []
    assert default.persona_id is None
    assert default.private_permissions == AgentProfilePermissions()
    assert default.public_permissions == AgentProfilePermissions()
    assert plan.private_permissions == plan.public_permissions
    assert plan.private_permissions.policies == {
        "filesystem.read": ToolExecutionPolicy.AUTO,
        "filesystem.write": ToolExecutionPolicy.DISABLED,
        "filesystem.delete": ToolExecutionPolicy.DISABLED,
        "session.files.write": ToolExecutionPolicy.DISABLED,
        "shell.read": ToolExecutionPolicy.AUTO,
        "shell.write": ToolExecutionPolicy.DISABLED,
    }
    assert build.private_permissions == build.public_permissions
    assert build.private_permissions.policies == {
        "filesystem.read": ToolExecutionPolicy.AUTO,
        "filesystem.write": ToolExecutionPolicy.AUTO,
        "filesystem.delete": ToolExecutionPolicy.AUTO,
        "shell.read": ToolExecutionPolicy.AUTO,
        "shell.write": ToolExecutionPolicy.AUTO,
    }
    assert not plan.private_permissions.integration_policies
    assert not build.private_permissions.integration_policies
    for profile in (plan, build):
        assert "simplest" in profile.instructions
        assert "current requirements justify" in profile.instructions
        assert "standard-library or native features" in profile.instructions
    assert default.delegation_enabled is False
    assert default.session_write_paths is None
    assert plan.delegation_enabled is True
    assert plan.session_write_paths == []
    assert build.delegation_enabled is True
    assert build.session_write_paths is None
    assert "Return the plan directly in the conversation" in plan.instructions
    assert "Do not implement" in plan.instructions
    assert "do not call any tool" not in plan.instructions
    assert "continue only permitted analysis and planning" in plan.runtime_instructions
    assert default.runtime_instructions == build.runtime_instructions == ""
    assert "keep an analysis-only task read-only" in build.instructions
    assert "plan discussed earlier in the conversation" in build.instructions
    assert "profile switch alone does not authorize implementation" in build.instructions
    assert "plan.md" not in plan.instructions
    assert "plan.md" not in build.instructions


def test_custom_profile_stores_only_sparse_typed_overrides() -> None:
    profile = CustomAgentProfile(
        name="Custom",
        description="Metadata",
        baseProfileId=PLAN_PROFILE_ID,
        overrides={
            "todoEnabled": False,
            "maxToolCalls": None,
            "integrationAvailability": {},
            "privatePermissions": {},
            "sessionWritePaths": [],
        },
    )
    saved = AgentProfilesSettings(profiles=[profile]).model_dump(mode="json", by_alias=True)

    assert saved["profiles"][0]["overrides"] == {
        "todoEnabled": False,
        "maxToolCalls": None,
        "integrationAvailability": {},
        "privatePermissions": {},
        "sessionWritePaths": [],
    }
    assert AgentProfilesSettings.model_validate(saved).model_dump(mode="json", by_alias=True) == saved
    effective = resolve_agent_profile(profile, core_profiles()[1], None)
    assert effective.todo_enabled is False
    assert effective.session_write_paths == []
    assert effective.public_permissions == core_profiles()[1].public_permissions


def test_runtime_instructions_inherit_override_and_clear() -> None:
    base = core_profiles()[1]
    profile = CustomAgentProfile(name="Custom", description="", baseProfileId=base.id, overrides={})
    assert resolve_agent_profile(profile, base, None).runtime_instructions == base.runtime_instructions
    for text in ("Inspect only; do not publish.", ""):
        profile.overrides = AgentProfileOverrides(runtimeInstructions=text)
        saved = CustomAgentProfile.model_validate(profile.model_dump(mode="json", by_alias=True))
        assert resolve_agent_profile(saved, base, None).runtime_instructions == text
    with pytest.raises(ValidationError):
        AgentProfileOverrides(runtimeInstructions=None)


@pytest.mark.parametrize(
    "path", ["", "/plan.md", "./plan.md", "notes//plan.md", "notes/../plan.md",
             "notes/", "*.md", "notes\\plan.md"]
)
def test_session_write_paths_require_normalized_relative_files(path: str) -> None:
    with pytest.raises(ValidationError):
        AgentProfileOverrides(sessionWritePaths=[path])


def test_core_profiles_preserve_instance_custom_profile_capacity() -> None:
    custom = [
        CustomAgentProfile(
            name=f"Custom {index}", description="", baseProfileId=GENERAL_PROFILE_ID,
            overrides={},
        )
        for index in range(99)
    ]

    AgentProfilesSettings(profiles=custom)
    with pytest.raises(ValidationError):
        AgentProfilesSettings(
            profiles=[
                *custom,
                CustomAgentProfile(
                    name="One too many", description="", baseProfileId=GENERAL_PROFILE_ID,
                    overrides={},
                ),
            ]
        )


@pytest.mark.parametrize("name", ["allgemein", "pLaN", "BUILD"])
def test_builtin_agent_profile_names_are_reserved_case_insensitively(name: str) -> None:
    with pytest.raises(ApiError, match="agentProfile.defaultProtected"):
        _ensure_unique_name(AgentProfilesSettings(), name)


def test_default_is_not_a_reserved_profile_name() -> None:
    _ensure_unique_name(AgentProfilesSettings(), "Default")


def test_agent_profile_payload_marks_only_builtins_as_protected() -> None:
    identity = AgentIdentitySettings()

    builtin_payload = _profile_payload(
        core_profiles()[1], "instance", identity, None
    )
    assert builtin_payload["protected"] is True
    assert builtin_payload["baseProfileId"] is None
    assert "overrides" not in builtin_payload
    assert builtin_payload["delegationEnabled"] is True
    assert builtin_payload["sessionWritePaths"] == []
    custom = CustomAgentProfile(
        name="Custom", description="", baseProfileId=GENERAL_PROFILE_ID,
        overrides={"todoEnabled": False, "sessionWritePaths": []},
    )
    custom_payload = _profile_payload(
        resolve_agent_profile(custom, core_profiles()[0], None),
        "instance", identity, None, custom,
    )
    assert custom_payload["protected"] is False
    assert custom_payload["baseProfileId"] == str(GENERAL_PROFILE_ID)
    assert custom_payload["overrides"] == {
        "todoEnabled": False,
        "sessionWritePaths": [],
    }
    assert custom_payload["instructions"] == core_profiles()[0].instructions


@pytest.mark.parametrize("profile_id", [PLAN_PROFILE_ID, BUILD_PROFILE_ID])
async def test_plan_and_build_cannot_be_renamed_or_deleted(
    monkeypatch, profile_id
) -> None:
    settings = AgentProfilesSettings()
    user = UserAccount(
        id=uuid4(), username="admin", password_hash="hash", is_admin=True
    )
    monkeypatch.setattr(SettingsStore, "load", AsyncMock(return_value=settings))

    with pytest.raises(ApiError, match="agentProfile.defaultProtected"):
        await update_agent_profile(
            profile_id,
            AgentProfileRequest(
                name="Renamed", description="", baseProfileId=profile_id, overrides={}
            ),
            user,
            AsyncMock(),
            scope="instance",
        )
    with pytest.raises(ApiError, match="agentProfile.defaultProtected"):
        await delete_agent_profile(
            profile_id,
            user,
            AsyncMock(),
            scope="instance",
        )

    user.is_admin = False
    with pytest.raises(ApiError, match="auth.adminRequired"):
        await delete_agent_profile(profile_id, user, AsyncMock(), scope="instance")


async def test_delete_agent_profile_moves_sessions_to_default_and_clears_grants(
    monkeypatch,
) -> None:
    profile = CustomAgentProfile(
        name="Custom", description="", baseProfileId=GENERAL_PROFILE_ID,
        overrides={},
    )
    settings = AgentProfilesSettings(profiles=[profile])
    user = UserAccount(
        id=uuid4(), username="admin", password_hash="hash", is_admin=True
    )
    sessions = [
        ChatSession(id=uuid4(), user_id=user.id, agent_profile_id=profile.id),
        ChatSession(id=uuid4(), user_id=uuid4(), agent_profile_id=profile.id),
    ]
    rows = Mock()
    rows.all.return_value = sessions
    db = AsyncMock()
    db.scalars.return_value = rows
    monkeypatch.setattr(SettingsStore, "load", AsyncMock(return_value=settings))
    monkeypatch.setattr(SettingsStore, "save", AsyncMock())

    await delete_agent_profile(profile.id, user, db, scope="instance")

    assert all(
        session.agent_profile_id == GENERAL_PROFILE_ID for session in sessions
    )
    assert profile not in settings.profiles
    db.execute.assert_awaited_once()
    assert "DELETE FROM permission_grants" in str(db.execute.await_args.args[0])
    SettingsStore.save.assert_awaited_once_with(db, settings, user_id=None)
    db.commit.assert_awaited_once()


def test_agent_profile_migration_installs_inheriting_default_profile() -> None:
    path = (
        Path(__file__).parents[1]
        / "alembic/versions/6bf7ebf0c5b3_add_agent_profiles_to_sessions.py"
    )
    spec = importlib.util.spec_from_file_location("agent_profile_migration", path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    assert migration.DEFAULT_AGENT_PROFILE_ID == GENERAL_PROFILE_ID
    profile = migration._default_profile()
    assert profile["persona"] == ""
    assert profile["private_permissions"]["policies"] == {}
    assert profile["public_permissions"]["policies"] == {}


def test_profile_inheritance_migration_resets_only_current_profile_state(monkeypatch) -> None:
    path = (
        Path(__file__).parents[1]
        / "alembic/versions/8cf1ced587b7_reset_agent_profiles_for_inheritance.py"
    )
    spec = importlib.util.spec_from_file_location("profile_inheritance_migration", path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    execute = Mock()
    rows = Mock()
    rows.scalars.return_value = []
    connection = Mock()
    connection.execute.return_value = rows
    monkeypatch.setattr(migration.op, "execute", execute)
    monkeypatch.setattr(migration.op, "get_bind", Mock(return_value=connection))

    migration.upgrade()

    statements = "\n".join(str(call.args[0]) for call in execute.call_args_list)
    assert "UPDATE settings" in statements
    assert "DELETE FROM permission_grants" in statements
    assert "UPDATE chat_sessions" in statements
    assert "UPDATE agent_runs" in statements
    assert "status NOT IN ('completed', 'failed', 'cancelled')" in statements
    assert "messages" not in statements

    execute.reset_mock()
    migration.downgrade()
    downgrade_statements = [call.args[0] for call in execute.call_args_list]
    statements = "\n".join(str(statement) for statement in downgrade_statements)
    downgrade = next(
        statement for statement in downgrade_statements
        if "CASE WHEN user_id IS NULL" in str(statement)
    )
    assert "WHERE key = 'agent-profiles'" in str(downgrade)
    profiles = downgrade.compile().params["profiles"]
    assert [profile["name"] for profile in profiles] == ["Allgemein", "Plan", "Build"]
    assert profiles[1]["private_permissions"]["policies"]["filesystem.write"] == "disabled"
    assert profiles[2]["private_permissions"]["policies"]["filesystem.write"] == "auto"
    assert "DELETE FROM permission_grants" in statements
    assert "UPDATE chat_sessions" in statements
    assert "UPDATE agent_runs" in statements


def test_policy_migration_splits_trust_branches_and_webfetch_targets() -> None:
    migration = _migration_module()
    migrated = migration._migrate_tool_data(
        {
            "policies": {"filesystem.read": "auto"},
            "integrationPolicies": {"one": {"calendar.read": "ask"}},
            "resourcePolicies": {"one": {"calendar:work": {"calendar.read": "auto"}}},
            "webFetch": {
                "enabled": True,
                "defaultPolicy": "auto_allowed",
                "allowedPrivateOrigins": ["http://internal.example"],
            },
        },
        ["disabled"],
    )

    assert migrated["private_permissions"] == migrated["public_permissions"]
    assert migrated["public_permissions"]["policies"]["network.fetch.public"] == "disabled"
    assert migrated["public_permissions"]["policies"]["network.fetch.private"] == "disabled"
    assert migrated["resource_availability"]["webfetch"] == {
        "network.fetch.public": True,
        "network.fetch.private": False,
    }
    assert "allowedPrivateOrigins" not in migrated["web_fetch"]


def test_policy_migration_creates_tools_for_explicit_model_policy(monkeypatch) -> None:
    migration = _migration_module()
    user_id = uuid4()
    existing_user_id = uuid4()
    rows = Mock()
    rows.mappings.return_value.all.return_value = [
        {
            "id": uuid4(),
            "key": "ai",
            "user_id": user_id,
            "data": {"models": [{"webfetchPolicy": "disabled"}]},
        },
        {
            "id": uuid4(),
            "key": "ai",
            "user_id": existing_user_id,
            "data": {"models": [{"webfetchPolicy": "ask"}]},
        },
        {
            "id": uuid4(),
            "key": "tools",
            "user_id": existing_user_id,
            "data": {"personal_tool_defaults_enabled": False},
        },
    ]
    connection = Mock()
    connection.execute.side_effect = [rows, None, None, None, None]
    monkeypatch.setattr(migration.op, "get_bind", Mock(return_value=connection))
    monkeypatch.setattr(migration.op, "add_column", Mock())
    monkeypatch.setattr(migration.op, "create_check_constraint", Mock())
    monkeypatch.setattr(migration.op, "execute", Mock())
    monkeypatch.setattr(migration.op, "alter_column", Mock())
    monkeypatch.setattr(migration, "_install_default_profile", Mock())

    migration.upgrade()

    insert_call = next(
        call for call in connection.execute.call_args_list if call.args[0].is_insert
    )
    document = insert_call.args[1][0]
    assert document["user_id"] == user_id
    assert document["data"]["personal_tool_defaults_enabled"] is True
    assert document["data"]["public_permissions"]["policies"] == {
        "network.fetch.public": "disabled",
        "network.fetch.private": "disabled",
    }
    existing_document = connection.execute.call_args_list[3].args[0].compile().params[
        "data"
    ]
    assert existing_document["personal_tool_defaults_enabled"] is True


def test_policy_downgrade_preserves_webfetch_hard_disable() -> None:
    migration = _migration_module()

    downgraded = migration._downgrade_tool_data(
        {
            "public_permissions": {
                "policies": {
                    "network.fetch.public": "auto",
                    "network.fetch.private": "ask",
                }
            },
            "private_permissions": {},
            "tool_availability": {"webfetch": False},
            "resource_availability": {
                "webfetch": {
                    "network.fetch.public": True,
                    "network.fetch.private": False,
                }
            },
        }
    )

    assert downgraded["web_fetch"]["enabled"] is False
    assert downgraded["web_fetch"]["default_policy"] == "disabled"


def _migration_module():
    path = (
        Path(__file__).parents[1]
        / "alembic/versions/6bf7ebf0c5b3_add_agent_profiles_to_sessions.py"
    )
    spec = importlib.util.spec_from_file_location("agent_profile_policy_migration", path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


async def test_effective_agent_profiles_merge_enabled_personal_profiles(
    monkeypatch,
) -> None:
    user_id = uuid4()
    instance = AgentProfilesSettings(
        profiles=[CustomAgentProfile(
            name="Global", description="Instance", baseProfileId=GENERAL_PROFILE_ID,
            overrides={"instructions": "Global task"},
        )]
    )
    personal = UserAgentProfilesSettings(
        profiles=[CustomAgentProfile(
            name="Personal", description="Personal", baseProfileId=PLAN_PROFILE_ID,
            overrides={"instructions": "Personal task", "todoEnabled": False},
        )]
    )
    async def load(_db, settings_type, **_kwargs):
        return instance if settings_type is AgentProfilesSettings else IntegrationSettings()

    monkeypatch.setattr(SettingsStore, "load", AsyncMock(side_effect=load))
    monkeypatch.setattr(
        SettingsStore, "load_optional", AsyncMock(
            side_effect=lambda _db, settings_type, **_kwargs: (
                personal if settings_type is UserAgentProfilesSettings else None
            )
        )
    )

    effective = await load_effective_agent_profiles(AsyncMock(), user_id)

    assert [profile.name for profile in effective.profiles] == [
        "Allgemein", "Plan", "Build", "Global", "Personal"
    ]
    assert effective.profiles[3].owner_id is None
    assert effective.profiles[4].owner_id == user_id
    assert effective.profiles[4].delegation_enabled is True
    assert effective.profiles[4].todo_enabled is False


async def test_builtin_access_inherits_per_entry_and_respects_scope(monkeypatch) -> None:
    from mato_loop_backend.settings.models import AgentProfileAccessOverrides

    integration_id = str(uuid4())
    child = CustomAgentProfile(name="Child", description="", baseProfileId=PLAN_PROFILE_ID,
        overrides={"privatePermissions": {"policies": {"shell.read": "ask"}}})
    instance = AgentProfilesSettings(profiles=[child], builtin_access_overrides={PLAN_PROFILE_ID:
        AgentProfileAccessOverrides(integrationAvailability={integration_id: True},
            privatePermissions={"policies": {"filesystem.read": "ask"},
                "integrationPolicies": {integration_id: {"browser.read": "auto", "browser.write": "ask"}},
                "resourcePolicies": {integration_id: {"page": {"browser.read": "ask"}}}})})
    personal = UserAgentProfilesSettings(profiles=[CustomAgentProfile(
        name="Personal", description="", baseProfileId=PLAN_PROFILE_ID,
        overrides={"integrationAvailability": {integration_id: False}, "privatePermissions": {
            "policies": {"shell.read": "ask"},
            "integrationPolicies": {integration_id: {"browser.read": "disabled"}}}},
    )])
    monkeypatch.setattr(SettingsStore, "load", AsyncMock(side_effect=lambda db, kind, **kw:
        instance if kind is AgentProfilesSettings else IntegrationSettings()))
    monkeypatch.setattr(SettingsStore, "load_optional", AsyncMock(side_effect=lambda db, kind, **kw:
        personal if kind is UserAgentProfilesSettings else None))

    async def get(include_personal=True):
        return (await load_effective_agent_profiles(AsyncMock(), uuid4(), include_personal=include_personal)).profiles[-1]

    effective = await get()
    assert effective.integration_availability[integration_id] is False
    assert effective.private_permissions.policies["filesystem.write"] == ToolExecutionPolicy.DISABLED
    assert effective.private_permissions.policies["filesystem.read"] == ToolExecutionPolicy.ASK
    assert effective.private_permissions.policies["shell.read"] == ToolExecutionPolicy.ASK
    assert effective.private_permissions.integration_policies[integration_id] == {
        "browser.read": ToolExecutionPolicy.DISABLED, "browser.write": ToolExecutionPolicy.ASK}
    assert effective.private_permissions.resource_policies[integration_id]["page"]["browser.read"] == ToolExecutionPolicy.ASK
    assert (await get(False)).integration_availability[integration_id] is True
    instance.builtin_access_overrides[PLAN_PROFILE_ID].private_permissions.policies["filesystem.read"] = ToolExecutionPolicy.AUTO
    assert (await get()).private_permissions.policies["filesystem.read"] == ToolExecutionPolicy.AUTO


def test_builtin_access_documents_are_sparse_and_restrict_fields() -> None:
    from mato_loop_backend.settings.models import AgentProfileAccessOverrides

    original = AgentProfilesSettings(builtin_access_overrides={PLAN_PROFILE_ID:
        AgentProfileAccessOverrides(integrationsEnabledByDefault=False)})
    restored = AgentProfilesSettings.model_validate(original.model_dump(mode="json", by_alias=True))
    override = restored.builtin_access_overrides[PLAN_PROFILE_ID]
    assert override.model_fields_set == {"integrations_enabled_by_default"}
    assert override.integrations_enabled_by_default is False
    assert "builtin_access_overrides" not in UserAgentProfilesSettings.model_fields
    with pytest.raises(ValidationError):
        AgentProfilesSettings(builtin_access_overrides={uuid4(): {}})
    for field in ("instructions", "runtimeInstructions", "personaId", "delegationEnabled"):
        with pytest.raises(ValidationError):
            AgentProfileAccessOverrides.model_validate({field: "changed"})
    with pytest.raises(ValidationError):
        AgentProfileAccessOverrides(publicPermissions=None)


async def test_builtin_access_update_validates_and_can_reset(monkeypatch) -> None:
    from mato_loop_backend.api.routes.agent_profiles import update_builtin_access
    from mato_loop_backend.settings.models import AgentProfileAccessOverrides

    settings = AgentProfilesSettings()
    user = UserAccount(id=uuid4(), username="admin", password_hash="hash", is_admin=True)
    monkeypatch.setattr(SettingsStore, "load", AsyncMock(side_effect=lambda db, kind, **kw:
        settings if kind is AgentProfilesSettings else kind()))
    save = AsyncMock()
    monkeypatch.setattr(SettingsStore, "save", save)
    monkeypatch.setattr("mato_loop_backend.api.routes.agent_profiles._response", AsyncMock(return_value={}))
    db = AsyncMock()
    await update_builtin_access(PLAN_PROFILE_ID, AgentProfileAccessOverrides(
        privatePermissions={"policies": {"filesystem.read": "ask"}}), user, db, scope="instance")
    assert settings.builtin_access_overrides[PLAN_PROFILE_ID].private_permissions.policies["filesystem.read"] == ToolExecutionPolicy.ASK
    with pytest.raises(ApiError):
        await update_builtin_access(PLAN_PROFILE_ID, AgentProfileAccessOverrides(
            integrationAvailability={str(uuid4()): True}), user, db, scope="instance")
    with pytest.raises(ApiError):
        await update_builtin_access(PLAN_PROFILE_ID, AgentProfileAccessOverrides(
            privatePermissions={"policies": {"unknown.action": "auto"}}), user, db, scope="instance")
    await update_builtin_access(PLAN_PROFILE_ID, AgentProfileAccessOverrides(), user, db, scope="instance")
    assert not settings.builtin_access_overrides
    assert save.await_count == 2
    user.is_admin = False
    with pytest.raises(ApiError, match="auth.adminRequired"):
        await update_builtin_access(PLAN_PROFILE_ID, AgentProfileAccessOverrides(), user, db, scope="instance")
    settings.personal_agents_enabled = False
    with pytest.raises(ApiError, match="agentProfile.defaultProtected"):
        await update_builtin_access(PLAN_PROFILE_ID, AgentProfileAccessOverrides(), user, db, scope="personal")


async def test_integration_profiles_remain_available_when_personal_profiles_are_disabled(
    monkeypatch,
) -> None:
    user_id = uuid4()
    integration = IntegrationInstance(
        name="Support",
        integration_type="support",
        adapter_type="tbs-desk",
        config={"base_url": "https://desk.example", "username": "agent"},
    )

    async def load(_db, settings_type, **_kwargs):
        if settings_type is AgentProfilesSettings:
            return AgentProfilesSettings(personal_agents_enabled=False)
        return IntegrationSettings()

    async def load_optional(_db, settings_type, **_kwargs):
        if settings_type is UserIntegrationSettings:
            return UserIntegrationSettings(instances=[integration])
        return None

    monkeypatch.setattr(SettingsStore, "load", AsyncMock(side_effect=load))
    monkeypatch.setattr(SettingsStore, "load_optional", AsyncMock(side_effect=load_optional))

    effective = await load_effective_agent_profiles(AsyncMock(), user_id)

    profile = next(item for item in effective.profiles if item.id == tbs_profile_id(integration.id))
    assert profile.owner_id == user_id
    assert profile.integration_id == integration.id
    assert profile.editable is False


def test_agent_profile_permissions_reject_unknown_or_disallowed_capabilities() -> None:
    allowed = {"filesystem.read": {ToolExecutionPolicy.AUTO}}

    _validate_permissions(
        AgentProfilePermissions(policies={"filesystem.read": "auto"}),
        allowed,
        {},
        set(),
        "privatePermissions",
    )

    with pytest.raises(ApiError):
        _validate_permissions(
            AgentProfilePermissions(policies={"filesystem.read": "ask"}),
            allowed,
            {},
            set(),
            "publicPermissions",
        )
    with pytest.raises(ApiError):
        _validate_permissions(
            AgentProfilePermissions(policies={"unknown.action": "auto"}),
            allowed,
            {},
            set(),
            "publicPermissions",
        )


def test_agent_profile_permissions_preserve_unavailable_existing_capabilities() -> None:
    permissions = AgentProfilePermissions(policies={"mail.read": "ask"})

    _validate_permissions(
        permissions,
        {},
        {},
        set(),
        "privatePermissions",
        permissions,
    )


def test_agent_profile_permissions_accept_scoped_only_grants() -> None:
    integration_id = str(uuid4())

    _validate_permissions(
        AgentProfilePermissions(
            integration_policies={integration_id: {"mail.read": "ask"}}
        ),
        {"mail.read": {ToolExecutionPolicy.ASK}},
        {integration_id: {"mail.read": {ToolExecutionPolicy.ASK}}},
        {integration_id},
        "privatePermissions",
    )


def test_agent_profile_catalog_maps_shared_tools_to_every_integration() -> None:
    first = str(uuid4())
    second = str(uuid4())

    result = _catalog_integration_policies(
        [
            {
                "tools": [
                    {
                        "source": {"id": None, "ids": [first, second]},
                        "actions": [
                            {
                                "capability": "calendar.read",
                                "configurable": True,
                                "allowedPolicies": ["disabled", "ask"],
                            }
                        ],
                    }
                ]
            }
        ]
    )

    assert set(result) == {first, second}
    assert result[first]["calendar.read"] == {
        ToolExecutionPolicy.DISABLED,
        ToolExecutionPolicy.ASK,
    }

    with pytest.raises(ApiError):
        _validate_permissions(
            AgentProfilePermissions(
                integration_policies={first: {"calendar.read": "auto"}}
            ),
            {},
            result,
            {first, second},
            "privatePermissions",
        )


def test_agent_profile_request_rejects_blank_trimmed_text() -> None:
    with pytest.raises(ValidationError):
        AgentProfileRequest(
            name="  ", description="", baseProfileId=GENERAL_PROFILE_ID, overrides={}
        )
    with pytest.raises(ValidationError):
        AgentProfileRequest(
            name="Agent", description="", baseProfileId=GENERAL_PROFILE_ID,
            overrides={"instructions": "  "},
        )

    persona_id = uuid4()
    request = AgentProfileRequest(
        name="Agent", description="", baseProfileId=GENERAL_PROFILE_ID,
        overrides={"personaId": persona_id},
    )
    assert request.overrides.persona_id == persona_id


async def test_agent_profile_request_replaces_sparse_overrides(monkeypatch) -> None:
    async def load_settings(_db, settings_type, **_kwargs):
        return settings_type()

    monkeypatch.setattr(SettingsStore, "load", AsyncMock(side_effect=load_settings))
    persona_id = uuid4()
    existing = CustomAgentProfile(
        name="Agent", description="Old", baseProfileId=GENERAL_PROFILE_ID,
        overrides={"personaId": persona_id, "instructions": "Old task", "answerDisplay": "timeline"},
    )
    user = UserAccount(
        id=uuid4(), username="admin", password_hash="hash", is_admin=True
    )
    payload = AgentProfileRequest(
        name="Agent", description="New", baseProfileId=PLAN_PROFILE_ID,
        overrides={"instructions": "New task"},
    )

    updated = await _profile_from_request(
        AsyncMock(),
        user,
        None,
        payload,
        profile_id=existing.id,
        existing=existing,
    )

    assert updated.base_profile_id == PLAN_PROFILE_ID
    assert updated.description == "New"
    assert updated.overrides.model_dump(exclude_unset=True) == {"instructions": "New task"}


async def test_agent_profile_integration_metadata_lists_actions_and_resources(
    monkeypatch,
) -> None:
    integration = IntegrationInstance(
        name="Kalender",
        integration_type="calendar",
        adapter_type="caldav",
        config={"base_url": "https://calendar.example.com", "username": "test"},
    )
    profiles = AgentProfilesSettings()
    integrations = IntegrationSettings(instances=[integration])
    tools = ToolSettings(
        policies={"calendar.read": ToolExecutionPolicy.AUTO},
        integration_policies={
            str(integration.id): {"calendar.read": ToolExecutionPolicy.ASK}
        },
    )

    async def load_settings(_db, settings_type, **_kwargs):
        if settings_type is IntegrationSettings:
            return integrations
        if settings_type is ToolSettings:
            return tools
        return profiles

    monkeypatch.setattr(SettingsStore, "load", AsyncMock(side_effect=load_settings))
    monkeypatch.setattr(
        agent_profiles_module,
        "_integration_resources",
        AsyncMock(return_value=(IntegrationResource("calendar:work", "Arbeit"),)),
    )
    monkeypatch.setattr(
        tool_registry,
        "integration_resource_actions",
        lambda _instance: (
            ToolAction(
                "calendar.read",
                "Kalender lesen",
                ToolExecutionPolicy.ASK,
                ToolExecutionPolicy.ASK,
            ),
        ),
    )

    result = await agent_profile_integration_metadata(
        integration.id,
        UserAccount(
            id=uuid4(), username="admin", password_hash="hash", is_admin=True
        ),
        AsyncMock(),
        scope="instance",
    )

    assert result["actions"][0]["capability"] == "calendar.read"
    assert result["actions"][0]["effectiveIntegrationPolicy"] == "ask"
    assert result["resources"][0]["key"] == "calendar:work"
    assert result["resources"][0]["effectivePolicies"]["calendar.read"] == "ask"
