import importlib.util
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from pydantic import ValidationError

from mato_loop_backend.agent.tools import ToolAction, tool_registry
from mato_loop_backend.api.errors import ApiError
from mato_loop_backend.api.routes import agent_profiles as agent_profiles_module
from mato_loop_backend.api.routes.agent_profiles import (
    AgentProfileRequest,
    _catalog_integration_policies,
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
    assert profile.name == "Default"
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
    assert profile["private_permissions"]["policies"] == {}
    assert profile["public_permissions"]["policies"] == {}
    assert not hasattr(migration, "_reset_tool_policies")


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
