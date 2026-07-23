from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import AliasChoices, BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.db.session import get_db_session
from mato_loop_backend.security import require_user
from mato_loop_backend.settings import (
    MediaToolSettings,
    SettingsStore,
    ToolSettings,
    WebFetchSettings,
)

router = APIRouter(prefix="/tools", dependencies=[Depends(require_user)])


class ToolSettingsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

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


def tool_settings_payload(settings: ToolSettings) -> dict:
    return {
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
    }


@router.get("/settings")
async def get_tool_settings(
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict:
    return tool_settings_payload(await SettingsStore.load(db, ToolSettings))


@router.put("/settings")
async def update_tool_settings(
    payload: ToolSettingsRequest,
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict:
    settings = await SettingsStore.load(db, ToolSettings, for_update=True)
    settings.web_fetch = payload.web_fetch
    for field_name in (
        "generate_image",
        "generate_speech",
        "generate_audio",
        "transcribe_audio",
    ):
        value = getattr(payload, field_name)
        if value is not None:
            setattr(settings, field_name, value)
    await SettingsStore.save(db, settings)
    await db.commit()
    return tool_settings_payload(settings)
