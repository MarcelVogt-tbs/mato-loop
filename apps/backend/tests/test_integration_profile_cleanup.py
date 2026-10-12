from unittest.mock import AsyncMock
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

import mato_loop_backend.tools.registry  # noqa: F401
from mato_loop_backend.integrations.service import (
    clear_profile_integration_policies,
)
from mato_loop_backend.settings import (
    PLAN_PROFILE_ID,
    AgentProfilesSettings,
    CustomAgentProfile,
    SettingsStore,
    UserAgentProfilesSettings,
)
from mato_loop_backend.settings.models import AgentProfileAccessOverrides


def _profile(integration_id: str, name: str = "Custom") -> CustomAgentProfile:
    return CustomAgentProfile(
        name=name,
        description="Keep me",
        baseProfileId=PLAN_PROFILE_ID,
        overrides={
            "instructions": "Keep these instructions",
            "privatePermissions": {
                "policies": {"shell.read": "ask"},
                "integrationPolicies": {
                    integration_id: {"calendar.read": "auto"},
                    "keep": {"calendar.read": "ask"},
                },
            },
            "publicPermissions": {
                "resourcePolicies": {
                    integration_id: {"calendar:old": {"calendar.read": "ask"}},
                    "keep": {"calendar:new": {"calendar.read": "auto"}},
                }
            },
        },
    )


async def test_instance_cleanup_removes_profile_policies_from_all_scopes(
    monkeypatch,
) -> None:
    integration_id = str(uuid4())
    first_user_id = uuid4()
    second_user_id = uuid4()
    instance = AgentProfilesSettings(
        profiles=[_profile(integration_id)],
        builtin_access_overrides={
            PLAN_PROFILE_ID: AgentProfileAccessOverrides(
                privatePermissions={
                    "resourcePolicies": {
                        integration_id: {"calendar:old": {"calendar.read": "ask"}}
                    }
                },
                integrationsEnabledByDefault=True,
            )
        },
    )
    first_user = UserAgentProfilesSettings(profiles=[_profile(integration_id, "First")])
    second_user = UserAgentProfilesSettings(profiles=[_profile("keep", "Second")])
    documents = {
        (AgentProfilesSettings, None): instance,
        (UserAgentProfilesSettings, first_user_id): first_user,
        (UserAgentProfilesSettings, second_user_id): second_user,
    }
    monkeypatch.setattr(
        SettingsStore,
        "list_user_scopes",
        AsyncMock(return_value=[first_user_id, second_user_id]),
    )
    monkeypatch.setattr(
        SettingsStore,
        "load_optional",
        AsyncMock(
            side_effect=lambda _db, settings_type, **kwargs: documents[
                (settings_type, kwargs.get("user_id"))
            ]
        ),
    )
    save = AsyncMock()
    monkeypatch.setattr(SettingsStore, "save", save)
    db = AsyncMock(spec=AsyncSession)

    await clear_profile_integration_policies(db, integration_id, owner_id=None)

    for settings in (instance, first_user):
        permissions = settings.profiles[0].overrides
        assert integration_id not in permissions.private_permissions.integration_policies
        assert integration_id not in permissions.public_permissions.resource_policies
        assert "keep" in permissions.private_permissions.integration_policies
        assert "keep" in permissions.public_permissions.resource_policies
        assert permissions.instructions == "Keep these instructions"
    builtin = instance.builtin_access_overrides[PLAN_PROFILE_ID]
    assert integration_id not in builtin.private_permissions.resource_policies
    assert builtin.integrations_enabled_by_default is True
    assert [call.kwargs.get("user_id") for call in save.await_args_list] == [
        None,
        first_user_id,
    ]
    db.commit.assert_not_awaited()


async def test_personal_cleanup_only_loads_owner_profile_document(monkeypatch) -> None:
    integration_id = str(uuid4())
    owner_id = uuid4()
    personal = UserAgentProfilesSettings(profiles=[_profile(integration_id)])
    load_optional = AsyncMock(return_value=personal)
    list_user_scopes = AsyncMock()
    monkeypatch.setattr(SettingsStore, "load_optional", load_optional)
    monkeypatch.setattr(SettingsStore, "list_user_scopes", list_user_scopes)
    monkeypatch.setattr(SettingsStore, "save", AsyncMock())
    db = AsyncMock(spec=AsyncSession)

    await clear_profile_integration_policies(
        db, integration_id, owner_id=owner_id
    )

    load_optional.assert_awaited_once_with(
        db,
        UserAgentProfilesSettings,
        user_id=owner_id,
        for_update=True,
    )
    list_user_scopes.assert_not_awaited()
