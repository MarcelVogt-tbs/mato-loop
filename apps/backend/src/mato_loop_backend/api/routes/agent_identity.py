from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.api.errors import ApiError
from mato_loop_backend.db.session import get_db_session
from mato_loop_backend.models import UserAccount
from mato_loop_backend.security import require_user
from mato_loop_backend.settings import (
    AgentIdentitySettings,
    PersonaProfile,
    SettingsStore,
    UserAgentIdentitySettings,
    load_effective_agent_identity,
    load_effective_agent_profiles,
    resolve_agent_persona,
)
from mato_loop_backend.settings.models import normalize_agent_display_name

router = APIRouter(prefix="/agent-identity", dependencies=[Depends(require_user)])
SettingsApiScope = Literal["instance", "personal"]


class DisplayNameRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")
    display_name: str = Field(alias="displayName", min_length=1, max_length=80)

    @field_validator("display_name", mode="before")
    @classmethod
    def validate_display_name(cls, value: object) -> object:
        return normalize_agent_display_name(value) if isinstance(value, str) else value


class PersonaRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=80)
    instructions: str = Field(max_length=32_002)


class PersonaAssignmentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    persona_id: UUID = Field(alias="personaId")


class EnabledRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool


async def _load_scoped(
    db: AsyncSession, scope: SettingsApiScope, user: UserAccount, *, for_update: bool = False,
) -> tuple[AgentIdentitySettings | UserAgentIdentitySettings, UUID | None]:
    if scope == "instance":
        if not user.is_admin:
            raise ApiError(403, "auth.adminRequired")
        return await SettingsStore.load(db, AgentIdentitySettings, for_update=for_update), None
    return await SettingsStore.load(db, UserAgentIdentitySettings, user_id=user.id, for_update=for_update), user.id


async def _settings_payload(db, user, scope, scoped) -> dict:
    instance = await SettingsStore.load(db, AgentIdentitySettings)
    effective = await load_effective_agent_identity(db, user.id)
    profiles = await load_effective_agent_profiles(db, user.id, include_personal=scope == "personal", include_integrations=scope == "personal")
    return {
        "scope": scope,
        "configuredDisplayName": scoped.display_name,
        "effectiveDisplayName": effective.display_name,
        "personalPersonasEnabled": instance.personal_personas_enabled,
        "defaultPersonaId": str(instance.default_persona_id),
        "personas": [
            {**persona.model_dump(mode="json"), "scope": owner_scope}
            for owner_scope, personas in [("instance", instance.personas), ("personal", scoped.personas if scope == "personal" else [])]
            for persona in personas
        ],
        "profilePersonas": {str(key): str(value) for key, value in scoped.profile_personas.items()},
        "profiles": [{
            "id": str(profile.id), "name": profile.name,
            "scope": "personal" if profile.owner_id == user.id else "instance",
            "inheritedPersonaId": str(resolve_agent_persona(instance, None, profile).id) if scope == "personal" else str(instance.default_persona_id),
        } for profile in profiles.profiles],
    }


@router.get("")
async def get_agent_identity(
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict[str, str]:
    identity = await load_effective_agent_identity(db, user.id)
    return {"displayName": identity.display_name}


@router.get("/settings")
async def get_agent_identity_settings(
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
    scope: SettingsApiScope = "personal",
) -> dict:
    scoped, _ = await _load_scoped(db, scope, user)
    return await _settings_payload(db, user, scope, scoped)


@router.patch("/settings/display-name")
async def update_agent_identity_display_name(
    payload: DisplayNameRequest,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
    scope: SettingsApiScope = "personal",
) -> dict:
    instance = await SettingsStore.load(db, AgentIdentitySettings, for_update=scope == "personal")
    if scope == "personal" and not instance.personal_personas_enabled and not user.is_admin:
        raise ApiError(409, "agentIdentity.personalDisplayNameDisabled")
    settings, owner_id = await _load_scoped(db, scope, user, for_update=True)
    settings.display_name = payload.display_name
    await SettingsStore.save(db, settings, user_id=owner_id)
    await db.commit()
    return await _settings_payload(db, user, scope, settings)


@router.delete("/settings/display-name")
async def reset_personal_agent_identity_display_name(
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict:
    instance = await SettingsStore.load(db, AgentIdentitySettings, for_update=True)
    if not instance.personal_personas_enabled and not user.is_admin:
        raise ApiError(409, "agentIdentity.personalDisplayNameDisabled")
    settings, owner_id = await _load_scoped(db, "personal", user, for_update=True)
    settings.display_name = None
    await SettingsStore.save(db, settings, user_id=owner_id)
    await db.commit()
    return await _settings_payload(db, user, "personal", settings)


@router.patch("/settings/personal-personas")
async def set_personal_personas_enabled(
    payload: EnabledRequest,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict:
    settings, _ = await _load_scoped(db, "instance", user, for_update=True)
    settings.personal_personas_enabled = payload.enabled
    await SettingsStore.save(db, settings)
    await db.commit()
    return await _settings_payload(db, user, "instance", settings)


@router.patch("/personas/default")
async def set_default_persona(
    payload: PersonaAssignmentRequest,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict:
    settings, _ = await _load_scoped(db, "instance", user, for_update=True)
    if not any(persona.id == payload.persona_id for persona in settings.personas):
        raise ApiError(422, "validation.invalidChoice", field="personaId")
    settings.default_persona_id = payload.persona_id
    await SettingsStore.save(db, settings)
    await db.commit()
    return await _settings_payload(db, user, "instance", settings)


async def _load_override_settings(db, user, scope):
    instance = await SettingsStore.load(db, AgentIdentitySettings, for_update=True)
    if scope == "personal" and not instance.personal_personas_enabled and not user.is_admin:
        raise ApiError(409, "personaProfile.personalDisabled")
    return await _load_scoped(db, scope, user, for_update=True)


def _ensure_unique_name(settings, name, exclude_id=None):
    if any(persona.id != exclude_id and persona.name.casefold() == name.casefold() for persona in settings.personas):
        raise ApiError(409, "personaProfile.duplicate")


@router.post("/personas", status_code=201)
async def create_persona(
    payload: PersonaRequest,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
    scope: SettingsApiScope = "personal",
) -> dict:
    settings, owner_id = await _load_override_settings(db, user, scope)
    _ensure_unique_name(settings, payload.name)
    settings.personas.append(PersonaProfile(id=uuid4(), **payload.model_dump()))
    await SettingsStore.save(db, settings, user_id=owner_id)
    await db.commit()
    return await _settings_payload(db, user, scope, settings)


@router.put("/personas/{persona_id}")
async def update_persona(
    persona_id: UUID,
    payload: PersonaRequest,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
    scope: SettingsApiScope = "personal",
) -> dict:
    settings, owner_id = await _load_override_settings(db, user, scope)
    index = next((index for index, persona in enumerate(settings.personas) if persona.id == persona_id), None)
    if index is None:
        raise ApiError(404, "personaProfile.notFound")
    _ensure_unique_name(settings, payload.name, persona_id)
    settings.personas[index] = PersonaProfile(id=persona_id, **payload.model_dump())
    await SettingsStore.save(db, settings, user_id=owner_id)
    await db.commit()
    return await _settings_payload(db, user, scope, settings)


@router.delete("/personas/{persona_id}")
async def delete_persona(
    persona_id: UUID,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
    scope: SettingsApiScope = "personal",
) -> dict:
    settings, owner_id = await _load_override_settings(db, user, scope)
    if isinstance(settings, AgentIdentitySettings) and settings.default_persona_id == persona_id:
        raise ApiError(409, "personaProfile.defaultProtected")
    if not any(persona.id == persona_id for persona in settings.personas):
        raise ApiError(404, "personaProfile.notFound")
    settings.personas = [persona for persona in settings.personas if persona.id != persona_id]
    settings.profile_personas = {key: value for key, value in settings.profile_personas.items() if value != persona_id}
    await SettingsStore.save(db, settings, user_id=owner_id)
    await db.commit()
    return await _settings_payload(db, user, scope, settings)


@router.put("/persona-overrides/{profile_id}")
async def update_persona_override(
    profile_id: UUID,
    payload: PersonaAssignmentRequest,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
    scope: SettingsApiScope = "personal",
) -> dict:
    settings, owner_id = await _load_override_settings(db, user, scope)
    profiles = await load_effective_agent_profiles(db, user.id, include_personal=scope == "personal", include_integrations=scope == "personal")
    if not any(profile.id == profile_id for profile in profiles.profiles):
        raise ApiError(404, "agentProfile.notFound")
    global_settings = await SettingsStore.load(db, AgentIdentitySettings)
    available = [*global_settings.personas, *(settings.personas if scope == "personal" else [])]
    if not any(persona.id == payload.persona_id for persona in available):
        raise ApiError(422, "validation.invalidChoice", field="personaId")
    settings.profile_personas[profile_id] = payload.persona_id
    await SettingsStore.save(db, settings, user_id=owner_id)
    await db.commit()
    return await _settings_payload(db, user, scope, settings)


@router.delete("/persona-overrides/{profile_id}")
async def delete_persona_override(
    profile_id: UUID,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
    scope: SettingsApiScope = "personal",
) -> dict:
    settings, owner_id = await _load_override_settings(db, user, scope)
    settings.profile_personas.pop(profile_id, None)
    await SettingsStore.save(db, settings, user_id=owner_id)
    await db.commit()
    return await _settings_payload(db, user, scope, settings)
