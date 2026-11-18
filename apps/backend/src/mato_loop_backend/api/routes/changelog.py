from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.changelog import (
    ChangelogQuery,
    ChangelogResponse,
    get_changelog,
    get_installed_changelog,
)
from mato_loop_backend.db.session import get_db_session
from mato_loop_backend.security import require_user

router = APIRouter(prefix="/changelog", dependencies=[Depends(require_user)])


@router.get("", response_model=ChangelogResponse)
async def changelog(
    query: Annotated[ChangelogQuery, Query()], response: Response,
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> ChangelogResponse:
    response.headers["Cache-Control"] = "no-store"
    await db.rollback()
    return await get_changelog(query)


@router.get("/installed", response_model=ChangelogResponse)
async def installed_changelog(
    db: Annotated[AsyncSession, Depends(get_db_session)], response: Response,
) -> ChangelogResponse:
    response.headers["Cache-Control"] = "no-store"
    return await get_installed_changelog(db)
