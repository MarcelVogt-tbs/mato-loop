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
    _profile_from_request,
    _validate_permissions,
    agent_profile_integration_metadata,
)
from mato_loop_backend.integrations.contracts import IntegrationResource
from mato_loop_backend.models import UserAccount
from mato_loop_backend.settings import (
    DEFAULT_AGENT_PROFILE_ID,
    AgentProfile,
    AgentProfilePermissions,
    AgentProfilesSettings,
    IntegrationInstance,
    IntegrationSettings,
    SettingsStore,
    ToolExecutionPolicy,
    ToolSettings,
    UserAgentProfilesSettings,
    load_effective_agent_profiles,
)


def test_default_agent_inherits_tool_defaults() -> None:
    profile = AgentProfilesSettings().profiles[0]

    assert profile.id == DEFAULT_AGENT_PROFILE_ID
    assert profile.name == "Allgemein"
    assert profile.persona_id is None
    assert profile.private_permissions == AgentProfilePermissions()
    assert profile.public_permissions == AgentProfilePermissions()


def test_agent_profile_migration_installs_inheriting_default_profile() -> None:
    path = (
        Path(__file__).parents[1]
        / "alembic/versions/6bf7ebf0c5b3_add_agent_profiles_to_sessions.py"
    )
    spec = importlib.util.spec_from_file_location("agent_profile_migration", path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    assert migration.DEFAULT_AGENT_PROFILE_ID == DEFAULT_AGENT_PROFILE_ID
    profile = migration._default_profile()
    assert profile["persona"] == ""
    assert profile["private_permissions"]["policies"] == {}
    assert profile["public_permissions"]["policies"] == {}
    assert not hasattr(migration, "_reset_tool_policies")


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
        profiles=[AgentProfile(name="Global", instructions="Global task")]
    )
    personal = UserAgentProfilesSettings(
        profiles=[AgentProfile(name="Personal", instructions="Personal task")]
    )
    monkeypatch.setattr(SettingsStore, "load", AsyncMock(return_value=instance))
    monkeypatch.setattr(
        SettingsStore, "load_optional", AsyncMock(return_value=personal)
    )

    effective = await load_effective_agent_profiles(AsyncMock(), user_id)

    assert [profile.name for profile in effective.profiles] == ["Global", "Personal"]
    assert effective.profiles[0].owner_id is None
    assert effective.profiles[1].owner_id == user_id


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
                                "allowedPolicies": ["disabled", "ask", "auto"],
                            }
                        ],
                    }
                ]
            }
        ]
    )

    assert set(result) == {first, second}
    assert result[first]["calendar.read"] == set(ToolExecutionPolicy)


def test_agent_profile_request_rejects_blank_trimmed_text() -> None:
    with pytest.raises(ValidationError):
        AgentProfileRequest(name="  ", instructions="Task")
    with pytest.raises(ValidationError):
        AgentProfileRequest(name="Agent", instructions="  ")

    persona_id = uuid4()
    request = AgentProfileRequest(
        name="Agent", personaId=persona_id, instructions="Task"
    )
    assert request.persona_id == persona_id


async def test_legacy_agent_profile_update_preserves_persona_id(monkeypatch) -> None:
    async def load_settings(_db, settings_type, **_kwargs):
        return settings_type()

    monkeypatch.setattr(SettingsStore, "load", AsyncMock(side_effect=load_settings))
    persona_id = uuid4()
    existing = AgentProfile(name="Agent", persona_id=persona_id, instructions="Old task")
    user = UserAccount(
        id=uuid4(), username="admin", password_hash="hash", is_admin=True
    )
    payload = AgentProfileRequest(name="Agent", instructions="New task")

    updated = await _profile_from_request(
        AsyncMock(),
        user,
        None,
        payload,
        profile_id=existing.id,
        existing=existing,
    )

    assert updated.persona_id == persona_id
    assert updated.instructions == "New task"

    cleared = await _profile_from_request(
        AsyncMock(),
        user,
        None,
        AgentProfileRequest(name="Agent", personaId=None, instructions="New task"),
        profile_id=existing.id,
        existing=existing,
    )
    assert cleared.persona_id is None


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
            ToolAction("calendar.read", "Kalender lesen", ToolExecutionPolicy.ASK),
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
