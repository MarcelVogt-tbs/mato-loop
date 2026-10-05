from typing import Annotated, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.api.errors import ApiError
from mato_loop_backend.db.session import get_db_session
from mato_loop_backend.models import UserAccount
from mato_loop_backend.providers.settings_service import (
    DEFAULT_MODEL_FIELDS,
    first_available_agent_model,
    model_reference_available,
)
from mato_loop_backend.security import require_admin, require_user
from mato_loop_backend.sessions.service import SessionServiceError
from mato_loop_backend.sessions.templates import validate_chat_model
from mato_loop_backend.settings import SettingsStore, load_effective_ai
from mato_loop_backend.settings.models import (
    ModelDefaults,
    ModelDefaultsSettings,
    UserModelDefaultsSettings,
)
from mato_loop_backend.settings.session_profiles import ChatModelOverride

router = APIRouter(prefix="/model-defaults", dependencies=[Depends(require_user)])


class EnabledRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool


@router.get("")
async def get_defaults(user: Annotated[UserAccount, Depends(require_user)], db: Annotated[AsyncSession, Depends(get_db_session)], scope: Literal["instance", "personal"] = "personal"):
    if scope == "instance" and not user.is_admin:
        raise ApiError(403, "auth.adminRequired")
    instance = await SettingsStore.load(db, ModelDefaultsSettings)
    configured = instance if scope == "instance" else await SettingsStore.load(db, UserModelDefaultsSettings, user_id=user.id)
    effective = await load_effective_ai(db, user.id, include_personal=scope == "personal")
    automatic_model_id = first_available_agent_model(effective)
    fields = set(ModelDefaults.model_fields)
    return {
        "personalSettingsEnabled": instance.personal_settings_enabled,
        "automaticModelId": str(automatic_model_id) if automatic_model_id else None,
        "configured": configured.model_dump(mode="json", by_alias=True, include=fields),
        "inherited": instance.model_dump(mode="json", by_alias=True, include=fields) if scope == "personal" else ModelDefaults().model_dump(mode="json", by_alias=True),
        "effective": effective.model_dump(mode="json", by_alias=True, include=fields),
    }


@router.put("")
async def save_defaults(payload: ModelDefaults, user: Annotated[UserAccount, Depends(require_user)], db: Annotated[AsyncSession, Depends(get_db_session)], scope: Literal["instance", "personal"] = "personal"):
    if scope == "instance" and not user.is_admin:
        raise ApiError(403, "auth.adminRequired")
    instance = await SettingsStore.load(db, ModelDefaultsSettings, for_update=True)
    if scope == "personal" and not instance.personal_settings_enabled and not user.is_admin:
        raise ApiError(409, "modelDefaults.personalDisabled")
    ai = await load_effective_ai(db, user.id, include_personal=scope == "personal")
    for field, (alias, _) in DEFAULT_MODEL_FIELDS.items():
        value = getattr(payload, field)
        if value is not None and not model_reference_available(ai, field, value):
            raise ApiError(422, "provider.modelCapabilityUnavailable", field=alias)
    try:
        inherited_model_id = (instance.default_model_id if scope == "personal" else None) or first_available_agent_model(ai)
        validate_chat_model(ai, ChatModelOverride(model_id=payload.default_model_id, reasoning_effort=payload.reasoning_effort), inherited_model_id)
    except SessionServiceError as exc:
        raise ApiError(exc.status_code, exc.code, field=exc.field) from exc
    configured = instance if scope == "instance" else await SettingsStore.load(db, UserModelDefaultsSettings, user_id=user.id, for_update=True)
    for field in ModelDefaults.model_fields:
        setattr(configured, field, getattr(payload, field))
    await SettingsStore.save(db, configured, user_id=user.id if scope == "personal" else None)
    await db.commit()
    return await get_defaults(user, db, scope)


@router.put("/personal-enabled")
async def set_enabled(payload: EnabledRequest, _admin: Annotated[UserAccount, Depends(require_admin)], db: Annotated[AsyncSession, Depends(get_db_session)]):
    settings = await SettingsStore.load(db, ModelDefaultsSettings, for_update=True)
    settings.personal_settings_enabled = payload.enabled
    await SettingsStore.save(db, settings)
    await db.commit()
    return {"personalSettingsEnabled": payload.enabled}
