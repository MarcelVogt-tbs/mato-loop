from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.api.errors import ApiError
from mato_loop_backend.db.session import get_db_session
from mato_loop_backend.models import Project, UserAccount
from mato_loop_backend.security import require_user
from mato_loop_backend.sessions.coding import (
    RepositoryResult,
    inspect_repository,
    validate_git_branch,
    validate_git_remote,
)
from mato_loop_backend.sessions.git_commands import check_workspace_admission
from mato_loop_backend.sessions.service import SessionServiceError, get_owned_session

router = APIRouter(prefix="/coding", dependencies=[Depends(require_user)])


class RepositoryRequest(BaseModel):
    execution_agent_id: UUID
    current_directory: str = Field(min_length=1, max_length=4096)
    target_branch: str | None = Field(default=None, max_length=300)

    _target = field_validator("target_branch")(validate_git_branch)


@router.post("/repository", response_model=RepositoryResult)
async def repository(
    payload: RepositoryRequest,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> RepositoryResult:
    try:
        return await inspect_repository(db, user.id, payload.execution_agent_id, payload.current_directory, payload.target_branch)
    except SessionServiceError as exc:
        raise ApiError(exc.status_code, exc.code, field=exc.field, params=exc.params) from exc


class GitTargetRequest(BaseModel):
    remote: str = Field(min_length=1, max_length=200)
    remote_identity: str = Field(pattern=r"^[0-9a-f]{64}$")

    _remote = field_validator("remote")(validate_git_remote)


@router.put("/sessions/{session_id}/target")
async def set_target(
    session_id: UUID,
    payload: GitTargetRequest,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict:
    try:
        await db.execute(select(UserAccount.id).where(UserAccount.id == user.id).with_for_update(key_share=True))
        session = await get_owned_session(db, session_id, user.id, for_update=True)
        if session is None:
            raise SessionServiceError(404, "session.notFound")
        from mato_loop_backend.sessions.archive import is_session_archived

        if await is_session_archived(db, session):
            raise SessionServiceError(409, "session.archived")
        info = await inspect_repository(db, user.id, session.preferred_execution_agent_id, session.current_directory)
        if info.repository is None:
            raise SessionServiceError(409, "git.notRepository")
        if payload.remote not in info.remotes:
            raise SessionServiceError(409, "git.remoteRequired")
        if info.remote_identities.get(payload.remote) != payload.remote_identity:
            raise SessionServiceError(409, "git.worktreeChanged")
        binding = dict(session.coding_workspace or {})
        if binding.get("mode") == "worktree":
            if (binding.get("commonDirectory") != info.common_directory
                    or binding.get("executionAgentId") != str(session.preferred_execution_agent_id)
                    or info.repository not in {binding["repository"], binding["path"]}
                    or (session.workspace_mode == "worktree" and info.branch != binding["branch"])):
                raise SessionServiceError(409, "git.worktreeChanged")
        resources = sorted({path for path in (info.repository, info.common_directory, binding.get("repository")) if path})
        await check_workspace_admission(db, user.id, session.preferred_execution_agent_id, session.current_directory,
                                        {"name": "git_remote", "resources": resources})
        project = await db.scalar(select(Project).where(Project.id == session.project_id, Project.user_id == user.id)) if session.project_id else None
        configured = (project is not None and project.default_execution_agent_id == session.preferred_execution_agent_id
                      and project.default_current_directory == binding.get("repository", info.repository))
        if configured:
            project.git_remote = payload.remote
        if not binding:
            binding = {"mode": "local", "path": info.repository, "repository": info.repository,
                       "executionAgentId": str(session.preferred_execution_agent_id)}
        binding.update(remote=payload.remote, remoteIdentity=info.remote_identities[payload.remote],
                       targetBranch=binding.get("targetBranch") or (project.git_target_branch if configured else None) or info.default_branch,
                       commonDirectory=info.common_directory)
        session.coding_workspace = binding
        await db.commit()
        return {"codingWorkspace": binding, "projectId": str(project.id) if configured else None}
    except SessionServiceError as exc:
        raise ApiError(exc.status_code, exc.code, field=exc.field, params=exc.params) from exc
