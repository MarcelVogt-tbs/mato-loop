from __future__ import annotations

import asyncio
import base64
import binascii
import json
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime
from pathlib import PurePosixPath
from typing import Any, Literal
from uuid import UUID, uuid4

from sqlalchemy import (
    and_,
    case,
    cast,
    delete,
    func,
    or_,
    select,
    tuple_,
    update,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased, selectinload

from mato_loop_backend.agent import AgentRuntime
from mato_loop_backend.agent.permissions import execution_directory_scope
from mato_loop_backend.agent.run_timing import run_timing_payload
from mato_loop_backend.agent.runtime import AgentRunError
from mato_loop_backend.agent.todos import session_todos
from mato_loop_backend.attachments import AttachmentInput
from mato_loop_backend.automations import automation_impact_token
from mato_loop_backend.chat_history import ChatHistoryService
from mato_loop_backend.execution import (
    DirectoryResult,
    ExecutionClient,
    ExecutionClientError,
    execution_agent_connections,
)
from mato_loop_backend.execution.client import _absolute_path
from mato_loop_backend.models import (
    AgentRun,
    Automation,
    AutomationRun,
    ChatSession,
    ContextSummary,
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
    DEFAULT_AGENT_PROFILE_ID,
    AgentProfileUnavailable,
    AiSettings,
    InstanceLimits,
    SettingsStore,
    effective_model_trust,
    load_agent_profile,
    load_effective_ai,
)
from mato_loop_backend.settings.models import ModelAvailability, ModelCapability
from mato_loop_backend.workspace import RootFileError, RootFileService

from .cleanup import descendant_sessions
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


def _latest_model_context(provider_data: Any) -> dict[str, int] | None:
    runtime_data = provider_data.get("_matoloop") if isinstance(provider_data, dict) else None
    performance = runtime_data.get("performance") if isinstance(runtime_data, dict) else None
    if not isinstance(performance, dict):
        return None
    estimated = performance.get("estimatedInputTokens")
    budget = performance.get("inputBudgetTokens")
    if type(estimated) is not int or estimated < 0 or type(budget) is not int or budget <= 0:
        return None
    return {"estimatedInputTokens": estimated, "inputBudgetTokens": budget}


def model_available(ai: AiSettings, model_id: UUID | None) -> bool:
    if model_id is None:
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


async def validate_agent_profile(
    db: AsyncSession, user_id: UUID, profile_id: UUID | None
) -> None:
    if profile_id in {None, DEFAULT_AGENT_PROFILE_ID}:
        return
    try:
        await load_agent_profile(db, user_id, profile_id, for_update=True)
    except AgentProfileUnavailable as exc:
        raise SessionServiceError(
            409,
            "agentProfile.selectedUnavailable",
            field="agentProfileId",
        ) from exc


async def execution_home(
    db: AsyncSession, agent: ExecutionAgent
) -> str | None:
    del db
    if agent.home_directory is None:
        return None
    snapshot = execution_agent_connections.snapshot(agent.id)
    if snapshot is not None and "filesystem.resolve" in snapshot:
        try:
            result = await ExecutionClient(agent.id, snapshot).request(
                "filesystem.resolve", {"path": agent.home_directory}, DirectoryResult
            )
        except ExecutionClientError:
            return None
        assert isinstance(result, DirectoryResult)
        return result.path if result.path == agent.home_directory else None
    return agent.home_directory


def _path_is_within(path: str, parent: str) -> bool:
    path_parts = PurePosixPath(path).parts
    parent_parts = PurePosixPath(parent).parts
    return path_parts[: len(parent_parts)] == parent_parts


def execution_filesystem_policy(agent: ExecutionAgent, path: str) -> str:
    at_home = agent.home_directory is not None and _path_is_within(path, agent.home_directory)
    return agent.home_filesystem_policy if at_home else agent.external_filesystem_policy


async def add_directory_grant(
    db: AsyncSession,
    session_id: UUID,
    agent_id: UUID | None,
    path: str | None,
    policy: str | None,
    agent_profile_id: UUID | None,
    trust_level: str | None,
) -> None:
    if agent_id is None or path is None or policy not in {"auto", "ask"} or trust_level is None:
        return
    scope = execution_directory_scope(agent_id, path)
    existing = await db.scalar(
        select(PermissionGrant.id).where(
            PermissionGrant.session_id == session_id,
            PermissionGrant.capability == "execution.directory",
            PermissionGrant.resource_type == "execution_directory",
            PermissionGrant.resource_scope == scope,
            PermissionGrant.agent_profile_id == agent_profile_id,
            PermissionGrant.trust_level == trust_level,
        )
    )
    if existing is None:
        db.add(
            PermissionGrant(
                session_id=session_id,
                capability="execution.directory",
                resource_type="execution_directory",
                resource_scope=scope,
                agent_profile_id=agent_profile_id,
                trust_level=trust_level,
            )
        )


async def validate_execution_preferences(
    db: AsyncSession,
    user_id: UUID,
    agent_id: UUID | None,
    current_directory: str | None,
) -> tuple[str | None, str | None]:
    if agent_id is None:
        if current_directory is not None:
            raise SessionServiceError(
                400, "session.invalidWorkingDirectory", field="currentDirectory"
            )
        return None, None
    agent = await db.scalar(
        select(ExecutionAgent).where(
            ExecutionAgent.id == agent_id, ExecutionAgent.user_id == user_id
        )
    )
    if agent is None:
        raise SessionServiceError(
            404, "executionAgent.notFound", field="preferredExecutionAgentId"
        )
    if agent.revoked_at is not None:
        raise SessionServiceError(
            409, "executionAgent.revoked", field="preferredExecutionAgentId"
        )
    selected = current_directory or await execution_home(db, agent)
    if selected is None:
        return None, None
    snapshot = execution_agent_connections.snapshot(agent.id)
    if snapshot is None or "filesystem.resolve" not in snapshot:
        raise SessionServiceError(409, "executionAgent.unavailable", field="currentDirectory")
    try:
        result = await ExecutionClient(agent.id, snapshot).request(
            "filesystem.resolve", {"path": selected}, DirectoryResult
        )
    except ExecutionClientError as exc:
        raise SessionServiceError(
            400, "session.invalidWorkingDirectory", field="currentDirectory"
        ) from exc
    assert isinstance(result, DirectoryResult)
    policy = execution_filesystem_policy(agent, result.path)
    if policy == "deny":
        raise SessionServiceError(403, "permission.denied", field="currentDirectory")
    return result.path, policy


async def workspace_context(db: AsyncSession, item: ChatSession) -> dict[str, Any]:
    agent_id = item.preferred_execution_agent_id
    context = {
        "executionAgentId": str(agent_id) if agent_id else None,
        "connected": False,
        "operations": [],
    }
    if agent_id is None:
        return context
    agent = await db.scalar(
        select(ExecutionAgent).where(
            ExecutionAgent.id == agent_id,
            ExecutionAgent.user_id == item.user_id,
            ExecutionAgent.revoked_at.is_(None),
        )
    )
    snapshot = execution_agent_connections.snapshot(agent_id)
    if agent is None or snapshot is None:
        return context
    return {**context, "connected": True, "operations": sorted(snapshot)}


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


async def get_owned_session(
    db: AsyncSession,
    session_id: UUID,
    user_id: UUID,
    *,
    for_update: bool = False,
) -> ChatSession | None:
    item = await owned_session(db, session_id, user_id, for_update=for_update)
    if item is not None and item.parent_session_id is not None:
        raise SessionServiceError(403, "session.readOnly")
    return item


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
    context_summary_serializer: MessageSerializer,
) -> dict[str, Any]:
    messages, context_summaries, next_message_cursor = await session_message_page(
        db,
        item.id,
        page_size=20,
        cursor=None,
    )
    attributions = await message_attributions(db, messages)
    active_run = await db.scalar(
        select(AgentRun)
        .where(
            AgentRun.session_id == item.id,
            AgentRun.parent_run_id.is_(None),
            AgentRun.status.in_(ACTIVE_RUN_STATUSES),
        )
        .order_by(AgentRun.created_at.desc(), AgentRun.id.desc())
        .limit(1)
    )
    retry_message_id = None
    if item.parent_session_id is None and active_run is None and messages:
        retry_source = await ChatHistoryService.retry_source(
            db,
            session_id=item.id,
            messages=messages,
            message_id=messages[-1].id,
        )
        if retry_source is not None:
            retry_message_id = messages[-1].id
    performance_message = aliased(Message)
    latest_performance_provider_data = (
        select(performance_message.provider_data)
        .where(
            performance_message.session_id == item.id,
            performance_message.role == "assistant",
            performance_message.provider_data["_matoloop"].op("?")("performance"),
        )
        .order_by(performance_message.created_at.desc(), performance_message.id.desc())
        .limit(1)
        .scalar_subquery()
    )
    (
        last_message_at,
        last_assistant_message_at,
        user_request_count,
        latest_performance_data,
    ) = (
        await db.execute(
            select(
                func.max(Message.created_at).filter(
                    Message.role.in_(VISIBLE_MESSAGE_ROLES)
                ),
                func.max(Message.created_at).filter(Message.role == "assistant"),
                func.count(Message.id).filter(Message.role == "user"),
                latest_performance_provider_data,
            ).where(Message.session_id == item.id)
        )
    ).one()
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
            message_serializer(
                message,
                retry_available=message.id == retry_message_id,
                attribution=attributions.get(message.id),
                attribution_resolved=True,
            )
            for message in messages
        ],
        "todos": session_todos(
            (
                await db.scalars(
                    select(Message)
                    .where(
                        Message.session_id == item.id,
                        Message.role == "tool",
                        Message.provider_data["_matoloop"]["toolName"].as_string()
                        == "todo_write",
                    )
                    .order_by(Message.created_at, Message.id)
                )
            ).all()
        ),
        "contextSummaries": [
            context_summary_serializer(summary) for summary in context_summaries
        ],
        "nextMessageCursor": next_message_cursor,
        "userRequestCount": user_request_count,
        "latestModelContext": _latest_model_context(latest_performance_data),
        "activeRun": (
            {
                "id": str(active_run.id),
                "status": active_run.status,
                "modelName": active_run.model_name,
                "agentProfileId": (
                    str(active_run.agent_profile_id)
                    if active_run.agent_profile_id
                    else None
                ),
                "createdAt": active_run.created_at.isoformat() if active_run.created_at else None,
                **run_timing_payload(active_run),
            }
            if active_run
            else None
        ),
    }


async def session_message_page(
    db: AsyncSession,
    session_id: UUID,
    *,
    page_size: int,
    cursor: UUID | None,
) -> tuple[list[Message], list[ContextSummary], str | None]:
    upper_bound: tuple[datetime, UUID] | None = None
    if cursor is not None:
        cursor_row = (
            await db.execute(
                select(Message.created_at, Message.id).where(
                    Message.id == cursor,
                    Message.session_id == session_id,
                    Message.role == "user",
                )
            )
        ).one_or_none()
        if cursor_row is None:
            raise SessionServiceError(400, "validation.invalid", field="cursor")
        upper_bound = (cursor_row.created_at, cursor_row.id)

    anchors_statement = select(Message.created_at, Message.id).where(
        Message.session_id == session_id,
        Message.role == "user",
    )
    if upper_bound is not None:
        anchors_statement = anchors_statement.where(
            tuple_(Message.created_at, Message.id) < upper_bound
        )
    anchors = (
        await db.execute(
            anchors_statement.order_by(Message.created_at.desc(), Message.id.desc()).limit(
                page_size + 1
            )
        )
    ).all()
    page_anchors = anchors[:page_size]
    if not page_anchors:
        return [], [], None

    lower_bound = (page_anchors[-1].created_at, page_anchors[-1].id)
    messages_statement = select(Message).where(
        Message.session_id == session_id,
        tuple_(Message.created_at, Message.id) >= lower_bound,
    )
    if upper_bound is not None:
        messages_statement = messages_statement.where(
            tuple_(Message.created_at, Message.id) < upper_bound
        )
    messages = list(
        (
            await db.scalars(
                messages_statement.options(selectinload(Message.attachments)).order_by(
                    Message.created_at, Message.id
                )
            )
        ).all()
    )
    summaries_statement = select(ContextSummary).where(
        ContextSummary.session_id == session_id,
        tuple_(ContextSummary.created_at, ContextSummary.id) >= lower_bound,
    )
    if upper_bound is not None:
        summaries_statement = summaries_statement.where(
            tuple_(ContextSummary.created_at, ContextSummary.id) < upper_bound
        )
    context_summaries = list(
        (
            await db.scalars(
                summaries_statement.order_by(
                    ContextSummary.created_at, ContextSummary.id
                )
            )
        ).all()
    )
    next_cursor = str(lower_bound[1]) if len(anchors) > page_size else None
    return messages, context_summaries, next_cursor


async def message_attributions(
    db: AsyncSession, messages: list[Message]
) -> dict[UUID, tuple[UUID, UUID | None]]:
    assistants = [message for message in messages if message.role == "assistant"]
    if not assistants:
        return {}
    stamped_messages: set[UUID] = set()
    stamped_run_ids: dict[UUID, UUID] = {}
    stamped_profile_ids: dict[UUID, UUID | None] = {}
    invalid_stamps: set[UUID] = set()
    for message in assistants:
        metadata = (message.provider_data or {}).get("_matoloop", {})
        if not isinstance(metadata, dict) or not (
            "runId" in metadata or "agentProfileId" in metadata
        ):
            continue
        stamped_messages.add(message.id)
        if "runId" not in metadata or "agentProfileId" not in metadata:
            invalid_stamps.add(message.id)
            continue
        try:
            stamped_run_id = UUID(metadata["runId"])
            stamped_profile_id = (
                UUID(metadata["agentProfileId"])
                if metadata.get("agentProfileId") is not None
                else None
            )
        except (AttributeError, KeyError, TypeError, ValueError):
            invalid_stamps.add(message.id)
            continue
        stamped_run_ids[message.id] = stamped_run_id
        stamped_profile_ids[message.id] = stamped_profile_id
    message_ids = {message.id for message in assistants}
    sessions_by_message = {message.id: message.session_id for message in assistants}
    session_ids = set(sessions_by_message.values())
    candidates: dict[UUID, set[UUID]] = {message.id: set() for message in assistants}
    error_run_ids = {
        message.error_run_id for message in assistants if message.error_run_id is not None
    }
    runs = list((await db.execute(select(
        AgentRun.id,
        AgentRun.agent_profile_id,
        AgentRun.result_message_id,
        AgentRun.session_id,
    ).where(
        or_(
            AgentRun.result_message_id.in_(message_ids),
            AgentRun.id.in_(error_run_ids | set(stamped_run_ids.values())),
        ),
        AgentRun.session_id.in_(session_ids),
    ))).all())
    run_sessions = {row.id: row.session_id for row in runs}
    profiles = {row.id: row.agent_profile_id for row in runs}
    for message in assistants:
        if (
            message.id not in stamped_messages
            and message.error_run_id is not None
            and run_sessions.get(message.error_run_id) == message.session_id
        ):
            candidates[message.id].add(message.error_run_id)
    for row in runs:
        if (
            row.result_message_id in candidates
            and row.result_message_id not in stamped_messages
            and row.session_id == sessions_by_message[row.result_message_id]
        ):
            candidates[row.result_message_id].add(row.id)

    automation_rows = list((await db.execute(
        select(
            AutomationRun.output_message_id,
            AgentRun.id,
            AgentRun.agent_profile_id,
            AgentRun.session_id,
        )
        .join(AgentRun, AgentRun.id == AutomationRun.agent_run_id)
        .where(
            AutomationRun.output_message_id.in_(message_ids),
        )
    )).all())
    automation_links: dict[UUID, set[UUID]] = {}
    for row in automation_rows:
        automation_links.setdefault(row.output_message_id, set()).add(row.id)
        if row.output_message_id not in stamped_messages:
            candidates[row.output_message_id].add(row.id)
        profiles[row.id] = row.agent_profile_id
        run_sessions[row.id] = row.session_id

    resolved = {}
    for message_id, run_id in stamped_run_ids.items():
        automation_run_ids = automation_links.get(message_id)
        valid_run = (
            automation_run_ids == {run_id}
            if automation_run_ids
            else run_sessions.get(run_id) == sessions_by_message[message_id]
        )
        if valid_run and profiles.get(run_id) == stamped_profile_ids[message_id]:
            resolved[message_id] = (run_id, profiles.get(run_id))
        else:
            invalid_stamps.add(message_id)

    calls_by_message = {
        message.id: {
            str(call["id"])
            for call in message.tool_calls or []
            if isinstance(call, dict) and call.get("id")
        }
        for message in assistants
        if message.id not in stamped_messages
    }
    tool_call_ids = set().union(*calls_by_message.values())
    if tool_call_ids:
        tool_rows = list((await db.execute(
            select(Message.session_id, Message.tool_call_id, Message.provider_data).where(
                Message.role == "tool",
                Message.session_id.in_({message.session_id for message in assistants}),
                Message.tool_call_id.in_(tool_call_ids),
            )
        )).all())
        tool_runs: dict[tuple[UUID, str], set[UUID]] = {}
        for row in tool_rows:
            raw_run_id = (row.provider_data or {}).get("_matoloop", {}).get("runId")
            try:
                linked_run_id = UUID(raw_run_id) if isinstance(raw_run_id, str) else None
            except ValueError:
                linked_run_id = None
            if linked_run_id is not None and row.tool_call_id is not None:
                tool_runs.setdefault((row.session_id, row.tool_call_id), set()).add(linked_run_id)
        linked_run_ids = set().union(*tool_runs.values()) if tool_runs else set()
        if linked_run_ids:
            profile_rows = list((await db.execute(
                select(AgentRun.id, AgentRun.agent_profile_id, AgentRun.session_id).where(
                    AgentRun.id.in_(linked_run_ids),
                    AgentRun.session_id.in_(session_ids),
                )
            )).all())
            profiles.update({row.id: row.agent_profile_id for row in profile_rows})
            run_sessions.update({row.id: row.session_id for row in profile_rows})
        for message in assistants:
            if message.id in stamped_messages:
                continue
            for call_id in calls_by_message[message.id]:
                candidates[message.id].update(
                    run_id
                    for run_id in tool_runs.get((message.session_id, call_id), ())
                    if run_sessions.get(run_id) == message.session_id
                )

    resolved.update({
        message_id: (run_id, profiles.get(run_id))
        for message_id, run_ids in candidates.items()
        if message_id not in invalid_stamps and len(run_ids) == 1
        for run_id in run_ids
    })
    return resolved


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
            AgentRun.parent_run_id.is_(None),
            ChatSession.user_id == user_id,
            ChatSession.session_type == "chat",
        )
    )
    if run is None:
        return None
    item = await get_owned_session(db, run.session_id, user_id)
    if requested_session_id is not None and run.session_id != requested_session_id:
        raise SessionServiceError(409, "http.conflict")
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
        "agentProfileId": str(run.agent_profile_id) if run.agent_profile_id else None,
        "modelName": run.model_name,
        **run_timing_payload(run),
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
            AgentRun.parent_run_id.is_(None),
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
        .where(
            ChatSession.user_id == user_id,
            ChatSession.session_type == "chat",
            ChatSession.parent_session_id.is_(None),
        )
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
    await validate_agent_profile(db, user.id, payload.agent_profile_id)
    current_directory, directory_policy = await validate_execution_preferences(
        db,
        user.id,
        payload.preferred_execution_agent_id,
        payload.current_directory,
    )
    item = ChatSession(
        id=uuid4(),
        user_id=user.id,
        project_id=payload.project_id,
        title="Neue Session",
        title_generation_status=TitleGenerationStatus.PENDING.value,
        model_config_id=model_id,
        agent_profile_id=payload.agent_profile_id,
        reasoning_effort=payload.reasoning_effort.value if payload.reasoning_effort else None,
        memory_mode=payload.memory_mode.value,
        web_search_policy=payload.web_search_policy.value,
        preferred_execution_agent_id=payload.preferred_execution_agent_id,
        current_directory=current_directory,
        control_center=payload.control_center.model_dump(mode="json"),
    )
    db.add(item)
    item_id = item.id
    item_project_id = item.project_id
    selected_model = model_for_session(ai, model_id)
    selected_provider = provider_for_session(ai, selected_model)
    assert selected_model is not None and selected_provider is not None
    await add_directory_grant(
        db,
        item.id,
        payload.preferred_execution_agent_id,
        current_directory,
        directory_policy,
        item.agent_profile_id,
        effective_model_trust(selected_provider, selected_model).value,
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
        "agentProfileId": str(run.agent_profile_id) if run.agent_profile_id else None,
        "modelName": run.model_name,
        **run_timing_payload(run),
    }


def active_file_payload(active_file, session: ChatSession) -> dict[str, Any] | None:
    if active_file is None:
        return None
    if active_file.root == "session":
        try:
            identity = UUID(active_file.root_identity)
            path = RootFileService(UUID(int=0)).normalize_relative_path(active_file.path)
        except (RootFileError, TypeError, ValueError) as exc:
            raise SessionServiceError(422, "validation.invalid", field="activeFile") from exc
        if (
            str(identity) != active_file.root_identity
            or identity != (session.project_id or session.id)
            or path == "."
        ):
            raise SessionServiceError(422, "validation.invalid", field="activeFile")
    else:
        try:
            path = _absolute_path(active_file.path)
        except ValueError as exc:
            raise SessionServiceError(422, "validation.invalid", field="activeFile") from exc
        if (
            active_file.root_identity is not None
            or active_file.execution_agent_id != session.preferred_execution_agent_id
        ):
            raise SessionServiceError(422, "validation.invalid", field="activeFile")
    return {
        "root": active_file.root,
        **(
            {"rootIdentity": active_file.root_identity}
            if active_file.root == "session"
            else {"executionAgentId": str(active_file.execution_agent_id)}
        ),
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
            AgentRun.parent_run_id.is_(None),
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
        "agentProfileId": str(run.agent_profile_id) if run.agent_profile_id else None,
        "modelName": run.model_name,
        **run_timing_payload(run),
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
        "current_directory",
    }
    policy_fields = {
        "project_id",
        "memory_mode",
        "model_id",
        "agent_profile_id",
        "reasoning_effort",
        "web_search_policy",
        "preferred_execution_agent_id",
        "current_directory",
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
                AgentRun.parent_run_id.is_(None),
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
    if (
        "agent_profile_id" in fields
        and (payload.agent_profile_id or DEFAULT_AGENT_PROFILE_ID)
        != item.agent_profile_id
    ) or ("model_id" in fields and payload.model_id != item.model_config_id):
        await db.execute(
            delete(PermissionGrant).where(PermissionGrant.session_id == session_id)
        )
    if "title" in fields and payload.title is not None:
        item.title = payload.title
        item.title_generation_status = TitleGenerationStatus.SKIPPED.value
    if "pinned" in fields and payload.pinned is not None:
        item.is_pinned = payload.pinned
    browser_closed = False
    if "control_center" in fields:
        browser_closed = any(
            tab.get("app_id") == "browser" and tab.get("kind", "app") == "app"
            for tab in (item.control_center or {}).get("tabs", [])
        ) and not any(tab.app_id == "browser" and tab.kind == "app" for tab in payload.control_center.tabs)
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
    if "agent_profile_id" in fields:
        agent_profile_id = payload.agent_profile_id or DEFAULT_AGENT_PROFILE_ID
        await validate_agent_profile(db, user.id, agent_profile_id)
        item.agent_profile_id = agent_profile_id
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
        ai = await load_effective_ai(db, user.id)
        model = model_for_session(ai, item.model_config_id or ai.default_model_id)
        provider = provider_for_session(ai, model)
        agent_id = (
            payload.preferred_execution_agent_id
            if "preferred_execution_agent_id" in fields
            else item.preferred_execution_agent_id
        )
        current_directory = (
            payload.current_directory
            if "current_directory" in fields
            else item.current_directory
        )
        selected_new_agent = (
            "preferred_execution_agent_id" in fields
            and agent_id != item.preferred_execution_agent_id
        )
        if selected_new_agent:
            current_directory = (
                payload.current_directory if "current_directory" in fields else None
            )
        item.current_directory, directory_policy = await validate_execution_preferences(
            db, user.id, agent_id, current_directory
        )
        item.preferred_execution_agent_id = agent_id
        await add_directory_grant(
            db,
            item.id,
            agent_id,
            item.current_directory,
            directory_policy,
            item.agent_profile_id,
            (
                effective_model_trust(provider, model).value
                if model is not None and provider is not None
                else None
            ),
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
        if browser_closed:
            from mato_loop_backend.execution.browser_control import browser_control_requests

            browser_control_requests.cancel_session(user.id, session_id)
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
    user_id: UUID,
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
            ChatSession.user_id == user_id,
            ChatSession.session_type == "chat",
        )
    )
    if item is None:
        raise SessionServiceError(404, "session.notFound")
    if item.parent_session_id is not None:
        raise SessionServiceError(403, "session.readOnly")
    linked_automations = list(
        (
            await db.scalars(
                select(Automation).where(
                    Automation.user_id == user_id,
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
                    AgentRun.parent_run_id.is_(None),
                    AgentRun.status.in_(ACTIVE_RUN_STATUSES),
                )
            )
        ).all()
    )
    active_run_ids = [*automation_agent_run_ids, *(run.id for run in active_runs)]
    descendant_roots = [session_id]
    if automation_mode == "deleteAutomations":
        descendant_roots.extend(automation.execution_session_id for automation in linked_automations)
    if active_run_ids:
        await db.rollback()
        for run_id in active_run_ids:
            await AgentRuntime.cancel_and_wait(run_id)
    await db.rollback()
    for root_id in descendant_roots:
        await AgentRuntime.cancel_descendants(root_id)
    linked_automations = list(
        (
            await db.scalars(
                select(Automation)
                .where(
                    Automation.user_id == user_id,
                    Automation.output_session_id == session_id,
                )
                .with_for_update()
            )
        ).all()
    )
    item = await owned_session_query(db, session_id, user_id, for_update=True)
    if item is None:
        raise SessionServiceError(404, "session.notFound")
    linked_automations = list(
        (
            await db.scalars(
                select(Automation)
                .where(
                    Automation.user_id == user_id,
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
                user_id,
                db,
                automation_mode=automation_mode,
                automation_impact=automation_impact,
                storage_factory=storage_factory,
                owned_session_query=owned_session_query,
            )
    if automation_mode == "unlink":
        for automation in linked_automations:
            automation.output_session_id = None
            automation.delivery_channel_type = None
            automation.delivery_endpoint_id = None
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
    storage_sessions.extend(
        await descendant_sessions(db, [storage_id for storage_id, _ in storage_sessions])
    )
    if (await db.scalars(
        select(AgentRun.id).where(
            AgentRun.session_id.in_([storage_id for storage_id, _ in storage_sessions]),
            AgentRun.status.in_(ACTIVE_RUN_STATUSES),
        ).limit(1)
    )).all():
        raise SessionServiceError(409, "session.runAlreadyActive")
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
