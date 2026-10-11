from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.api.errors import ApiError
from mato_loop_backend.db.session import get_db_session
from mato_loop_backend.public_url import resolve_public_base_url
from mato_loop_backend.security import require_admin
from mato_loop_backend.settings import GeneralSettings, SettingsStore, ToolSettings, TrustLevel
from mato_loop_backend.settings.tool_presets import (
    ToolPermissionPreset,
    tool_permission_presets,
    tool_preset_policies,
)

router = APIRouter(prefix="/general-settings", dependencies=[Depends(require_admin)])


class GeneralSettingsUpdate(GeneralSettings):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    tool_permission_presets: dict[TrustLevel, ToolPermissionPreset] = Field(
        default_factory=dict, alias="toolPermissionPresets"
    )


def _payload(settings: GeneralSettings, tools: ToolSettings, request: Request) -> dict:
    try:
        effective, source = resolve_public_base_url(settings.public_base_url, request)
    except ValueError as exc:
        raise ApiError(422, "settings.publicUrlInvalid") from exc
    return {
        "publicBaseUrl": settings.public_base_url,
        "effectivePublicBaseUrl": effective,
        "publicUrlSource": source,
        "modelTrustEnabled": settings.model_trust_enabled,
        "toolPermissionPresets": tool_permission_presets(tools),
    }


@router.get("")
async def get_general_settings(
    request: Request, db: Annotated[AsyncSession, Depends(get_db_session)]
):
    return _payload(
        await SettingsStore.load(db, GeneralSettings),
        await SettingsStore.load(db, ToolSettings),
        request,
    )


@router.put("")
async def update_general_settings(
    payload: GeneralSettingsUpdate, request: Request, db: Annotated[AsyncSession, Depends(get_db_session)]
):
    settings = await SettingsStore.load(db, GeneralSettings, for_update=True)
    settings.public_base_url = payload.public_base_url
    if "model_trust_enabled" in payload.model_fields_set:
        settings.model_trust_enabled = payload.model_trust_enabled
    if not settings.model_trust_enabled and TrustLevel.PUBLIC in payload.tool_permission_presets:
        raise ApiError(422, "validation.invalidChoice", field="toolPermissionPresets")
    tools = await SettingsStore.load(db, ToolSettings, for_update=bool(payload.tool_permission_presets))
    for trust, preset in payload.tool_permission_presets.items():
        getattr(tools, f"{trust.value}_permissions").policies.update(tool_preset_policies(preset))
    result = _payload(settings, tools, request)
    if payload.tool_permission_presets:
        await SettingsStore.save(db, tools)
    await SettingsStore.save(db, settings)
    await db.commit()
    return result
