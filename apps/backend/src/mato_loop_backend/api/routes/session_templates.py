from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.api.errors import ApiError
from mato_loop_backend.db.session import get_db_session
from mato_loop_backend.models import UserAccount
from mato_loop_backend.security import require_admin, require_user
from mato_loop_backend.sessions.service import SessionServiceError
from mato_loop_backend.sessions.templates import (
    available_templates,
    validate_chat_model,
    validate_session_profile,
)
from mato_loop_backend.settings import SettingsStore
from mato_loop_backend.settings.session_profiles import (
    DEFAULT_TEMPLATE_ID,
    SessionTemplate,
    SessionTemplatesSettings,
    UserSessionTemplatesSettings,
)

router = APIRouter(prefix="/session-templates", dependencies=[Depends(require_user)])
Scope = Literal["instance", "personal"]


class EnabledRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool


async def load_scoped(db, user, scope, *, for_update=False):
    if scope == "instance" and not user.is_admin:
        raise ApiError(403, "auth.adminRequired")
    instance = await SettingsStore.load(db, SessionTemplatesSettings, for_update=for_update)
    if scope == "instance":
        return instance, None
    if for_update and not instance.personal_settings_enabled and not user.is_admin:
        raise ApiError(409, "sessionTemplate.personalDisabled")
    return await SettingsStore.load(db, UserSessionTemplatesSettings, user_id=user.id, for_update=for_update), user.id


@router.get("")
async def list_templates(user: Annotated[UserAccount, Depends(require_user)], db: Annotated[AsyncSession, Depends(get_db_session)], scope: Scope | None = None):
    instance = await SettingsStore.load(db, SessionTemplatesSettings)
    entries = await available_templates(db, user.id) if scope is None else [(item, scope) for item in (await load_scoped(db, user, scope))[0].templates]
    return {"templates": [{**item.model_dump(mode="json", by_alias=True), "name": "Allgemein" if item.id == DEFAULT_TEMPLATE_ID and item.name == "Default" else item.name, "scope": owner} for item, owner in entries], "personalSettingsEnabled": instance.personal_settings_enabled, "defaultTemplateId": str(DEFAULT_TEMPLATE_ID)}


@router.put("/personal-enabled")
async def set_enabled(payload: EnabledRequest, _admin: Annotated[UserAccount, Depends(require_admin)], db: Annotated[AsyncSession, Depends(get_db_session)]):
    settings = await SettingsStore.load(db, SessionTemplatesSettings, for_update=True)
    settings.personal_settings_enabled = payload.enabled
    await SettingsStore.save(db, settings)
    await db.commit()
    return {"personalSettingsEnabled": payload.enabled}


@router.put("/{template_id}")
async def save_template(template_id: UUID, payload: SessionTemplate, user: Annotated[UserAccount, Depends(require_user)], db: Annotated[AsyncSession, Depends(get_db_session)], scope: Scope = "personal"):
    settings, owner_id = await load_scoped(db, user, scope, for_update=True)
    if payload.id != template_id:
        raise ApiError(422, "validation.invalidChoice", field="id")
    try:
        ai = await validate_session_profile(db, user.id, payload, instance=scope == "instance", model_id=payload.model_id)
        validate_chat_model(ai, payload)
    except SessionServiceError as exc:
        raise ApiError(exc.status_code, exc.code, field=exc.field) from exc
    payload.name = payload.name.strip()
    if not payload.name:
        raise ApiError(422, "validation.required", field="name")
    if any(item.id != template_id and item.name.casefold() == payload.name.casefold() for item in settings.templates):
        raise ApiError(409, "sessionTemplate.duplicate")
    index = next((i for i, item in enumerate(settings.templates) if item.id == template_id), None)
    if index is None:
        if len(settings.templates) >= 100:
            raise ApiError(409, "sessionTemplate.limitReached")
        if scope == "personal" and any(item.id == template_id for item in (await SettingsStore.load(db, SessionTemplatesSettings)).templates):
            raise ApiError(422, "validation.invalidChoice", field="id")
        settings.templates.append(payload)
    else:
        settings.templates[index] = payload
    await SettingsStore.save(db, settings, user_id=owner_id)
    await db.commit()
    return await list_templates(user, db, scope)


@router.delete("/{template_id}")
async def delete_template(template_id: UUID, user: Annotated[UserAccount, Depends(require_user)], db: Annotated[AsyncSession, Depends(get_db_session)], scope: Scope = "personal"):
    settings, owner_id = await load_scoped(db, user, scope, for_update=True)
    if scope == "instance" and template_id == DEFAULT_TEMPLATE_ID:
        raise ApiError(409, "sessionTemplate.defaultProtected")
    item = next((item for item in settings.templates if item.id == template_id), None)
    if item is None:
        raise ApiError(404, "sessionTemplate.notFound")
    settings.templates.remove(item)
    await SettingsStore.save(db, settings, user_id=owner_id)
    await db.commit()
    return await list_templates(user, db, scope)
