from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.api.errors import ApiError
from mato_loop_backend.db.session import get_db_session
from mato_loop_backend.models import UserAccount
from mato_loop_backend.security import require_user
from mato_loop_backend.settings import (
    DEFAULT_PERSONA_ID,
    AgentIdentitySettings,
    PersonaProfile,
    SettingsStore,
    UserAgentIdentitySettings,
    load_effective_agent_identity,
)
from mato_loop_backend.settings.models import normalize_agent_display_name

router = APIRouter(prefix="/agent-identity", dependencies=[Depends(require_user)])
SettingsApiScope = Literal["instance", "personal"]


def _camel_case(value: str) -> str:
    first, *rest = value.split("_")
    return first + "".join(part.capitalize() for part in rest)


class DisplayNameRequest(BaseModel):
    model_config = ConfigDict(
        alias_generator=_camel_case, populate_by_name=True, extra="forbid"
    )

    display_name: str = Field(min_length=1, max_length=80)

    @field_validator("display_name", mode="before")
    @classmethod
    def validate_display_name(cls, value: object) -> object:
        return normalize_agent_display_name(value) if isinstance(value, str) else value


class PersonaRequest(BaseModel):
    model_config = ConfigDict(
        alias_generator=_camel_case, populate_by_name=True, extra="forbid"
    )

    name: str = Field(min_length=1, max_length=80)
    instructions: str = Field(min_length=1, max_length=16_000)

    @field_validator("name", "instructions", mode="before")
    @classmethod
    def strip_text(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


class PersonaDefaultRequest(BaseModel):
    model_config = ConfigDict(
        alias_generator=_camel_case, populate_by_name=True, extra="forbid"
    )

    persona_id: UUID | None = None


class EnabledRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool


async def _load_scoped(
    db: AsyncSession,
    scope: SettingsApiScope,
    user: UserAccount,
    *,
    for_update: bool = False,
) -> tuple[AgentIdentitySettings | UserAgentIdentitySettings, UUID | None]:
    if scope == "instance":
        if not user.is_admin:
            raise ApiError(403, "auth.adminRequired")
        return (
            await SettingsStore.load(
                db, AgentIdentitySettings, for_update=for_update
            ),
            None,
        )
    return (
        await SettingsStore.load(
            db,
            UserAgentIdentitySettings,
            user_id=user.id,
            for_update=for_update,
        ),
        user.id,
    )


def _persona_payload(
    persona: PersonaProfile, scope: SettingsApiScope, *, effective: bool = True
) -> dict:
    return {
        **persona.model_dump(mode="json", by_alias=True),
        "scope": scope,
        "effective": effective,
        "protected": persona.id == DEFAULT_PERSONA_ID,
    }


def _effective_personal_default(
    instance: AgentIdentitySettings,
    personal: UserAgentIdentitySettings,
) -> UUID:
    if instance.personal_personas_enabled and personal.default_persona_id is not None:
        valid_ids = {persona.id for persona in instance.personas}
        valid_ids.update(persona.id for persona in personal.personas)
        if personal.default_persona_id in valid_ids:
            return personal.default_persona_id
    return instance.default_persona_id


async def _settings_payload(
    db: AsyncSession,
    user: UserAccount,
    scope: SettingsApiScope,
    scoped: AgentIdentitySettings | UserAgentIdentitySettings | None = None,
) -> dict:
    instance = await SettingsStore.load(db, AgentIdentitySettings)
    personal = await SettingsStore.load_optional(
        db, UserAgentIdentitySettings, user_id=user.id
    )
    effective = await load_effective_agent_identity(db, user.id)
    if scoped is None:
        scoped = instance if scope == "instance" else (personal or UserAgentIdentitySettings())

    personas = [
        _persona_payload(persona, "instance") for persona in instance.personas
    ]
    if scope == "personal" and personal is not None:
        personas.extend(
            _persona_payload(
                persona,
                "personal",
                effective=instance.personal_personas_enabled,
            )
            for persona in personal.personas
        )

    return {
        "scope": scope,
        "configuredDisplayName": scoped.display_name,
        "effectiveDisplayName": effective.display_name,
        "personalDisplayNamesEnabled": instance.personal_display_names_enabled,
        "personalPersonasEnabled": instance.personal_personas_enabled,
        "defaultPersonaId": (
            str(scoped.default_persona_id)
            if scoped.default_persona_id is not None
            else None
        ),
        "effectiveDefaultPersonaId": str(
            instance.default_persona_id
            if scope == "instance" or personal is None
            else _effective_personal_default(instance, personal)
        ),
        "personas": personas,
    }


def _ensure_personas_enabled(instance: AgentIdentitySettings) -> None:
    if not instance.personal_personas_enabled:
        raise ApiError(409, "personaProfile.personalDisabled")


def _ensure_unique_name(
    settings: AgentIdentitySettings | UserAgentIdentitySettings,
    name: str,
    *,
    exclude_id: UUID | None = None,
) -> None:
    folded = name.casefold()
    if any(
        persona.id != exclude_id and persona.name.casefold() == folded
        for persona in settings.personas
    ):
        raise ApiError(409, "personaProfile.duplicate")


@router.get("")
async def get_agent_identity(
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict[str, str]:
    identity = await load_effective_agent_identity(db, user.id)
    return {"displayName": identity.display_name}


@router.patch("")
async def update_agent_identity(
    payload: DisplayNameRequest,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict[str, str]:
    instance = await SettingsStore.load(
        db, AgentIdentitySettings, for_update=True
    )
    if not instance.personal_display_names_enabled:
        raise ApiError(409, "agentIdentity.personalDisplayNameDisabled")
    personal = await SettingsStore.load(
        db, UserAgentIdentitySettings, user_id=user.id, for_update=True
    )
    personal.display_name = payload.display_name
    await SettingsStore.save(db, personal, user_id=user.id)
    await db.commit()
    return {"displayName": payload.display_name}


@router.get("/settings")
async def get_agent_identity_settings(
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
    scope: SettingsApiScope = "personal",
) -> dict:
    scoped, _owner_id = await _load_scoped(db, scope, user)
    return await _settings_payload(db, user, scope, scoped)


@router.patch("/settings/display-name")
async def update_agent_identity_display_name(
    payload: DisplayNameRequest,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
    scope: SettingsApiScope = "personal",
) -> dict:
    instance = await SettingsStore.load(
        db, AgentIdentitySettings, for_update=scope == "personal"
    )
    if scope == "personal" and not instance.personal_display_names_enabled:
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
    if not instance.personal_display_names_enabled:
        raise ApiError(409, "agentIdentity.personalDisplayNameDisabled")
    settings = await SettingsStore.load(
        db,
        UserAgentIdentitySettings,
        user_id=user.id,
        for_update=True,
    )
    settings.display_name = None
    await SettingsStore.save(db, settings, user_id=user.id)
    await db.commit()
    return await _settings_payload(db, user, "personal", settings)


@router.patch("/settings/personal-display-names")
async def set_personal_display_names_enabled(
    payload: EnabledRequest,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict:
    settings, _owner_id = await _load_scoped(
        db, "instance", user, for_update=True
    )
    assert isinstance(settings, AgentIdentitySettings)
    settings.personal_display_names_enabled = payload.enabled
    await SettingsStore.save(db, settings)
    await db.commit()
    return await _settings_payload(db, user, "instance", settings)


@router.patch("/settings/personal-personas")
async def set_personal_personas_enabled(
    payload: EnabledRequest,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict:
    settings, _owner_id = await _load_scoped(
        db, "instance", user, for_update=True
    )
    assert isinstance(settings, AgentIdentitySettings)
    settings.personal_personas_enabled = payload.enabled
    await SettingsStore.save(db, settings)
    await db.commit()
    return await _settings_payload(db, user, "instance", settings)


@router.post("/personas", status_code=status.HTTP_201_CREATED)
async def create_persona_profile(
    payload: PersonaRequest,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
    scope: SettingsApiScope = "personal",
) -> dict:
    instance = await SettingsStore.load(
        db, AgentIdentitySettings, for_update=scope == "personal"
    )
    if scope == "personal":
        _ensure_personas_enabled(instance)
    settings, owner_id = await _load_scoped(db, scope, user, for_update=True)
    _ensure_unique_name(settings, payload.name)
    limit = 20 if owner_id is not None else 100
    if len(settings.personas) >= limit:
        raise ApiError(409, "personaProfile.limitReached")
    persona = PersonaProfile(id=uuid4(), **payload.model_dump())
    settings.personas.append(persona)
    await SettingsStore.save(db, settings, user_id=owner_id)
    await db.commit()
    result = await _settings_payload(db, user, scope, settings)
    result["personaId"] = str(persona.id)
    return result


@router.put("/personas/{persona_id}")
async def update_persona_profile(
    persona_id: UUID,
    payload: PersonaRequest,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
    scope: SettingsApiScope = "personal",
) -> dict:
    instance = await SettingsStore.load(
        db, AgentIdentitySettings, for_update=scope == "personal"
    )
    if scope == "personal":
        _ensure_personas_enabled(instance)
    settings, owner_id = await _load_scoped(db, scope, user, for_update=True)
    index = next(
        (index for index, item in enumerate(settings.personas) if item.id == persona_id),
        None,
    )
    if index is None:
        raise ApiError(404, "personaProfile.notFound")
    if persona_id == DEFAULT_PERSONA_ID:
        raise ApiError(409, "personaProfile.defaultProtected")
    _ensure_unique_name(settings, payload.name, exclude_id=persona_id)
    settings.personas[index] = PersonaProfile(id=persona_id, **payload.model_dump())
    await SettingsStore.save(db, settings, user_id=owner_id)
    await db.commit()
    return await _settings_payload(db, user, scope, settings)


@router.delete("/personas/{persona_id}")
async def delete_persona_profile(
    persona_id: UUID,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
    scope: SettingsApiScope = "personal",
) -> dict:
    instance = await SettingsStore.load(
        db, AgentIdentitySettings, for_update=scope == "personal"
    )
    if scope == "personal":
        _ensure_personas_enabled(instance)
    settings, owner_id = await _load_scoped(db, scope, user, for_update=True)
    persona = next((item for item in settings.personas if item.id == persona_id), None)
    if persona is None:
        raise ApiError(404, "personaProfile.notFound")
    if isinstance(settings, AgentIdentitySettings) and (
        persona_id == DEFAULT_PERSONA_ID or settings.default_persona_id == persona_id
    ):
        raise ApiError(409, "personaProfile.defaultProtected")
    settings.personas.remove(persona)
    if (
        isinstance(settings, UserAgentIdentitySettings)
        and settings.default_persona_id == persona_id
    ):
        settings.default_persona_id = None
    await SettingsStore.save(db, settings, user_id=owner_id)
    await db.commit()
    return await _settings_payload(db, user, scope, settings)


@router.patch("/personas/default")
async def set_default_persona_profile(
    payload: PersonaDefaultRequest,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
    scope: SettingsApiScope = "personal",
) -> dict:
    instance = await SettingsStore.load(
        db, AgentIdentitySettings, for_update=scope == "personal"
    )
    if scope == "personal":
        _ensure_personas_enabled(instance)
    settings, owner_id = await _load_scoped(db, scope, user, for_update=True)
    if isinstance(settings, AgentIdentitySettings):
        if payload.persona_id is None or not any(
            item.id == payload.persona_id for item in settings.personas
        ):
            raise ApiError(422, "validation.invalidChoice", field="personaId")
        settings.default_persona_id = payload.persona_id
    else:
        valid_ids = {item.id for item in instance.personas}
        valid_ids.update(item.id for item in settings.personas)
        if payload.persona_id is not None and payload.persona_id not in valid_ids:
            raise ApiError(422, "validation.invalidChoice", field="personaId")
        settings.default_persona_id = payload.persona_id
    await SettingsStore.save(db, settings, user_id=owner_id)
    await db.commit()
    return await _settings_payload(db, user, scope, settings)
