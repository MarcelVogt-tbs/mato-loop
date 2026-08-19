from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.api.errors import ApiError
from mato_loop_backend.db.session import get_db_session
from mato_loop_backend.models import ChatSession, Project, UserAccount
from mato_loop_backend.security import require_user
from mato_loop_backend.session_storage import SessionStorage, SessionStorageError

router = APIRouter(prefix="/projects", dependencies=[Depends(require_user)])
logger = logging.getLogger(__name__)


class ProjectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    name: str = Field(min_length=1, max_length=240)

    @field_validator("name", mode="before")
    @classmethod
    def normalize_name(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


class ProjectResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    name: str
    session_count: int = Field(alias="sessionCount")
    created_at: datetime = Field(alias="createdAt")
    updated_at: datetime = Field(alias="updatedAt")


class ProjectListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[ProjectResponse]


def _payload(project: Project, session_count: int) -> ProjectResponse:
    return ProjectResponse(
        id=project.id,
        name=project.name,
        sessionCount=session_count,
        createdAt=project.created_at,
        updatedAt=project.updated_at,
    )


async def _owned_project(
    db: AsyncSession, project_id: UUID, user_id: UUID, *, for_update: bool = False
) -> Project:
    statement = select(Project).where(Project.id == project_id, Project.user_id == user_id)
    if for_update:
        statement = statement.with_for_update()
    project = await db.scalar(statement)
    if project is None:
        raise ApiError(status.HTTP_404_NOT_FOUND, "project.notFound")
    return project


async def _session_count(db: AsyncSession, project: Project) -> int:
    count = await db.scalar(
        select(func.count(ChatSession.id)).where(
            ChatSession.project_id == project.id,
            ChatSession.user_id == project.user_id,
        )
    )
    return count or 0


@router.get("", response_model=ProjectListResponse)
async def list_projects(
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> ProjectListResponse:
    rows = (
        await db.execute(
            select(Project, func.count(ChatSession.id))
            .outerjoin(
                ChatSession,
                (ChatSession.project_id == Project.id)
                & (ChatSession.user_id == Project.user_id),
            )
            .where(Project.user_id == user.id)
            .group_by(Project.id)
            .order_by(Project.updated_at.desc(), Project.id.desc())
        )
    ).all()
    return ProjectListResponse(
        items=[_payload(project, session_count) for project, session_count in rows]
    )


@router.post("", response_model=ProjectResponse, status_code=status.HTTP_201_CREATED)
async def create_project(
    payload: ProjectRequest,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> ProjectResponse:
    project = Project(user_id=user.id, name=payload.name)
    db.add(project)
    await db.commit()
    return _payload(project, 0)


@router.patch("/{project_id}", response_model=ProjectResponse)
async def rename_project(
    project_id: UUID,
    payload: ProjectRequest,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> ProjectResponse:
    project = await _owned_project(db, project_id, user.id)
    session_count = await _session_count(db, project)
    project.name = payload.name
    project.updated_at = datetime.now(UTC)
    await db.commit()
    return _payload(project, session_count)


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(
    project_id: UUID,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> None:
    project = await _owned_project(db, project_id, user.id, for_update=True)
    if await _session_count(db, project):
        raise ApiError(status.HTTP_409_CONFLICT, "project.notEmpty")
    try:
        await asyncio.to_thread(SessionStorage().remove_project, project.id)
    except (OSError, SessionStorageError) as exc:
        logger.exception("Empty project storage could not be removed")
        raise ApiError(500, "session.storageUnavailable") from exc
    await db.delete(project)
    await db.commit()
