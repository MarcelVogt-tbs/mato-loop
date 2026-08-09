from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.api.errors import ApiError
from mato_loop_backend.db.session import get_db_session
from mato_loop_backend.models import UserAccount
from mato_loop_backend.security import require_user
from mato_loop_backend.settings import SkillDefinition
from mato_loop_backend.skills import SkillDraft, SkillError, SkillService

router = APIRouter(prefix="/skills", dependencies=[Depends(require_user)])

_ERROR_STATUS = {
    "skill.duplicate": status.HTTP_409_CONFLICT,
    "skill.limitReached": status.HTTP_409_CONFLICT,
    "skill.notFound": status.HTTP_404_NOT_FOUND,
}


class SkillListResponse(BaseModel):
    items: list[SkillDefinition]


def _raise_api_error(exc: SkillError) -> None:
    code = exc.code if exc.code in _ERROR_STATUS else "skill.draftInvalid"
    raise ApiError(_ERROR_STATUS.get(code, status.HTTP_422_UNPROCESSABLE_ENTITY), code) from exc


@router.get("", response_model=SkillListResponse)
async def list_skills(
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> SkillListResponse:
    return SkillListResponse(items=await SkillService.list(db, user.id))


@router.post("", response_model=SkillDefinition, status_code=status.HTTP_201_CREATED)
async def create_skill(
    payload: SkillDraft,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> SkillDefinition:
    try:
        skill = await SkillService.create(db, user.id, payload)
    except SkillError as exc:
        _raise_api_error(exc)
    await db.commit()
    return skill


@router.put("/{skill_id}", response_model=SkillDefinition)
async def replace_skill(
    skill_id: UUID,
    payload: SkillDraft,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> SkillDefinition:
    try:
        skill = await SkillService.replace(db, user.id, skill_id, payload)
    except SkillError as exc:
        _raise_api_error(exc)
    await db.commit()
    return skill


@router.delete("/{skill_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_skill(
    skill_id: UUID,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> None:
    try:
        await SkillService.delete(db, user.id, skill_id)
    except SkillError as exc:
        _raise_api_error(exc)
    await db.commit()
