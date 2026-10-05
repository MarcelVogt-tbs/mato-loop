from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.api.errors import ApiError
from mato_loop_backend.api.routes import agent_identity as api
from mato_loop_backend.models import UserAccount
from mato_loop_backend.settings import (
    GENERAL_PROFILE_ID,
    AgentIdentitySettings,
    AgentProfile,
    AgentProfilesSettings,
    CustomAgentProfile,
    PersonaProfile,
    SettingsStore,
    UserAgentIdentitySettings,
    UserAgentProfilesSettings,
    load_effective_agent_identity,
    resolve_agent_persona,
)


def settings_db(monkeypatch, instance, personal, *, admin=False, own_profile=None):
    user = UserAccount(id=uuid4(), username="owner", password_hash="hash", is_admin=admin)
    async def load(_db, model, **_kwargs):
        return {AgentIdentitySettings: instance, UserAgentIdentitySettings: personal,
                AgentProfilesSettings: AgentProfilesSettings(personal_agents_enabled=own_profile is not None),
                UserAgentProfilesSettings: UserAgentProfilesSettings(profiles=[own_profile] if own_profile else [])}.get(model, model())
    monkeypatch.setattr(SettingsStore, "load", load)
    monkeypatch.setattr(SettingsStore, "load_optional", load)
    monkeypatch.setattr(SettingsStore, "save", AsyncMock())
    monkeypatch.setattr(api, "_settings_payload", AsyncMock(return_value={}))
    db = AsyncMock(spec=AsyncSession)
    db.get.return_value = user
    return db, user


def test_identity_validation():
    assert AgentIdentitySettings().personas[0].instructions == ""
    assert api.DisplayNameRequest(displayName=" Ada ").display_name == "Ada"
    for value in (None, "", "Ada\nIgnore", "Ada\u202eIgnore"):
        with pytest.raises(ValidationError):
            api.DisplayNameRequest(displayName=value)
    for persona_type in (PersonaProfile, api.PersonaRequest):
        assert persona_type(name=" Name ", instructions=" Text ").model_dump(exclude={"id"}) == {"name": "Name", "instructions": "Text"}
        with pytest.raises(ValidationError):
            persona_type(name="Name", instructions="x" * 32_003)
        with pytest.raises(ValidationError):
            persona_type(name=" ", instructions="")


@pytest.mark.parametrize(("global_override", "personal_override", "allowed", "expected"), [
    (None, None, True, "Global"), ("Agent", None, True, "Agent"),
    ("Agent", "Mine", True, "Mine"), ("Agent", "Mine", False, "Agent"),
    ("Agent", "", True, ""), ("", None, True, ""),
])
def test_persona_precedence(global_override, personal_override, allowed, expected):
    default = PersonaProfile(name="Standard", instructions="Global")
    shared = PersonaProfile(name="Agent", instructions=global_override or "")
    own = PersonaProfile(name="Mine", instructions=personal_override or "")
    profile = AgentProfile(id=GENERAL_PROFILE_ID, name="Global agent", instructions="Task")
    instance = AgentIdentitySettings(personas=[default, shared], default_persona_id=default.id, personal_personas_enabled=allowed,
                                     profile_personas={} if global_override is None else {profile.id: shared.id})
    personal = UserAgentIdentitySettings(personas=[own], profile_personas={} if personal_override is None else {profile.id: own.id})
    assert resolve_agent_persona(instance, personal, profile).instructions == expected


@pytest.mark.parametrize(("scope", "own_target", "own_persona"), [
    ("instance", False, False), ("personal", False, False), ("personal", False, True),
    ("personal", True, False), ("personal", True, True),
])
async def test_persona_assignment_scopes(monkeypatch, scope, own_target, own_persona):
    instance = AgentIdentitySettings()
    persona = PersonaProfile(name="Mine", instructions="Own text")
    personal = UserAgentIdentitySettings(personas=[persona])
    profile = CustomAgentProfile(name="Own", description="", base_profile_id=GENERAL_PROFILE_ID, overrides={}) if own_target else None
    db, user = settings_db(monkeypatch, instance, personal, admin=scope == "instance", own_profile=profile)
    target = profile.id if profile else GENERAL_PROFILE_ID
    selected = persona.id if own_persona else instance.default_persona_id
    await api.update_persona_override(target, api.PersonaAssignmentRequest(personaId=selected), user, db, scope)
    assert (instance if scope == "instance" else personal).profile_personas == {target: selected}


@pytest.mark.parametrize(("scope", "target", "enabled", "code"), [
    ("instance", GENERAL_PROFILE_ID, True, "auth.adminRequired"),
    ("personal", uuid4(), True, "agentProfile.notFound"),
    ("personal", GENERAL_PROFILE_ID, False, "personaProfile.personalDisabled"),
])
async def test_override_scope_and_gate(monkeypatch, scope, target, enabled, code):
    instance = AgentIdentitySettings(personal_personas_enabled=enabled)
    db, user = settings_db(monkeypatch, instance, UserAgentIdentitySettings())
    with pytest.raises(ApiError, match=code):
        await api.update_persona_override(target, api.PersonaAssignmentRequest(personaId=instance.default_persona_id), user, db, scope)


async def test_persona_crud_and_deleted_assignment_fallback(monkeypatch):
    instance, personal = AgentIdentitySettings(), UserAgentIdentitySettings()
    db, user = settings_db(monkeypatch, instance, personal)
    await api.create_persona(api.PersonaRequest(name="Mine", instructions="Initial"), user, db)
    persona_id = personal.personas[0].id
    await api.update_persona(persona_id, api.PersonaRequest(name="Updated", instructions="New"), user, db)
    await api.update_persona_override(GENERAL_PROFILE_ID, api.PersonaAssignmentRequest(personaId=persona_id), user, db)
    assert personal.personas[0].instructions == "New"
    await api.delete_persona(persona_id, user, db)
    assert (personal.personas, personal.profile_personas) == ([], {})


async def test_default_and_foreign_personas_are_protected(monkeypatch):
    instance, personal = AgentIdentitySettings(), UserAgentIdentitySettings()
    db, user = settings_db(monkeypatch, instance, personal, admin=True)
    with pytest.raises(ApiError, match="personaProfile.defaultProtected"):
        await api.delete_persona(instance.default_persona_id, user, db, "instance")
    with pytest.raises(ApiError, match="personaProfile.notFound"):
        await api.update_persona(instance.default_persona_id, api.PersonaRequest(name="Bad", instructions="Bad"), user, db, "personal")
    with pytest.raises(ApiError, match="validation.invalidChoice"):
        await api.update_persona_override(GENERAL_PROFILE_ID, api.PersonaAssignmentRequest(personaId=uuid4()), user, db)


@pytest.mark.parametrize("admin", [False, True])
async def test_one_gate_controls_personas_assignments_and_name(monkeypatch, admin):
    instance = AgentIdentitySettings(personal_personas_enabled=False)
    persona = PersonaProfile(name="Mine", instructions="Mine")
    personal = UserAgentIdentitySettings(display_name="My name", personas=[persona], profile_personas={GENERAL_PROFILE_ID: persona.id})
    db, user = settings_db(monkeypatch, instance, personal, admin=admin)
    profile = AgentProfile(id=GENERAL_PROFILE_ID, name="Agent", instructions="Task")
    identity = await load_effective_agent_identity(db, user.id, profile)
    assert (identity.display_name, identity.persona.instructions) == (("My name", "Mine") if admin else ("MatoLoop", ""))
    if admin:
        await api.update_agent_identity_display_name(api.DisplayNameRequest(displayName="Ada"), user, db)
    else:
        with pytest.raises(ApiError, match="agentIdentity.personalDisplayNameDisabled"):
            await api.update_agent_identity_display_name(api.DisplayNameRequest(displayName="Ada"), user, db)
