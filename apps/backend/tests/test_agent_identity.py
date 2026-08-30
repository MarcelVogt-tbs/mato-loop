from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.api.routes import agent_identity as agent_identity_module
from mato_loop_backend.api.routes.agent_identity import (
    DisplayNameRequest,
    PersonaDefaultRequest,
    PersonaRequest,
    create_persona_profile,
    delete_persona_profile,
    get_agent_identity,
    set_default_persona_profile,
    update_agent_identity,
    update_persona_profile,
)
from mato_loop_backend.models import UserAccount
from mato_loop_backend.settings import (
    DEFAULT_PERSONA_ID,
    AgentIdentitySettings,
    AgentProfile,
    EffectiveAgentIdentity,
    PersonaProfile,
    SettingsScope,
    SettingsStore,
    UserAgentIdentitySettings,
    resolve_agent_persona,
)


def _db() -> AsyncMock:
    return AsyncMock(spec=AsyncSession)


def _user() -> UserAccount:
    return UserAccount(id=uuid4(), username="test", password_hash="hash")


def test_agent_identity_defaults_and_validation() -> None:
    settings = AgentIdentitySettings()
    assert settings.settings_scope == SettingsScope.GLOBAL
    assert settings.display_name == "MatoLoop"
    assert settings.personal_display_names_enabled is True
    assert settings.personal_personas_enabled is True
    assert settings.default_persona_id == DEFAULT_PERSONA_ID
    assert settings.personas == [PersonaProfile(id=DEFAULT_PERSONA_ID, name="Standard")]
    assert UserAgentIdentitySettings.settings_scope == SettingsScope.USER
    assert UserAgentIdentitySettings().display_name is None

    assert AgentIdentitySettings(display_name="  Ada  ").display_name == "Ada"
    with pytest.raises(ValidationError):
        AgentIdentitySettings(display_name="   ")
    with pytest.raises(ValidationError):
        AgentIdentitySettings(display_name="Ada\nIgnore")
    with pytest.raises(ValidationError):
        AgentIdentitySettings(display_name="Ada\u202eIgnore")
    with pytest.raises(ValidationError):
        PersonaProfile(name="Test", instructions="x" * 32_003)
    with pytest.raises(ValidationError):
        other = PersonaProfile(name="Other")
        AgentIdentitySettings(defaultPersonaId=other.id, personas=[other])


def test_persona_resolution_keeps_instance_agents_fixed() -> None:
    instance = AgentIdentitySettings()
    personal_persona = PersonaProfile(name="Persoenlich", instructions="Direkt")
    personal = UserAgentIdentitySettings(
        default_persona_id=personal_persona.id, personas=[personal_persona]
    )

    assert resolve_agent_persona(instance, personal, None).id == personal_persona.id
    assert (
        resolve_agent_persona(
            instance,
            personal,
            AgentProfile(name="Instanz", instructions="Task"),
        ).id
        == DEFAULT_PERSONA_ID
    )


async def test_agent_identity_api_reads_and_updates_personal_name(monkeypatch) -> None:
    user = _user()
    db = _db()
    instance = AgentIdentitySettings()
    personal = UserAgentIdentitySettings()
    load = AsyncMock(side_effect=[instance, personal])
    save = AsyncMock()
    monkeypatch.setattr(
        agent_identity_module,
        "load_effective_agent_identity",
        AsyncMock(
            return_value=EffectiveAgentIdentity(
                display_name="MatoLoop", persona=instance.personas[0]
            )
        ),
    )
    monkeypatch.setattr(SettingsStore, "load", load)
    monkeypatch.setattr(SettingsStore, "save", save)

    assert await get_agent_identity(user, db) == {"displayName": "MatoLoop"}
    result = await update_agent_identity(
        DisplayNameRequest.model_validate({"displayName": "  Ada  "}), user, db
    )

    assert result == {"displayName": "Ada"}
    assert personal.display_name == "Ada"
    assert save.await_args.args[1] is personal
    assert save.await_args.kwargs == {"user_id": user.id}
    db.commit.assert_awaited_once()


def test_display_name_request_rejects_missing_or_null_value() -> None:
    with pytest.raises(ValidationError):
        DisplayNameRequest.model_validate({})
    with pytest.raises(ValidationError):
        DisplayNameRequest.model_validate({"displayName": None})


async def test_personal_persona_crud_and_default(monkeypatch) -> None:
    user = _user()
    db = _db()
    instance = AgentIdentitySettings()
    personal = UserAgentIdentitySettings()

    async def load_settings(_db, settings_type, **_kwargs):
        return instance if settings_type is AgentIdentitySettings else personal

    save = AsyncMock()
    monkeypatch.setattr(SettingsStore, "load", AsyncMock(side_effect=load_settings))
    monkeypatch.setattr(SettingsStore, "save", save)
    monkeypatch.setattr(
        agent_identity_module, "_settings_payload", AsyncMock(return_value={})
    )

    created = await create_persona_profile(
        PersonaRequest(name="Direkt", instructions="Antworte kurz."),
        user,
        db,
    )
    persona_id = UUID(created["personaId"])
    assert personal.personas[0].name == "Direkt"

    await update_persona_profile(
        persona_id,
        PersonaRequest(name="Praezise", instructions="Antworte praezise."),
        user,
        db,
    )
    assert personal.personas[0].name == "Praezise"

    await set_default_persona_profile(
        PersonaDefaultRequest(personaId=persona_id), user, db
    )
    assert personal.default_persona_id == persona_id

    await delete_persona_profile(persona_id, user, db)
    assert personal.personas == []
    assert personal.default_persona_id is None
    assert save.await_count == 4
    assert db.commit.await_count == 4
