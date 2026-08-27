from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import AliasChoices, BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.agent.tools import tool_execution_policy, tool_registry
from mato_loop_backend.api.errors import ApiError
from mato_loop_backend.db.session import get_db_session
from mato_loop_backend.integrations import (
    normalize_search_tool_settings,
    search_provider_options,
)
from mato_loop_backend.models import UserAccount
from mato_loop_backend.security import require_user
from mato_loop_backend.settings import (
    IntegrationSettings,
    MediaToolSettings,
    PolicyToolSettings,
    SettingsStore,
    ShellPrefixSettings,
    ToolExecutionPolicy,
    ToolSettings,
    UserToolSettings,
    WebFetchSettings,
    WebSearchToolSettings,
    load_effective_integrations,
    load_effective_tools,
)

router = APIRouter(prefix="/tools", dependencies=[Depends(require_user)])
SettingsApiScope = Literal["instance", "personal"]


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
    if personal is None:
        personal = UserToolSettings.model_validate(
            instance.model_dump(exclude={"personal_settings_enabled"})
        )
    return personal, user.id


class ToolSettingsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    policies: dict[str, ToolExecutionPolicy] | None = None
    shell_prefixes: ShellPrefixSettings | None = Field(
        default=None, validation_alias=AliasChoices("shellPrefixes", "shell_prefixes")
    )
    web_search: WebSearchToolSettings | None = Field(
        default=None, validation_alias=AliasChoices("webSearch", "web_search")
    )
    web_fetch: WebFetchSettings = Field(
        validation_alias=AliasChoices("webFetch", "web_fetch")
    )
    generate_image: MediaToolSettings | None = Field(
        default=None, validation_alias=AliasChoices("generateImage", "generate_image")
    )
    edit_image: MediaToolSettings | None = Field(
        default=None, validation_alias=AliasChoices("editImage", "edit_image")
    )
    generate_speech: MediaToolSettings | None = Field(
        default=None, validation_alias=AliasChoices("generateSpeech", "generate_speech")
    )
    generate_audio: MediaToolSettings | None = Field(
        default=None, validation_alias=AliasChoices("generateAudio", "generate_audio")
    )
    transcribe_audio: MediaToolSettings | None = Field(
        default=None, validation_alias=AliasChoices("transcribeAudio", "transcribe_audio")
    )
    analyze_video: MediaToolSettings | None = Field(
        default=None, validation_alias=AliasChoices("analyzeVideo", "analyze_video")
    )
    update_agent_identity: PolicyToolSettings | None = Field(
        default=None,
        validation_alias=AliasChoices("updateAgentIdentity", "update_agent_identity"),
    )


class PersonalSettingsEnabledRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool


def tool_settings_payload(
    settings: ToolSettings,
    integrations: IntegrationSettings | None = None,
    *,
    include_private_origins: bool = True,
) -> dict:
    integration_settings = integrations or IntegrationSettings()
    effective_settings = settings.model_copy(deep=True)
    normalize_search_tool_settings(effective_settings, integration_settings)
    provider_options = search_provider_options(integration_settings)
    groups = tool_registry.catalog(settings, integration_settings)
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
        "webSearch": {
            "primaryProvider": effective_settings.web_search.primary_provider,
            "fallbackProvider": effective_settings.web_search.fallback_provider,
            "providerOptions": provider_options,
        },
        "webFetch": {
            "enabled": settings.web_fetch.enabled,
            "userAgent": settings.web_fetch.user_agent,
            "maxResponseMiB": settings.web_fetch.max_response_mib,
            "defaultPolicy": settings.web_fetch.default_policy.value,
            "allowedPrivateOrigins": (
                settings.web_fetch.allowed_private_origins
                if include_private_origins
                else []
            ),
        },
        "generateImage": {
            "policy": tool_execution_policy(
                settings, "media.generate.image", settings.generate_image.policy
            ).value,
            "timeoutSeconds": settings.generate_image.timeout_seconds,
        },
        "editImage": {
            "policy": tool_execution_policy(
                settings, "media.edit.image", settings.edit_image.policy
            ).value,
            "timeoutSeconds": settings.edit_image.timeout_seconds,
        },
        "generateSpeech": {
            "policy": tool_execution_policy(
                settings, "media.generate.speech", settings.generate_speech.policy
            ).value,
            "timeoutSeconds": settings.generate_speech.timeout_seconds,
        },
        "generateAudio": {
            "policy": tool_execution_policy(
                settings, "media.generate.audio", settings.generate_audio.policy
            ).value,
            "timeoutSeconds": settings.generate_audio.timeout_seconds,
        },
        "transcribeAudio": {
            "policy": tool_execution_policy(
                settings, "media.transcribe.audio", settings.transcribe_audio.policy
            ).value,
            "timeoutSeconds": settings.transcribe_audio.timeout_seconds,
        },
        "analyzeVideo": {
            "policy": tool_execution_policy(
                settings, "media.analyze.video", settings.analyze_video.policy
            ).value,
            "timeoutSeconds": settings.analyze_video.timeout_seconds,
        },
        "updateAgentIdentity": {
            "policy": tool_execution_policy(
                settings,
                "settings.agent_identity.update",
                settings.update_agent_identity.policy,
            ).value,
        },
    }


@router.get("/settings")
async def get_tool_settings(
    db: Annotated[AsyncSession, Depends(get_db_session)],
    scope: SettingsApiScope = "instance",
    user: Annotated[UserAccount | None, Depends(require_user)] = None,
) -> dict:
    if scope == "personal":
        if user is None:
            raise ApiError(401, "auth.notAuthenticated")
        settings = await load_effective_tools(db, user.id)
        if not settings.personal_settings_enabled:
            raise ApiError(409, "tool.personalSettingsDisabled")
        integrations = await load_effective_integrations(db, user.id)
    else:
        settings, _owner_id = await _load_scoped_tools(db, scope, user)
        integrations = await SettingsStore.load(db, IntegrationSettings)
    return tool_settings_payload(
        settings,
        integrations,
        include_private_origins=user is None or getattr(user, "is_admin", False),
    )


@router.get("/personal-settings")
async def get_personal_tool_settings(
    _user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict[str, bool]:
    settings = await SettingsStore.load(db, ToolSettings)
    return {"enabled": settings.personal_settings_enabled}


@router.put("/settings")
async def update_tool_settings(
    payload: ToolSettingsRequest,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    scope: SettingsApiScope = "instance",
    user: Annotated[UserAccount | None, Depends(require_user)] = None,
) -> dict:
    settings, owner_id = await _load_scoped_tools(
        db, scope, user, for_update=True, mutation=True
    )
    integrations = (
        await load_effective_integrations(db, owner_id)
        if owner_id is not None
        else await SettingsStore.load(db, IntegrationSettings, for_update=True)
    )
    if payload.web_search is not None:
        available = {
            str(option["value"]) for option in search_provider_options(integrations)
        }
        if payload.web_search.primary_provider not in available:
            raise ApiError(
                409, "integration.providerUnavailable", field="primary_provider"
            )
        if (
            payload.web_search.fallback_provider is not None
            and payload.web_search.fallback_provider not in available
        ):
            raise ApiError(
                409, "integration.providerUnavailable", field="fallback_provider"
            )
        settings.web_search = payload.web_search
    if payload.shell_prefixes is not None:
        settings.shell_prefixes = payload.shell_prefixes
    if owner_id is None:
        settings.web_fetch = payload.web_fetch
    else:
        settings.web_fetch.enabled = payload.web_fetch.enabled
        settings.web_fetch.default_policy = payload.web_fetch.default_policy
    for field_name in (
        "generate_image",
        "edit_image",
        "generate_speech",
        "generate_audio",
        "transcribe_audio",
        "analyze_video",
        "update_agent_identity",
    ):
        value = getattr(payload, field_name)
        if value is not None:
            setattr(settings, field_name, value)
    legacy_policies = {
        "media.generate.image": settings.generate_image.policy,
        "media.edit.image": settings.edit_image.policy,
        "media.generate.speech": settings.generate_speech.policy,
        "media.generate.audio": settings.generate_audio.policy,
        "media.transcribe.audio": settings.transcribe_audio.policy,
        "media.analyze.video": settings.analyze_video.policy,
        "settings.agent_identity.update": settings.update_agent_identity.policy,
    }
    for capability, policy in legacy_policies.items():
        field_name = {
            "media.generate.image": "generate_image",
            "media.edit.image": "edit_image",
            "media.generate.speech": "generate_speech",
            "media.generate.audio": "generate_audio",
            "media.transcribe.audio": "transcribe_audio",
            "media.analyze.video": "analyze_video",
            "settings.agent_identity.update": "update_agent_identity",
        }[capability]
        if getattr(payload, field_name) is not None:
            settings.policies[capability] = policy
        else:
            settings.policies.setdefault(capability, policy)
    if payload.policies is not None:
        groups = tool_registry.catalog(settings, integrations)
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
        allowed: dict[str, set[ToolExecutionPolicy]] = {}
        for group in groups:
            for tool in group["tools"]:
                for action in tool["actions"]:
                    if not action["configurable"]:
                        continue
                    modes = {
                        ToolExecutionPolicy(value)
                        for value in action["allowedPolicies"]
                    }
                    allowed.setdefault(action["capability"], modes).intersection_update(
                        modes
                    )
        if any(
            policy not in allowed[capability]
            for capability, policy in payload.policies.items()
        ):
            raise ApiError(422, "validation.invalidChoice", field="policies")
        settings.policies.update(payload.policies)
    for capability, field_name in {
        "media.generate.image": "generate_image",
        "media.edit.image": "edit_image",
        "media.generate.speech": "generate_speech",
        "media.generate.audio": "generate_audio",
        "media.transcribe.audio": "transcribe_audio",
        "media.analyze.video": "analyze_video",
        "settings.agent_identity.update": "update_agent_identity",
    }.items():
        getattr(settings, field_name).policy = tool_execution_policy(
            settings, capability, getattr(settings, field_name).policy
        )
    await SettingsStore.save(db, settings, user_id=owner_id)
    await db.commit()
    response_settings = (
        await load_effective_tools(db, owner_id) if owner_id is not None else settings
    )
    return tool_settings_payload(
        response_settings,
        integrations,
        include_private_origins=user is None or getattr(user, "is_admin", False),
    )


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
    await SettingsStore.delete(db, UserToolSettings, user_id=user.id)
    await db.commit()
    return tool_settings_payload(
        await load_effective_tools(db, user.id),
        await load_effective_integrations(db, user.id),
        include_private_origins=getattr(user, "is_admin", False),
    )


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
