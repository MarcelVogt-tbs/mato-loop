from __future__ import annotations

from datetime import datetime
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import urlencode
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    model_validator,
)

from mato_loop_backend.error_contract import describe_error
from mato_loop_backend.integrations import WebFetchError, canonicalize_url
from mato_loop_backend.message_content import canonical_assistant_content
from mato_loop_backend.models import (
    AgentRun,
    ChatSession,
    MemoryMode,
    Message,
    WebSearchPolicy,
)
from mato_loop_backend.workspace import RootFileError, RootFileService

LAST_MESSAGE_AT_UNSET = object()


class _StoredFileAction(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    operation: str = Field(pattern=r"^(write|copy|move)$")
    root: str = Field(pattern=r"^(session|workspace)$")
    root_identity: str | None = Field(
        default=None, alias="rootIdentity", min_length=1, max_length=1_000
    )
    path: str = Field(min_length=1, max_length=4_096)
    filename: str = Field(min_length=1, max_length=1_000)
    size_bytes: int = Field(alias="sizeBytes", ge=0)
    execution_agent_id: str | None = Field(
        default=None, alias="executionAgentId", min_length=1, max_length=36
    )
    current_directory: str | None = Field(
        default=None, alias="currentDirectory", min_length=1, max_length=4_096
    )

    @model_validator(mode="after")
    def validate_paths(self) -> _StoredFileAction:
        service = RootFileService(UUID(int=0))
        if self.root == "session" and (
            service.normalize_relative_path(self.path) != self.path or self.path == "."
        ):
            raise ValueError("path must be a normalized file path")
        if self.root == "workspace" and not PurePosixPath(self.path).is_absolute():
            raise ValueError("workspace path must be absolute")
        if self.filename != self.path.rsplit("/", 1)[-1]:
            raise ValueError("filename must match path")
        snapshot_fields = {"execution_agent_id", "current_directory"}
        if self.root == "session":
            if self.model_fields_set & snapshot_fields:
                raise ValueError("session actions cannot contain workspace snapshot fields")
            try:
                if self.root_identity is None:
                    raise ValueError
                if str(UUID(self.root_identity)) != self.root_identity:
                    raise ValueError("session identity must be a canonical UUID")
            except ValueError as exc:
                raise ValueError("session identity must be a canonical UUID") from exc
            return self
        if self.root_identity is not None or any(
            getattr(self, field) is None for field in snapshot_fields
        ):
            raise ValueError("workspace snapshot fields are required")
        assert self.execution_agent_id is not None
        assert self.current_directory is not None
        try:
            valid_id = str(UUID(self.execution_agent_id)) == self.execution_agent_id
        except ValueError:
            valid_id = False
        if not valid_id or not PurePosixPath(self.current_directory).is_absolute():
            raise ValueError("workspace identifiers must be canonical and absolute")
        return self


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
            linked_sources.append({"title": title.strip()[:500], "url": safe_url})
    return {"policy": policy, "used": used, "linkedSources": linked_sources}


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
        scope = "session" if action.root == "session" else "filesystem"
        query = {
            "path": action.path,
            "scope": scope,
            "session_id": str(item.session_id),
            **(
                {"root_identity": action.root_identity}
                if action.root == "session"
                else {"execution_agent_id": action.execution_agent_id}
            ),
        }
        if action.root == "workspace":
            query.update(
                execution_agent_id=action.execution_agent_id,
                current_directory=action.current_directory,
            )
        actions.append(
            {
                **action.model_dump(mode="json", by_alias=True, exclude_none=True),
                "downloadUrl": "/api/workspace/files/download?" + urlencode(query),
            }
        )
    return actions


def rebind_session_file_metadata(
    session: ChatSession,
    messages: list[Message],
    source_identity: UUID,
    target_identity: UUID | None,
) -> None:
    source = str(source_identity)
    target = str(target_identity) if target_identity is not None else None
    control_center = dict(session.control_center or {})
    file_view = control_center.get("file_view")
    if (
        isinstance(file_view, dict)
        and file_view.get("scope") == "session"
        and file_view.get("root_identity") == source
    ):
        control_center["file_view"] = (
            {**file_view, "root_identity": target} if target is not None else None
        )
        session.control_center = control_center
    for message in messages:
        provider_data = dict(message.provider_data or {})
        runtime_data = provider_data.get("_matoloop")
        if not isinstance(runtime_data, dict):
            continue
        rebound_runtime = dict(runtime_data)
        actions = runtime_data.get("fileActions")
        if isinstance(actions, list):
            rebound_runtime["fileActions"] = [
                {**action, "rootIdentity": target}
                if target is not None
                and isinstance(action, dict)
                and action.get("root") == "session"
                and action.get("rootIdentity") == source
                else action
                for action in actions
                if target is not None
                or not (
                    isinstance(action, dict)
                    and action.get("root") == "session"
                    and action.get("rootIdentity") == source
                )
            ]
        active_file = runtime_data.get("activeFile")
        if (
            isinstance(active_file, dict)
            and active_file.get("root") == "session"
            and active_file.get("rootIdentity") == source
        ):
            if target is None:
                rebound_runtime.pop("activeFile", None)
            else:
                rebound_runtime["activeFile"] = {**active_file, "rootIdentity": target}
        if rebound_runtime != runtime_data:
            provider_data["_matoloop"] = rebound_runtime
            message.provider_data = provider_data


def session_payload(
    item: ChatSession,
    control_center: dict[str, Any],
    *,
    effective_memory_mode: MemoryMode | None = None,
    memory_tools_available: bool | None = None,
    memory_unavailable_reason: str | None = None,
    workspace_context: dict[str, Any] | None = None,
    last_message_at: datetime | None | object = LAST_MESSAGE_AT_UNSET,
    has_unread_messages: bool | None = None,
) -> dict[str, Any]:
    file_view = control_center.get("file_view")
    payload = {
        "id": str(item.id),
        "projectId": str(item.project_id) if item.project_id else None,
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
                    "currentDirectory": file_view.get("current_directory"),
                }
                if isinstance(file_view, dict)
                else None
            ),
        },
        "modelId": str(item.model_config_id) if item.model_config_id else None,
        "reasoningEffort": item.reasoning_effort,
        "memoryMode": item.memory_mode,
        "webSearchPolicy": item.web_search_policy,
        "preferredExecutionAgentId": (
            str(item.preferred_execution_agent_id)
            if item.preferred_execution_agent_id
            else None
        ),
        "currentDirectory": item.current_directory,
        "workspaceContext": workspace_context
        or {
            "executionAgentId": (
                str(item.preferred_execution_agent_id)
                if item.preferred_execution_agent_id
                else None
            ),
            "connected": False,
            "operations": [],
        },
        "createdAt": item.created_at.isoformat() if item.created_at else None,
        "updatedAt": item.updated_at.isoformat() if item.updated_at else None,
    }
    if last_message_at is not LAST_MESSAGE_AT_UNSET:
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


def message_payload(item: Message, *, retry_available: bool = False) -> dict[str, Any]:
    error = (
        describe_error(item.error_code, params=item.error_params)
        if item.error_code
        else None
    )
    source = (item.provider_data or {}).get("_matoloop", {}).get("source", {})
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
        "sourceType": (
            "automationRun"
            if isinstance(source, dict) and source.get("type") == "automationRun"
            else None
        ),
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
                "fileRootId": str(attachment.file_root_id) if attachment.file_root_id else None,
                "fileId": str(attachment.file_id) if attachment.file_id else None,
                "fileRevision": attachment.file_revision,
                "filePath": attachment.file_path,
                "url": (
                    f"/api/sessions/{item.session_id}/messages/{item.id}"
                    f"/attachments/{attachment.id}"
                ),
            }
            for attachment in item.attachments
        ],
        "createdAt": item.created_at.isoformat() if item.created_at else None,
    }


def run_payload(run: AgentRun) -> dict[str, Any]:
    return {
        "id": str(run.id),
        "sessionId": str(run.session_id),
        "status": run.status,
        "modelName": run.model_name,
        "error": describe_error(run.error).payload() if run.error else None,
        "createdAt": run.created_at.isoformat() if run.created_at else None,
    }
