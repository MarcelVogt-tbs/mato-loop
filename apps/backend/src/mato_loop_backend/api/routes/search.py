from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.db.session import get_db_session
from mato_loop_backend.models import UserAccount
from mato_loop_backend.search import GlobalSearchService
from mato_loop_backend.security import require_user

router = APIRouter(prefix="/search", dependencies=[Depends(require_user)])


class SearchProjectResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    name: str
    session_count: int = Field(alias="sessionCount")


class SearchSessionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    title: str
    project_id: UUID | None = Field(alias="projectId")
    project_name: str | None = Field(alias="projectName")
    match_type: Literal["title", "message"] = Field(alias="matchType")
    message_id: UUID | None = Field(alias="messageId")
    message_role: Literal["user", "assistant"] | None = Field(alias="messageRole")
    message_excerpt: str | None = Field(alias="messageExcerpt")
    matched_at: datetime | None = Field(alias="matchedAt")


class GlobalSearchResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    projects: list[SearchProjectResponse]
    sessions: list[SearchSessionResponse]
    strategy: Literal["hybrid", "lexical"]


@router.get("", response_model=GlobalSearchResponse)
async def global_search(
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
    query: Annotated[str, Query(alias="q", min_length=2, max_length=500)],
    limit: Annotated[int, Query(ge=1, le=20)] = 10,
) -> GlobalSearchResponse:
    result = await GlobalSearchService.search(
        db,
        owner_id=user.id,
        query=query,
        limit=limit,
    )
    return GlobalSearchResponse(
        projects=[
            SearchProjectResponse(
                id=item.id,
                name=item.name,
                sessionCount=item.session_count,
            )
            for item in result.projects
        ],
        sessions=[
            SearchSessionResponse(
                id=item.id,
                title=item.title,
                projectId=item.project_id,
                projectName=item.project_name,
                matchType=item.match_type,
                messageId=item.message_id,
                messageRole=item.message_role,
                messageExcerpt=item.message_excerpt,
                matchedAt=item.matched_at,
            )
            for item in result.sessions
        ],
        strategy=result.strategy.value,
    )
