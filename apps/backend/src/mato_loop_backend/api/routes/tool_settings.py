from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import AliasChoices, BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.db.session import get_db_session
from mato_loop_backend.security import require_user
from mato_loop_backend.settings import SettingsStore, ToolSettings, WebFetchSettings

router = APIRouter(prefix="/tools", dependencies=[Depends(require_user)])


class ToolSettingsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    web_fetch: WebFetchSettings = Field(
        validation_alias=AliasChoices("webFetch", "web_fetch")
    )


def tool_settings_payload(settings: ToolSettings) -> dict:
    return {
        "webFetch": {
            "enabled": settings.web_fetch.enabled,
            "defaultPolicy": settings.web_fetch.default_policy.value,
            "allowedPrivateOrigins": settings.web_fetch.allowed_private_origins,
        }
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
    await SettingsStore.save(db, settings)
    await db.commit()
    return tool_settings_payload(settings)
