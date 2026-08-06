from typing import Annotated

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
from mato_loop_backend.security import require_user
from mato_loop_backend.settings import (
    IntegrationSettings,
    MediaToolSettings,
    PolicyToolSettings,
    SettingsStore,
    ToolExecutionPolicy,
    ToolSettings,
    WebFetchSettings,
    WebSearchToolSettings,
)

router = APIRouter(prefix="/tools", dependencies=[Depends(require_user)])


class ToolSettingsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    policies: dict[str, ToolExecutionPolicy] | None = None
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


def tool_settings_payload(
    settings: ToolSettings, integrations: IntegrationSettings | None = None
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
        "groups": groups,
        "policies": policies,
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
            "allowedPrivateOrigins": settings.web_fetch.allowed_private_origins,
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
) -> dict:
    integrations = await SettingsStore.load(db, IntegrationSettings)
    settings = await SettingsStore.load(db, ToolSettings)
    return tool_settings_payload(settings, integrations)


@router.put("/settings")
async def update_tool_settings(
    payload: ToolSettingsRequest,
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict:
    integrations = await SettingsStore.load(db, IntegrationSettings, for_update=True)
    settings = await SettingsStore.load(db, ToolSettings, for_update=True)
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
    settings.web_fetch = payload.web_fetch
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
    await SettingsStore.save(db, settings)
    await db.commit()
    return tool_settings_payload(settings, integrations)
