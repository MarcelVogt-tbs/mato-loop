from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import AliasChoices, BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.api.errors import ApiError
from mato_loop_backend.api.permission_settings import (
    SettingsApiScope,
    _catalog_policies,
    _load_profile_access,
    _validate_permissions,
)
from mato_loop_backend.db.session import get_db_session
from mato_loop_backend.models import UserAccount
from mato_loop_backend.security import require_user
from mato_loop_backend.settings import (
    IntegrationSettings,
    SettingsStore,
    ToolSettings,
    WebFetchSettings,
    load_effective_tools,
)
from mato_loop_backend.settings.models import (
    AgentProfileAccessOverrides,
    AgentProfileOverrides,
    AgentProfilePermissions,
    AgentProfilesSettings,
    ShellPrefixSettings,
    TrustLevel,
)
from mato_loop_backend.tools.catalog import tool_registry

router = APIRouter(prefix="/tools", dependencies=[Depends(require_user)])


class MediaTimeoutRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    timeout_seconds: int = Field(
        default=300,
        ge=10,
        le=3_600,
        validation_alias=AliasChoices("timeoutSeconds", "timeout_seconds"),
    )


class ToolConfigurationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    personal_settings_enabled: bool = Field(default=False, alias="personalSettingsEnabled")
    private_permissions: AgentProfilePermissions = Field(default_factory=AgentProfilePermissions, alias="privatePermissions")
    public_permissions: AgentProfilePermissions = Field(default_factory=AgentProfilePermissions, alias="publicPermissions")
    shell_prefixes: ShellPrefixSettings = Field(default_factory=ShellPrefixSettings, alias="shellPrefixes")

    tool_availability: dict[str, bool] = Field(
        validation_alias=AliasChoices("toolAvailability", "tool_availability")
    )
    web_fetch: WebFetchSettings = Field(
        validation_alias=AliasChoices("webFetch", "web_fetch")
    )
    generate_image: MediaTimeoutRequest = Field(
        validation_alias=AliasChoices("generateImage", "generate_image")
    )
    edit_image: MediaTimeoutRequest = Field(
        validation_alias=AliasChoices("editImage", "edit_image")
    )
    generate_speech: MediaTimeoutRequest = Field(
        validation_alias=AliasChoices("generateSpeech", "generate_speech")
    )
    generate_audio: MediaTimeoutRequest = Field(
        validation_alias=AliasChoices("generateAudio", "generate_audio")
    )
    transcribe_audio: MediaTimeoutRequest = Field(
        validation_alias=AliasChoices("transcribeAudio", "transcribe_audio")
    )
    analyze_video: MediaTimeoutRequest = Field(
        validation_alias=AliasChoices("analyzeVideo", "analyze_video")
    )
    analyze_audio: MediaTimeoutRequest = Field(
        default_factory=lambda: MediaTimeoutRequest(timeout_seconds=1_800),
        validation_alias=AliasChoices("analyzeAudio", "analyze_audio"),
    )


def tool_configuration_payload(settings: ToolSettings) -> dict:
    defaults = settings.model_copy(deep=True)
    defaults.private_permissions = AgentProfilePermissions()
    defaults.public_permissions = AgentProfilePermissions()
    groups = [
        group
        for group in tool_registry.catalog(settings, IntegrationSettings())
        if group["tools"]
    ]
    for group in groups:
        for tool in group["tools"]:
            tool["available"] = settings.tool_availability.get(tool["name"], True)
    return {
        "personalSettingsEnabled": settings.personal_settings_enabled,
        "privatePermissions": _tool_permissions_payload(settings.private_permissions, tool_registry.catalog(defaults, IntegrationSettings())),
        "publicPermissions": _tool_permissions_payload(settings.public_permissions, tool_registry.catalog(defaults, IntegrationSettings())),
        "shellPrefixes": settings.shell_prefixes.model_dump(),
        "privateGroups": tool_registry.catalog(defaults.model_copy(deep=True).select_trust(TrustLevel.PRIVATE), IntegrationSettings()),
        "publicGroups": tool_registry.catalog(defaults.model_copy(deep=True).select_trust(TrustLevel.PUBLIC), IntegrationSettings()),
        "groups": groups,
        "toolAvailability": settings.tool_availability,
        "webFetch": {
            "javascriptHosts": settings.web_fetch.javascript_hosts,
            "userAgent": settings.web_fetch.user_agent,
            "maxResponseMiB": settings.web_fetch.max_response_mib,
            "publicAddressesOnly": settings.web_fetch.public_addresses_only,
        },
        "generateImage": {"timeoutSeconds": settings.generate_image.timeout_seconds},
        "editImage": {"timeoutSeconds": settings.edit_image.timeout_seconds},
        "generateSpeech": {"timeoutSeconds": settings.generate_speech.timeout_seconds},
        "generateAudio": {"timeoutSeconds": settings.generate_audio.timeout_seconds},
        "transcribeAudio": {"timeoutSeconds": settings.transcribe_audio.timeout_seconds},
        "analyzeVideo": {"timeoutSeconds": settings.analyze_video.timeout_seconds},
        "analyzeAudio": {"timeoutSeconds": settings.analyze_audio.timeout_seconds},
    }


def _tool_permissions_payload(permissions: AgentProfilePermissions, groups: list[dict]) -> dict:
    allowed = _catalog_policies(groups)
    return AgentProfilePermissions(policies={key: value for key, value in permissions.policies.items() if key in allowed}).model_dump(mode="json", by_alias=True)


@router.get("/availability")
async def get_tool_availability(
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict:
    settings = await load_effective_tools(db, user.id)
    return {
        "tools": settings.tool_availability,
    }


@router.get("/configuration")
async def get_tool_configuration(
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict:
    if not user.is_admin:
        raise ApiError(403, "auth.adminRequired")
    return tool_configuration_payload(await SettingsStore.load(db, ToolSettings))


@router.put("/configuration")
async def update_tool_configuration(
    payload: ToolConfigurationRequest,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict:
    if not user.is_admin:
        raise ApiError(403, "auth.adminRequired")
    settings = await SettingsStore.load(db, ToolSettings, for_update=True)
    known_tools = {
        tool["name"]
        for group in tool_registry.catalog(settings, IntegrationSettings())
        for tool in group["tools"]
    }
    if set(payload.tool_availability) - known_tools:
        raise ApiError(422, "validation.invalidChoice", field="toolAvailability")
    settings.tool_availability = dict(payload.tool_availability)
    for field in ("private_permissions", "public_permissions"):
        if field in payload.model_fields_set:
            value = getattr(payload, field)
            _validate_permissions(value, _catalog_policies(tool_registry.catalog(settings, IntegrationSettings())), field)
            setattr(settings, field, value)
    for field in ("personal_settings_enabled", "shell_prefixes"):
        if field in payload.model_fields_set:
            setattr(settings, field, getattr(payload, field))
    settings.web_fetch = payload.web_fetch
    for field_name in (
        "generate_image",
        "edit_image",
        "generate_speech",
        "generate_audio",
        "transcribe_audio",
        "analyze_video",
        "analyze_audio",
    ):
        getattr(settings, field_name).timeout_seconds = getattr(
            payload, field_name
        ).timeout_seconds
    await SettingsStore.save(db, settings)
    await db.commit()
    return tool_configuration_payload(settings)


class ProfileToolPermissionsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    private_permissions: AgentProfilePermissions = Field(default_factory=AgentProfilePermissions, alias="privatePermissions")
    public_permissions: AgentProfilePermissions = Field(default_factory=AgentProfilePermissions, alias="publicPermissions")
    shell_prefixes: ShellPrefixSettings | None = Field(default=None, alias="shellPrefixes")


async def _profile_permissions(db, user, profile_id, scope, *, for_update=False):
    settings, owner_id, overrides, baseline, tools = await _load_profile_access(db, user, profile_id, scope, for_update=for_update)
    groups = {}
    for trust in TrustLevel:
        copy = tools.model_copy(deep=True).select_trust(trust)
        copy._agent_permissions = getattr(baseline, f"{trust.value}_permissions")
        groups[trust.value] = tool_registry.catalog(copy, IntegrationSettings())
    payload = {
        "groupsByTrust": groups,
        "shellPrefixes": overrides.shell_prefixes.model_dump() if overrides.shell_prefixes is not None else None,
        "inheritedShellPrefixes": (baseline.shell_prefixes if baseline._shell_prefixes_configured else tools.shell_prefixes).model_dump(),
        **{f"{trust.value}Permissions": _tool_permissions_payload(getattr(overrides, f"{trust.value}_permissions") or AgentProfilePermissions(), groups[trust.value]) for trust in TrustLevel},
    }
    return settings, owner_id, overrides, payload


@router.get("/profiles/{profile_id}/permissions")
async def get_profile_tool_permissions(
    profile_id: UUID,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
    scope: SettingsApiScope = "personal",
) -> dict:
    return (await _profile_permissions(db, user, profile_id, scope))[3]


@router.put("/profiles/{profile_id}/permissions")
async def update_profile_tool_permissions(
    profile_id: UUID,
    payload: ProfileToolPermissionsRequest,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
    scope: SettingsApiScope = "personal",
) -> dict:
    settings, owner_id, overrides, current = await _profile_permissions(db, user, profile_id, scope, for_update=True)
    values = overrides.model_dump(exclude_unset=True)
    for trust in TrustLevel:
        field = f"{trust.value}_permissions"
        branch = getattr(payload, field)
        _validate_permissions(branch, _catalog_policies(current["groupsByTrust"][trust.value]), field)
        values.pop(field, None)
        if branch.policies:
            values[field] = branch.model_dump()
    values.pop("shell_prefixes", None)
    if payload.shell_prefixes is not None:
        values["shell_prefixes"] = payload.shell_prefixes.model_dump()
    if isinstance(settings, AgentProfilesSettings):
        settings.builtin_access_overrides[profile_id] = AgentProfileAccessOverrides.model_validate(values)
    else:
        next(item for item in settings.profiles if item.id == profile_id).overrides = AgentProfileOverrides.model_validate(values)
    await SettingsStore.save(db, settings, user_id=owner_id)
    await db.commit()
    return (await _profile_permissions(db, user, profile_id, scope))[3]
