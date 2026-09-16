import re
from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.agent.tools import tool_registry
from mato_loop_backend.api.errors import ApiError
from mato_loop_backend.api.routes.integrations import (
    _access_payload,
    _integration_resources,
)
from mato_loop_backend.db.session import get_db_session
from mato_loop_backend.integrations.registry import integration_adapter
from mato_loop_backend.models import ChatSession, PermissionGrant, UserAccount
from mato_loop_backend.security import require_user
from mato_loop_backend.settings import (
    BUILTIN_AGENT_PROFILE_IDS,
    BUILTIN_AGENT_PROFILE_NAMES,
    DEFAULT_AGENT_PROFILE_ID,
    AgentIdentitySettings,
    AgentProfile,
    AgentProfilePermissions,
    AgentProfilesSettings,
    IntegrationSettings,
    SettingsStore,
    ToolExecutionPolicy,
    ToolSettings,
    TrustLevel,
    UserAgentIdentitySettings,
    UserAgentProfilesSettings,
    load_effective_agent_profiles,
    load_effective_integrations,
    load_effective_tools,
    resolve_agent_persona,
)
from mato_loop_backend.settings.models import (
    ProfileToolCallLimit,
    validate_integration_availability,
)

router = APIRouter(prefix="/agent-profiles", dependencies=[Depends(require_user)])
SettingsApiScope = Literal["instance", "personal"]
_RESOURCE_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,499}$")


def _camel_case(value: str) -> str:
    first, *rest = value.split("_")
    return first + "".join(part.capitalize() for part in rest)


class AgentProfileRequest(BaseModel):
    model_config = ConfigDict(
        alias_generator=_camel_case, populate_by_name=True, extra="forbid"
    )

    name: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=500)
    persona_id: UUID | None = None
    instructions: str = Field(min_length=1, max_length=16_000)
    answer_display: Literal["timeline", "compact"] = "compact"
    max_tool_calls: ProfileToolCallLimit = 25
    todo_enabled: StrictBool = False
    integrations_enabled_by_default: StrictBool = True
    integration_availability: dict[str, StrictBool] = Field(default_factory=dict)

    _validate_availability = field_validator("integration_availability")(
        validate_integration_availability
    )
    private_permissions: AgentProfilePermissions = Field(
        default_factory=AgentProfilePermissions
    )
    public_permissions: AgentProfilePermissions = Field(
        default_factory=AgentProfilePermissions
    )

    @field_validator("name", "description", "instructions", mode="before")
    @classmethod
    def strip_text(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


class EnabledRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool


async def _load_scoped(
    db: AsyncSession,
    scope: SettingsApiScope,
    user: UserAccount,
    *,
    for_update: bool = False,
) -> tuple[AgentProfilesSettings | UserAgentProfilesSettings, UUID | None]:
    if scope == "instance":
        if not user.is_admin:
            raise ApiError(403, "auth.adminRequired")
        return (
            await SettingsStore.load(
                db, AgentProfilesSettings, for_update=for_update
            ),
            None,
        )
    instance = await SettingsStore.load(
        db, AgentProfilesSettings, for_update=for_update
    )
    if not instance.personal_agents_enabled:
        raise ApiError(409, "agentProfile.personalDisabled")
    return (
        await SettingsStore.load(
            db, UserAgentProfilesSettings, user_id=user.id, for_update=for_update
        ),
        user.id,
    )


def _profile_payload(
    profile: AgentProfile,
    scope: Literal["instance", "personal", "integration"],
    instance_identity: AgentIdentitySettings,
    personal_identity: UserAgentIdentitySettings | None,
) -> dict:
    resolved = resolve_agent_persona(instance_identity, personal_identity, profile)
    return {
        **profile.model_dump(mode="json", by_alias=True),
        "scope": scope,
        "protected": profile.id in BUILTIN_AGENT_PROFILE_IDS or not profile.editable,
        "editable": profile.editable,
        "resolvedPersonaId": str(resolved.id),
        "resolvedPersonaName": resolved.name,
    }


async def _response(
    db: AsyncSession,
    user: UserAccount,
    settings: AgentProfilesSettings | UserAgentProfilesSettings | None = None,
    scope: SettingsApiScope | None = None,
) -> dict:
    effective = await load_effective_agent_profiles(db, user.id)
    instance_identity = await SettingsStore.load(db, AgentIdentitySettings)
    personal_identity = await SettingsStore.load_optional(
        db, UserAgentIdentitySettings, user_id=user.id
    )
    integrations = (
        await SettingsStore.load(db, IntegrationSettings)
        if scope == "instance"
        else await load_effective_integrations(db, user.id)
    )
    tools = (
        await SettingsStore.load(db, ToolSettings)
        if scope == "instance"
        else await load_effective_tools(db, user.id)
    )
    effective_profiles = [
        _profile_payload(
            profile,
            "integration" if profile.integration_id is not None else (
                "personal" if profile.owner_id == user.id else "instance"
            ),
            instance_identity,
            personal_identity,
        )
        for profile in effective.profiles
    ]
    public_tools = tools.model_copy(deep=True).select_trust(TrustLevel.PUBLIC)
    private_tools = tools.model_copy(deep=True).select_trust(TrustLevel.PRIVATE)
    groups = tool_registry.catalog(public_tools, IntegrationSettings())
    return {
        "defaultAgentProfileId": str(DEFAULT_AGENT_PROFILE_ID),
        "personalAgentsEnabled": effective.personal_agents_enabled,
        "groups": groups,
        "publicGroups": groups,
        "privateGroups": tool_registry.catalog(private_tools, IntegrationSettings()),
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
                "supportsPolicies": bool(
                    tool_registry.integration_resource_actions(instance)
                ),
                "supportsResources": bool(
                    (adapter := integration_adapter(
                        instance.integration_type, instance.adapter_type
                    ))
                    and adapter.resource_loader
                    and tool_registry.integration_resource_actions(instance)
                ),
            }
            for instance in integrations.instances
        ],
        "personas": [
            {
                **persona.model_dump(mode="json", by_alias=True),
                "scope": "instance",
                "effective": True,
            }
            for persona in instance_identity.personas
        ]
        + (
            [
                {
                    **persona.model_dump(mode="json", by_alias=True),
                    "scope": "personal",
                    "effective": instance_identity.personal_personas_enabled,
                }
                for persona in personal_identity.personas
            ]
            if scope != "instance" and personal_identity is not None
            else []
        ),
        "personalPersonasEnabled": instance_identity.personal_personas_enabled,
        "effectiveAgents": effective_profiles,
        "agents": (
            effective_profiles
            if settings is None or scope is None
            else [
                _profile_payload(
                    profile.model_copy(
                        update={"owner_id": user.id if scope == "personal" else None}
                    ),
                    scope,
                    instance_identity,
                    personal_identity,
                )
                for profile in settings.profiles
            ]
        ),
    }


def _catalog_policies(groups: list[dict]) -> dict[str, set[ToolExecutionPolicy]]:
    allowed: dict[str, set[ToolExecutionPolicy]] = {}
    for group in groups:
        for tool in group["tools"]:
            for action in tool["actions"]:
                if not action["configurable"]:
                    continue
                modes = set(ToolExecutionPolicy)
                allowed.setdefault(action["capability"], modes).intersection_update(
                    modes
                )
    return allowed


def _catalog_integration_policies(
    groups: list[dict],
) -> dict[str, dict[str, set[ToolExecutionPolicy]]]:
    result: dict[str, dict[str, set[ToolExecutionPolicy]]] = {}
    for group in groups:
        for tool in group["tools"]:
            source = tool.get("source", {})
            integration_ids = {
                value for value in [source.get("id"), *source.get("ids", [])] if value
            }
            for integration_id in integration_ids:
                allowed = result.setdefault(integration_id, {})
                for action in tool["actions"]:
                    if not action["configurable"]:
                        continue
                    modes = set(ToolExecutionPolicy)
                    allowed.setdefault(
                        action["capability"], modes
                    ).intersection_update(modes)
    return result


def _validate_permissions(
    permissions: AgentProfilePermissions,
    allowed: dict[str, set[ToolExecutionPolicy]],
    integration_allowed: dict[str, dict[str, set[ToolExecutionPolicy]]],
    integration_ids: set[str],
    field: str,
    existing: AgentProfilePermissions | None = None,
) -> None:
    def entries(value: AgentProfilePermissions):
        for capability, policy in value.policies.items():
            yield None, None, capability, policy
        for integration_id, policies in value.integration_policies.items():
            for capability, policy in policies.items():
                yield integration_id, None, capability, policy
        for integration_id, resources in value.resource_policies.items():
            for resource_key, policies in resources.items():
                for capability, policy in policies.items():
                    yield integration_id, resource_key, capability, policy

    existing_entries = set(entries(existing)) if existing is not None else set()
    for entry in entries(permissions):
        integration_id, resource_key, capability, policy = entry
        if entry in existing_entries:
            continue
        if integration_id is not None:
            try:
                canonical_id = str(UUID(integration_id))
            except ValueError as exc:
                raise ApiError(422, "validation.invalid", field=field) from exc
            if canonical_id != integration_id or integration_id not in integration_ids:
                raise ApiError(422, "validation.invalidChoice", field=field)
            scoped_allowed = integration_allowed.get(integration_id, {})
            if capability not in scoped_allowed or policy not in scoped_allowed[capability]:
                raise ApiError(422, "validation.invalidChoice", field=field)
            if resource_key is not None and _RESOURCE_KEY.fullmatch(resource_key) is None:
                raise ApiError(422, "validation.invalid", field=field)
            continue
        if capability not in allowed or policy not in allowed[capability]:
            raise ApiError(422, "validation.invalidChoice", field=field)


async def _profile_from_request(
    db: AsyncSession,
    user: UserAccount,
    owner_id: UUID | None,
    payload: AgentProfileRequest,
    *,
    profile_id: UUID | None = None,
    existing: AgentProfile | None = None,
) -> AgentProfile:
    persona_id = (
        existing.persona_id
        if existing is not None and "persona_id" not in payload.model_fields_set
        else payload.persona_id
    )
    if persona_id is not None and (
        existing is None or persona_id != existing.persona_id
    ):
        identity = await SettingsStore.load(
            db, AgentIdentitySettings, for_update=owner_id is not None
        )
        allowed_persona_ids = {persona.id for persona in identity.personas}
        if owner_id is not None and identity.personal_personas_enabled:
            personal_identity = await SettingsStore.load_optional(
                db,
                UserAgentIdentitySettings,
                user_id=user.id,
                for_update=True,
            )
            if personal_identity is not None:
                allowed_persona_ids.update(
                    persona.id for persona in personal_identity.personas
                )
        if persona_id not in allowed_persona_ids:
            raise ApiError(422, "validation.invalidChoice", field="personaId")

    integrations = (
        await load_effective_integrations(db, user.id)
        if owner_id is not None
        else await SettingsStore.load(db, IntegrationSettings)
    )
    tools = (
        await load_effective_tools(db, user.id)
        if owner_id is not None
        else await SettingsStore.load(db, ToolSettings)
    )
    groups = tool_registry.catalog(tools, integrations)
    allowed = _catalog_policies(tool_registry.catalog(tools, IntegrationSettings()))
    integration_allowed = _catalog_integration_policies(groups)
    integration_ids = {str(item.id) for item in integrations.instances}
    for integration_id, enabled in payload.integration_availability.items():
        if integration_id not in integration_ids and (
            existing is None
            or existing.integration_availability.get(integration_id) is not enabled
        ):
            raise ApiError(422, "validation.invalidChoice", field="integrationAvailability")
    _validate_permissions(
        payload.private_permissions,
        allowed,
        integration_allowed,
        integration_ids,
        "privatePermissions",
        existing.private_permissions if existing is not None else None,
    )
    _validate_permissions(
        payload.public_permissions,
        allowed,
        integration_allowed,
        integration_ids,
        "publicPermissions",
        existing.public_permissions if existing is not None else None,
    )
    values = payload.model_dump()
    values["persona_id"] = persona_id
    for field in (
        "answer_display", "max_tool_calls", "todo_enabled", "integrations_enabled_by_default",
        "integration_availability",
    ):
        if field not in payload.model_fields_set:
            if existing is not None:
                values[field] = getattr(existing, field)
            else:
                values.pop(field)
    return AgentProfile(id=profile_id or uuid4(), **values)


def _ensure_unique_name(
    settings: AgentProfilesSettings | UserAgentProfilesSettings,
    name: str,
    *,
    exclude_id: UUID | None = None,
) -> None:
    folded = name.strip().casefold()
    reserved_id = next(
        (
            profile_id
            for profile_id, builtin_name in BUILTIN_AGENT_PROFILE_NAMES.items()
            if builtin_name.casefold() == folded
        ),
        DEFAULT_AGENT_PROFILE_ID if folded == "default" else None,
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
    settings, _owner_id = await _load_scoped(db, scope, user)
    return await _response(db, user, settings, scope)


@router.get("/integration-metadata/{integration_id}")
async def agent_profile_integration_metadata(
    integration_id: UUID,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
    scope: SettingsApiScope = "personal",
) -> dict:
    await _load_scoped(db, scope, user)
    integrations = (
        await SettingsStore.load(db, IntegrationSettings)
        if scope == "instance"
        else await load_effective_integrations(db, user.id)
    )
    instance = next(
        (item for item in integrations.instances if item.id == integration_id), None
    )
    if instance is None:
        raise ApiError(404, "integration.notFound")
    resources = await _integration_resources(db, instance)
    if scope == "instance":
        base_tools = await SettingsStore.load(db, ToolSettings)
        public_tools = base_tools.model_copy(deep=True).select_trust(TrustLevel.PUBLIC)
        private_tools = base_tools.model_copy(deep=True).select_trust(TrustLevel.PRIVATE)
    else:
        public_tools = await load_effective_tools(db, user.id, TrustLevel.PUBLIC)
        private_tools = await load_effective_tools(db, user.id, TrustLevel.PRIVATE)
    access = _access_payload(instance, resources, public_tools)
    private_access = _access_payload(instance, resources, private_tools)
    return {
        "integrationId": str(instance.id),
        "actions": access["actions"],
        "resources": access["resources"],
        "public": {"actions": access["actions"], "resources": access["resources"]},
        "private": {
            "actions": private_access["actions"],
            "resources": private_access["resources"],
        },
    }


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
    limit = 20 if owner_id is not None else 102
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
    builtin_name = BUILTIN_AGENT_PROFILE_NAMES.get(profile_id)
    if builtin_name is not None and payload.name != builtin_name:
        raise ApiError(409, "agentProfile.defaultProtected", field="name")
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


@router.delete("/{profile_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_agent_profile(
    profile_id: UUID,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
    scope: SettingsApiScope = "personal",
) -> None:
    settings, owner_id = await _load_scoped(db, scope, user, for_update=True)
    profile = next((item for item in settings.profiles if item.id == profile_id), None)
    if profile is None:
        raise ApiError(404, "agentProfile.notFound")
    if profile_id in BUILTIN_AGENT_PROFILE_IDS:
        raise ApiError(409, "agentProfile.defaultProtected")
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
            session.agent_profile_id = DEFAULT_AGENT_PROFILE_ID
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
