from __future__ import annotations

import json
import logging
from dataclasses import replace
from pathlib import PurePosixPath
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    Query,
    UploadFile,
    status,
)
from fastapi.responses import FileResponse, Response, StreamingResponse
from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    ValidationError,
    model_validator,
)
from sqlalchemy import and_, select, update
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from mato_loop_backend.agent import AgentRuntime
from mato_loop_backend.agent.run_timing import run_timing_payload
from mato_loop_backend.agent.runtime import AgentRunError
from mato_loop_backend.api.errors import ApiError
from mato_loop_backend.attachments import (
    AttachmentError,
    AttachmentInput,
    AttachmentService,
    classify_attachment,
    image_mime_type,
    processing_limits,
    safe_filename,
)
from mato_loop_backend.chat_history import ChatHistoryError, ChatHistoryService
from mato_loop_backend.db.session import get_db_session
from mato_loop_backend.error_contract import describe_error
from mato_loop_backend.events import run_events
from mato_loop_backend.memory import effective_memory
from mato_loop_backend.models import (
    AgentRun,
    ChatSession,
    ExecutionAgent,
    MemoryMode,
    Message,
    MessageAttachment,
    UserAccount,
    WebSearchPolicy,
)
from mato_loop_backend.security import require_user
from mato_loop_backend.session_storage import SessionStorage
from mato_loop_backend.sessions import service as session_service
from mato_loop_backend.sessions.serialization import (
    LAST_MESSAGE_AT_UNSET as _LAST_MESSAGE_AT_UNSET,
)
from mato_loop_backend.sessions.serialization import (
    context_summary_payload,
    message_payload,
    run_payload,
)
from mato_loop_backend.sessions.serialization import (
    rebind_session_file_metadata as _rebind_session_file_metadata,
)
from mato_loop_backend.sessions.serialization import (
    session_payload as _serialize_session_payload,
)
from mato_loop_backend.settings import (
    DEFAULT_AGENT_PROFILE_ID,
    InstanceLimits,
    MemorySettings,
    SettingsStore,
    UserPreferences,
    load_effective_ai,
)
from mato_loop_backend.settings.models import ReasoningEffort
from mato_loop_backend.workspace import RootFileError, RootFileService

__all__ = ["_rebind_session_file_metadata"]


router = APIRouter(prefix="/sessions", dependencies=[Depends(require_user)])
logger = logging.getLogger(__name__)

_model_available = session_service.model_available
_model_supports_tools = session_service.model_supports_tools
_model_for_session = session_service.model_for_session
_provider_for_session = session_service.provider_for_session
_execution_home = session_service.execution_home
_owned_session = session_service.owned_session


def _normalized_absolute_path(value: str) -> bool:
    parsed = PurePosixPath(value)
    return (
        "\\" not in value
        and not any(ord(character) < 32 or 127 <= ord(character) <= 159 for character in value)
        and parsed.is_absolute()
        and parsed.as_posix() == value
        and (
            value == "/"
            or not any(part in {"", ".", ".."} for part in value.split("/")[1:])
        )
    )


def _raise_api_error(exc: session_service.SessionServiceError) -> None:
    raise ApiError(
        exc.status_code,
        exc.code,
        field=exc.field,
        params=exc.params,
    ) from exc


class ControlCenterFileTarget(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    scope: Literal["session", "filesystem"]
    path: str = Field(min_length=1, max_length=4_096)
    name: str = Field(min_length=1, max_length=255)
    root_identity: str | None = Field(default=None, min_length=36, max_length=36)
    execution_agent_id: str | None = Field(default=None, min_length=36, max_length=36)
    current_directory: str | None = Field(default=None, min_length=1, max_length=4_096)
    read_only: bool = False

    @model_validator(mode="after")
    def validate_target(self) -> ControlCenterFileTarget:
        if self.scope == "session":
            service = RootFileService(UUID(int=0))
            try:
                valid_root = (
                    self.root_identity is not None
                    and str(UUID(self.root_identity)) == self.root_identity
                )
            except ValueError:
                valid_root = False
            if (
                service.normalize_relative_path(self.path) != self.path
                or self.path == "."
                or not valid_root
            ):
                raise ValueError("session file targets require a normalized file path and root")
            if self.execution_agent_id is not None or self.current_directory is not None:
                raise ValueError("session file targets cannot contain computer fields")
            return self
        if (
            self.root_identity is not None
            or self.execution_agent_id is None
            or self.current_directory is None
            or not _normalized_absolute_path(self.path)
            or not _normalized_absolute_path(self.current_directory)
        ):
            raise ValueError("computer file targets require canonical absolute context")
        try:
            if str(UUID(self.execution_agent_id)) != self.execution_agent_id:
                raise ValueError("computer ID must be canonical")
        except ValueError as exc:
            raise ValueError("computer ID must be canonical") from exc
        return self


class ControlCenterTabState(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    tab_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=64,
        pattern=r"^(?:[a-z][a-z0-9_-]*|file-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$",
    )
    kind: Literal["app", "file"] = "app"
    app_id: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_-]*$")
    pinned: bool
    file_target: ControlCenterFileTarget | None = None

    @model_validator(mode="after")
    def validate_tab(self) -> ControlCenterTabState:
        if self.tab_id is None:
            self.tab_id = self.app_id
        if self.kind == "app":
            if self.tab_id != self.app_id or self.file_target is not None:
                raise ValueError("app tabs must use their app ID and cannot contain a file")
            return self
        if (
            self.app_id != "file-viewer"
            or not self.tab_id.startswith("file-")
            or self.file_target is None
        ):
            raise ValueError("file tabs require an opaque file tab ID and target")
        try:
            valid_id = f"file-{UUID(self.tab_id.removeprefix('file-'))}" == self.tab_id
        except ValueError:
            valid_id = False
        if not valid_id:
            raise ValueError("file tabs require a canonical UUID tab ID")
        return self


class ControlCenterFileView(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    scope: Literal["session", "filesystem"]
    selected_path: str = Field(min_length=1, max_length=1_000)
    selected_kind: Literal["file", "directory"]
    viewed_file_path: str | None = Field(default=None, min_length=1, max_length=1_000)
    root_identity: str | None = Field(default=None, min_length=1, max_length=1_000)
    execution_agent_id: str | None = Field(default=None, min_length=36, max_length=36)
    current_directory: str | None = Field(default=None, min_length=1, max_length=4_096)

    @model_validator(mode="after")
    def validate_view(self) -> ControlCenterFileView:
        service = RootFileService(UUID(int=0))
        if self.scope == "session":
            if service.normalize_relative_path(self.selected_path) != self.selected_path:
                raise ValueError("selected path must be normalized")
            if self.selected_path == "." and self.selected_kind != "directory":
                raise ValueError("the root path must be a directory")
        elif not _normalized_absolute_path(self.selected_path):
            raise ValueError("filesystem paths must be absolute")
        if self.viewed_file_path is not None:
            if (
                self.selected_kind != "file"
                or self.viewed_file_path == "."
                or self.viewed_file_path != self.selected_path
            ):
                raise ValueError("viewed file path must match the selected file")
        if self.scope == "session":
            if self.root_identity is None:
                return self
            if self.execution_agent_id is not None or self.current_directory is not None:
                raise ValueError("session views cannot contain workspace snapshot fields")
            return self
        if self.root_identity is not None:
            raise ValueError("filesystem views cannot contain root identity")
        if self.execution_agent_id is None or self.current_directory is None:
            raise ValueError("historical workspace views require snapshot fields")
        assert self.execution_agent_id is not None
        try:
            valid_id = str(UUID(self.execution_agent_id)) == self.execution_agent_id
        except ValueError:
            valid_id = False
        if not valid_id or not _normalized_absolute_path(self.current_directory):
            raise ValueError("workspace snapshot must use canonical absolute identifiers")
        return self


class ControlCenterFileViews(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    session: ControlCenterFileView | None = None
    filesystem: ControlCenterFileView | None = None

    @model_validator(mode="after")
    def validate_scopes(self) -> ControlCenterFileViews:
        if self.session is not None and self.session.scope != "session":
            raise ValueError("session file view must use session scope")
        if self.filesystem is not None and self.filesystem.scope != "filesystem":
            raise ValueError("filesystem file view must use filesystem scope")
        return self


class ControlCenterState(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    tabs: list[ControlCenterTabState] = Field(max_length=21)
    active_app_id: str | None = Field(max_length=64, pattern=r"^[a-z][a-z0-9_-]*$")
    open: bool = False
    file_view: ControlCenterFileView | None = None
    file_views: ControlCenterFileViews = Field(default_factory=ControlCenterFileViews)

    @model_validator(mode="after")
    def validate_tabs(self) -> ControlCenterState:
        session_tabs = [tab for tab in self.tabs if tab.app_id == "session"]
        if session_tabs:
            session_tab = session_tabs[0]
            if session_tab.kind != "app":
                raise ValueError("the Session tab must be an app")
            self.tabs = [session_tab, *[tab for tab in self.tabs if tab is not session_tab]]
        else:
            self.tabs.insert(0, ControlCenterTabState(app_id="session", pinned=False))
        if len(self.tabs) > 21:
            raise ValueError("control center supports at most 21 tabs")
        if self.active_app_id is None:
            self.active_app_id = "session"
        tab_ids = [tab.tab_id for tab in self.tabs]
        if len(tab_ids) != len(set(tab_ids)):
            raise ValueError("control center tab IDs must be unique")
        if self.active_app_id not in tab_ids:
            raise ValueError("active ID must identify a tab")
        return self


def default_control_center_state() -> ControlCenterState:
    return ControlCenterState(
        tabs=[
            ControlCenterTabState(app_id="session", pinned=False),
            ControlCenterTabState(app_id="files", pinned=False),
        ],
        active_app_id="session",
    )


class ActiveFileRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    root: Literal["session", "workspace"]
    root_identity: str | None = Field(
        default=None,
        min_length=36,
        max_length=36,
        validation_alias=AliasChoices("root_identity", "rootIdentity"),
    )
    execution_agent_id: UUID | None = Field(
        default=None,
        validation_alias=AliasChoices("execution_agent_id", "executionAgentId"),
    )
    path: str = Field(min_length=1, max_length=4_096)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    dirty: bool


class SessionCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str = Field(max_length=200_000)
    voice_attachment_indices: list[StrictInt] = Field(
        default_factory=list, alias="voiceAttachmentIndices"
    )
    run_id: UUID | None = None
    project_id: UUID | None = None
    model_id: UUID | None = None
    agent_profile_id: UUID = Field(
        default=DEFAULT_AGENT_PROFILE_ID,
        validation_alias=AliasChoices("agentProfileId", "agent_profile_id"),
    )
    reasoning_effort: ReasoningEffort | None = None
    memory_mode: MemoryMode = MemoryMode.FULL
    web_search_policy: WebSearchPolicy = WebSearchPolicy.AVAILABLE
    preferred_execution_agent_id: UUID | None = None
    current_directory: str | None = Field(default=None, max_length=4_096)
    control_center: ControlCenterState = Field(default_factory=default_control_center_state)
    active_file: ActiveFileRequest | None = Field(
        default=None, validation_alias=AliasChoices("active_file", "activeFile")
    )


class SessionUpdateRequest(BaseModel):
    title: str | None = Field(default=None, max_length=240)
    pinned: bool | None = None
    project_id: UUID | None = None
    model_id: UUID | None = None
    agent_profile_id: UUID | None = Field(
        default=None,
        validation_alias=AliasChoices("agentProfileId", "agent_profile_id"),
    )
    reasoning_effort: ReasoningEffort | None = None
    memory_mode: MemoryMode | None = None
    web_search_policy: WebSearchPolicy | None = None
    preferred_execution_agent_id: UUID | None = None
    current_directory: str | None = Field(default=None, max_length=4_096)
    control_center: ControlCenterState = Field(default=None)


class SendMessageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str = Field(max_length=200_000)
    voice_attachment_indices: list[StrictInt] = Field(
        default_factory=list, alias="voiceAttachmentIndices"
    )
    run_id: UUID | None = None
    active_file: ActiveFileRequest | None = Field(
        default=None, validation_alias=AliasChoices("active_file", "activeFile")
    )


class SessionReadRequest(BaseModel):
    through_message_id: UUID = Field(alias="throughMessageId")


async def _validate_execution_preferences(
    db: AsyncSession,
    user_id: UUID,
    agent_id: UUID | None,
    current_directory: str | None,
) -> tuple[str | None, str | None]:
    try:
        return await session_service.validate_execution_preferences(
            db, user_id, agent_id, current_directory
        )
    except session_service.SessionServiceError as exc:
        _raise_api_error(exc)


def _multipart_payload(value: str, model: type[BaseModel]) -> BaseModel:
    if len(value) > 1_000_000:
        raise ApiError(413, "validation.tooLong", field="payload")
    try:
        return model.model_validate_json(value)
    except ValidationError as exc:
        error = exc.errors(include_url=False)[0] if exc.errors() else {}
        field = next(
            (part for part in reversed(error.get("loc") or ()) if isinstance(part, str)),
            "payload",
        )
        raise ApiError(422, "validation.invalid", field=field) from exc


def _attachment_api_error(exc: AttachmentError) -> ApiError:
    status_code = 413 if exc.code in {
        "attachment.sessionQuotaExceeded",
        "attachment.textTooLarge",
        "attachment.tooLarge",
    } else 400
    return ApiError(status_code, exc.code)


async def _attachment_uploads(
    files: list[UploadFile], settings: InstanceLimits | None = None,
    voice_attachment_indices: list[int] | None = None,
) -> list[AttachmentInput]:
    settings = settings or InstanceLimits()
    if not files:
        raise ApiError(422, "validation.required", field="attachments")
    attachments = []
    try:
        indices = voice_attachment_indices or []
        if len(set(indices)) != len(indices) or any(
            type(index) is not int or not 0 <= index < len(files) for index in indices
        ):
            raise ApiError(422, "validation.invalid", field="voiceAttachmentIndices")
        if len(files) > settings.max_attachments_per_message:
            raise ApiError(
                422,
                "validation.tooManyItems",
                field="attachments",
                params={"maxItems": settings.max_attachments_per_message},
            )
        total_bytes = 0
        limits = processing_limits(settings)
        for index, upload in enumerate(files):
            data = await upload.read(settings.max_upload_bytes + 1)
            total_bytes += len(data)
            if total_bytes > settings.max_message_upload_bytes:
                raise ApiError(413, "attachment.tooLarge")
            mime_type = (upload.content_type or "").split(";", 1)[0].strip().lower()
            try:
                attachments.append(
                    classify_attachment(
                        data,
                        mime_type,
                        upload.filename,
                        max_bytes=settings.max_upload_bytes,
                        limits=limits,
                    )
                )
                if index in indices:
                    if attachments[-1].kind != "audio":
                        raise ApiError(422, "validation.invalid", field="voiceAttachmentIndices")
                    attachments[-1] = replace(
                        attachments[-1], metadata={"voiceMessage": True}
                    )
            except AttachmentError as exc:
                if exc.code == "attachment.invalidImage":
                    filename = safe_filename(upload.filename)
                    suffix = (
                        f".{filename.rsplit('.', 1)[1].casefold()}" if "." in filename else ""
                    )
                    logger.warning(
                        "Attachment image validation failed index=%d size_bytes=%d "
                        "content_type=%r detected_type=%r suffix=%r diagnostic=%r",
                        index,
                        len(data),
                        mime_type,
                        image_mime_type(data),
                        suffix,
                        exc.diagnostic,
                    )
                raise _attachment_api_error(exc) from exc
    finally:
        for upload in files:
            await upload.close()
    return attachments


def session_payload(
    item: ChatSession,
    *,
    effective_memory_mode: MemoryMode | None = None,
    memory_tools_available: bool | None = None,
    memory_unavailable_reason: str | None = None,
    workspace_context: dict[str, Any] | None = None,
    last_message_at: Any = _LAST_MESSAGE_AT_UNSET,
    has_unread_messages: bool | None = None,
    default_channel: dict[str, Any] | None = None,
) -> dict[str, Any]:
    try:
        control_center = ControlCenterState.model_validate(
            item.control_center or default_control_center_state()
        ).model_dump(mode="json")
    except (RootFileError, ValidationError):
        control_center = default_control_center_state().model_dump(mode="json")
    return _serialize_session_payload(
        item,
        control_center,
        effective_memory_mode=effective_memory_mode,
        memory_tools_available=memory_tools_available,
        memory_unavailable_reason=memory_unavailable_reason,
        workspace_context=workspace_context,
        last_message_at=last_message_at,
        has_unread_messages=has_unread_messages,
        default_channel=default_channel,
    )


async def _session_payload(
    db: AsyncSession,
    item: ChatSession,
    *,
    last_message_at: Any = _LAST_MESSAGE_AT_UNSET,
    has_unread_messages: bool | None = None,
) -> dict[str, Any]:
    workspace_context = await session_service.workspace_context(db, item)
    first_user_message = (
        await db.scalar(
            select(Message)
            .where(Message.session_id == item.id, Message.role == "user")
            .order_by(Message.created_at, Message.id)
            .limit(1)
        )
        if sa_inspect(item).persistent
        else None
    )
    default_channel = (
        {
            "type": "endpoint",
            "provider": first_user_message.source_provider,
            "endpointId": str(first_user_message.source_endpoint_id),
        }
        if first_user_message is not None
        and first_user_message.source_type == "endpoint"
        and first_user_message.source_provider
        and first_user_message.source_endpoint_id
        else {"type": "app"}
    )
    if item.memory_mode == MemoryMode.DISABLED.value:
        return session_payload(
            item,
            effective_memory_mode=MemoryMode.DISABLED,
            memory_tools_available=False,
            memory_unavailable_reason="memory.sessionDisabled",
            workspace_context=workspace_context,
            last_message_at=last_message_at,
            has_unread_messages=has_unread_messages,
            default_channel=default_channel,
        )
    ai = await load_effective_ai(db, item.user_id)
    memory = await SettingsStore.load(db, MemorySettings, user_id=item.user_id)
    model_id = item.model_config_id or ai.default_model_id
    model = _model_for_session(ai, model_id) if _model_available(ai, model_id) else None
    state = effective_memory(item, memory, _provider_for_session(ai, model), model)
    return session_payload(
        item,
        effective_memory_mode=state.mode,
        memory_tools_available=state.tools_enabled,
        memory_unavailable_reason=state.unavailable_reason,
        workspace_context=workspace_context,
        last_message_at=last_message_at,
        has_unread_messages=has_unread_messages,
        default_channel=default_channel,
    )


async def _duplicate_submission_response(
    db: AsyncSession,
    run_id: UUID | None,
    user_id: UUID,
    *,
    requested_session_id: UUID | None = None,
) -> dict[str, Any] | None:
    try:
        return await session_service.duplicate_submission_response(
            db,
            run_id,
            user_id,
            requested_session_id=requested_session_id,
            session_serializer=_session_payload,
            message_serializer=message_payload,
        )
    except session_service.SessionServiceError as exc:
        _raise_api_error(exc)


@router.get("")
async def list_sessions(
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
    page_size: Annotated[int, Query(alias="pageSize", ge=1, le=100)] = 20,
    cursor: Annotated[str | None, Query()] = None,
    project_id: Annotated[UUID | None, Query(alias="projectId")] = None,
    standalone: Annotated[bool, Query()] = False,
) -> dict[str, Any]:
    try:
        return await session_service.list_sessions(
            db,
            user.id,
            page_size=page_size,
            cursor=cursor,
            project_id=project_id,
            standalone=standalone,
            session_serializer=_session_payload,
        )
    except session_service.SessionServiceError as exc:
        _raise_api_error(exc)


async def _create_session(
    payload: SessionCreateRequest,
    user: UserAccount,
    db: AsyncSession,
    attachments: list[AttachmentInput] | None = None,
) -> dict:
    try:
        return await session_service.create_session(
            payload,
            user,
            db,
            attachments=attachments,
            storage_factory=SessionStorage,
            session_serializer=_session_payload,
            message_serializer=message_payload,
            duplicate_submission=_duplicate_submission_response,
        )
    except session_service.SessionServiceError as exc:
        _raise_api_error(exc)


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_session(
    payload: SessionCreateRequest,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict:
    if payload.voice_attachment_indices:
        raise ApiError(422, "validation.invalid", field="voiceAttachmentIndices")
    return await _create_session(payload, user, db)


@router.post("/with-attachments", status_code=status.HTTP_201_CREATED)
async def create_session_with_attachments(
    payload: Annotated[str, Form()],
    attachments: Annotated[list[UploadFile], File()],
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict:
    request = _multipart_payload(payload, SessionCreateRequest)
    assert isinstance(request, SessionCreateRequest)
    duplicate = await _duplicate_submission_response(db, request.run_id, user.id)
    if duplicate is not None:
        return duplicate
    instance_limits = await SettingsStore.load(db, InstanceLimits)
    try:
        return await _create_session(
            request,
            user,
            db,
            await _attachment_uploads(
                attachments, instance_limits, request.voice_attachment_indices
            ),
        )
    except AttachmentError as exc:
        raise _attachment_api_error(exc) from exc


@router.get("/defaults")
async def session_defaults(
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
    model_id: Annotated[UUID | None, Query(alias="modelId")] = None,
    memory_mode: Annotated[MemoryMode, Query(alias="memoryMode")] = MemoryMode.FULL,
    agent_profile_id: Annotated[
        UUID | None, Query(alias="agentProfileId")
    ] = None,
) -> dict:
    ai = await load_effective_ai(db, user.id)
    memory = await SettingsStore.load(db, MemorySettings, user_id=user.id)
    preferences = await SettingsStore.load(db, UserPreferences, user_id=user.id)
    try:
        selected_agent_profile_id = agent_profile_id or DEFAULT_AGENT_PROFILE_ID
        await session_service.validate_agent_profile(
            db, user.id, selected_agent_profile_id
        )
    except session_service.SessionServiceError as exc:
        _raise_api_error(exc)
    selected_model_id = model_id or ai.default_model_id
    model = (
        _model_for_session(ai, selected_model_id)
        if _model_available(ai, selected_model_id)
        else None
    )
    default_session = ChatSession(user_id=user.id, memory_mode=memory_mode.value)
    state = effective_memory(
        default_session, memory, _provider_for_session(ai, model), model
    )
    preferred_agent_id = preferences.default_execution_agent_id
    current_directory = None
    if preferred_agent_id is not None:
        agent = await db.scalar(
            select(ExecutionAgent).where(
                ExecutionAgent.id == preferred_agent_id,
                ExecutionAgent.user_id == user.id,
                ExecutionAgent.revoked_at.is_(None),
            )
        )
        if agent is not None:
            current_directory = await _execution_home(db, agent)
    return {
        "modelId": str(selected_model_id) if selected_model_id else None,
        "agentProfileId": str(selected_agent_profile_id),
        "reasoningEffort": None,
        "modelAvailable": bool(model and model.supports_tools),
        "modelUnavailableReason": (
            None
            if model and model.supports_tools
            else (
                "session.modelToolsUnavailable"
                if model is not None
                else "session.modelUnavailable"
            )
        ),
        "memoryMode": memory_mode.value,
        "effectiveMemoryMode": state.mode.value,
        "memoryToolsAvailable": state.tools_enabled,
        "memoryUnavailableReason": state.unavailable_reason,
        "webSearchPolicy": WebSearchPolicy.AVAILABLE.value,
        "preferredExecutionAgentId": str(preferred_agent_id) if preferred_agent_id else None,
        "currentDirectory": current_directory,
    }


@router.get("/{session_id}")
async def get_session(
    session_id: UUID,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict:
    item = await _owned_session(db, session_id, user.id)
    if item is None:
        raise ApiError(404, "session.notFound")
    return await session_service.session_detail_payload(
        db,
        item,
        session_serializer=_session_payload,
        message_serializer=message_payload,
        context_summary_serializer=context_summary_payload,
    )


@router.get("/{session_id}/messages")
async def get_session_messages(
    session_id: UUID,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
    page_size: Annotated[int, Query(alias="pageSize", ge=1, le=100)] = 20,
    cursor: Annotated[UUID | None, Query()] = None,
) -> dict[str, Any]:
    if await _owned_session(db, session_id, user.id) is None:
        raise ApiError(404, "session.notFound")
    try:
        messages, context_summaries, next_cursor = await session_service.session_message_page(
            db,
            session_id,
            page_size=page_size,
            cursor=cursor,
        )
    except session_service.SessionServiceError as exc:
        _raise_api_error(exc)
    return {
        "items": [message_payload(message) for message in messages],
        "contextSummaries": [
            context_summary_payload(summary) for summary in context_summaries
        ],
        "nextCursor": next_cursor,
    }


@router.get("/{session_id}/messages/{message_id}")
async def get_session_message(
    session_id: UUID,
    message_id: UUID,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict[str, Any]:
    if await _owned_session(db, session_id, user.id) is None:
        raise ApiError(404, "session.notFound")
    message = await db.scalar(
        select(Message)
        .where(Message.id == message_id, Message.session_id == session_id)
        .options(selectinload(Message.attachments))
    )
    if message is None:
        raise ApiError(404, "session.messageNotFound")
    return message_payload(message)


@router.post("/{session_id}/read")
async def mark_session_read(
    session_id: UUID,
    payload: SessionReadRequest,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict[str, bool]:
    item = await _owned_session(db, session_id, user.id, for_update=True)
    if item is None:
        raise ApiError(404, "session.notFound")
    read_at = await db.scalar(
        select(Message.created_at).where(
            Message.id == payload.through_message_id,
            Message.session_id == session_id,
            Message.role == "assistant",
        )
    )
    if read_at is None:
        raise ApiError(400, "validation.invalid", field="throughMessageId")
    await db.execute(
        update(ChatSession)
        .where(
            ChatSession.id == session_id,
            ChatSession.last_read_at < read_at,
        )
        .values(last_read_at=read_at, updated_at=ChatSession.updated_at)
    )
    await db.commit()
    return {"read": True}


@router.patch("/{session_id}")
async def update_session(
    session_id: UUID,
    payload: SessionUpdateRequest,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict:
    try:
        return await session_service.update_session(
            session_id,
            payload,
            user,
            db,
            storage_factory=SessionStorage,
            owned_session_query=_owned_session,
            session_serializer=_session_payload,
        )
    except session_service.SessionServiceError as exc:
        _raise_api_error(exc)


@router.get("/{session_id}/delete-impact")
async def session_delete_impact(
    session_id: UUID,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict[str, int | str]:
    try:
        return await session_service.session_delete_impact(
            session_id,
            user.id,
            db,
            owned_session_query=_owned_session,
        )
    except session_service.SessionServiceError as exc:
        _raise_api_error(exc)


@router.delete("/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_session(
    session_id: UUID,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
    automation_mode: Annotated[
        Literal["unlink", "deleteAutomations"] | None,
        Query(alias="automationMode"),
    ] = None,
    automation_impact: Annotated[
        str | None,
        Query(alias="automationImpact", min_length=64, max_length=64),
    ] = None,
) -> None:
    try:
        await session_service.delete_session(
            session_id,
            user,
            db,
            automation_mode=automation_mode,
            automation_impact=automation_impact,
            storage_factory=SessionStorage,
            owned_session_query=_owned_session,
        )
    except session_service.SessionServiceError as exc:
        _raise_api_error(exc)


@router.post("/{session_id}/messages/{message_id}/undo")
async def undo_user_message(
    session_id: UUID,
    message_id: UUID,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict[str, Any]:
    if await _owned_session(db, session_id, user.id) is None:
        raise ApiError(404, "session.notFound")
    try:
        result = await ChatHistoryService.undo_user_message(
            db,
            session_id=session_id,
            user_id=user.id,
            message_id=message_id,
        )
    except ChatHistoryError as exc:
        raise ApiError(exc.status_code, exc.code) from exc
    return {
        "sessionDeleted": result.session_deleted,
        "draft": {"content": result.content},
    }


@router.post(
    "/{session_id}/messages/{message_id}/retry",
    status_code=status.HTTP_202_ACCEPTED,
)
async def retry_failed_message(
    session_id: UUID,
    message_id: UUID,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict[str, Any]:
    if await _owned_session(db, session_id, user.id) is None:
        raise ApiError(404, "session.notFound")
    try:
        result = await ChatHistoryService.retry_failed_message(
            db,
            session_id=session_id,
            user_id=user.id,
            message_id=message_id,
        )
    except ChatHistoryError as exc:
        raise ApiError(exc.status_code, exc.code) from exc
    except AgentRunError as exc:
        raise ApiError(409, exc.code, params=exc.params) from exc
    return {
        "runId": str(result.run.id),
        "modelName": result.run.model_name,
        "createdAt": result.run.created_at.isoformat() if result.run.created_at else None,
        **run_timing_payload(result.run),
    }


@router.get(
    "/{session_id}/messages/{message_id}/attachments/{attachment_id}",
    response_class=FileResponse,
)
async def download_attachment(
    session_id: UUID,
    message_id: UUID,
    attachment_id: UUID,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
    playback: bool = False,
) -> Response:
    attachment = await db.scalar(
        select(MessageAttachment)
        .join(Message, Message.id == MessageAttachment.message_id)
        .where(
            MessageAttachment.id == attachment_id,
            Message.id == message_id,
            Message.session_id == session_id,
            Message.chat_session.has(
                and_(
                    ChatSession.user_id == user.id,
                    ChatSession.session_type == "chat",
                )
            ),
        )
    )
    if attachment is None:
        raise ApiError(404, "attachment.notFound")
    try:
        if playback:
            return Response(
                await AttachmentService().read_audio(attachment, session_id),
                media_type="audio/wav",
                headers={
                    "Content-Disposition": "inline",
                    "X-Content-Type-Options": "nosniff",
                    "Cross-Origin-Resource-Policy": "same-origin",
                    "Cache-Control": "private, no-store",
                },
            )
        path = await AttachmentService().verified_path(attachment, session_id)
    except AttachmentError as exc:
        logger.warning("Attachment file for existing metadata was not found")
        raise ApiError(404, "attachment.notFound") from exc
    filename = safe_filename(attachment.filename)
    inline = attachment.kind in {"image", "audio"}
    return FileResponse(
        path,
        media_type=attachment.mime_type,
        filename=filename,
        content_disposition_type="inline" if inline else "attachment",
        headers={
            "X-Content-Type-Options": "nosniff",
            "Cross-Origin-Resource-Policy": "same-origin",
            "Cache-Control": "private, no-store",
        },
    )


async def _send_message(
    session_id: UUID,
    payload: SendMessageRequest,
    user: UserAccount,
    db: AsyncSession,
    attachments: list[AttachmentInput] | None = None,
) -> dict[str, Any]:
    try:
        return await session_service.send_message(
            session_id,
            payload,
            user,
            db,
            attachments=attachments,
            owned_session_query=_owned_session,
            duplicate_submission=_duplicate_submission_response,
            message_serializer=message_payload,
        )
    except session_service.SessionServiceError as exc:
        _raise_api_error(exc)


@router.post("/{session_id}/messages", status_code=status.HTTP_202_ACCEPTED)
async def send_message(
    session_id: UUID,
    payload: SendMessageRequest,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict[str, Any]:
    if payload.voice_attachment_indices:
        raise ApiError(422, "validation.invalid", field="voiceAttachmentIndices")
    return await _send_message(session_id, payload, user, db)


@router.post("/{session_id}/messages/with-attachments", status_code=status.HTTP_202_ACCEPTED)
async def send_message_with_attachments(
    session_id: UUID,
    payload: Annotated[str, Form()],
    attachments: Annotated[list[UploadFile], File()],
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict[str, Any]:
    request = _multipart_payload(payload, SendMessageRequest)
    assert isinstance(request, SendMessageRequest)
    duplicate = await _duplicate_submission_response(
        db, request.run_id, user.id, requested_session_id=session_id
    )
    if duplicate is not None:
        return duplicate
    instance_limits = await SettingsStore.load(db, InstanceLimits)
    try:
        return await _send_message(
            session_id,
            request,
            user,
            db,
            await _attachment_uploads(
                attachments, instance_limits, request.voice_attachment_indices
            ),
        )
    except AttachmentError as exc:
        raise _attachment_api_error(exc) from exc


@router.get("/{session_id}/runs/{run_id}")
async def get_run(
    session_id: UUID,
    run_id: UUID,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict:
    if await _owned_session(db, session_id, user.id) is None:
        raise ApiError(404, "session.runNotFound")
    run = await db.get(AgentRun, run_id)
    if run is None or run.session_id != session_id or run.parent_run_id is not None:
        raise ApiError(404, "session.runNotFound")
    return run_payload(run)


@router.post(
    "/{session_id}/runs/{run_id}/cancel",
    status_code=status.HTTP_202_ACCEPTED,
)
async def cancel_run(
    session_id: UUID,
    run_id: UUID,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict:
    if await _owned_session(db, session_id, user.id) is None:
        raise ApiError(404, "session.runNotFound")
    run = await db.get(AgentRun, run_id)
    if run is None or run.session_id != session_id or run.parent_run_id is not None:
        raise ApiError(404, "session.runNotFound")
    if run.status in {"completed", "failed", "cancelled"}:
        return run_payload(run)
    if not AgentRuntime.cancel(run_id):
        raise ApiError(409, "session.runInactive")
    return run_payload(run)


@router.get("/{session_id}/runs/{run_id}/events")
async def stream_run_events(
    session_id: UUID,
    run_id: UUID,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> StreamingResponse:
    if await _owned_session(db, session_id, user.id) is None:
        raise ApiError(404, "session.runNotFound")
    run = await db.get(AgentRun, run_id)
    if run is None or run.session_id != session_id or run.parent_run_id is not None:
        raise ApiError(404, "session.runNotFound")
    run_status = run.status
    run_error = run.error
    run_timing = run_timing_payload(run)
    error_message = (
        await db.scalar(
            select(Message)
            .where(Message.error_run_id == run_id)
            .options(selectinload(Message.attachments))
            .limit(1)
        )
        if run_status == "failed"
        else None
    )
    error_params = error_message.error_params if error_message is not None else None
    error_message_payload = (
        message_payload(error_message) if error_message is not None else None
    )
    await db.rollback()

    async def events():
        if run_status in {"completed", "failed", "cancelled"}:
            if run_status == "failed":
                error = describe_error(
                    run_error or "agent.failed",
                    params=error_params,
                )
                if error_message_payload is not None:
                    event = {
                        "type": "message.completed",
                        "message": error_message_payload,
                    }
                    yield f"event: message.completed\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
                event = {
                    "type": "run.failed",
                    "error": error.payload(),
                    **run_timing,
                }
            else:
                event = {
                    "type": f"run.{run_status}",
                    "runId": str(run_id),
                    **run_timing,
                }
            yield f"event: {event['type']}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
            return
        async for event in run_events.subscribe(run_id):
            event_type = event.get("type", "message")
            yield f"event: {event_type}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
            if event_type in {"run.completed", "run.failed", "run.cancelled"}:
                break

    return StreamingResponse(events(), media_type="text/event-stream")
