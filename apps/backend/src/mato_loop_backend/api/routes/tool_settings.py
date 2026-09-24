from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import AliasChoices, BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.api.errors import ApiError
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


def tool_configuration_payload(settings: ToolSettings) -> dict:
    groups = [
        group
        for group in tool_registry.catalog(settings, IntegrationSettings())
        if group["tools"]
    ]
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
        "generateImage": {"timeoutSeconds": settings.generate_image.timeout_seconds},
        "editImage": {"timeoutSeconds": settings.edit_image.timeout_seconds},
        "generateSpeech": {"timeoutSeconds": settings.generate_speech.timeout_seconds},
        "generateAudio": {"timeoutSeconds": settings.generate_audio.timeout_seconds},
        "transcribeAudio": {"timeoutSeconds": settings.transcribe_audio.timeout_seconds},
        "analyzeVideo": {"timeoutSeconds": settings.analyze_video.timeout_seconds},
    }


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
    for field_name in (
        "generate_image",
        "edit_image",
        "generate_speech",
        "generate_audio",
        "transcribe_audio",
        "analyze_video",
    ):
        getattr(settings, field_name).timeout_seconds = getattr(
            payload, field_name
        ).timeout_seconds
    await SettingsStore.save(db, settings)
    await db.commit()
    return tool_configuration_payload(settings)
