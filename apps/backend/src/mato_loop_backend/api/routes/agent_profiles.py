from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.api.errors import ApiError
from mato_loop_backend.api.permission_settings import SettingsApiScope, _load_scoped
from mato_loop_backend.db.session import get_db_session
from mato_loop_backend.models import ChatSession, PermissionGrant, UserAccount
from mato_loop_backend.providers.reasoning import reasoning_profile
from mato_loop_backend.providers.settings_service import (
    DEFAULT_MODEL_FIELDS,
    available_model,
    model_reference_available,
)
from mato_loop_backend.security import require_user
from mato_loop_backend.settings import (
    AgentIdentitySettings,
    AgentProfile,
    AgentProfileOverrides,
    AgentProfilesSettings,
    AiSettings,
    CustomAgentProfile,
    IntegrationSettings,
    ModelCapability,
    SettingsStore,
    ToolSettings,
    UserAgentIdentitySettings,
    UserAgentProfilesSettings,
    load_effective_agent_profiles,
    load_effective_ai,
    load_effective_integrations,
    resolve_agent_persona,
    resolve_agent_profile,
    shipped_profiles,
)
from mato_loop_backend.settings.effective import personal_settings_allowed
from mato_loop_backend.settings.models import (
    AgentProfileAccessOverrides,
)

router = APIRouter(prefix="/agent-profiles", dependencies=[Depends(require_user)])


def _camel_case(value: str) -> str:
    first, *rest = value.split("_")
    return first + "".join(part.capitalize() for part in rest)


class AgentProfileRequest(BaseModel):
    model_config = ConfigDict(
        alias_generator=_camel_case, populate_by_name=True, extra="forbid"
    )

    name: str = Field(min_length=1, max_length=80)
    description: str = Field(max_length=500)
    base_profile_id: UUID
    overrides: AgentProfileOverrides

    @field_validator("name", "description", mode="before")
    @classmethod
    def strip_text(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


class EnabledRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool


class DefaultProfileRequest(BaseModel):
    model_config = ConfigDict(alias_generator=_camel_case, populate_by_name=True, extra="forbid")

    profile_id: UUID


def _profile_payload(
    profile: AgentProfile,
    scope: Literal["instance", "personal", "integration"],
    instance_identity: AgentIdentitySettings,
    personal_identity: UserAgentIdentitySettings | None,
    stored: CustomAgentProfile | None = None,
) -> dict:
    return {
        **profile.model_dump(mode="json", by_alias=True),
        "scope": scope,
        "protected": stored is None or not profile.editable,
        "editable": profile.editable,
        "baseProfileId": str(stored.base_profile_id) if stored is not None else None,
        **(
            {"overrides": stored.overrides.model_dump(mode="json", by_alias=True, exclude_unset=True)}
            if stored is not None
            else {}
        ),
        "resolvedPersonaName": resolve_agent_persona(instance_identity, personal_identity, profile).name,
    }


async def _response(
    db: AsyncSession,
    user: UserAccount,
    settings: AgentProfilesSettings | UserAgentProfilesSettings | None = None,
    scope: SettingsApiScope | None = None,
) -> dict:
    effective = await load_effective_agent_profiles(db, user.id, include_personal=scope != "instance")
    instance_profiles = await SettingsStore.load(db, AgentProfilesSettings)
    personal_profiles = await SettingsStore.load_optional(
        db, UserAgentProfilesSettings, user_id=user.id
    )
    stored = {
        profile.id: profile
        for profile in [
            *instance_profiles.profiles,
            *(personal_profiles.profiles if personal_profiles is not None else []),
        ]
    }
    builtins = {str(profile.id): profile for profile in shipped_profiles()}
    instance_identity = await SettingsStore.load(db, AgentIdentitySettings)
    personal_identity = await SettingsStore.load_optional(
        db, UserAgentIdentitySettings, user_id=user.id
    )
    if scope == "instance":
        personal_identity = None
    instance_identity = instance_identity.model_copy(deep=True)
    instance_identity.personal_personas_enabled = await personal_settings_allowed(db, user.id, instance_identity.personal_personas_enabled)
    integrations = (
        await SettingsStore.load(db, IntegrationSettings)
        if scope == "instance"
        else await load_effective_integrations(db, user.id)
    )
    tools = await SettingsStore.load(db, ToolSettings)
    effective_profiles = [
        _profile_payload(
            profile,
            "integration" if profile.integration_id is not None else (
                "personal" if profile.owner_id == user.id else "instance"
            ),
            instance_identity,
            personal_identity,
            stored.get(profile.id),
        )
        for profile in effective.profiles
    ]
    disabled_personal_profiles = []
    if scope == "personal" and not effective.personal_agents_enabled and not user.is_admin and personal_profiles is not None:
        base_ids = {profile.id for profile in shipped_profiles()}
        bases = {profile.id: profile for profile in effective.profiles if profile.id in base_ids}
        disabled_personal_profiles = [
            {
                **_profile_payload(
                    resolve_agent_profile(profile, bases[profile.base_profile_id], user.id),
                    "personal", instance_identity, personal_identity, profile,
                ),
                "effective": False,
            }
            for profile in personal_profiles.profiles
            if profile.base_profile_id in bases
        ]
    for profile in effective_profiles:
        builtin = builtins.get(profile["id"])
        if builtin is None:
            continue
        access = instance_profiles.builtin_access_overrides.get(builtin.id)
        profile.update(
            accessEditable=scope == "instance" and user.is_admin,
            accessBaseline=builtin.model_dump(mode="json", by_alias=True),
            overrides=access.model_dump(mode="json", by_alias=True) if access else {},
        )
    return {
        "defaultAgentProfileId": str(instance_profiles.default_profile_id),
        "personalAgentsEnabled": effective.personal_agents_enabled,
        "personalToolPermissionsEnabled": tools.personal_settings_enabled,
        "integrations": [
            {
                "id": str(instance.id),
                "name": instance.name,
                "integrationType": instance.integration_type,
                "adapterType": instance.adapter_type,
                "enabled": instance.enabled,
                "available": instance.enabled and integrations.availability_for(
                    instance.adapter_type
                ).value != "disabled",
                "scope": "personal" if instance.owner_id == user.id else "instance",
            }
            for instance in integrations.instances
        ],
        "effectiveAgents": [profile for profile in effective_profiles if profile["enabled"]],
        "baseProfiles": [profile for profile in effective_profiles if profile["id"] in builtins],
        "agents": disabled_personal_profiles if disabled_personal_profiles else (
            effective_profiles if settings is None or scope is None else [
                profile for profile in effective_profiles if profile["scope"] == scope
            ]
        ),
    }


async def _profile_from_request(
    db: AsyncSession,
    user: UserAccount,
    owner_id: UUID | None,
    payload: AgentProfileRequest,
    *,
    profile_id: UUID | None = None,
    existing: CustomAgentProfile | None = None,
) -> CustomAgentProfile:
    bases = {profile.id: profile for profile in shipped_profiles()}
    if payload.base_profile_id not in bases:
        raise ApiError(422, "validation.invalidChoice", field="baseProfileId")
    overrides = payload.overrides
    # Profile-content updates preserve permissions edited through the tool settings API.
    permission_fields = {"private_permissions", "public_permissions", "shell_prefixes", "integration_availability", "integrations_enabled_by_default"}
    if permission_fields & overrides.model_fields_set:
        raise ApiError(422, "tools.usePermissionSettings", field="overrides")
    values = overrides.model_dump(exclude_unset=True)
    if existing is not None:
        values.update(existing.overrides.model_dump(include=permission_fields, exclude_unset=True))
    overrides = AgentProfileOverrides.model_validate(values)
    ai = (
        await load_effective_ai(db, user.id)
        if owner_id is not None
        else await SettingsStore.load(db, AiSettings)
    )
    for field, (api_field, _capability) in DEFAULT_MODEL_FIELDS.items():
        if field not in overrides.model_fields_set:
            continue
        model_id = getattr(overrides, field)
        if model_id is None:
            continue
        selected = available_model(ai, model_id)
        if selected is None:
            raise ApiError(422, "provider.modelUnavailable", field=api_field)
        if model_reference_available(ai, field, model_id):
            continue
        if field in {
            "default_model_id",
            "skill_creation_model_id",
            "dreaming_model_id",
        } and not selected.supports_tools:
            raise ApiError(422, "session.modelToolsUnavailable", field=api_field)
        raise ApiError(422, "provider.modelCapabilityUnavailable", field=api_field)
    if "reasoning_effort" in overrides.model_fields_set and overrides.reasoning_effort is not None:
        model_id = (
            overrides.default_model_id
            if "default_model_id" in overrides.model_fields_set
            else bases[payload.base_profile_id].default_model_id
        )
        selected = available_model(ai, model_id, ModelCapability.CHAT)
        profile = reasoning_profile(selected.model_id, selected.discovery_metadata) if selected else None
        if profile is None or overrides.reasoning_effort not in profile.options:
            raise ApiError(422, "validation.invalidChoice", field="reasoningEffort")

    return CustomAgentProfile(
        id=profile_id or uuid4(),
        name=payload.name,
        description=payload.description,
        base_profile_id=payload.base_profile_id,
        overrides=overrides,
        enabled=existing.enabled if existing is not None else True,
    )


def _ensure_unique_name(
    settings: AgentProfilesSettings | UserAgentProfilesSettings,
    name: str,
    *,
    exclude_id: UUID | None = None,
) -> None:
    folded = name.strip().casefold()
    reserved_id = next(
        (
            profile.id
            for profile in shipped_profiles()
            if profile.name.casefold() == folded
        ),
        None,
    )
    if reserved_id is not None and exclude_id != reserved_id:
        raise ApiError(409, "agentProfile.defaultProtected")
    if any(
        profile.id != exclude_id and profile.name.casefold() == folded
        for profile in settings.profiles
    ):
        raise ApiError(409, "agentProfile.duplicate")


@router.get("")
async def list_agent_profiles(
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
    scope: SettingsApiScope | None = None,
) -> dict:
    if scope is None:
        return await _response(db, user)
    if scope == "personal":
        settings = await SettingsStore.load(
            db, UserAgentProfilesSettings, user_id=user.id
        )
    else:
        settings, _owner_id = await _load_scoped(db, scope, user)
    return await _response(db, user, settings, scope)


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_agent_profile(
    payload: AgentProfileRequest,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
    scope: SettingsApiScope = "personal",
) -> dict:
    settings, owner_id = await _load_scoped(db, scope, user, for_update=True)
    profile = await _profile_from_request(db, user, owner_id, payload)
    _ensure_unique_name(settings, profile.name)
    limit = 20 if owner_id is not None else 99
    if len(settings.profiles) >= limit:
        raise ApiError(409, "agentProfile.limitReached")
    settings.profiles.append(profile)
    await SettingsStore.save(db, settings, user_id=owner_id)
    await db.commit()
    result = await _response(db, user, settings, scope)
    result["agentProfileId"] = str(profile.id)
    return result


@router.put("/{profile_id}")
async def update_agent_profile(
    profile_id: UUID,
    payload: AgentProfileRequest,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
    scope: SettingsApiScope = "personal",
) -> dict:
    settings, owner_id = await _load_scoped(db, scope, user, for_update=True)
    if profile_id in {profile.id for profile in shipped_profiles()}:
        raise ApiError(409, "agentProfile.defaultProtected")
    index = next(
        (
            index
            for index, profile in enumerate(settings.profiles)
            if profile.id == profile_id
        ),
        None,
    )
    if index is None:
        raise ApiError(404, "agentProfile.notFound")
    replacement = await _profile_from_request(
        db,
        user,
        owner_id,
        payload,
        profile_id=profile_id,
        existing=settings.profiles[index],
    )
    _ensure_unique_name(settings, replacement.name, exclude_id=profile_id)
    settings.profiles[index] = replacement
    await SettingsStore.save(db, settings, user_id=owner_id)
    await db.commit()
    return await _response(db, user, settings, scope)


@router.patch("/{profile_id}/enabled")
async def set_agent_profile_enabled(
    profile_id: UUID,
    payload: EnabledRequest,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
    scope: SettingsApiScope = "personal",
) -> dict:
    settings, owner_id = await _load_scoped(db, scope, user, for_update=True)
    if isinstance(settings, AgentProfilesSettings) and profile_id == settings.default_profile_id and not payload.enabled:
        raise ApiError(409, "agentProfile.defaultMustBeActive")
    if profile_id in {profile.id for profile in shipped_profiles()}:
        if not isinstance(settings, AgentProfilesSettings):
            raise ApiError(409, "agentProfile.defaultProtected")
        settings.builtin_enabled[profile_id] = payload.enabled
        await SettingsStore.save(db, settings)
        await db.commit()
        return await _response(db, user, settings, scope)
    profile = next((item for item in settings.profiles if item.id == profile_id), None)
    if profile is None:
        raise ApiError(404, "agentProfile.notFound")
    profile.enabled = payload.enabled
    await SettingsStore.save(db, settings, user_id=owner_id)
    await db.commit()
    return await _response(db, user, settings, scope)


@router.put("/{profile_id}/access")
async def update_builtin_access(
    profile_id: UUID,
    payload: AgentProfileAccessOverrides,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
    scope: SettingsApiScope = "instance",
) -> dict:
    if scope != "instance":
        raise ApiError(409, "agentProfile.defaultProtected")
    settings, owner_id = await _load_scoped(db, scope, user, for_update=True)
    base = next((profile for profile in shipped_profiles() if profile.id == profile_id), None)
    if base is None:
        raise ApiError(404, "agentProfile.notFound")
    existing = CustomAgentProfile(
        id=base.id, name=base.name, description=base.description, base_profile_id=base.id,
        overrides=AgentProfileOverrides.model_validate(
            settings.builtin_access_overrides.get(profile_id, AgentProfileAccessOverrides()).model_dump()
        ),
    )
    permission_fields = {"private_permissions", "public_permissions", "shell_prefixes", "integration_availability", "integrations_enabled_by_default"}
    if permission_fields & payload.model_fields_set:
        raise ApiError(422, "tools.usePermissionSettings")
    values = payload.model_dump()
    values.update(existing.overrides.model_dump(include=permission_fields, exclude_unset=True))
    await _profile_from_request(
        db, user, owner_id,
        AgentProfileRequest(name=base.name, description=base.description, base_profile_id=base.id,
                            overrides=AgentProfileOverrides.model_validate(payload.model_dump())),
        existing=existing,
    )
    payload = AgentProfileAccessOverrides.model_validate(values)
    if payload.model_fields_set:
        settings.builtin_access_overrides[profile_id] = payload
    else:
        settings.builtin_access_overrides.pop(profile_id, None)
    await SettingsStore.save(db, settings, user_id=owner_id)
    await db.commit()
    return await _response(db, user, settings, scope)


@router.delete("/{profile_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_agent_profile(
    profile_id: UUID,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
    scope: SettingsApiScope = "personal",
) -> None:
    settings, owner_id = await _load_scoped(db, scope, user, for_update=True)
    if isinstance(settings, AgentProfilesSettings) and profile_id == settings.default_profile_id:
        raise ApiError(409, "agentProfile.defaultMustBeActive")
    if profile_id in {profile.id for profile in shipped_profiles()}:
        raise ApiError(409, "agentProfile.defaultProtected")
    profile = next((item for item in settings.profiles if item.id == profile_id), None)
    if profile is None:
        raise ApiError(404, "agentProfile.notFound")
    sessions = list(
        (
            await db.scalars(
                select(ChatSession)
                .where(
                    ChatSession.agent_profile_id == profile_id,
                    *([ChatSession.user_id == user.id] if owner_id is not None else []),
                )
                .with_for_update()
            )
        ).all()
    )
    if sessions:
        session_ids = [session.id for session in sessions]
        await db.execute(
            delete(PermissionGrant).where(PermissionGrant.session_id.in_(session_ids))
        )
        for session in sessions:
            session.agent_profile_id = (await SettingsStore.load(db, AgentProfilesSettings)).default_profile_id
    settings.profiles.remove(profile)
    await SettingsStore.save(db, settings, user_id=owner_id)
    await db.commit()


@router.patch("/personal-enabled")
async def set_personal_agents_enabled(
    payload: EnabledRequest,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict:
    settings, _owner_id = await _load_scoped(
        db, "instance", user, for_update=True
    )
    assert isinstance(settings, AgentProfilesSettings)
    settings.personal_agents_enabled = payload.enabled
    await SettingsStore.save(db, settings)
    await db.commit()
    return await _response(db, user, settings, "instance")


@router.patch("/default")
async def set_default_agent_profile(
    payload: DefaultProfileRequest,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict:
    settings, _ = await _load_scoped(db, "instance", user, for_update=True)
    profiles = await load_effective_agent_profiles(db, user.id, include_personal=False, include_integrations=False)
    if not any(profile.id == payload.profile_id and profile.enabled for profile in profiles.profiles):
        raise ApiError(409, "agentProfile.defaultMustBeActive")
    settings.default_profile_id = payload.profile_id
    await SettingsStore.save(db, settings)
    await db.commit()
    return await _response(db, user, settings, "instance")
