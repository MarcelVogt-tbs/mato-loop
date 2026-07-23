from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.api.errors import ApiError
from mato_loop_backend.db.session import get_db_session
from mato_loop_backend.models import ChatSession, PermissionGrant, UserAccount
from mato_loop_backend.security import require_user
from mato_loop_backend.workspace import WorkspaceError, WorkspaceService

router = APIRouter(prefix="/workspace", dependencies=[Depends(require_user)])


class CreateDirectoryRequest(BaseModel):
    parent: str = "."
    name: str = Field(min_length=1, max_length=255)


@router.get("/entries")
async def list_entries(path: str = Query(default=".")) -> dict:
    try:
        return {"path": path, "entries": WorkspaceService().list_entries(path)}
    except WorkspaceError as exc:
        raise ApiError(400, exc.code) from exc


@router.post("/directories", status_code=status.HTTP_201_CREATED)
async def create_directory(payload: CreateDirectoryRequest) -> dict:
    try:
        return WorkspaceService().create_directory(payload.parent, payload.name)
    except WorkspaceError as exc:
        raise ApiError(400, exc.code) from exc
    except FileExistsError as exc:
        raise ApiError(409, "workspace.pathAlreadyExists") from exc
    except FileNotFoundError as exc:
        raise ApiError(400, "workspace.parentNotFound") from exc


@router.delete("/entries", status_code=status.HTTP_204_NO_CONTENT)
async def delete_entry(
    path: str,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> None:
    workspace = WorkspaceService()
    try:
        target = workspace.resolve(path)
    except WorkspaceError as exc:
        raise ApiError(400, exc.code) from exc
    sessions = list(
        (
            await db.scalars(
                select(ChatSession).where(
                    ChatSession.user_id == user.id,
                    ChatSession.working_directory.is_not(None),
                )
            )
        ).all()
    )
    for item in sessions:
        workdir = workspace.resolve(item.working_directory)
        if target == workdir or target in workdir.parents:
            raise ApiError(
                409,
                "workspace.pathInUse",
                params={"sessionTitle": item.title},
            )
    try:
        workspace.delete(path)
    except WorkspaceError as exc:
        raise ApiError(400, exc.code) from exc
    grants = list(
        (
            await db.scalars(
                select(PermissionGrant).where(PermissionGrant.resource_type == "path")
                .join(ChatSession, ChatSession.id == PermissionGrant.session_id)
                .where(ChatSession.user_id == user.id)
            )
        ).all()
    )
    for grant in grants:
        try:
            grant_path = workspace.resolve(grant.resource_scope)
            if target == grant_path or target in grant_path.parents:
                await db.delete(grant)
        except WorkspaceError:
            await db.delete(grant)
    await db.commit()
