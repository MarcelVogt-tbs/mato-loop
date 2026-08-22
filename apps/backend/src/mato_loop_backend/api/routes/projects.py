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
from mato_loop_backend.models import ChatSession, MessageAttachment, Project, UserAccount
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
    referenced_files = await db.scalar(
        select(func.count(MessageAttachment.id)).where(
            MessageAttachment.file_root_id == project.id
        )
    )
    if await _session_count(db, project) or referenced_files:
        raise ApiError(status.HTTP_409_CONFLICT, "project.notEmpty")
    storage = SessionStorage()
    try:
        trashed = await asyncio.to_thread(storage.trash_project, project.id)
    except (OSError, SessionStorageError) as exc:
        logger.exception("Empty project storage could not be removed")
        raise ApiError(500, "session.storageUnavailable") from exc

    async def restore_storage() -> None:
        if trashed is None:
            return
        try:
            await asyncio.to_thread(storage.restore_project, project.id, trashed)
        except Exception:
            logger.exception("Project storage could not be restored after database failure")

    commit_task: asyncio.Task[None] | None = None
    try:
        await db.delete(project)
        commit_task = asyncio.create_task(db.commit())
        await asyncio.shield(commit_task)
    except asyncio.CancelledError:
        if commit_task is None:
            await db.rollback()
            await restore_storage()
            raise
        try:
            await commit_task
        except BaseException:
            await db.rollback()
            await restore_storage()
        else:
            await asyncio.to_thread(storage.purge_trashed, trashed)
        raise
    except BaseException:
        await db.rollback()
        await restore_storage()
        raise
    try:
        await asyncio.to_thread(storage.purge_trashed, trashed)
    except Exception:
        logger.exception("Deleted project storage could not be removed from trash")
