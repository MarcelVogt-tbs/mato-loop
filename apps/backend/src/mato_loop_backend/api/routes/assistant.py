from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.api.errors import ApiError
from mato_loop_backend.assistant import AssistantService, ensure_personal_chat
from mato_loop_backend.automations import AutomationError
from mato_loop_backend.db.session import get_db_session
from mato_loop_backend.models import UserAccount
from mato_loop_backend.security import require_user
from mato_loop_backend.sessions.service import SessionServiceError
from mato_loop_backend.skills import SkillError

router = APIRouter(prefix="/assistant", dependencies=[Depends(require_user)])


def assistant_api_error(exc: AutomationError | SessionServiceError | SkillError) -> ApiError:
    statuses = {"http.conflict": 409, "session.archived": 409, "session.readOnly": 403,
                "automation.executionSessionMissing": 409, "automation.notFound": 409,
                "skill.notFound": 404}
    return ApiError(getattr(exc, "status_code", statuses.get(exc.code, 422)), exc.code,
                    field=getattr(exc, "field", None), params=getattr(exc, "params", None))


@router.get("")
async def get_assistant(
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict:
    return await AssistantService.get(db, user.id)


@router.post("/setup")
async def setup_assistant(
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict:
    try:
        result = await AssistantService.setup(db, user)
        await db.commit()
        return result
    except (AutomationError, SessionServiceError, SkillError) as exc:
        await db.rollback()
        raise assistant_api_error(exc) from exc


@router.post("/chat")
async def open_personal_chat(
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict:
    try:
        item = await ensure_personal_chat(db, user.id)
        await db.commit()
        return {"sessionId": str(item.id)}
    except (AutomationError, SessionServiceError) as exc:
        await db.rollback()
        raise assistant_api_error(exc) from exc
