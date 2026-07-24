from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import AliasChoices, BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

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
    ToolSettings,
    WebFetchSettings,
    WebSearchToolSettings,
)

router = APIRouter(prefix="/tools", dependencies=[Depends(require_user)])


class ToolSettingsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    web_search: WebSearchToolSettings | None = Field(
        default=None, validation_alias=AliasChoices("webSearch", "web_search")
    )
    web_fetch: WebFetchSettings = Field(
        validation_alias=AliasChoices("webFetch", "web_fetch")
    )
    generate_image: MediaToolSettings | None = Field(
        default=None, validation_alias=AliasChoices("generateImage", "generate_image")
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
    return {
        "webSearch": {
            "primaryProvider": effective_settings.web_search.primary_provider,
            "fallbackProvider": effective_settings.web_search.fallback_provider,
            "providerOptions": provider_options,
        },
        "webFetch": {
            "enabled": settings.web_fetch.enabled,
            "defaultPolicy": settings.web_fetch.default_policy.value,
            "allowedPrivateOrigins": settings.web_fetch.allowed_private_origins,
        },
        "generateImage": {
            "policy": settings.generate_image.policy.value,
            "timeoutSeconds": settings.generate_image.timeout_seconds,
        },
        "generateSpeech": {
            "policy": settings.generate_speech.policy.value,
            "timeoutSeconds": settings.generate_speech.timeout_seconds,
        },
        "generateAudio": {
            "policy": settings.generate_audio.policy.value,
            "timeoutSeconds": settings.generate_audio.timeout_seconds,
        },
        "transcribeAudio": {
            "policy": settings.transcribe_audio.policy.value,
            "timeoutSeconds": settings.transcribe_audio.timeout_seconds,
        },
        "updateAgentIdentity": {
            "policy": settings.update_agent_identity.policy.value,
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
        "generate_speech",
        "generate_audio",
        "transcribe_audio",
        "update_agent_identity",
    ):
        value = getattr(payload, field_name)
        if value is not None:
            setattr(settings, field_name, value)
    await SettingsStore.save(db, settings)
    await db.commit()
    return tool_settings_payload(settings, integrations)
