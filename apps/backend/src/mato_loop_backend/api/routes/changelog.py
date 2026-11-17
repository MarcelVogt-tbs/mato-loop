from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response

from mato_loop_backend.changelog import ChangelogQuery, ChangelogResponse, get_changelog
from mato_loop_backend.security import require_user

router = APIRouter(prefix="/changelog", dependencies=[Depends(require_user)])


@router.get("", response_model=ChangelogResponse)
async def changelog(
    query: Annotated[ChangelogQuery, Query()], response: Response,
) -> ChangelogResponse:
    response.headers["Cache-Control"] = "no-store"
    return await get_changelog(query)
