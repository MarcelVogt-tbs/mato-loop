from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.api.errors import ApiError
from mato_loop_backend.db.session import get_db_session
from mato_loop_backend.models import ChatSession, UserAccount
from mato_loop_backend.security import require_user
from mato_loop_backend.sessions.commands import command_catalog
from mato_loop_backend.sessions.service import SessionServiceError, get_owned_session

router = APIRouter(prefix="/commands", dependencies=[Depends(require_user)])


class CommandCatalogRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: UUID | None = None
    project_id: UUID | None = None
    execution_agent_id: UUID | None = None
    current_directory: str | None = Field(default=None, max_length=4096)
    workspace_mode: Literal["local", "worktree"] = "local"


class CommandItem(BaseModel):
    name: str
    label: str
    description: str
    source: str
    icon: str
    disabled_reason: str | None = Field(alias="disabledReason")


class CommandCatalogResponse(BaseModel):
    items: list[CommandItem]


@router.post("/catalog", response_model=CommandCatalogResponse)
async def catalog(payload: CommandCatalogRequest, user: Annotated[UserAccount, Depends(require_user)],
                  db: Annotated[AsyncSession, Depends(get_db_session)]) -> dict:
    try:
        if payload.session_id:
            session = await get_owned_session(db, payload.session_id, user.id)
            if session is None:
                raise SessionServiceError(404, "session.notFound")
        else:
            session = ChatSession(user_id=user.id, project_id=payload.project_id,
                                  preferred_execution_agent_id=payload.execution_agent_id,
                                  current_directory=payload.current_directory, workspace_mode=payload.workspace_mode)
        return {"items": await command_catalog(db, session)}
    except SessionServiceError as exc:
        raise ApiError(exc.status_code, exc.code, field=exc.field, params=exc.params) from exc
