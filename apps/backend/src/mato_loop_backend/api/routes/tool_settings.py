from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import AliasChoices, BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.agent.tools import tool_registry
from mato_loop_backend.api.errors import ApiError
from mato_loop_backend.db.session import get_db_session
from mato_loop_backend.models import UserAccount
from mato_loop_backend.security import require_user
from mato_loop_backend.settings import (
    IntegrationSettings,
    SettingsStore,
    ShellPrefixSettings,
    ToolExecutionPolicy,
    ToolSettings,
    TrustLevel,
    UserToolSettings,
    WebFetchSettings,
    load_effective_tools,
)
from mato_loop_backend.settings.effective import personal_tool_settings_snapshot

router = APIRouter(prefix="/tools", dependencies=[Depends(require_user)])
SettingsApiScope = Literal["instance", "personal"]


class MediaTimeoutRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    timeout_seconds: int = Field(
        default=300,
        ge=10,
        le=3_600,
        validation_alias=AliasChoices("timeoutSeconds", "timeout_seconds"),
    )


async def _load_scoped_tools(
    db: AsyncSession,
    scope: SettingsApiScope,
    user: UserAccount | None,
    *,
    for_update: bool = False,
    mutation: bool = False,
) -> tuple[ToolSettings | UserToolSettings, UUID | None]:
    instance = await SettingsStore.load(db, ToolSettings)
    if scope == "instance":
        if user is not None and not getattr(user, "is_admin", False):
            raise ApiError(403, "auth.adminRequired")
        settings = (
            await SettingsStore.load(db, ToolSettings, for_update=True)
            if for_update
            else instance
        )
        return settings, None
    if user is None:
        raise ApiError(401, "auth.notAuthenticated")
    if not instance.personal_settings_enabled:
        raise ApiError(409, "tool.personalSettingsDisabled")
    personal = await SettingsStore.load_optional(
        db, UserToolSettings, user_id=user.id, for_update=for_update
    )
    if personal is None or (mutation and not personal.personal_tool_defaults_enabled):
        personal = personal_tool_settings_snapshot(instance, personal)
    return personal, user.id


class ToolSettingsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    policies: dict[str, ToolExecutionPolicy] | None = None
    shell_prefixes: ShellPrefixSettings | None = Field(
        default=None, validation_alias=AliasChoices("shellPrefixes", "shell_prefixes")
    )
    generate_image: MediaTimeoutRequest | None = Field(
        default=None, validation_alias=AliasChoices("generateImage", "generate_image")
    )
    edit_image: MediaTimeoutRequest | None = Field(
        default=None, validation_alias=AliasChoices("editImage", "edit_image")
    )
    generate_speech: MediaTimeoutRequest | None = Field(
        default=None, validation_alias=AliasChoices("generateSpeech", "generate_speech")
    )
    generate_audio: MediaTimeoutRequest | None = Field(
        default=None, validation_alias=AliasChoices("generateAudio", "generate_audio")
    )
    transcribe_audio: MediaTimeoutRequest | None = Field(
        default=None, validation_alias=AliasChoices("transcribeAudio", "transcribe_audio")
    )
    analyze_video: MediaTimeoutRequest | None = Field(
        default=None, validation_alias=AliasChoices("analyzeVideo", "analyze_video")
    )


class PersonalSettingsEnabledRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool


class ToolConfigurationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool_availability: dict[str, bool] = Field(
        validation_alias=AliasChoices("toolAvailability", "tool_availability")
    )
    web_fetch: WebFetchSettings = Field(
        validation_alias=AliasChoices("webFetch", "web_fetch")
    )


def tool_settings_payload(
    settings: ToolSettings,
) -> dict:
    groups = tool_registry.catalog(settings, IntegrationSettings())
    policies = {
        action["capability"]: action["policy"]
        for group in groups
        for tool in group["tools"]
        for action in tool["actions"]
        if action["configurable"]
    }
    return {
        "personalSettingsEnabled": settings.personal_settings_enabled,
        "groups": groups,
        "policies": policies,
        "shellPrefixes": settings.shell_prefixes.model_dump(),
        "webFetch": {
            "userAgent": settings.web_fetch.user_agent,
            "maxResponseMiB": settings.web_fetch.max_response_mib,
            "publicAddressesOnly": settings.web_fetch.public_addresses_only,
        },
        "generateImage": {
            "timeoutSeconds": settings.generate_image.timeout_seconds,
        },
        "editImage": {
            "timeoutSeconds": settings.edit_image.timeout_seconds,
        },
        "generateSpeech": {
            "timeoutSeconds": settings.generate_speech.timeout_seconds,
        },
        "generateAudio": {
            "timeoutSeconds": settings.generate_audio.timeout_seconds,
        },
        "transcribeAudio": {
            "timeoutSeconds": settings.transcribe_audio.timeout_seconds,
        },
        "analyzeVideo": {
            "timeoutSeconds": settings.analyze_video.timeout_seconds,
        },
    }


def tool_configuration_payload(settings: ToolSettings) -> dict:
    groups = tool_registry.catalog(settings, IntegrationSettings())
    for group in groups:
        for tool in group["tools"]:
            tool["available"] = settings.tool_availability.get(tool["name"], True)
    return {
        "groups": groups,
        "toolAvailability": settings.tool_availability,
        "webFetch": {
            "userAgent": settings.web_fetch.user_agent,
            "maxResponseMiB": settings.web_fetch.max_response_mib,
            "publicAddressesOnly": settings.web_fetch.public_addresses_only,
        },
    }


@router.get("/settings")
async def get_tool_settings(
    db: Annotated[AsyncSession, Depends(get_db_session)],
    trust: TrustLevel,
    scope: SettingsApiScope = "instance",
    user: Annotated[UserAccount | None, Depends(require_user)] = None,
) -> dict:
    if scope == "personal":
        if user is None:
            raise ApiError(401, "auth.notAuthenticated")
        settings = await load_effective_tools(db, user.id, trust)
        if not settings.personal_settings_enabled:
            raise ApiError(409, "tool.personalSettingsDisabled")
    else:
        settings, _owner_id = await _load_scoped_tools(db, scope, user)
        settings.select_trust(trust)
    return tool_settings_payload(settings)


@router.get("/personal-settings")
async def get_personal_tool_settings(
    _user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict[str, bool]:
    settings = await SettingsStore.load(db, ToolSettings)
    return {"enabled": settings.personal_settings_enabled}


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
    settings.web_fetch = payload.web_fetch
    await SettingsStore.save(db, settings)
    await db.commit()
    return tool_configuration_payload(settings)


@router.put("/settings")
async def update_tool_settings(
    payload: ToolSettingsRequest,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    trust: TrustLevel,
    scope: SettingsApiScope = "instance",
    user: Annotated[UserAccount | None, Depends(require_user)] = None,
) -> dict:
    settings, owner_id = await _load_scoped_tools(
        db, scope, user, for_update=True, mutation=True
    )
    settings.select_trust(trust)
    if payload.shell_prefixes is not None:
        settings.shell_prefixes = payload.shell_prefixes
    for field_name in (
        "generate_image",
        "edit_image",
        "generate_speech",
        "generate_audio",
        "transcribe_audio",
        "analyze_video",
    ):
        value = getattr(payload, field_name)
        if value is not None:
            getattr(settings, field_name).timeout_seconds = value.timeout_seconds
    if payload.policies is not None:
        groups = tool_registry.catalog(settings, IntegrationSettings())
        configurable = {
            action["capability"]
            for group in groups
            for tool in group["tools"]
            for action in tool["actions"]
            if action["configurable"]
        }
        unknown = set(payload.policies) - configurable
        if unknown:
            raise ApiError(422, "validation.invalidChoice", field="policies")
        settings.permissions.policies = dict(payload.policies)
    await SettingsStore.save(db, settings, user_id=owner_id)
    await db.commit()
    response_settings = (
        await load_effective_tools(db, owner_id, trust)
        if owner_id is not None
        else settings
    )
    return tool_settings_payload(response_settings)


@router.delete("/settings")
async def reset_personal_tool_settings(
    db: Annotated[AsyncSession, Depends(get_db_session)],
    scope: SettingsApiScope = "personal",
    user: Annotated[UserAccount | None, Depends(require_user)] = None,
) -> dict:
    if scope != "personal":
        raise ApiError(400, "validation.invalidChoice", field="scope")
    if user is None:
        raise ApiError(401, "auth.notAuthenticated")
    instance = await SettingsStore.load(db, ToolSettings)
    if not instance.personal_settings_enabled:
        raise ApiError(409, "tool.personalSettingsDisabled")
    personal = await SettingsStore.load_optional(
        db, UserToolSettings, user_id=user.id, for_update=True
    )
    if personal is not None:
        personal.personal_tool_defaults_enabled = False
        await SettingsStore.save(db, personal, user_id=user.id)
    await db.commit()
    return tool_settings_payload(await load_effective_tools(db, user.id))


@router.patch("/personal-enabled")
async def set_personal_tool_settings_enabled(
    payload: PersonalSettingsEnabledRequest,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    user: Annotated[UserAccount | None, Depends(require_user)] = None,
) -> dict[str, bool]:
    settings, _owner_id = await _load_scoped_tools(
        db, "instance", user, for_update=True, mutation=True
    )
    assert isinstance(settings, ToolSettings)
    settings.personal_settings_enabled = payload.enabled
    await SettingsStore.save(db, settings)
    await db.commit()
    return {"enabled": settings.personal_settings_enabled}
