from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.api.errors import ApiError
from mato_loop_backend.db.session import get_db_session
from mato_loop_backend.execution.maintenance import schedule_auto_updates
from mato_loop_backend.public_url import resolve_public_base_url
from mato_loop_backend.security import require_admin
from mato_loop_backend.settings import GeneralSettings, SettingsStore

router = APIRouter(prefix="/general-settings", dependencies=[Depends(require_admin)])


class GeneralSettingsUpdate(GeneralSettings):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


def _payload(settings: GeneralSettings, request: Request) -> dict:
    try:
        effective, source = resolve_public_base_url(settings.public_base_url, request)
    except ValueError as exc:
        raise ApiError(422, "settings.publicUrlInvalid") from exc
    return {
        "publicBaseUrl": settings.public_base_url,
        "effectivePublicBaseUrl": effective,
        "publicUrlSource": source,
        "modelTrustEnabled": settings.model_trust_enabled,
        "agentAutoUpdateEnabled": settings.agent_auto_update_enabled,
    }


@router.get("")
async def get_general_settings(
    request: Request, db: Annotated[AsyncSession, Depends(get_db_session)]
):
    return _payload(
        await SettingsStore.load(db, GeneralSettings),
        request,
    )


@router.put("")
async def update_general_settings(
    payload: GeneralSettingsUpdate, request: Request, db: Annotated[AsyncSession, Depends(get_db_session)]
):
    settings = await SettingsStore.load(db, GeneralSettings, for_update=True)
    auto_update_changed = (
        "agent_auto_update_enabled" in payload.model_fields_set
        and settings.agent_auto_update_enabled != payload.agent_auto_update_enabled
    )
    if "public_base_url" in payload.model_fields_set:
        settings.public_base_url = payload.public_base_url
    if "model_trust_enabled" in payload.model_fields_set:
        settings.model_trust_enabled = payload.model_trust_enabled
    if "agent_auto_update_enabled" in payload.model_fields_set:
        settings.agent_auto_update_enabled = payload.agent_auto_update_enabled
    result = _payload(settings, request)
    await SettingsStore.save(db, settings)
    await db.commit()
    if auto_update_changed:
        schedule_auto_updates()
    return result
