"""Owner-bound repository discovery and persistent session worktree bindings."""

import re
from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.execution.client import (
    ExecutionClient,
    ExecutionClientError,
    ExecutionResult,
)
from mato_loop_backend.execution.manager import execution_agent_connections
from mato_loop_backend.execution.paths import absolute_path, within
from mato_loop_backend.models import ChatSession, ExecutionAgent, Project


def validate_git_remote(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    value = value.strip()
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._-]{0,199}", value):
        raise ValueError("Invalid Git remote name")
    return value


def validate_git_branch(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    value = value.strip()
    if (len(value) > 300 or value.startswith("-") or ".." in value or "@{" in value
            or re.search(r"[\s\x00-\x1f\x7f~^:?*\[\\]", value)
            or any(not part or part.startswith(".") or part.endswith((".", ".lock"))
                   for part in value.split("/"))):
        raise ValueError("Invalid Git branch name")
    return value


def validate_git_url(value: str) -> str:
    value = value.strip()
    if not value or len(value) > 4096 or any(char.isspace() or ord(char) < 32 for char in value):
        raise ValueError("Invalid Git remote address")
    parsed = urlsplit(value)
    if parsed.password or parsed.query or parsed.fragment or (parsed.scheme in {"http", "https"} and parsed.username):
        raise ValueError("Credentials must be configured on the computer")
    if parsed.scheme in {"http", "https", "ssh", "git"} and parsed.hostname:
        return value
    if "://" not in value and re.fullmatch(r"(?:[^@/:\\]+@)?[^/:\\]{2,}:.+", value) and parsed.scheme != "file":
        return value
    raise ValueError("A network remote address is required")


class RepositoryResult(ExecutionResult):
    repository: str | None = Field(max_length=4096)
    common_directory: str | None = Field(default=None, max_length=4096)
    remotes: list[str] = Field(max_length=100)
    default_remote: str | None = Field(max_length=200)
    default_branch: str | None = Field(max_length=300)
    branch: str | None = Field(max_length=300)
    remote_identities: dict[str, str] = Field(default_factory=dict, max_length=100)
    remote_urls: dict[str, str | None] = Field(default_factory=dict, max_length=100)
    git_directory: str | None = Field(default=None, max_length=4096)
    worktree_root: str | None = Field(default=None, max_length=4096)
    worktree_layout: Literal["short-session", "project-session"] = "project-session"
    target_has_commit: bool | None = None

    _repository_path = field_validator("repository", "common_directory", "git_directory", "worktree_root")(
        lambda value: absolute_path(value) if value is not None else None
    )


class WorktreeResult(ExecutionResult):
    repository: str
    path: str
    branch: str
    base_commit: str = Field(pattern=r"^[0-9a-f]{40,64}$")
    remote: str | None
    target_branch: str
    remote_identity: str | None = Field(pattern=r"^[0-9a-f]{64}$")
    common_directory: str
    git_directory: str

    _paths = field_validator("repository", "path", "common_directory", "git_directory")(
        lambda value: absolute_path(value) if value is not None else None
    )


class WorktreeRemovedResult(ExecutionResult):
    removed: bool


def resolve_git_target(
    session: ChatSession, project: Project | None, info: RepositoryResult,
) -> tuple[dict, str | None, str | None]:
    binding = session.coding_workspace or {}
    if (binding.get("executionAgentId") != str(session.preferred_execution_agent_id)
            or session.current_directory not in {binding.get("repository"), binding.get("path")}):
        binding = {}
    if binding.get("mode") == "worktree":
        return binding, binding["remote"], binding["targetBranch"]
    configured = (project and project.default_execution_agent_id == session.preferred_execution_agent_id
                  and project.default_current_directory == info.repository)
    return (
        binding,
        binding.get("remote") or (project.git_remote if configured else None) or info.default_remote,
        binding.get("targetBranch") or (project.git_target_branch if configured else None) or info.default_branch,
    )


async def coding_client(db: AsyncSession, user_id: UUID, agent_id: UUID | None) -> ExecutionClient:
    from mato_loop_backend.sessions.service import SessionServiceError

    if agent_id is None:
        raise SessionServiceError(409, "file.workspaceNotSelected")
    agent = await db.scalar(select(ExecutionAgent).where(
        ExecutionAgent.id == agent_id, ExecutionAgent.user_id == user_id,
        ExecutionAgent.revoked_at.is_(None),
    ))
    if agent is None:
        raise SessionServiceError(404, "executionAgent.notFound")
    operations = execution_agent_connections.snapshot(agent_id)
    if operations is None:
        raise SessionServiceError(409, "executionAgent.unavailable")
    return ExecutionClient(agent_id, operations)


async def inspect_repository(db, user_id, agent_id, directory, target_branch=None) -> RepositoryResult:
    from mato_loop_backend.sessions.service import (
        SessionServiceError,
        validate_execution_preferences,
    )

    directory, _ = await validate_execution_preferences(db, user_id, agent_id, directory)
    if directory is None:
        raise SessionServiceError(409, "file.workspaceNotSelected")
    client = await coding_client(db, user_id, agent_id)
    try:
        result = await client.request("git.repository", {"path": directory, **({"targetBranch": target_branch} if target_branch else {})}, RepositoryResult)
    except ExecutionClientError as exc:
        raise SessionServiceError(409, exc.code) from exc
    if result.repository is not None:
        if result.common_directory is None:
            raise SessionServiceError(502, "executionAgent.protocolError")
        await validate_execution_preferences(db, user_id, agent_id, result.common_directory)
    return result


async def prepare_session_worktree(db, session: ChatSession, project: Project | None) -> None:
    from mato_loop_backend.sessions.service import (
        SessionServiceError,
        execution_filesystem_policy,
        validate_execution_preferences,
    )

    if project is None:
        raise SessionServiceError(409, "git.configurationRequired")
    if (project.default_execution_agent_id != session.preferred_execution_agent_id
            or project.default_current_directory != session.current_directory):
        raise SessionServiceError(409, "git.projectWorkspaceRequired")
    client = await coding_client(db, session.user_id, session.preferred_execution_agent_id)
    agent = await db.get(ExecutionAgent, session.preferred_execution_agent_id)
    if agent is None:
        raise SessionServiceError(403, "permission.denied")
    info = await inspect_repository(db, session.user_id, session.preferred_execution_agent_id, session.current_directory)
    if info.repository is None:
        raise SessionServiceError(409, "git.notRepository")
    if info.worktree_root is None:
        raise SessionServiceError(409, "executionAgent.updateRequired")
    binding, remote, target = resolve_git_target(session, project, info)
    legacy_path = f"{info.worktree_root.rstrip('/')}/{project.id}/{session.id}"
    expected_paths = [legacy_path]
    if info.worktree_layout == "short-session":
        expected_paths.insert(0, f"{info.worktree_root.rstrip('/')}/{str(session.id)[:8]}")
    expected_paths = [path for path in expected_paths if execution_filesystem_policy(agent, path) != "deny"]
    if not expected_paths:
        raise SessionServiceError(403, "permission.denied")
    if target is None:
        raise SessionServiceError(409, "git.configurationRequired")
    identity = info.remote_identities.get(remote)
    if remote is not None and identity is None:
        raise SessionServiceError(409, "git.remoteRequired")
    if binding.get("remoteIdentity") and binding["remoteIdentity"] != identity:
        raise SessionServiceError(409, "git.worktreeChanged")
    try:
        result = await client.request("git.worktree", {
            "path": session.current_directory,
            "commonDirectory": info.common_directory,
            "ownerId": str(session.user_id), "projectId": str(project.id),
            "sessionId": str(session.id), "remote": remote,
            "targetBranch": target,
            "remoteIdentity": identity,
            **({"destinations": expected_paths} if info.worktree_layout == "short-session" else {}),
        }, WorktreeResult, timeout=300)
    except ExecutionClientError as exc:
        code = exc.code if exc.code.startswith(("git.", "executionAgent.")) else "git.worktreeFailed"
        raise SessionServiceError(409, code) from exc
    expected_branches = {f"matoloop/{session.user_id}/{session.id}", f"worktree-{str(session.id)[:8]}"}
    if (result.repository != session.current_directory or result.path not in expected_paths or result.branch not in expected_branches
            or result.remote != remote or result.target_branch != target
            or result.remote_identity != identity or result.common_directory != info.common_directory):
        raise SessionServiceError(502, "executionAgent.protocolError")
    directory, _ = await validate_execution_preferences(
        db, session.user_id, session.preferred_execution_agent_id, result.path
    )
    session.coding_workspace = {"mode": "worktree", "projectId": str(project.id), "executionAgentId": str(session.preferred_execution_agent_id),
                                **result.model_dump(by_alias=True)}
    session.current_directory = directory


async def remove_session_worktree(db, session: ChatSession, deleting_session_ids: list[UUID]) -> None:
    from mato_loop_backend.sessions.git_commands import check_workspace_admission
    from mato_loop_backend.sessions.service import (
        SessionServiceError,
        execution_filesystem_policy,
    )

    binding = session.coding_workspace or {}
    if binding.get("mode") != "worktree":
        return
    agent_id = UUID(binding["executionAgentId"])
    client = await coding_client(db, session.user_id, agent_id)
    agent = await db.get(ExecutionAgent, agent_id)
    if agent is None or execution_filesystem_policy(agent, binding["path"]) == "deny":
        raise SessionServiceError(403, "permission.denied")
    sessions = (await db.scalars(select(ChatSession).where(
        ChatSession.user_id == session.user_id, ChatSession.id.not_in(deleting_session_ids),
        ChatSession.preferred_execution_agent_id == agent_id,
    ))).all()
    if any(item.current_directory and within(item.current_directory, binding["path"]) for item in sessions):
        raise SessionServiceError(409, "git.worktreeInUse")
    info = await inspect_repository(db, session.user_id, agent_id, binding["repository"])
    if info.common_directory != binding["commonDirectory"]:
        raise SessionServiceError(409, "git.worktreeChanged")
    await check_workspace_admission(db, session.user_id, agent_id, binding["path"],
                                    {"name": "git_remote", "resources": [binding["path"], binding["repository"], binding["commonDirectory"]]})
    try:
        result = await client.request("git.worktree.remove", {
            "ownerId": str(session.user_id), "projectId": binding["projectId"], "sessionId": str(session.id),
            "path": binding["path"], "commonDirectory": binding["commonDirectory"],
        }, WorktreeRemovedResult, timeout=180)
    except ExecutionClientError as exc:
        raise SessionServiceError(409, exc.code) from exc
    if not result.removed:
        raise SessionServiceError(409, "git.worktreeRemoveFailed")
