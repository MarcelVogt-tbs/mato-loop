from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel, ConfigDict, Field, StrictBool
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.api.errors import ApiError
from mato_loop_backend.db.session import get_db_session
from mato_loop_backend.models import UserAccount
from mato_loop_backend.security import require_user
from mato_loop_backend.settings import (
    DEFAULT_SKILLS,
    InstanceSkillsSettings,
    SettingsStore,
    SkillDefinition,
)
from mato_loop_backend.skills import (
    SkillDraft,
    SkillError,
    SkillScope,
    SkillService,
)

router = APIRouter(prefix="/skills", dependencies=[Depends(require_user)])

_ERROR_STATUS = {
    "skill.duplicate": status.HTTP_409_CONFLICT,
    "skill.limitReached": status.HTTP_409_CONFLICT,
    "skill.notFound": status.HTTP_404_NOT_FOUND,
    "skill.inUse": status.HTTP_409_CONFLICT,
    "skill.defaultProtected": status.HTTP_409_CONFLICT,
    "skill.personalDisabled": status.HTTP_409_CONFLICT,
}


class SkillCatalogItem(BaseModel):
    model_config = ConfigDict(
        alias_generator=lambda value: value.split("_")[0]
        + "".join(part.capitalize() for part in value.split("_")[1:]),
        populate_by_name=True,
    )

    id: UUID
    name: str
    description: str
    instructions: str
    enabled: bool
    scope: SkillScope
    is_default: bool
    modified: bool


class SkillListResponse(BaseModel):
    items: list[SkillCatalogItem]


class SkillEnabledRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool


class SkillSettingsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    personal_skills_enabled: StrictBool = Field(alias="personalSkillsEnabled")


def _raise_api_error(exc: SkillError) -> None:
    code = exc.code if exc.code in _ERROR_STATUS else "skill.draftInvalid"
    raise ApiError(_ERROR_STATUS.get(code, status.HTTP_422_UNPROCESSABLE_ENTITY), code) from exc


def _require_scope(user: UserAccount, scope: SkillScope) -> None:
    if scope == "instance" and not user.is_admin:
        raise ApiError(status.HTTP_403_FORBIDDEN, "auth.adminRequired")


def _payload(skill: SkillDefinition, scope: SkillScope) -> SkillCatalogItem:
    default = DEFAULT_SKILLS.get(skill.id)
    return SkillCatalogItem(
        **skill.model_dump(),
        scope=scope,
        is_default=default is not None,
        modified=default is not None and any(
            getattr(skill, field) != getattr(default, field)
            for field in ("name", "description", "instructions")
        ),
    )


@router.get("", response_model=SkillListResponse)
async def list_skills(
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
    scope: Annotated[SkillScope | None, Query()] = None,
) -> SkillListResponse:
    if scope is not None:
        _require_scope(user, scope)
        skills = await SkillService.list_scoped(db, user.id, scope)
        return SkillListResponse(items=[_payload(skill, scope) for skill in skills])
    skills = await SkillService.list(db, user.id)
    return SkillListResponse(items=[
        _payload(skill, "personal" if skill.owner_id == user.id else "instance")
        for skill in skills
    ])


@router.get("/settings", response_model=SkillSettingsRequest)
async def get_skill_settings(
    _user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> SkillSettingsRequest:
    settings = await SettingsStore.load(db, InstanceSkillsSettings)
    return SkillSettingsRequest(personal_skills_enabled=settings.personal_skills_enabled)


@router.put("/settings", response_model=SkillSettingsRequest)
async def update_skill_settings(
    payload: SkillSettingsRequest,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> SkillSettingsRequest:
    _require_scope(user, "instance")
    settings = await SettingsStore.load(db, InstanceSkillsSettings, for_update=True)
    settings.personal_skills_enabled = payload.personal_skills_enabled
    await SettingsStore.save(db, settings)
    await db.commit()
    return payload


@router.post("", response_model=SkillCatalogItem, status_code=status.HTTP_201_CREATED)
async def create_skill(
    payload: SkillDraft,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
    scope: Annotated[SkillScope, Query()] = "personal",
) -> SkillCatalogItem:
    _require_scope(user, scope)
    try:
        skill = await SkillService.create(db, user.id, payload, scope)
    except SkillError as exc:
        _raise_api_error(exc)
    await db.commit()
    return _payload(skill, scope)


@router.put("/{skill_id}", response_model=SkillCatalogItem)
async def replace_skill(
    skill_id: UUID,
    payload: SkillDraft,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
    scope: Annotated[SkillScope, Query()] = "personal",
) -> SkillCatalogItem:
    _require_scope(user, scope)
    try:
        skill = await SkillService.replace(db, user.id, skill_id, payload, scope=scope)
    except SkillError as exc:
        _raise_api_error(exc)
    await db.commit()
    return _payload(skill, scope)


@router.patch("/{skill_id}/enabled", response_model=SkillCatalogItem)
async def set_skill_enabled(
    skill_id: UUID,
    payload: SkillEnabledRequest,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
    scope: Annotated[SkillScope, Query()] = "personal",
) -> SkillCatalogItem:
    _require_scope(user, scope)
    try:
        skill = await SkillService.set_enabled(db, user.id, skill_id, payload.enabled, scope)
    except SkillError as exc:
        _raise_api_error(exc)
    await db.commit()
    return _payload(skill, scope)


@router.post("/{skill_id}/reset", response_model=SkillCatalogItem)
async def reset_skill(
    skill_id: UUID,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
    scope: Annotated[SkillScope, Query()] = "personal",
) -> SkillCatalogItem:
    _require_scope(user, scope)
    try:
        skill = await SkillService.reset(db, user.id, skill_id, scope)
    except SkillError as exc:
        _raise_api_error(exc)
    await db.commit()
    return _payload(skill, scope)


@router.delete("/{skill_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_skill(
    skill_id: UUID,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
    scope: Annotated[SkillScope, Query()] = "personal",
) -> None:
    _require_scope(user, scope)
    try:
        await SkillService.delete(db, user.id, skill_id, scope)
    except SkillError as exc:
        _raise_api_error(exc)
    await db.commit()
