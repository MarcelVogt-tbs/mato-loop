from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import json
import logging
from datetime import datetime
from typing import Annotated, Any, Literal
from urllib.parse import urlencode
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile, status
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from mato_loop_backend.agent import AgentRuntime
from mato_loop_backend.agent.permissions import execution_directory_scope
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
from mato_loop_backend.execution import (
    DirectoryResult,
    ExecutionClient,
    ExecutionClientError,
    execution_agent_connections,
)
from mato_loop_backend.integrations import WebFetchError, canonicalize_url
from mato_loop_backend.memory import effective_memory
from mato_loop_backend.message_content import canonical_assistant_content
from mato_loop_backend.models import (
    AgentRun,
    Automation,
    AutomationRun,
    ChatSession,
    ExecutionAgent,
    MemoryMode,
    Message,
    MessageAttachment,
    PermissionGrant,
    TitleGenerationStatus,
    UserAccount,
    WebSearchPolicy,
)
from mato_loop_backend.security import require_user
from mato_loop_backend.session_storage import SessionStorage, SessionStorageError
from mato_loop_backend.settings import (
    AiSettings,
    FileMediaSettings,
    MemorySettings,
    SettingsStore,
    UserPreferences,
    load_effective_ai,
)
from mato_loop_backend.settings.models import (
    ModelAvailability,
    ModelCapability,
)
from mato_loop_backend.workspace import (
    RootFileError,
    RootFileService,
)

router = APIRouter(prefix="/sessions", dependencies=[Depends(require_user)])
logger = logging.getLogger(__name__)


def _automation_impact_token(automations: list[Automation]) -> str:
    value = "|".join(
        f"{item.id}:{item.revision}:{int(item.output_session_required)}"
        for item in sorted(automations, key=lambda item: item.id)
    )
    return hashlib.sha256(value.encode()).hexdigest()


_LAST_MESSAGE_AT_UNSET = object()
_VISIBLE_MESSAGE_ROLES = ("user", "assistant")


def _encode_session_cursor(
    user_id: UUID, pinned: bool, activity_at: datetime, session_id: UUID
) -> str:
    value = json.dumps(
        [str(user_id), pinned, activity_at.isoformat(), str(session_id)],
        separators=(",", ":"),
    ).encode()
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()


def _decode_session_cursor(
    value: str, user_id: UUID
) -> tuple[bool, datetime, UUID]:
    try:
        decoded = base64.b64decode(
            value + "=" * (-len(value) % 4), altchars=b"-_", validate=True
        )
        payload = json.loads(decoded)
        if not (
            isinstance(payload, list)
            and len(payload) == 4
            and all(isinstance(item, str) for item in (payload[0], payload[2], payload[3]))
            and type(payload[1]) is bool
            and UUID(payload[0]) == user_id
        ):
            raise ValueError
        activity_at = datetime.fromisoformat(payload[2])
        if activity_at.tzinfo is None:
            raise ValueError
        return payload[1], activity_at, UUID(payload[3])
    except (binascii.Error, UnicodeDecodeError, ValueError, TypeError) as exc:
        raise ApiError(400, "validation.invalid", field="cursor") from exc


def _public_research_payload(item: Message) -> dict[str, Any] | None:
    provider_data = item.provider_data if isinstance(item.provider_data, dict) else {}
    runtime_data = provider_data.get("_matoloop")
    if not isinstance(runtime_data, dict):
        return None
    research = runtime_data.get("research")
    if not isinstance(research, dict):
        return None
    policy = research.get("policy")
    used = research.get("used")
    if policy not in {item.value for item in WebSearchPolicy} or not isinstance(used, bool):
        return None
    linked_sources = []
    raw_sources = research.get("linkedSources")
    if isinstance(raw_sources, list):
        for source in raw_sources[:100]:
            if not isinstance(source, dict):
                continue
            title = source.get("title")
            url = source.get("url")
            if not isinstance(title, str) or not title.strip() or not isinstance(url, str):
                continue
            try:
                safe_url = canonicalize_url(url)
            except WebFetchError:
                continue
            linked_sources.append(
                {"title": title.strip()[:500], "url": safe_url}
            )
    return {"policy": policy, "used": used, "linkedSources": linked_sources}


class _StoredFileAction(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    operation: Literal["write", "copy", "move"]
    root: Literal["session", "workspace"]
    root_identity: str = Field(alias="rootIdentity", min_length=1, max_length=1_000)
    path: str = Field(min_length=1, max_length=1_000)
    filename: str = Field(min_length=1, max_length=1_000)
    size_bytes: int = Field(alias="sizeBytes", ge=0)
    intent: Literal["draft"] | None = None
    draft_id: str | None = Field(
        default=None, alias="draftId", min_length=36, max_length=36
    )
    revision: int | None = Field(default=None, ge=1)
    execution_agent_id: str | None = Field(
        default=None, alias="executionAgentId", min_length=1, max_length=36
    )
    root_id: str | None = Field(default=None, alias="rootId", min_length=1, max_length=36)
    root_revision: str | None = Field(
        default=None, alias="rootRevision", min_length=64, max_length=64
    )
    working_directory: str | None = Field(
        default=None, alias="workingDirectory", min_length=1, max_length=1_000
    )

    @model_validator(mode="after")
    def validate_paths(self) -> _StoredFileAction:
        service = RootFileService(UUID(int=0))
        if service.normalize_relative_path(self.path) != self.path or self.path == ".":
            raise ValueError("path must be a normalized file path")
        if self.filename != self.path.rsplit("/", 1)[-1]:
            raise ValueError("filename must match path")
        draft_fields = {"draft_id", "revision"}
        if self.intent is None:
            if self.model_fields_set & draft_fields:
                raise ValueError("non-draft actions cannot contain draft fields")
        else:
            if any(getattr(self, field) is None for field in draft_fields):
                raise ValueError("draft fields are required")
            assert self.draft_id is not None
            try:
                canonical_draft_id = str(UUID(self.draft_id))
            except ValueError as exc:
                raise ValueError("draft ID must be a canonical UUID") from exc
            if (
                canonical_draft_id != self.draft_id
                or self.root != "session"
                or self.operation != "write"
                or self.path != f"files/drafts/{self.draft_id}/{self.filename}"
            ):
                raise ValueError("invalid draft action")
        snapshot_fields = {
            "execution_agent_id",
            "root_id",
            "root_revision",
            "working_directory",
        }
        if self.root == "session":
            if self.model_fields_set & snapshot_fields:
                raise ValueError("session actions cannot contain workspace snapshot fields")
            return self
        if any(getattr(self, field) is None for field in snapshot_fields):
            raise ValueError("workspace snapshot fields are required")
        assert self.execution_agent_id is not None
        assert self.root_id is not None
        assert self.root_revision is not None
        assert self.working_directory is not None
        try:
            valid_ids = all(
                str(UUID(value)) == value
                for value in (self.execution_agent_id, self.root_id)
            )
        except ValueError:
            valid_ids = False
        if not valid_ids or self.root_identity != self.root_id:
            raise ValueError("workspace identifiers must match canonical UUIDs")
        if any(character not in "0123456789abcdef" for character in self.root_revision):
            raise ValueError("root revision must be a lowercase SHA-256 digest")
        if service.normalize_relative_path(self.working_directory) != self.working_directory:
            raise ValueError("working directory must be normalized")
        return self


def _public_file_actions(item: Message) -> list[dict[str, Any]]:
    if item.role != "tool" or not isinstance(item.provider_data, dict):
        return []
    runtime_data = item.provider_data.get("_matoloop")
    raw_actions = runtime_data.get("fileActions") if isinstance(runtime_data, dict) else None
    if not isinstance(raw_actions, list) or len(raw_actions) > 1:
        return []
    actions = []
    for raw_action in raw_actions:
        try:
            action = _StoredFileAction.model_validate(raw_action)
        except (RootFileError, ValidationError, ValueError):
            continue
        if action.root == "session" and action.root_identity != str(item.session_id):
            continue
        scope = "session" if action.root == "session" else "filesystem"
        query = {
            "path": action.path,
            "scope": scope,
            "session_id": str(item.session_id),
            "root_identity": action.root_identity,
        }
        if action.root == "workspace":
            query.update(
                execution_agent_id=action.execution_agent_id,
                root_id=action.root_id,
                root_revision=action.root_revision,
                working_directory=action.working_directory,
            )
        actions.append(
            {
                **action.model_dump(mode="json", by_alias=True, exclude_none=True),
                "downloadUrl": "/api/workspace/files/download?"
                + urlencode(query),
            }
        )
    return actions


def _model_available(ai: AiSettings, model_id: UUID | None) -> bool:
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


def _model_supports_tools(ai: AiSettings, model_id: UUID | None) -> bool:
    return any(
        item.id == model_id
        and ModelCapability.CHAT in item.capabilities
        and item.supports_tools
        for item in ai.models
    )


def _model_for_session(ai: AiSettings, model_id: UUID | None):
    return next(
        (
            item
            for item in ai.models
            if item.id == model_id and ModelCapability.CHAT in item.capabilities
        ),
        None,
    )


def _provider_for_session(ai: AiSettings, model):
    if model is None:
        return None
    return next((item for item in ai.providers if item.id == model.provider_id), None)


class ControlCenterTabState(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    app_id: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_-]*$")
    pinned: bool


class ControlCenterFileView(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    scope: Literal["session", "filesystem"]
    selected_path: str = Field(min_length=1, max_length=1_000)
    selected_kind: Literal["file", "directory"]
    viewed_file_path: str | None = Field(default=None, min_length=1, max_length=1_000)
    root_identity: str | None = Field(default=None, min_length=1, max_length=1_000)
    execution_agent_id: str | None = Field(default=None, min_length=36, max_length=36)
    root_id: str | None = Field(default=None, min_length=36, max_length=36)
    root_revision: str | None = Field(default=None, min_length=64, max_length=64)
    working_directory: str | None = Field(default=None, min_length=1, max_length=1_000)

    @model_validator(mode="after")
    def validate_view(self) -> ControlCenterFileView:
        service = RootFileService(UUID(int=0))
        if service.normalize_relative_path(self.selected_path) != self.selected_path:
            raise ValueError("selected path must be normalized")
        if self.selected_path == "." and self.selected_kind != "directory":
            raise ValueError("the root path must be a directory")
        if self.viewed_file_path is not None:
            if (
                self.selected_kind != "file"
                or self.viewed_file_path == "."
                or service.normalize_relative_path(self.viewed_file_path)
                != self.viewed_file_path
                or self.viewed_file_path != self.selected_path
            ):
                raise ValueError("viewed file path must match the selected file")
        snapshot_fields = (
            self.execution_agent_id,
            self.root_id,
            self.root_revision,
            self.working_directory,
        )
        if self.root_identity is None:
            if any(value is not None for value in snapshot_fields):
                raise ValueError("snapshot fields require a root identity")
            return self
        if self.scope == "session":
            if any(value is not None for value in snapshot_fields):
                raise ValueError("session views cannot contain workspace snapshot fields")
            return self
        if any(value is None for value in snapshot_fields):
            raise ValueError("historical workspace views require snapshot fields")
        assert self.execution_agent_id is not None
        assert self.root_id is not None
        assert self.root_revision is not None
        assert self.working_directory is not None
        try:
            valid_ids = all(
                str(UUID(value)) == value
                for value in (self.execution_agent_id, self.root_id)
            )
        except ValueError:
            valid_ids = False
        if not valid_ids or self.root_identity != self.root_id:
            raise ValueError("workspace identifiers must match canonical UUIDs")
        if any(character not in "0123456789abcdef" for character in self.root_revision):
            raise ValueError("root revision must be a lowercase SHA-256 digest")
        if service.normalize_relative_path(self.working_directory) != self.working_directory:
            raise ValueError("working directory must be normalized")
        return self


class ControlCenterState(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    tabs: list[ControlCenterTabState] = Field(max_length=20)
    active_app_id: str | None = Field(max_length=64, pattern=r"^[a-z][a-z0-9_-]*$")
    open: bool = False
    file_view: ControlCenterFileView | None = None

    @model_validator(mode="after")
    def validate_tabs(self) -> ControlCenterState:
        app_ids = [tab.app_id for tab in self.tabs]
        if len(app_ids) != len(set(app_ids)):
            raise ValueError("control center app IDs must be unique")
        if (not app_ids and self.active_app_id is not None) or (
            app_ids and self.active_app_id not in app_ids
        ):
            raise ValueError("active app ID must identify a tab, or be null when tabs are empty")
        return self


def default_control_center_state() -> ControlCenterState:
    return ControlCenterState(
        tabs=[ControlCenterTabState(app_id="files", pinned=False)],
        active_app_id="files",
    )


class SessionCreateRequest(BaseModel):
    content: str = Field(min_length=1, max_length=200_000)
    model_id: UUID | None = None
    memory_mode: MemoryMode = MemoryMode.FULL
    web_search_policy: WebSearchPolicy = WebSearchPolicy.AVAILABLE
    preferred_execution_agent_id: UUID | None = None
    preferred_root_id: UUID | None = None
    working_directory: str | None = Field(default=None, max_length=1_000)
    workspace_access: str = Field(default="read-only", pattern="^(read-only|read-write)$")
    control_center: ControlCenterState = Field(default_factory=default_control_center_state)


class SessionUpdateRequest(BaseModel):
    title: str | None = Field(default=None, max_length=240)
    pinned: bool | None = None
    model_id: UUID | None = None
    memory_mode: MemoryMode | None = None
    web_search_policy: WebSearchPolicy | None = None
    preferred_execution_agent_id: UUID | None = None
    preferred_root_id: UUID | None = None
    working_directory: str | None = Field(default=None, max_length=1_000)
    workspace_access: str | None = Field(default=None, pattern="^(read-only|read-write)$")
    control_center: ControlCenterState = Field(default=None)


async def _execution_home(
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
            result = await ExecutionClient(
                agent.id, None, None, None, snapshot[0]
            ).request(
                "filesystem.resolve",
                {"path": agent.home_directory},
                DirectoryResult,
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
    return (
        agent.home_root_id,
        agent.home_root_revision,
        agent.home_directory,
        relative,
    )


async def _add_directory_grant(
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


class SendMessageRequest(BaseModel):
    content: str = Field(min_length=1, max_length=200_000)


class SessionReadRequest(BaseModel):
    through_message_id: UUID = Field(alias="throughMessageId")


async def _validate_execution_preferences(
    db: AsyncSession,
    user_id: UUID,
    agent_id: UUID | None,
    root_id: UUID | None,
    working_directory: str | None,
) -> tuple[str | None, str | None, UUID | None, str | None]:
    if agent_id is None:
        if root_id is not None:
            raise ApiError(400, "validation.invalid", field="preferredRootId")
        if working_directory is not None:
            raise ApiError(400, "session.invalidWorkingDirectory", field="workingDirectory")
        return None, None, None, None
    agent = await db.scalar(
        select(ExecutionAgent)
        .options(selectinload(ExecutionAgent.roots))
        .where(ExecutionAgent.id == agent_id, ExecutionAgent.user_id == user_id)
    )
    if agent is None:
        raise ApiError(404, "executionAgent.notFound", field="preferredExecutionAgentId")
    if agent.revoked_at is not None:
        raise ApiError(409, "executionAgent.revoked", field="preferredExecutionAgentId")
    home_root_id, home_revision, home_directory, home_relative = await _execution_home(db, agent)
    if root_id is None and working_directory is None:
        return home_relative, home_directory, home_root_id, home_revision
    if root_id is not None and not any(root.id == root_id for root in agent.roots):
        raise ApiError(400, "validation.invalid", field="preferredRootId")
    if working_directory is None and root_id == home_root_id:
        return home_relative, home_directory, home_root_id, home_revision
    if working_directory is None:
        return None, None, root_id, next(
            (root.revision for root in agent.roots if root.id == root_id), None
        )
    if root_id is None:
        raise ApiError(400, "session.invalidWorkingDirectory", field="workingDirectory")
    try:
        normalized = RootFileService(UUID(int=0)).normalize_relative_path(working_directory)
    except RootFileError as exc:
        raise ApiError(400, "session.invalidWorkingDirectory", field="workingDirectory") from exc
    if normalized != working_directory:
        raise ApiError(400, "session.invalidWorkingDirectory", field="workingDirectory")
    revision = next(root.revision for root in agent.roots if root.id == root_id)
    current_directory = (
        home_directory
        if root_id == home_root_id and normalized == home_relative
        else None
    )
    snapshot = execution_agent_connections.snapshot(agent.id)
    if current_directory is None and snapshot is not None and "workspace.resolve" in snapshot[0]:
        try:
            result = await ExecutionClient(
                agent.id, root_id, revision, ".", snapshot[0]
            ).request("workspace.resolve", {"path": normalized}, DirectoryResult)
        except ExecutionClientError as exc:
            raise ApiError(
                400, "session.invalidWorkingDirectory", field="workingDirectory"
            ) from exc
        assert isinstance(result, DirectoryResult)
        if (
            result.root_id != root_id
            or result.root_revision != revision
            or result.relative_path != normalized
        ):
            raise ApiError(
                502, "executionAgent.protocolError", field="workingDirectory"
            )
        current_directory = result.path
    return normalized, current_directory, root_id, revision


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


async def _attachment_uploads(
    files: list[UploadFile], settings: FileMediaSettings | None = None
) -> list[AttachmentInput]:
    settings = settings or FileMediaSettings()
    if not files:
        raise ApiError(422, "validation.required", field="attachments")
    attachments = []
    try:
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
                raise ApiError(
                    413 if exc.code == "attachment.tooLarge" else 400, exc.code
                ) from exc
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
    last_message_at: datetime | None | object = _LAST_MESSAGE_AT_UNSET,
    has_unread_messages: bool | None = None,
) -> dict[str, Any]:
    try:
        control_center = ControlCenterState.model_validate(
            item.control_center or default_control_center_state()
        ).model_dump(mode="json")
    except (RootFileError, ValidationError):
        control_center = default_control_center_state().model_dump(mode="json")
    file_view = control_center.get("file_view")
    payload = {
        "id": str(item.id),
        "title": item.title,
        "titleGenerationStatus": item.title_generation_status,
        "pinned": bool(item.is_pinned),
        "controlCenter": {
            "tabs": [
                {"appId": tab["app_id"], "pinned": tab["pinned"]}
                for tab in control_center["tabs"]
            ],
            "activeAppId": control_center["active_app_id"],
            "open": bool(control_center.get("open", False)),
            "fileView": (
                {
                    "scope": file_view["scope"],
                    "selectedPath": file_view["selected_path"],
                    "selectedKind": file_view["selected_kind"],
                    "viewedFilePath": file_view.get("viewed_file_path"),
                    "rootIdentity": file_view.get("root_identity"),
                    "executionAgentId": file_view.get("execution_agent_id"),
                    "rootId": file_view.get("root_id"),
                    "rootRevision": file_view.get("root_revision"),
                    "workingDirectory": file_view.get("working_directory"),
                }
                if isinstance(file_view, dict)
                else None
            ),
        },
        "modelId": str(item.model_config_id) if item.model_config_id else None,
        "memoryMode": item.memory_mode,
        "webSearchPolicy": item.web_search_policy,
        "preferredExecutionAgentId": (
            str(item.preferred_execution_agent_id)
            if item.preferred_execution_agent_id
            else None
        ),
        "preferredRootId": str(item.preferred_root_id) if item.preferred_root_id else None,
        "workingDirectory": item.working_directory,
        "currentDirectory": item.current_directory,
        "workspaceAccess": item.workspace_access,
        "workspaceContext": workspace_context
        or {
            "executionAgentId": (
                str(item.preferred_execution_agent_id)
                if item.preferred_execution_agent_id
                else None
            ),
            "rootId": str(item.preferred_root_id) if item.preferred_root_id else None,
            "connected": False,
            "operations": [],
        },
        "createdAt": item.created_at.isoformat() if item.created_at else None,
        "updatedAt": item.updated_at.isoformat() if item.updated_at else None,
    }
    if last_message_at is not _LAST_MESSAGE_AT_UNSET:
        payload["lastMessageAt"] = (
            last_message_at.isoformat() if isinstance(last_message_at, datetime) else None
        )
    if has_unread_messages is not None:
        payload["hasUnreadMessages"] = has_unread_messages
    if effective_memory_mode is not None:
        payload["effectiveMemoryMode"] = effective_memory_mode.value
        payload["memoryToolsAvailable"] = memory_tools_available
        payload["memoryUnavailableReason"] = memory_unavailable_reason
    return payload


async def _session_payload(
    db: AsyncSession,
    item: ChatSession,
    *,
    last_message_at: datetime | None | object = _LAST_MESSAGE_AT_UNSET,
    has_unread_messages: bool | None = None,
) -> dict[str, Any]:
    workspace_context = await _workspace_context(db, item)
    if item.memory_mode == MemoryMode.DISABLED.value:
        return session_payload(
            item,
            effective_memory_mode=MemoryMode.DISABLED,
            memory_tools_available=False,
            memory_unavailable_reason="memory.sessionDisabled",
            workspace_context=workspace_context,
            last_message_at=last_message_at,
            has_unread_messages=has_unread_messages,
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
    )


async def _workspace_context(db: AsyncSession, item: ChatSession) -> dict[str, Any]:
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
    if item.workspace_access == "read-only":
        operations = operations & {
            "workspace.list",
            "workspace.read",
            "workspace.stat",
            "workspace.export",
            "workspace.glob",
            "workspace.grep",
        }
    return {**context, "connected": True, "operations": sorted(operations)}


async def _owned_session(
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
    if for_update:
        statement = statement.with_for_update()
    return await db.scalar(statement)


def message_payload(item: Message, *, retry_available: bool = False) -> dict[str, Any]:
    error = (
        describe_error(item.error_code, params=item.error_params)
        if item.error_code
        else None
    )
    return {
        "id": str(item.id),
        "role": item.role,
        "kind": "runtime_error" if error else "message",
        "content": (
            error.message
            if error
            else canonical_assistant_content(item.content)
            if item.role == "assistant"
            else item.content
        ),
        "modelName": item.model_name,
        "generationStatus": item.generation_status or "completed",
        "trustLevel": item.trust_level,
        "toolCallId": item.tool_call_id,
        "toolCalls": item.tool_calls,
        "reasoning": item.public_reasoning,
        "research": _public_research_payload(item),
        "fileActions": _public_file_actions(item),
        "error": error.payload() if error else None,
        "retryAvailable": retry_available,
        "attachments": [
            {
                "id": str(attachment.id),
                "kind": attachment.kind,
                "category": attachment.category,
                "purpose": attachment.purpose,
                "processingStatus": attachment.processing_status,
                "mimeType": attachment.mime_type,
                "sizeBytes": attachment.size_bytes,
                "filename": attachment.filename,
                "width": attachment.width,
                "height": attachment.height,
                "durationMs": attachment.duration_ms,
                "metadata": attachment.metadata_json or {},
                "url": (
                    f"/api/sessions/{item.session_id}/messages/{item.id}"
                    f"/attachments/{attachment.id}"
                ),
            }
            for attachment in item.attachments
        ],
        "createdAt": item.created_at.isoformat() if item.created_at else None,
    }


def _has_unread_messages(
    item: ChatSession, last_assistant_message_at: datetime | None
) -> bool:
    return bool(
        last_assistant_message_at
        and (
            item.last_read_at is None
            or last_assistant_message_at > item.last_read_at
        )
    )


async def _session_detail_payload(db: AsyncSession, item: ChatSession) -> dict[str, Any]:
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
        .where(
            AgentRun.session_id == item.id,
            AgentRun.status.in_(["queued", "running", "waiting_approval", "waiting_input"]),
        )
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
            if message.role in _VISIBLE_MESSAGE_ROLES and message.created_at
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
            await _session_payload(
                db,
                item,
                last_message_at=last_message_at,
                has_unread_messages=_has_unread_messages(
                    item, last_assistant_message_at
                ),
            )
        ),
        "messages": [
            message_payload(message, retry_available=message.id == retry_message_id)
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


async def _latest_user_message(db: AsyncSession, session_id: UUID) -> Message:
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


def run_payload(run: AgentRun) -> dict[str, Any]:
    return {
        "id": str(run.id),
        "sessionId": str(run.session_id),
        "status": run.status,
        "modelName": run.model_name,
        "error": describe_error(run.error).payload() if run.error else None,
        "createdAt": run.created_at.isoformat() if run.created_at else None,
    }


@router.get("")
async def list_sessions(
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
    page_size: Annotated[int, Query(alias="pageSize", ge=1, le=100)] = 20,
    cursor: Annotated[str | None, Query()] = None,
) -> dict[str, Any]:
    active_run_status = (
        select(AgentRun.status)
        .where(
            AgentRun.session_id == ChatSession.id,
            AgentRun.status.in_(["queued", "running", "waiting_approval", "waiting_input"]),
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
            ChatSession.user_id == user.id,
            Message.role.in_(_VISIBLE_MESSAGE_ROLES),
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
        .where(ChatSession.user_id == user.id, ChatSession.session_type == "chat")
    )
    if cursor is not None:
        cursor_pinned, cursor_activity_at, cursor_id = _decode_session_cursor(
            cursor, user.id
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
            )
            .limit(page_size + 1)
        )
    ).all()
    page = rows[:page_size]
    items = [
        {
            **(
                await _session_payload(
                    db,
                    item,
                    last_message_at=last_message_at,
                    has_unread_messages=_has_unread_messages(
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
            user.id, bool(item.is_pinned), item_activity_at, item.id
        )
    return {"items": items, "nextCursor": next_cursor}


async def _create_session(
    payload: SessionCreateRequest,
    user: UserAccount,
    db: AsyncSession,
    attachments: list[AttachmentInput] | None = None,
) -> dict:
    content = payload.content.strip()
    if not content:
        raise ApiError(422, "validation.required", field="content")
    ai = await load_effective_ai(db, user.id)
    model_id = payload.model_id or ai.default_model_id
    if not _model_available(ai, model_id):
        raise ApiError(409, "session.modelUnavailable", field="modelId")
    if not _model_supports_tools(ai, model_id):
        raise ApiError(409, "session.modelToolsUnavailable", field="modelId")
    working_directory, current_directory, root_id, root_revision = await _validate_execution_preferences(
        db,
        user.id,
        payload.preferred_execution_agent_id,
        payload.preferred_root_id,
        payload.working_directory,
    )
    item = ChatSession(
        id=uuid4(),
        user_id=user.id,
        title="Neue Session",
        title_generation_status=TitleGenerationStatus.PENDING.value,
        model_config_id=model_id,
        memory_mode=payload.memory_mode.value,
        web_search_policy=payload.web_search_policy.value,
        preferred_execution_agent_id=payload.preferred_execution_agent_id,
        preferred_root_id=root_id,
        working_directory=working_directory,
        current_directory=current_directory,
        workspace_access=payload.workspace_access,
        control_center=payload.control_center.model_dump(mode="json"),
    )
    db.add(item)
    await _add_directory_grant(
        db,
        item.id,
        payload.preferred_execution_agent_id,
        root_id,
        root_revision,
        current_directory,
    )
    await db.flush()
    session_storage = SessionStorage()
    try:
        await asyncio.to_thread(session_storage.provision, item.id)
        run = (
            await AgentRuntime.start(db, item, content, attachments=attachments)
            if attachments
            else await AgentRuntime.start(db, item, content)
        )
    except (OSError, SessionStorageError) as exc:
        await db.rollback()
        logger.exception("Session storage could not be provisioned")
        raise ApiError(500, "session.storageUnavailable") from exc
    except AgentRunError as exc:
        await db.rollback()
        try:
            await asyncio.to_thread(session_storage.remove_session, item.id)
        except Exception:
            logger.exception("Unpersisted session storage could not be removed")
        raise ApiError(409, exc.code, params=exc.params) from exc
    except Exception:
        await db.rollback()
        try:
            await asyncio.to_thread(session_storage.remove_session, item.id)
        except Exception:
            logger.exception("Unpersisted session storage could not be removed")
        raise
    await db.refresh(item)
    user_message = await _latest_user_message(db, item.id)
    return {
        "session": await _session_payload(
            db, item, last_message_at=user_message.created_at
        ),
        "message": message_payload(user_message),
        "runId": str(run.id),
        "modelName": run.model_name,
    }


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_session(
    payload: SessionCreateRequest,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict:
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
    file_settings = await SettingsStore.load(db, FileMediaSettings)
    return await _create_session(
        request,
        user,
        db,
        await _attachment_uploads(attachments, file_settings),
    )


@router.get("/defaults")
async def session_defaults(
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
    model_id: Annotated[UUID | None, Query(alias="modelId")] = None,
    memory_mode: Annotated[MemoryMode, Query(alias="memoryMode")] = MemoryMode.FULL,
) -> dict:
    ai = await load_effective_ai(db, user.id)
    memory = await SettingsStore.load(db, MemorySettings, user_id=user.id)
    preferences = await SettingsStore.load(db, UserPreferences, user_id=user.id)
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
    preferred_root_id = None
    working_directory = None
    current_directory = None
    if preferred_agent_id is not None:
        agent = await db.scalar(
            select(ExecutionAgent)
            .options(selectinload(ExecutionAgent.roots))
            .where(
                ExecutionAgent.id == preferred_agent_id,
                ExecutionAgent.user_id == user.id,
                ExecutionAgent.revoked_at.is_(None),
            )
        )
        if agent is not None:
            preferred_root_id, _revision, current_directory, working_directory = (
                await _execution_home(db, agent)
            )
    return {
        "modelId": str(selected_model_id) if selected_model_id else None,
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
        "preferredRootId": str(preferred_root_id) if preferred_root_id else None,
        "workingDirectory": working_directory,
        "currentDirectory": current_directory,
        "workspaceAccess": "read-only",
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
    return await _session_detail_payload(db, item)


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
    fields = payload.model_fields_set
    execution_fields = {
        "preferred_execution_agent_id",
        "preferred_root_id",
        "working_directory",
    }
    policy_fields = {
        "memory_mode",
        "model_id",
        "web_search_policy",
        "preferred_execution_agent_id",
        "preferred_root_id",
        "working_directory",
        "workspace_access",
    }
    if fields & policy_fields:
        await db.scalar(
            select(ChatSession.id)
            .where(ChatSession.id == session_id, ChatSession.user_id == user.id)
            .with_for_update()
        )
    item = await _owned_session(db, session_id, user.id)
    if item is None:
        raise ApiError(404, "session.notFound")
    if fields & policy_fields:
        active = await db.scalar(
            select(AgentRun.id).where(
                AgentRun.session_id == session_id,
                AgentRun.status.in_(["queued", "running", "waiting_approval", "waiting_input"]),
            )
        )
        if active is not None:
            raise ApiError(409, "session.runAlreadyActive")
    if "title" in fields and payload.title is not None:
        item.title = payload.title
        item.title_generation_status = TitleGenerationStatus.SKIPPED.value
    if "pinned" in fields and payload.pinned is not None:
        item.is_pinned = payload.pinned
    if "control_center" in fields:
        item.control_center = payload.control_center.model_dump(mode="json")
    if "model_id" in fields:
        ai = await load_effective_ai(db, user.id)
        if payload.model_id is not None:
            if not _model_available(ai, payload.model_id):
                raise ApiError(400, "session.modelUnavailable")
        effective_model_id = payload.model_id or ai.default_model_id
        if not _model_available(ai, effective_model_id):
            raise ApiError(409, "session.modelUnavailable")
        if not _model_supports_tools(ai, effective_model_id):
            raise ApiError(409, "session.modelToolsUnavailable")
        item.model_config_id = payload.model_id
    if "memory_mode" in fields and payload.memory_mode is not None:
        item.memory_mode = payload.memory_mode.value
    if "web_search_policy" in fields and payload.web_search_policy is not None:
        if payload.web_search_policy == WebSearchPolicy.REQUIRED:
            ai = await load_effective_ai(db, user.id)
            model_id = item.model_config_id or ai.default_model_id
            if not _model_supports_tools(ai, model_id):
                raise ApiError(409, "session.modelToolsUnavailable")
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
            working_directory = payload.working_directory if "working_directory" in fields else None
        item.working_directory, item.current_directory, root_id, root_revision = await _validate_execution_preferences(
            db, user.id, agent_id, root_id, working_directory
        )
        item.preferred_execution_agent_id = agent_id
        item.preferred_root_id = root_id
        await _add_directory_grant(
            db, item.id, agent_id, root_id, root_revision, item.current_directory
        )
    if "workspace_access" in fields and payload.workspace_access is not None:
        item.workspace_access = payload.workspace_access
    await db.flush()
    await db.refresh(item)
    await db.commit()
    return await _session_payload(db, item)


@router.get("/{session_id}/delete-impact")
async def session_delete_impact(
    session_id: UUID,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict[str, int | str]:
    if await _owned_session(db, session_id, user.id) is None:
        raise ApiError(404, "session.notFound")
    linked = list(
        (
            await db.scalars(
                select(Automation).where(
                    Automation.user_id == user.id,
                    Automation.output_session_id == session_id,
                )
            )
        ).all()
    )
    return {
        "automationCount": len(linked),
        "requiredAutomationCount": sum(item.output_session_required for item in linked),
        "automationImpact": _automation_impact_token(linked),
    }


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
    item = await db.scalar(
        select(ChatSession).where(
            ChatSession.id == session_id,
            ChatSession.user_id == user.id,
            ChatSession.session_type == "chat",
        )
    )
    if item is None:
        raise ApiError(404, "session.notFound")
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
    if automation_mode is not None and automation_impact != _automation_impact_token(
        linked_automations
    ):
        raise ApiError(409, "session.automationImpactChanged")
    if required_count and automation_mode != "deleteAutomations":
        raise ApiError(
            409,
            "session.automationOutputRequired",
            params={
                "automationCount": required_count,
                "totalAutomationCount": len(linked_automations),
            },
        )
    if linked_automations and automation_mode is None:
        raise ApiError(
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
                        AgentRun.status.in_(
                            ["queued", "running", "waiting_approval", "waiting_input"]
                        ),
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
                    AgentRun.status.in_(["queued", "running", "waiting_approval", "waiting_input"]),
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
    item = await _owned_session(db, session_id, user.id, for_update=True)
    if item is None:
        raise ApiError(404, "session.notFound")
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
    if automation_mode is not None and automation_impact != _automation_impact_token(
        linked_automations
    ):
        raise ApiError(409, "session.automationImpactChanged")
    if required_count and automation_mode != "deleteAutomations":
        raise ApiError(
            409,
            "session.automationOutputRequired",
            params={
                "automationCount": required_count,
                "totalAutomationCount": len(linked_automations),
            },
        )
    if linked_automations and automation_mode is None:
        raise ApiError(
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
                        AgentRun.status.in_(
                            ["queued", "running", "waiting_approval", "waiting_input"]
                        ),
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
    session_storage = SessionStorage()
    storage_session_ids = [
        session_id,
        *(execution_session.id for execution_session in execution_sessions),
    ]
    trashed_trees = []
    try:
        for storage_session_id in storage_session_ids:
            trashed_trees.append(
                await asyncio.to_thread(
                    session_storage.trash_session, storage_session_id
                )
            )
    except (OSError, SessionStorageError) as exc:
        for storage_session_id, trashed in zip(
            storage_session_ids, trashed_trees, strict=False
        ):
            if trashed is not None:
                try:
                    await asyncio.to_thread(
                        session_storage.restore_session, storage_session_id, trashed
                    )
                except Exception:
                    logger.exception("Partially trashed session storage could not be restored")
        logger.exception("Session storage could not be moved to trash")
        raise ApiError(500, "session.storageUnavailable") from exc

    async def restore_tree() -> None:
        for storage_session_id, trashed in zip(
            storage_session_ids, trashed_trees, strict=False
        ):
            if trashed is None:
                continue
            try:
                await asyncio.to_thread(
                    session_storage.restore_session, storage_session_id, trashed
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
) -> FileResponse:
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
    item = await _owned_session(db, session_id, user.id, for_update=True)
    if item is None:
        raise ApiError(404, "session.notFound")
    content = payload.content.strip()
    if not content:
        raise ApiError(422, "validation.required", field="content")
    active = await db.scalar(
        select(AgentRun).where(
            AgentRun.session_id == session_id,
            AgentRun.status.in_(["queued", "running", "waiting_approval", "waiting_input"]),
        )
    )
    if active:
        raise ApiError(409, "session.runAlreadyActive")
    ai = await load_effective_ai(db, user.id)
    model_id = item.model_config_id or ai.default_model_id
    if not _model_available(ai, model_id):
        raise ApiError(409, "session.modelUnavailable")
    if not _model_supports_tools(ai, model_id):
        raise ApiError(409, "session.modelToolsUnavailable")
    try:
        run = (
            await AgentRuntime.start(db, item, content, attachments=attachments)
            if attachments
            else await AgentRuntime.start(db, item, content)
        )
    except AgentRunError as exc:
        raise ApiError(409, exc.code, params=exc.params) from exc
    user_message = await _latest_user_message(db, item.id)
    return {
        "runId": str(run.id),
        "modelName": run.model_name,
        "message": message_payload(user_message),
    }


@router.post("/{session_id}/messages", status_code=status.HTTP_202_ACCEPTED)
async def send_message(
    session_id: UUID,
    payload: SendMessageRequest,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict[str, Any]:
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
    file_settings = await SettingsStore.load(db, FileMediaSettings)
    return await _send_message(
        session_id,
        request,
        user,
        db,
        await _attachment_uploads(attachments, file_settings),
    )


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
    if run is None or run.session_id != session_id:
        raise ApiError(404, "session.runNotFound")
    return run_payload(run)


@router.post("/{session_id}/runs/{run_id}/cancel")
async def cancel_run(
    session_id: UUID,
    run_id: UUID,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict:
    if await _owned_session(db, session_id, user.id) is None:
        raise ApiError(404, "session.runNotFound")
    run = await db.get(AgentRun, run_id)
    if run is None or run.session_id != session_id:
        raise ApiError(404, "session.runNotFound")
    if run.status in {"completed", "failed", "cancelled"}:
        return run_payload(run)

    await AgentRuntime.cancel_and_wait(run_id)
    await db.refresh(run)
    if run.status not in {"completed", "failed", "cancelled"}:
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
    if run is None or run.session_id != session_id:
        raise ApiError(404, "session.runNotFound")
    run_status = run.status
    run_error = run.error
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
                event = {"type": "run.failed", "error": error.payload()}
            else:
                event = {"type": f"run.{run_status}", "runId": str(run_id)}
            yield f"event: {event['type']}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
            return
        async for event in run_events.subscribe(run_id):
            event_type = event.get("type", "message")
            yield f"event: {event_type}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
            if event_type in {"run.completed", "run.failed", "run.cancelled"}:
                break

    return StreamingResponse(events(), media_type="text/event-stream")
