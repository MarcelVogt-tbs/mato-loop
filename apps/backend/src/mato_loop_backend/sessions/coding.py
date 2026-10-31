"""Owner-bound repository discovery, worktree selection and session Git targets."""

import asyncio
import re
from urllib.parse import urlsplit
from uuid import UUID, uuid5

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
from mato_loop_backend.models import AgentRun, ChatSession, ExecutionAgent, Project


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


class WorktreeSession(ExecutionResult):
    id: UUID
    title: str
    project_id: UUID | None = None


class WorktreeStatusResult(ExecutionResult):
    repository: str | None
    dirty: bool | None = None


class WorktreeEntry(ExecutionResult):
    path: str = Field(max_length=4096)
    branch: str | None = Field(max_length=300)
    main: bool
    locked: bool
    dirty: bool | None = None
    sessions: list[WorktreeSession] = Field(default_factory=list)

    _path = field_validator("path")(absolute_path)


class RepositoryResult(ExecutionResult):
    repository: str | None = Field(max_length=4096)
    common_directory: str | None = Field(default=None, max_length=4096)
    remotes: list[str] = Field(max_length=100)
    default_remote: str | None = Field(max_length=200)
    default_branch: str | None = Field(max_length=300)
    branch: str | None = Field(max_length=300)
    local_branches: list[str] = Field(default_factory=list, max_length=1000)
    local_branches_truncated: bool = False
    remote_identities: dict[str, str] = Field(default_factory=dict, max_length=100)
    remote_urls: dict[str, str | None] = Field(default_factory=dict, max_length=100)
    git_directory: str | None = Field(default=None, max_length=4096)
    worktree_root: str | None = Field(default=None, max_length=4096)
    project_worktree_directory: str | None = Field(default=None, max_length=4096)
    target_has_commit: bool | None = None
    worktrees: list[WorktreeEntry] = Field(default_factory=list, max_length=1000)

    _repository_path = field_validator("repository", "common_directory", "git_directory", "worktree_root", "project_worktree_directory")(
        lambda value: absolute_path(value) if value is not None else None
    )


class WorktreeResult(ExecutionResult):
    repository: str
    path: str
    branch: str
    base_commit: str = Field(pattern=r"^[0-9a-f]{40,64}$")
    target_branch: str
    common_directory: str
    git_directory: str

    _paths = field_validator("repository", "path", "common_directory", "git_directory")(
        lambda value: absolute_path(value) if value is not None else None
    )


class WorktreeRemovedResult(ExecutionResult):
    removed: bool


def git_metadata_directory(binding: dict | None, agent_id: UUID | None, directory: str | None) -> str | None:
    binding = binding or {}
    if binding.get("executionAgentId") == str(agent_id) and directory and binding.get("path") and within(directory, binding["path"]):
        return binding.get("commonDirectory")
    return None


def resolve_git_target(
    session: ChatSession, project: Project | None, info: RepositoryResult,
) -> tuple[dict, str | None, str | None]:
    binding = session.coding_workspace or {}
    if (binding.get("executionAgentId") != str(session.preferred_execution_agent_id)
            or (info.repository not in {binding.get("repository"), binding.get("path")}
                and not (info.common_directory and info.common_directory == binding.get("commonDirectory")))):
        binding = {}
    configured = (project and project.default_execution_agent_id == session.preferred_execution_agent_id
                   and (project.default_current_directory == info.repository
                        or any(item.path == project.default_current_directory for item in info.worktrees)))
    return (
        binding,
        binding["remote"] if "remote" in binding else (project.git_remote if configured else None) or info.default_remote,
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


async def inspect_repository(db, user_id, agent_id, directory, target_branch=None, *, project=None, worktree_details=False) -> RepositoryResult:
    from mato_loop_backend.sessions.service import (
        SessionServiceError,
        validate_execution_preferences,
    )

    directory, _ = await validate_execution_preferences(db, user_id, agent_id, directory)
    if directory is None:
        raise SessionServiceError(409, "file.workspaceNotSelected")
    client = await coding_client(db, user_id, agent_id)
    try:
        result = await client.request("git.repository", {"path": directory, **({"targetBranch": target_branch} if target_branch else {}),
            **({"projectId": str(project.id)} if project else {})}, RepositoryResult)
    except ExecutionClientError as exc:
        raise SessionServiceError(409, exc.code) from exc
    if result.repository is not None:
        if result.common_directory is None:
            raise SessionServiceError(502, "executionAgent.protocolError")
        await validate_execution_preferences(db, user_id, agent_id, result.common_directory)
    if result.worktrees:
        from mato_loop_backend.sessions.service import execution_filesystem_policy

        agent = await db.get(ExecutionAgent, agent_id)
        result.worktrees = [item for item in result.worktrees if execution_filesystem_policy(agent, item.path) != "deny"]
    if worktree_details and result.worktrees:
        sessions = list(await db.scalars(select(ChatSession).where(
            ChatSession.user_id == user_id, ChatSession.preferred_execution_agent_id == agent_id,
            ChatSession.current_directory.is_not(None),
            ChatSession.session_type == "chat", ChatSession.parent_session_id.is_(None),
        ).order_by(ChatSession.updated_at.desc())))
        limit = asyncio.Semaphore(4)

        async def details(item):
            item.sessions = [WorktreeSession(id=session.id, title=session.title, projectId=session.project_id)
                             for session in sessions if within(session.current_directory, item.path)]
            async with limit:
                try:
                    status = await client.request("git.changes", {"path": item.path, "statusOnly": True}, WorktreeStatusResult, timeout=10)
                    item.dirty = status.dirty if status.repository == item.path else None
                except ExecutionClientError:
                    item.dirty = None

        await asyncio.gather(*(details(item) for item in result.worktrees))
    return result


async def select_session_workspace(db, session, project, directory=None, *, create=False, worktree_id=None):
    from mato_loop_backend.sessions.git_commands import check_workspace_admission
    from mato_loop_backend.sessions.service import (
        SessionServiceError,
        execution_filesystem_policy,
        validate_execution_preferences,
    )

    agent_id = session.preferred_execution_agent_id
    if project is None or project.user_id != session.user_id or (create and project.default_execution_agent_id != agent_id):
        raise SessionServiceError(409, "git.projectWorkspaceRequired")
    if await db.scalar(select(AgentRun.id).where(
        AgentRun.session_id == session.id, AgentRun.status.in_(("queued", "running", "waiting_approval", "waiting_input")),
    )):
        raise SessionServiceError(409, "session.runAlreadyActive")
    original = await inspect_repository(db, session.user_id, agent_id,
        project.default_current_directory if create else session.current_directory, **({"project": project} if create else {}))
    root = project.default_current_directory if create else next((item.path for item in original.worktrees if item.main), None)
    if not root or (create and original.repository != root):
        raise SessionServiceError(409, "git.repositoryRootRequired")
    directory = directory or root
    if (create or directory != root) and not (session.session_profile or {}).get("worktrees_enabled", False):
        raise SessionServiceError(409, "sessionTemplate.worktreesDisabled")
    previous, remote, target = resolve_git_target(session, project, original)
    if create:
        if not target or not original.worktree_root or not original.project_worktree_directory:
            raise SessionServiceError(409, "git.configurationRequired")
        if original.project_worktree_directory.rsplit("/", 1)[0] != original.worktree_root:
            raise SessionServiceError(502, "executionAgent.protocolError")
        await check_workspace_admission(db, session.user_id, agent_id, root,
            {"name": "git-worktree", "resources": [root, original.common_directory]})
        identity = str(worktree_id or uuid5(session.id, "worktree"))
        # Stable candidates let a repeated creation request recover its chosen destination.
        names = [identity[:8], *(uuid5(UUID(identity), str(attempt)).hex[:8] for attempt in range(1, 10))]
        paths = [f"{original.project_worktree_directory}/{name}" for name in names]
        agent = await db.get(ExecutionAgent, agent_id)
        paths = [path for path in paths if execution_filesystem_policy(agent, path) != "deny"]
        if not paths:
            raise SessionServiceError(403, "permission.denied")
        client = await coding_client(db, session.user_id, agent_id)
        try:
            created = await client.request("git.worktree", {
                "path": root, "commonDirectory": original.common_directory,
                "ownerId": str(session.user_id), "projectId": str(project.id), "sessionId": identity,
                "targetBranch": target, "destinations": paths,
            }, WorktreeResult, timeout=300)
        except ExecutionClientError as exc:
            raise SessionServiceError(409, exc.code) from exc
        if (created.path not in paths or created.repository != root or created.common_directory != original.common_directory
                or created.target_branch != target or created.branch != f"worktree-{created.path.rsplit('/', 1)[-1]}"):
            raise SessionServiceError(502, "executionAgent.protocolError")
        directory = created.path
        info = original
    else:
        if directory != root and not any(item.path == directory for item in original.worktrees):
            raise SessionServiceError(409, "git.worktreeChanged")
        info = original if directory == original.repository else await inspect_repository(db, session.user_id, agent_id, directory)
        if info.repository != directory or info.common_directory != original.common_directory:
            raise SessionServiceError(409, "git.worktreeChanged")
    normalized, directory_policy = await validate_execution_preferences(db, session.user_id, agent_id, directory)
    if normalized != directory:
        raise SessionServiceError(409, "git.worktreeChanged")
    session.current_directory = directory
    session.workspace_mode = "local" if directory == root else "worktree"
    session.coding_workspace = {
        "mode": session.workspace_mode, "executionAgentId": str(agent_id), "repository": root,
        "path": directory, "targetBranch": target, "remote": remote, "commonDirectory": info.common_directory,
        "remoteIdentity": previous.get("remoteIdentity") if previous.get("remote") == remote else info.remote_identities.get(remote),
    }
    return directory_policy
