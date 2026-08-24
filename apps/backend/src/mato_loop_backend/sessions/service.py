from __future__ import annotations

import asyncio
import base64
import binascii
import json
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any, Literal
from uuid import UUID, uuid4

from sqlalchemy import (
    and_,
    case,
    cast,
    func,
    or_,
    select,
    update,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from mato_loop_backend.agent import AgentRuntime
from mato_loop_backend.agent.permissions import execution_directory_scope
from mato_loop_backend.agent.runtime import AgentRunError
from mato_loop_backend.attachments import AttachmentInput
from mato_loop_backend.automations import automation_impact_token
from mato_loop_backend.chat_history import ChatHistoryService
from mato_loop_backend.execution import (
    DirectoryResult,
    ExecutionClient,
    ExecutionClientError,
    execution_agent_connections,
)
from mato_loop_backend.models import (
    AgentRun,
    Automation,
    AutomationRun,
    ChatSession,
    ExecutionAgent,
    Message,
    MessageAttachment,
    PermissionGrant,
    Project,
    TitleGenerationStatus,
)
from mato_loop_backend.session_storage import (
    SessionMoveReceipt,
    SessionStorageError,
)
from mato_loop_backend.settings import (
    AiSettings,
    InstanceLimits,
    SettingsStore,
    load_effective_ai,
)
from mato_loop_backend.settings.models import ModelAvailability, ModelCapability
from mato_loop_backend.workspace import RootFileError, RootFileService

from .serialization import rebind_session_file_metadata

logger = logging.getLogger(__name__)
VISIBLE_MESSAGE_ROLES = ("user", "assistant")
ACTIVE_RUN_STATUSES = ("queued", "running", "waiting_approval", "waiting_input")

PayloadSerializer = Callable[..., Awaitable[dict[str, Any]]]
MessageSerializer = Callable[..., dict[str, Any]]


class SessionServiceError(Exception):
    def __init__(
        self,
        status_code: int,
        code: str,
        *,
        field: str | None = None,
        params: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(code)
        self.status_code = status_code
        self.code = code
        self.field = field
        self.params = params


def model_available(ai: AiSettings, model_id: UUID | None) -> bool:
    if not ai.enabled or model_id is None:
        return False
    model = next(
        (
            item
            for item in ai.models
            if item.id == model_id
            and item.enabled
            and item.availability != ModelAvailability.MISSING
            and ModelCapability.CHAT in item.capabilities
        ),
        None,
    )
    if model is None:
        return False
    provider = next(
        (item for item in ai.providers if item.id == model.provider_id and item.enabled), None
    )
    if provider is None:
        return False
    return provider.adapter_type != "openai-codex" or provider.credential_id is not None


def model_supports_tools(ai: AiSettings, model_id: UUID | None) -> bool:
    return any(
        item.id == model_id
        and ModelCapability.CHAT in item.capabilities
        and item.supports_tools
        for item in ai.models
    )


def model_for_session(ai: AiSettings, model_id: UUID | None):
    return next(
        (
            item
            for item in ai.models
            if item.id == model_id and ModelCapability.CHAT in item.capabilities
        ),
        None,
    )


def provider_for_session(ai: AiSettings, model):
    if model is None:
        return None
    return next((item for item in ai.providers if item.id == model.provider_id), None)


async def execution_home(
    db: AsyncSession, agent: ExecutionAgent
) -> tuple[UUID | None, str | None, str | None, str | None]:
    if (
        agent.home_directory is None
        or agent.home_root_id is None
        or agent.home_root_revision is None
    ):
        return None, None, None, None
    relative = agent.home_relative_directory
    snapshot = execution_agent_connections.snapshot(agent.id)
    if snapshot is not None and "filesystem.resolve" in snapshot[0]:
        try:
            result = await ExecutionClient(agent.id, None, None, None, snapshot[0]).request(
                "filesystem.resolve", {"path": agent.home_directory}, DirectoryResult
            )
        except ExecutionClientError:
            pass
        else:
            assert isinstance(result, DirectoryResult)
            if (
                result.path == agent.home_directory
                and result.root_id == agent.home_root_id
                and result.root_revision == agent.home_root_revision
            ):
                relative = result.relative_path
                agent.home_relative_directory = relative
                await db.flush()
    if relative is None:
        return None, None, None, None
    return agent.home_root_id, agent.home_root_revision, agent.home_directory, relative


async def add_directory_grant(
    db: AsyncSession,
    session_id: UUID,
    agent_id: UUID | None,
    root_id: UUID | None,
    root_revision: str | None,
    path: str | None,
) -> None:
    if any(value is None for value in (agent_id, root_id, root_revision, path)):
        return
    assert agent_id is not None
    assert root_id is not None
    assert root_revision is not None
    assert path is not None
    scope = execution_directory_scope(agent_id, path, root_id, root_revision)
    existing = await db.scalar(
        select(PermissionGrant.id).where(
            PermissionGrant.session_id == session_id,
            PermissionGrant.capability == "execution.directory",
            PermissionGrant.resource_type == "execution_directory",
            PermissionGrant.resource_scope == scope,
        )
    )
    if existing is None:
        db.add(
            PermissionGrant(
                session_id=session_id,
                capability="execution.directory",
                resource_type="execution_directory",
                resource_scope=scope,
            )
        )


async def validate_execution_preferences(
    db: AsyncSession,
    user_id: UUID,
    agent_id: UUID | None,
    root_id: UUID | None,
    working_directory: str | None,
) -> tuple[str | None, str | None, UUID | None, str | None]:
    if agent_id is None:
        if root_id is not None:
            raise SessionServiceError(400, "validation.invalid", field="preferredRootId")
        if working_directory is not None:
            raise SessionServiceError(
                400, "session.invalidWorkingDirectory", field="workingDirectory"
            )
        return None, None, None, None
    agent = await db.scalar(
        select(ExecutionAgent)
        .options(selectinload(ExecutionAgent.roots))
        .where(ExecutionAgent.id == agent_id, ExecutionAgent.user_id == user_id)
    )
    if agent is None:
        raise SessionServiceError(
            404, "executionAgent.notFound", field="preferredExecutionAgentId"
        )
    if agent.revoked_at is not None:
        raise SessionServiceError(
            409, "executionAgent.revoked", field="preferredExecutionAgentId"
        )
    home_root_id, home_revision, home_directory, home_relative = await execution_home(db, agent)
    if root_id is None and working_directory is None:
        return home_relative, home_directory, home_root_id, home_revision
    if root_id is not None and not any(root.id == root_id for root in agent.roots):
        raise SessionServiceError(400, "validation.invalid", field="preferredRootId")
    if working_directory is None and root_id == home_root_id:
        return home_relative, home_directory, home_root_id, home_revision
    if working_directory is None:
        return None, None, root_id, next(
            (root.revision for root in agent.roots if root.id == root_id), None
        )
    if root_id is None:
        raise SessionServiceError(
            400, "session.invalidWorkingDirectory", field="workingDirectory"
        )
    try:
        normalized = RootFileService(UUID(int=0)).normalize_relative_path(working_directory)
    except RootFileError as exc:
        raise SessionServiceError(
            400, "session.invalidWorkingDirectory", field="workingDirectory"
        ) from exc
    if normalized != working_directory:
        raise SessionServiceError(
            400, "session.invalidWorkingDirectory", field="workingDirectory"
        )
    revision = next(root.revision for root in agent.roots if root.id == root_id)
    current_directory = (
        home_directory if root_id == home_root_id and normalized == home_relative else None
    )
    snapshot = execution_agent_connections.snapshot(agent.id)
    if current_directory is None and snapshot is not None and "workspace.resolve" in snapshot[0]:
        try:
            result = await ExecutionClient(
                agent.id, root_id, revision, ".", snapshot[0]
            ).request("workspace.resolve", {"path": normalized}, DirectoryResult)
        except ExecutionClientError as exc:
            raise SessionServiceError(
                400, "session.invalidWorkingDirectory", field="workingDirectory"
            ) from exc
        assert isinstance(result, DirectoryResult)
        if (
            result.root_id != root_id
            or result.root_revision != revision
            or result.relative_path != normalized
        ):
            raise SessionServiceError(
                502, "executionAgent.protocolError", field="workingDirectory"
            )
        current_directory = result.path
    return normalized, current_directory, root_id, revision


async def workspace_context(db: AsyncSession, item: ChatSession) -> dict[str, Any]:
    agent_id = item.preferred_execution_agent_id
    root_id = item.preferred_root_id
    context = {
        "executionAgentId": str(agent_id) if agent_id else None,
        "rootId": str(root_id) if root_id else None,
        "connected": False,
        "operations": [],
    }
    if agent_id is None:
        return context
    agent = await db.scalar(
        select(ExecutionAgent)
        .options(selectinload(ExecutionAgent.roots))
        .where(
            ExecutionAgent.id == agent_id,
            ExecutionAgent.user_id == item.user_id,
            ExecutionAgent.revoked_at.is_(None),
        )
    )
    snapshot = execution_agent_connections.snapshot(agent_id)
    if agent is None or snapshot is None:
        return context
    operations = snapshot[0]
    if root_id is not None:
        root = next((root for root in agent.roots if root.id == root_id), None)
        live_root = next((root for root in snapshot[1] if root.id == root_id), None)
        if root is None or live_root is None or live_root.revision != root.revision:
            operations = operations & {"system.info", "filesystem.resolve"}
    else:
        operations = operations & {"system.info", "filesystem.resolve"}
    return {**context, "connected": True, "operations": sorted(operations)}


async def owned_session(
    db: AsyncSession,
    session_id: UUID,
    user_id: UUID,
    *,
    for_update: bool = False,
) -> ChatSession | None:
    if not for_update:
        item = await db.get(ChatSession, session_id)
        return (
            item
            if item is not None
            and item.user_id == user_id
            and item.session_type != "automation"
            else None
        )
    statement = select(ChatSession).where(
        ChatSession.id == session_id,
        ChatSession.user_id == user_id,
        ChatSession.session_type == "chat",
    )
    return await db.scalar(statement.with_for_update())


def has_unread_messages(
    item: ChatSession, last_assistant_message_at: datetime | None
) -> bool:
    return bool(
        last_assistant_message_at
        and (item.last_read_at is None or last_assistant_message_at > item.last_read_at)
    )


async def session_detail_payload(
    db: AsyncSession,
    item: ChatSession,
    *,
    session_serializer: PayloadSerializer,
    message_serializer: MessageSerializer,
) -> dict[str, Any]:
    messages = list(
        (
            await db.scalars(
                select(Message)
                .where(Message.session_id == item.id)
                .options(selectinload(Message.attachments))
                .order_by(Message.created_at, Message.id)
            )
        ).all()
    )
    active_run = await db.scalar(
        select(AgentRun)
        .where(AgentRun.session_id == item.id, AgentRun.status.in_(ACTIVE_RUN_STATUSES))
        .order_by(AgentRun.created_at.desc())
    )
    retry_message_id = None
    if active_run is None and messages:
        retry_source = await ChatHistoryService.retry_source(
            db,
            session_id=item.id,
            messages=messages,
            message_id=messages[-1].id,
        )
        if retry_source is not None:
            retry_message_id = messages[-1].id
    last_message_at = max(
        (
            message.created_at
            for message in messages
            if message.role in VISIBLE_MESSAGE_ROLES and message.created_at
        ),
        default=None,
    )
    last_assistant_message_at = max(
        (
            message.created_at
            for message in messages
            if message.role == "assistant" and message.created_at
        ),
        default=None,
    )
    return {
        **(
            await session_serializer(
                db,
                item,
                last_message_at=last_message_at,
                has_unread_messages=has_unread_messages(item, last_assistant_message_at),
            )
        ),
        "messages": [
            message_serializer(message, retry_available=message.id == retry_message_id)
            for message in messages
        ],
        "activeRun": (
            {
                "id": str(active_run.id),
                "status": active_run.status,
                "modelName": active_run.model_name,
                "createdAt": active_run.created_at.isoformat() if active_run.created_at else None,
            }
            if active_run
            else None
        ),
    }


async def latest_user_message(db: AsyncSession, session_id: UUID) -> Message:
    message = await db.scalar(
        select(Message)
        .where(Message.session_id == session_id, Message.role == "user")
        .options(selectinload(Message.attachments))
        .order_by(Message.created_at.desc(), Message.id.desc())
        .limit(1)
    )
    if message is None:
        raise RuntimeError("Persisted user message is missing after agent start")
    return message


async def duplicate_submission_response(
    db: AsyncSession,
    run_id: UUID | None,
    user_id: UUID,
    *,
    requested_session_id: UUID | None = None,
    session_serializer: PayloadSerializer,
    message_serializer: MessageSerializer,
) -> dict[str, Any] | None:
    if run_id is None:
        return None
    run = await db.scalar(
        select(AgentRun)
        .join(ChatSession, ChatSession.id == AgentRun.session_id)
        .where(
            AgentRun.id == run_id,
            ChatSession.user_id == user_id,
            ChatSession.session_type == "chat",
        )
    )
    if run is None:
        return None
    if requested_session_id is not None and run.session_id != requested_session_id:
        raise SessionServiceError(409, "http.conflict")
    item = await db.get(ChatSession, run.session_id)
    message = await db.scalar(
        select(Message)
        .where(
            Message.id == run.input_message_id,
            Message.session_id == run.session_id,
            Message.role == "user",
        )
        .options(selectinload(Message.attachments))
    )
    if item is None or message is None:
        raise RuntimeError("Persisted agent run input is missing")
    if run.status == "queued":
        AgentRuntime._schedule_run(run)
    response = {
        "runId": str(run.id),
        "modelName": run.model_name,
        "message": message_serializer(message),
    }
    if requested_session_id is not None:
        return response
    return {
        "session": await session_serializer(db, item, last_message_at=message.created_at),
        **response,
    }


def _encode_session_cursor(
    user_id: UUID,
    scope: str,
    pinned: bool,
    activity_at: datetime,
    session_id: UUID,
) -> str:
    value = json.dumps(
        [str(user_id), scope, pinned, activity_at.isoformat(), str(session_id)],
        separators=(",", ":"),
    ).encode()
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()


def _decode_session_cursor(
    value: str, user_id: UUID, scope: str
) -> tuple[bool, datetime, UUID]:
    try:
        decoded = base64.b64decode(
            value + "=" * (-len(value) % 4), altchars=b"-_", validate=True
        )
        payload = json.loads(decoded)
        if not (
            isinstance(payload, list)
            and len(payload) == 5
            and all(
                isinstance(item, str)
                for item in (payload[0], payload[1], payload[3], payload[4])
            )
            and payload[1] == scope
            and type(payload[2]) is bool
            and UUID(payload[0]) == user_id
        ):
            raise ValueError
        activity_at = datetime.fromisoformat(payload[3])
        if activity_at.tzinfo is None:
            raise ValueError
        return payload[2], activity_at, UUID(payload[4])
    except (binascii.Error, UnicodeDecodeError, ValueError, TypeError) as exc:
        raise SessionServiceError(400, "validation.invalid", field="cursor") from exc


async def list_sessions(
    db: AsyncSession,
    user_id: UUID,
    *,
    page_size: int,
    cursor: str | None,
    project_id: UUID | None,
    standalone: bool,
    session_serializer: PayloadSerializer,
) -> dict[str, Any]:
    if project_id is not None and standalone:
        raise SessionServiceError(400, "validation.invalid", field="projectId")
    if project_id is not None:
        owned_project = await db.scalar(
            select(Project.id).where(Project.id == project_id, Project.user_id == user_id)
        )
        if owned_project is None:
            raise SessionServiceError(404, "project.notFound", field="projectId")
    scope = str(project_id) if project_id is not None else "standalone" if standalone else "all"
    active_run_status = (
        select(AgentRun.status)
        .where(
            AgentRun.session_id == ChatSession.id,
            AgentRun.status.in_(ACTIVE_RUN_STATUSES),
        )
        .order_by(AgentRun.created_at.desc(), AgentRun.id.desc())
        .limit(1)
        .correlate(ChatSession)
        .scalar_subquery()
    )
    latest_messages = (
        select(
            Message.session_id.label("session_id"),
            func.max(Message.created_at).label("last_message_at"),
            func.max(Message.created_at)
            .filter(Message.role == "assistant")
            .label("last_assistant_message_at"),
        )
        .join(ChatSession, ChatSession.id == Message.session_id)
        .where(
            ChatSession.user_id == user_id,
            Message.role.in_(VISIBLE_MESSAGE_ROLES),
        )
        .group_by(Message.session_id)
        .subquery()
    )
    activity_at = func.coalesce(latest_messages.c.last_message_at, ChatSession.created_at)
    statement = (
        select(
            ChatSession,
            latest_messages.c.last_message_at,
            latest_messages.c.last_assistant_message_at,
            active_run_status.label("active_run_status"),
            activity_at.label("activity_at"),
        )
        .outerjoin(latest_messages, latest_messages.c.session_id == ChatSession.id)
        .where(ChatSession.user_id == user_id, ChatSession.session_type == "chat")
    )
    if project_id is not None:
        statement = statement.where(ChatSession.project_id == project_id)
    elif standalone:
        statement = statement.where(ChatSession.project_id.is_(None))
    if cursor is not None:
        cursor_pinned, cursor_activity_at, cursor_id = _decode_session_cursor(
            cursor, user_id, scope
        )
        activity_tail = or_(
            activity_at < cursor_activity_at,
            and_(activity_at == cursor_activity_at, ChatSession.id < cursor_id),
        )
        statement = statement.where(
            or_(
                ChatSession.is_pinned.is_(False),
                and_(ChatSession.is_pinned.is_(True), activity_tail),
            )
            if cursor_pinned
            else and_(ChatSession.is_pinned.is_(False), activity_tail)
        )
    rows = (
        await db.execute(
            statement.order_by(
                ChatSession.is_pinned.desc(),
                activity_at.desc(),
                ChatSession.id.desc(),
            ).limit(page_size + 1)
        )
    ).all()
    page = rows[:page_size]
    items = [
        {
            **(
                await session_serializer(
                    db,
                    item,
                    last_message_at=last_message_at,
                    has_unread_messages=has_unread_messages(
                        item, last_assistant_message_at
                    ),
                )
            ),
            "activeRunStatus": active_status,
        }
        for item, last_message_at, last_assistant_message_at, active_status, _activity_at in page
    ]
    next_cursor = None
    if len(rows) > page_size:
        item, _last_message_at, _last_assistant_message_at, _active_status, item_activity_at = page[-1]
        next_cursor = _encode_session_cursor(
            user_id, scope, bool(item.is_pinned), item_activity_at, item.id
        )
    return {"items": items, "nextCursor": next_cursor}


async def create_session(
    payload,
    user,
    db: AsyncSession,
    *,
    attachments: list[AttachmentInput] | None,
    storage_factory,
    session_serializer: PayloadSerializer,
    message_serializer: MessageSerializer,
    duplicate_submission,
) -> dict[str, Any]:
    duplicate = await duplicate_submission(db, payload.run_id, user.id)
    if duplicate is not None:
        return duplicate
    content = payload.content.strip()
    if not content and not attachments:
        raise SessionServiceError(422, "validation.required", field="content")
    if payload.project_id is not None:
        project = await db.scalar(
            select(Project.id)
            .where(Project.id == payload.project_id, Project.user_id == user.id)
            .with_for_update()
        )
        if project is None:
            raise SessionServiceError(404, "project.notFound", field="projectId")
    ai = await load_effective_ai(db, user.id)
    model_id = payload.model_id or ai.default_model_id
    if not model_available(ai, model_id):
        raise SessionServiceError(409, "session.modelUnavailable", field="modelId")
    if not model_supports_tools(ai, model_id):
        raise SessionServiceError(409, "session.modelToolsUnavailable", field="modelId")
    working_directory, current_directory, root_id, root_revision = (
        await validate_execution_preferences(
            db,
            user.id,
            payload.preferred_execution_agent_id,
            payload.preferred_root_id,
            payload.working_directory,
        )
    )
    item = ChatSession(
        id=uuid4(),
        user_id=user.id,
        project_id=payload.project_id,
        title="Neue Session",
        title_generation_status=TitleGenerationStatus.PENDING.value,
        model_config_id=model_id,
        reasoning_effort=payload.reasoning_effort.value if payload.reasoning_effort else None,
        memory_mode=payload.memory_mode.value,
        web_search_policy=payload.web_search_policy.value,
        preferred_execution_agent_id=payload.preferred_execution_agent_id,
        preferred_root_id=root_id,
        working_directory=working_directory,
        current_directory=current_directory,
        control_center=payload.control_center.model_dump(mode="json"),
    )
    db.add(item)
    item_id = item.id
    item_project_id = item.project_id
    await add_directory_grant(
        db,
        item.id,
        payload.preferred_execution_agent_id,
        root_id,
        root_revision,
        current_directory,
    )
    await db.flush()
    session_storage = storage_factory()
    try:
        await asyncio.to_thread(session_storage.provision, item_id, item_project_id)
        run = await AgentRuntime.start(
            db,
            item,
            content,
            run_id=payload.run_id,
            attachments=attachments,
            active_file=active_file_payload(payload.active_file, item),
        )
    except (OSError, SessionStorageError) as exc:
        await db.rollback()
        logger.exception("Session storage could not be provisioned")
        raise SessionServiceError(500, "session.storageUnavailable") from exc
    except AgentRunError as exc:
        await db.rollback()
        try:
            await asyncio.to_thread(
                session_storage.remove_session, item_id, item_project_id
            )
        except Exception:
            logger.exception("Unpersisted session storage could not be removed")
        raise SessionServiceError(409, exc.code, params=exc.params) from exc
    except IntegrityError as exc:
        await db.rollback()
        try:
            await asyncio.to_thread(
                session_storage.remove_session, item_id, item_project_id
            )
        except Exception:
            logger.exception("Unpersisted session storage could not be removed")
        duplicate = await duplicate_submission(db, payload.run_id, user.id)
        if duplicate is not None:
            return duplicate
        raise SessionServiceError(409, "http.conflict") from exc
    except Exception:
        await db.rollback()
        raise
    await db.refresh(item)
    user_message = await latest_user_message(db, item.id)
    return {
        "session": await session_serializer(
            db, item, last_message_at=user_message.created_at
        ),
        "message": message_serializer(user_message),
        "runId": str(run.id),
        "modelName": run.model_name,
    }


def active_file_payload(active_file, session: ChatSession) -> dict[str, Any] | None:
    if active_file is None:
        return None
    try:
        identity = UUID(active_file.root_identity)
        path = RootFileService(UUID(int=0)).normalize_relative_path(active_file.path)
    except (RootFileError, ValueError) as exc:
        raise SessionServiceError(422, "validation.invalid", field="activeFile") from exc
    expected_identity = (
        session.project_id or session.id
        if active_file.root == "session"
        else session.preferred_root_id
    )
    if (
        str(identity) != active_file.root_identity
        or identity != expected_identity
        or path != active_file.path
        or path == "."
    ):
        raise SessionServiceError(422, "validation.invalid", field="activeFile")
    return {
        "root": active_file.root,
        "rootIdentity": str(identity),
        "path": path,
        "sha256": active_file.sha256,
        "dirty": active_file.dirty,
    }


async def send_message(
    session_id: UUID,
    payload,
    user,
    db: AsyncSession,
    *,
    attachments: list[AttachmentInput] | None,
    owned_session_query,
    duplicate_submission,
    message_serializer: MessageSerializer,
) -> dict[str, Any]:
    item = await owned_session_query(db, session_id, user.id, for_update=True)
    if item is None:
        raise SessionServiceError(404, "session.notFound")
    duplicate = await duplicate_submission(
        db, payload.run_id, user.id, requested_session_id=session_id
    )
    if duplicate is not None:
        return duplicate
    content = payload.content.strip()
    if not content and not attachments:
        raise SessionServiceError(422, "validation.required", field="content")
    active = await db.scalar(
        select(AgentRun).where(
            AgentRun.session_id == session_id,
            AgentRun.status.in_(ACTIVE_RUN_STATUSES),
        )
    )
    if active:
        raise SessionServiceError(409, "session.runAlreadyActive")
    ai = await load_effective_ai(db, user.id)
    model_id = item.model_config_id or ai.default_model_id
    if not model_available(ai, model_id):
        raise SessionServiceError(409, "session.modelUnavailable")
    if not model_supports_tools(ai, model_id):
        raise SessionServiceError(409, "session.modelToolsUnavailable")
    try:
        run = await AgentRuntime.start(
            db,
            item,
            content,
            run_id=payload.run_id,
            attachments=attachments,
            active_file=active_file_payload(payload.active_file, item),
        )
    except AgentRunError as exc:
        raise SessionServiceError(409, exc.code, params=exc.params) from exc
    except IntegrityError as exc:
        await db.rollback()
        duplicate = await duplicate_submission(
            db, payload.run_id, user.id, requested_session_id=session_id
        )
        if duplicate is not None:
            return duplicate
        raise SessionServiceError(409, "http.conflict") from exc
    user_message = await latest_user_message(db, item.id)
    return {
        "runId": str(run.id),
        "modelName": run.model_name,
        "message": message_serializer(user_message),
    }


async def update_session(
    session_id: UUID,
    payload,
    user,
    db: AsyncSession,
    *,
    storage_factory,
    owned_session_query,
    session_serializer: PayloadSerializer,
) -> dict[str, Any]:
    fields = payload.model_fields_set
    execution_fields = {
        "preferred_execution_agent_id",
        "preferred_root_id",
        "working_directory",
    }
    policy_fields = {
        "project_id",
        "memory_mode",
        "model_id",
        "reasoning_effort",
        "web_search_policy",
        "preferred_execution_agent_id",
        "preferred_root_id",
        "working_directory",
    }
    if fields & policy_fields:
        await db.scalar(
            select(ChatSession.id)
            .where(ChatSession.id == session_id, ChatSession.user_id == user.id)
            .with_for_update()
        )
    item = await owned_session_query(db, session_id, user.id)
    if item is None:
        raise SessionServiceError(404, "session.notFound")
    if fields & policy_fields:
        active = await db.scalar(
            select(AgentRun.id).where(
                AgentRun.session_id == session_id,
                AgentRun.status.in_(ACTIVE_RUN_STATUSES),
            )
        )
        if active is not None:
            raise SessionServiceError(409, "session.runAlreadyActive")
    source_project_id = item.project_id
    target_project_id = (
        payload.project_id if "project_id" in fields else source_project_id
    )
    if target_project_id is not None and target_project_id != source_project_id:
        project = await db.scalar(
            select(Project.id)
            .where(Project.id == target_project_id, Project.user_id == user.id)
            .with_for_update()
        )
        if project is None:
            raise SessionServiceError(404, "project.notFound", field="projectId")
    if target_project_id != source_project_id:
        for grant in list(
            (
                await db.scalars(
                    select(PermissionGrant).where(
                        PermissionGrant.session_id == session_id
                    )
                )
            ).all()
        ):
            await db.delete(grant)
    if "title" in fields and payload.title is not None:
        item.title = payload.title
        item.title_generation_status = TitleGenerationStatus.SKIPPED.value
    if "pinned" in fields and payload.pinned is not None:
        item.is_pinned = payload.pinned
    if "control_center" in fields:
        item.control_center = payload.control_center.model_dump(mode="json")
    if "model_id" in fields:
        ai = await load_effective_ai(db, user.id)
        if payload.model_id is not None and not model_available(ai, payload.model_id):
            raise SessionServiceError(400, "session.modelUnavailable")
        effective_model_id = payload.model_id or ai.default_model_id
        if not model_available(ai, effective_model_id):
            raise SessionServiceError(409, "session.modelUnavailable")
        if not model_supports_tools(ai, effective_model_id):
            raise SessionServiceError(409, "session.modelToolsUnavailable")
        item.model_config_id = payload.model_id
    if "reasoning_effort" in fields:
        item.reasoning_effort = (
            payload.reasoning_effort.value if payload.reasoning_effort else None
        )
    if "memory_mode" in fields and payload.memory_mode is not None:
        item.memory_mode = payload.memory_mode.value
    if "web_search_policy" in fields and payload.web_search_policy is not None:
        if payload.web_search_policy.value == "required":
            ai = await load_effective_ai(db, user.id)
            model_id = item.model_config_id or ai.default_model_id
            if not model_supports_tools(ai, model_id):
                raise SessionServiceError(409, "session.modelToolsUnavailable")
        item.web_search_policy = payload.web_search_policy.value
    if fields & execution_fields:
        agent_id = (
            payload.preferred_execution_agent_id
            if "preferred_execution_agent_id" in fields
            else item.preferred_execution_agent_id
        )
        root_id = (
            payload.preferred_root_id
            if "preferred_root_id" in fields
            else item.preferred_root_id
        )
        working_directory = (
            payload.working_directory
            if "working_directory" in fields
            else item.working_directory
        )
        selected_new_agent = (
            "preferred_execution_agent_id" in fields
            and agent_id != item.preferred_execution_agent_id
        )
        if selected_new_agent:
            root_id = payload.preferred_root_id if "preferred_root_id" in fields else None
            working_directory = (
                payload.working_directory if "working_directory" in fields else None
            )
        item.working_directory, item.current_directory, root_id, root_revision = (
            await validate_execution_preferences(
                db, user.id, agent_id, root_id, working_directory
            )
        )
        item.preferred_execution_agent_id = agent_id
        item.preferred_root_id = root_id
        await add_directory_grant(
            db, item.id, agent_id, root_id, root_revision, item.current_directory
        )
    storage = storage_factory()
    move_receipt: SessionMoveReceipt | None = None

    async def restore_storage() -> None:
        nonlocal move_receipt
        if move_receipt is None:
            return
        receipt, move_receipt = move_receipt, None
        try:
            await asyncio.to_thread(storage.rollback_session_move, receipt)
        except BaseException:
            logger.exception("Session storage could not be restored after project move")

    async def finalize_storage() -> None:
        nonlocal move_receipt
        if move_receipt is None:
            return
        receipt, move_receipt = move_receipt, None
        try:
            await asyncio.to_thread(storage.finalize_session_move, receipt)
        except BaseException:
            logger.exception("Session storage move could not be finalized")

    async def resolve_storage_after_commit_error() -> None:
        nonlocal move_receipt
        await db.rollback()
        if move_receipt is None:
            return
        receipt, move_receipt = move_receipt, None
        try:
            row = (
                await db.execute(
                    select(ChatSession.project_id)
                    .where(ChatSession.id == item.id)
                    .with_for_update()
                )
            ).one_or_none()
            persisted_project_id = row[0] if row is not None else None
        except BaseException:
            try:
                await db.rollback()
            except BaseException:
                logger.exception("Database rollback failed while resolving session move")
            await asyncio.to_thread(storage.abandon_session_move, receipt)
            logger.exception(
                "Session storage move outcome remains journaled after commit failure"
            )
            return
        try:
            if row is not None and persisted_project_id == receipt.source_project_id:
                await asyncio.to_thread(storage.rollback_session_move, receipt)
            else:
                await asyncio.to_thread(storage.finalize_session_move, receipt)
        except BaseException:
            logger.exception("Session storage move could not be reconciled after commit failure")
        finally:
            await db.rollback()

    commit_task: asyncio.Task[None] | None = None
    try:
        if target_project_id != source_project_id:
            quota_bytes = None
            if source_project_id is None and target_project_id is not None:
                quota_bytes = (
                    await SettingsStore.load(db, InstanceLimits)
                ).session_quota_bytes
            stage_task = asyncio.create_task(
                asyncio.to_thread(
                    storage.stage_session_move,
                    item.id,
                    source_project_id,
                    target_project_id,
                    quota_bytes=quota_bytes,
                )
            )
            try:
                move_receipt = await asyncio.shield(stage_task)
            except asyncio.CancelledError:
                move_receipt = await stage_task
                raise
            item.project_id = target_project_id
            if source_project_id is None and target_project_id is not None:
                await db.execute(
                    update(MessageAttachment)
                    .where(
                        MessageAttachment.file_root_id == item.id,
                        MessageAttachment.message_id.in_(
                            select(Message.id).where(Message.session_id == item.id)
                        ),
                    )
                    .values(
                        file_root_id=target_project_id,
                        metadata_json=case(
                            (
                                MessageAttachment.metadata_json[
                                    "companionFileRootId"
                                ].as_string()
                                == str(item.id),
                                MessageAttachment.metadata_json.op("||")(
                                    cast(
                                        {
                                            "companionFileRootId": str(
                                                target_project_id
                                            )
                                        },
                                        JSONB,
                                    )
                                ),
                            ),
                            else_=MessageAttachment.metadata_json,
                        ),
                    )
                )
            source_identity = source_project_id or item.id
            stored_messages = list(
                (
                    await db.scalars(
                        select(Message).where(Message.session_id == item.id)
                    )
                ).all()
            )
            rebind_session_file_metadata(
                item,
                stored_messages,
                source_identity,
                target_project_id if source_project_id is None else None,
            )
        await db.flush()
        await db.refresh(item)
        commit_task = asyncio.create_task(db.commit())
        await asyncio.shield(commit_task)
        await finalize_storage()
    except asyncio.CancelledError:
        if commit_task is None:
            await db.rollback()
            await restore_storage()
            raise
        try:
            await commit_task
        except BaseException:
            await resolve_storage_after_commit_error()
        else:
            await finalize_storage()
        raise
    except SessionStorageError as exc:
        await db.rollback()
        await restore_storage()
        if exc.code == "file.pathAlreadyExists":
            raise SessionServiceError(409, exc.code) from exc
        if exc.code == "file.sessionQuotaExceeded":
            raise SessionServiceError(507, exc.code) from exc
        raise SessionServiceError(500, "session.storageUnavailable") from exc
    except OSError as exc:
        await db.rollback()
        await restore_storage()
        raise SessionServiceError(500, "session.storageUnavailable") from exc
    except BaseException:
        if commit_task is None:
            await db.rollback()
            await restore_storage()
        else:
            await resolve_storage_after_commit_error()
        raise
    return await session_serializer(db, item)


async def session_delete_impact(
    session_id: UUID,
    user_id: UUID,
    db: AsyncSession,
    *,
    owned_session_query,
) -> dict[str, int | str]:
    if await owned_session_query(db, session_id, user_id) is None:
        raise SessionServiceError(404, "session.notFound")
    linked = list(
        (
            await db.scalars(
                select(Automation).where(
                    Automation.user_id == user_id,
                    Automation.output_session_id == session_id,
                )
            )
        ).all()
    )
    return {
        "automationCount": len(linked),
        "requiredAutomationCount": sum(item.output_session_required for item in linked),
        "automationImpact": automation_impact_token(linked),
    }


async def delete_session(
    session_id: UUID,
    user,
    db: AsyncSession,
    *,
    automation_mode: Literal["unlink", "deleteAutomations"] | None,
    automation_impact: str | None,
    storage_factory,
    owned_session_query,
) -> None:
    item = await db.scalar(
        select(ChatSession).where(
            ChatSession.id == session_id,
            ChatSession.user_id == user.id,
            ChatSession.session_type == "chat",
        )
    )
    if item is None:
        raise SessionServiceError(404, "session.notFound")
    linked_automations = list(
        (
            await db.scalars(
                select(Automation).where(
                    Automation.user_id == user.id,
                    Automation.output_session_id == session_id,
                )
            )
        ).all()
    )
    required_count = sum(item.output_session_required for item in linked_automations)
    if automation_mode is not None and automation_impact != automation_impact_token(
        linked_automations
    ):
        raise SessionServiceError(409, "session.automationImpactChanged")
    if required_count and automation_mode != "deleteAutomations":
        raise SessionServiceError(
            409,
            "session.automationOutputRequired",
            params={
                "automationCount": required_count,
                "totalAutomationCount": len(linked_automations),
            },
        )
    if linked_automations and automation_mode is None:
        raise SessionServiceError(
            409,
            "session.automationOutputLinked",
            params={"automationCount": len(linked_automations)},
        )
    automation_agent_run_ids = (
        list(
            (
                await db.scalars(
                    select(AgentRun.id)
                    .join(AutomationRun, AutomationRun.agent_run_id == AgentRun.id)
                    .where(
                        AutomationRun.automation_id.in_(
                            [automation.id for automation in linked_automations]
                        ),
                        AgentRun.status.in_(ACTIVE_RUN_STATUSES),
                    )
                )
            ).all()
        )
        if automation_mode == "deleteAutomations" and linked_automations
        else []
    )
    active_runs = list(
        (
            await db.scalars(
                select(AgentRun).where(
                    AgentRun.session_id == session_id,
                    AgentRun.status.in_(ACTIVE_RUN_STATUSES),
                )
            )
        ).all()
    )
    if automation_agent_run_ids or active_runs:
        await db.rollback()
        for run_id in [*automation_agent_run_ids, *(run.id for run in active_runs)]:
            await AgentRuntime.cancel_and_wait(run_id)
    linked_automations = list(
        (
            await db.scalars(
                select(Automation)
                .where(
                    Automation.user_id == user.id,
                    Automation.output_session_id == session_id,
                )
                .with_for_update()
            )
        ).all()
    )
    item = await owned_session_query(db, session_id, user.id, for_update=True)
    if item is None:
        raise SessionServiceError(404, "session.notFound")
    linked_automations = list(
        (
            await db.scalars(
                select(Automation)
                .where(
                    Automation.user_id == user.id,
                    Automation.output_session_id == session_id,
                )
                .with_for_update()
            )
        ).all()
    )
    required_count = sum(item.output_session_required for item in linked_automations)
    if automation_mode is not None and automation_impact != automation_impact_token(
        linked_automations
    ):
        raise SessionServiceError(409, "session.automationImpactChanged")
    if required_count and automation_mode != "deleteAutomations":
        raise SessionServiceError(
            409,
            "session.automationOutputRequired",
            params={
                "automationCount": required_count,
                "totalAutomationCount": len(linked_automations),
            },
        )
    if linked_automations and automation_mode is None:
        raise SessionServiceError(
            409,
            "session.automationOutputLinked",
            params={"automationCount": len(linked_automations)},
        )
    if automation_mode == "deleteAutomations" and linked_automations:
        late_agent_run_ids = list(
            (
                await db.scalars(
                    select(AgentRun.id)
                    .join(AutomationRun, AutomationRun.agent_run_id == AgentRun.id)
                    .where(
                        AutomationRun.automation_id.in_(
                            [automation.id for automation in linked_automations]
                        ),
                        AgentRun.status.in_(ACTIVE_RUN_STATUSES),
                    )
                )
            ).all()
        )
        if late_agent_run_ids:
            await db.rollback()
            for run_id in late_agent_run_ids:
                await AgentRuntime.cancel_and_wait(run_id)
            return await delete_session(
                session_id,
                user,
                db,
                automation_mode=automation_mode,
                automation_impact=automation_impact,
                storage_factory=storage_factory,
                owned_session_query=owned_session_query,
            )
    if automation_mode == "unlink":
        for automation in linked_automations:
            automation.output_session_id = None
            automation.revision += 1
    execution_sessions = []
    if automation_mode == "deleteAutomations":
        execution_sessions = list(
            (
                await db.scalars(
                    select(ChatSession).where(
                        ChatSession.id.in_(
                            [automation.execution_session_id for automation in linked_automations]
                        )
                    )
                )
            ).all()
        )
    session_storage = storage_factory()
    storage_sessions = [
        (session_id, item.project_id),
        *((execution_session.id, None) for execution_session in execution_sessions),
    ]
    trashed_trees = []
    try:
        for storage_session_id, project_id in storage_sessions:
            trashed_trees.append(
                await asyncio.to_thread(
                    session_storage.trash_session, storage_session_id, project_id
                )
            )
    except (OSError, SessionStorageError) as exc:
        for (storage_session_id, project_id), trashed in zip(
            storage_sessions, trashed_trees, strict=False
        ):
            if trashed is not None:
                try:
                    await asyncio.to_thread(
                        session_storage.restore_session,
                        storage_session_id,
                        trashed,
                        project_id,
                    )
                except Exception:
                    logger.exception("Partially trashed session storage could not be restored")
        logger.exception("Session storage could not be moved to trash")
        raise SessionServiceError(500, "session.storageUnavailable") from exc

    async def restore_tree() -> None:
        for (storage_session_id, project_id), trashed in zip(
            storage_sessions, trashed_trees, strict=False
        ):
            if trashed is None:
                continue
            try:
                await asyncio.to_thread(
                    session_storage.restore_session,
                    storage_session_id,
                    trashed,
                    project_id,
                )
            except Exception:
                logger.exception("Session storage could not be restored after database failure")

    async def purge_tree() -> None:
        for trashed in trashed_trees:
            try:
                await asyncio.to_thread(session_storage.purge_trashed, trashed)
            except Exception:
                logger.exception("Deleted session storage could not be removed from trash")

    commit_task: asyncio.Task[None] | None = None
    try:
        for automation in linked_automations:
            if automation_mode == "deleteAutomations":
                await db.delete(automation)
        if automation_mode == "deleteAutomations":
            await db.flush()
            for execution_session in execution_sessions:
                await db.delete(execution_session)
        await db.delete(item)
        commit_task = asyncio.create_task(db.commit())
        await asyncio.shield(commit_task)
    except asyncio.CancelledError:
        if commit_task is None:
            await db.rollback()
            await restore_tree()
            raise
        try:
            await commit_task
        except BaseException:
            await db.rollback()
            await restore_tree()
        else:
            await purge_tree()
        raise
    except BaseException:
        await db.rollback()
        await restore_tree()
        raise
    await purge_tree()
