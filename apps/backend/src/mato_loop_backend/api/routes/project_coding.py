from __future__ import annotations

import base64
import binascii
import re
from collections.abc import Awaitable, Mapping, Sequence
from datetime import datetime
from pathlib import PurePosixPath
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.api.errors import ApiError
from mato_loop_backend.db.session import get_db_session
from mato_loop_backend.models import Project, ProjectPlan, ProjectPlanExecution, UserAccount
from mato_loop_backend.project_coding import (
    MAX_BRIEF_CHARS,
    MAX_PLAN_CHARS,
    MAX_TITLE_CHARS,
    CodingFileContent,
    CodingFileEntry,
    CodingFileStatus,
    CodingModelOption,
    CodingProjectOption,
    ProjectCodingError,
    ProjectCodingService,
)
from mato_loop_backend.security import require_user
from mato_loop_backend.settings import AgentIdentitySettings, SettingsStore

router = APIRouter(prefix="/projects", dependencies=[Depends(require_user)])

_ERROR_CODE = re.compile(r"^(?:project|integration|opencode|validation|session)\.[a-zA-Z]+$")
_SAFE_BINARY_MIMES = {
    "application/octet-stream",
    "application/pdf",
    "audio/mpeg",
    "audio/ogg",
    "audio/wav",
    "image/gif",
    "image/jpeg",
    "image/png",
    "image/webp",
    "video/mp4",
    "video/webm",
}
_NOT_FOUND_CODES = {
    "integration.notFound",
    "project.executionNotFound",
    "project.notFound",
    "project.planNotFound",
    "session.notFound",
}
_UNAVAILABLE_CODES = {
    "opencode.rateLimited",
    "opencode.timeout",
    "opencode.unavailable",
}


class ApiModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        extra="forbid",
        populate_by_name=True,
    )


class CodingProjectOptionResponse(ApiModel):
    project_key: str
    workspace_key: str
    label: str


class CodingProjectOptionsResponse(ApiModel):
    items: list[CodingProjectOptionResponse]


class CodingBindingRequest(ApiModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        extra="forbid",
        populate_by_name=True,
    )

    integration_id: UUID
    project_key: str = Field(min_length=1, max_length=300)
    workspace_key: str = Field(min_length=1, max_length=1000)


class CodingBindingResponse(ApiModel):
    adapter_type: Literal["opencode"] = "opencode"
    integration_id: UUID
    project_key: str
    workspace_key: str
    analysis_model_key: str | None
    analysis_model_variant: str | None
    execution_model_key: str | None
    execution_model_variant: str | None
    queue_pause_reason: str | None


class CodingModelOptionResponse(ApiModel):
    key: str
    label: str
    provider_label: str
    variants: list[str]


class CodingModelSelectionResponse(ApiModel):
    workspace_key: str
    analysis_model_key: str | None
    analysis_model_variant: str | None
    execution_model_key: str | None
    execution_model_variant: str | None


class CodingModelsResponse(CodingModelSelectionResponse):
    items: list[CodingModelOptionResponse]


class CodingModelsRequest(ApiModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        extra="forbid",
        populate_by_name=True,
        strict=True,
    )

    analysis_model_key: str = Field(min_length=3, max_length=600)
    analysis_model_variant: str | None = Field(default=None, min_length=1, max_length=300)
    execution_model_key: str = Field(min_length=3, max_length=600)
    execution_model_variant: str | None = Field(default=None, min_length=1, max_length=300)


class CodingFileEntryResponse(ApiModel):
    path: str
    name: str
    type: Literal["file", "directory"]
    ignored: bool


class CodingFilesResponse(ApiModel):
    workspace_key: str
    path: str
    items: list[CodingFileEntryResponse]


class CodingFileContentResponse(ApiModel):
    workspace_key: str
    path: str
    type: Literal["text", "binary"]
    content: str
    encoding: Literal["base64"] | None
    mime_type: str


class CodingFileStatusResponse(ApiModel):
    path: str
    added: int = Field(ge=0)
    removed: int = Field(ge=0)
    status: Literal["added", "deleted", "modified"]


class CodingFileStatusesResponse(ApiModel):
    workspace_key: str
    items: list[CodingFileStatusResponse]


class CodingAnalyzeRequest(ApiModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        extra="forbid",
        populate_by_name=True,
        strict=True,
    )

    title: str = Field(min_length=1, max_length=MAX_TITLE_CHARS)
    brief: str = Field(min_length=1, max_length=MAX_BRIEF_CHARS)


class CodingAnalysisResponse(ApiModel):
    workspace_key: str
    analysis_model_key: str
    analysis_model_variant: str | None
    title: str
    text: str
    external_session_id: str


class CodingPlanRequest(ApiModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        extra="forbid",
        populate_by_name=True,
        strict=True,
    )

    title: str = Field(min_length=1, max_length=MAX_TITLE_CHARS)
    markdown: str = Field(min_length=1, max_length=MAX_PLAN_CHARS)
    originating_session_id: UUID | None = None
    external_session_id: str | None = Field(default=None, min_length=1, max_length=300)


class CodingPlanResponse(ApiModel):
    id: UUID
    project_id: UUID
    workspace_key: str
    originating_session_id: UUID | None
    external_session_id: str | None
    analysis_model_key: str
    analysis_model_variant: str | None
    title: str
    markdown: str
    created_at: datetime | None
    updated_at: datetime | None


class CodingPlansResponse(ApiModel):
    workspace_key: str
    items: list[CodingPlanResponse]


class CodingEnqueueRequest(ApiModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        extra="forbid",
        populate_by_name=True,
        strict=True,
    )

    plan_ids: list[UUID] = Field(min_length=1, max_length=500)


class CodingPermissionResponse(ApiModel):
    id: str
    title: str
    type: str
    patterns: list[str]


class CodingExecutionResponse(ApiModel):
    id: UUID
    project_id: UUID
    plan_id: UUID
    workspace_key: str
    queue_position: int = Field(gt=0)
    status: Literal[
        "queued",
        "running",
        "waiting_permission",
        "completed",
        "failed",
        "cancelled",
        "outcome_unknown",
    ]
    model_key: str
    model_variant: str | None
    external_session_id: str | None
    pending_permission: CodingPermissionResponse | None
    result_summary: str | None
    error_code: str | None
    created_at: datetime | None
    started_at: datetime | None
    completed_at: datetime | None


class CodingExecutionsResponse(ApiModel):
    workspace_key: str
    queue_pause_reason: str | None
    items: list[CodingExecutionResponse]


class CodingQueueResponse(ApiModel):
    workspace_key: str
    paused: bool
    queue_pause_reason: str | None


class CodingPermissionRequest(ApiModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        extra="forbid",
        populate_by_name=True,
        strict=True,
    )

    response: Literal["once", "always", "reject"]


async def _owned_project(
    db: AsyncSession, project_id: UUID, user_id: UUID
) -> Project:
    project = await db.scalar(
        select(Project).where(Project.id == project_id, Project.user_id == user_id)
    )
    if project is None:
        raise ApiError(status.HTTP_404_NOT_FOUND, "project.notFound")
    return project


def _mapped_error(exc: ProjectCodingError) -> ApiError:
    code = exc.code if _ERROR_CODE.fullmatch(exc.code) else "http.conflict"
    params = {
        key: value
        for key, value in (exc.params or {}).items()
        if key in {"keys", "maxLength", "minLength"}
    } or None
    field = (exc.params or {}).get("field")
    field = field if isinstance(field, str) else None
    if code.startswith("opencode."):
        status_code = 503 if code in _UNAVAILABLE_CODES else 502
    elif code.startswith("validation."):
        status_code = 422
    elif code in _NOT_FOUND_CODES:
        status_code = 404
    else:
        status_code = 409
    return ApiError(status_code, code, field=field, params=params)


async def _call[T](awaitable: Awaitable[T]) -> T:
    try:
        return await awaitable
    except ProjectCodingError as exc:
        raise _mapped_error(exc) from exc


def _relative(value: str, *, allow_root: bool = False) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 4096
        or "\\" in value
        or any(ord(character) < 32 for character in value)
    ):
        raise ApiError(502, "integration.invalidResponse")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or (not allow_root and path == PurePosixPath(".")):
        raise ApiError(502, "integration.invalidResponse")
    return path.as_posix()


def _workspace(project: Project) -> str:
    if project.coding_workspace_key is None:
        raise ApiError(409, "project.codingNotConfigured")
    try:
        return _relative(project.coding_workspace_key, allow_root=True)
    except ApiError as exc:
        raise ApiError(409, "project.codingBindingInvalid") from exc


def _project_option(item: CodingProjectOption) -> CodingProjectOptionResponse:
    return CodingProjectOptionResponse(
        project_key=item.project_key,
        workspace_key=_relative(item.workspace_key, allow_root=True),
        label=item.label,
    )


def _model_option(item: CodingModelOption) -> CodingModelOptionResponse:
    return CodingModelOptionResponse(
        key=item.key,
        label=item.label,
        provider_label=item.provider_label,
        variants=list(item.variants),
    )


def _binding(project: Project) -> CodingBindingResponse:
    if (
        project.coding_integration_id is None
        or project.coding_project_key is None
        or project.coding_workspace_key is None
    ):
        raise ApiError(409, "project.codingNotConfigured")
    return CodingBindingResponse(
        integration_id=project.coding_integration_id,
        project_key=project.coding_project_key,
        workspace_key=_relative(project.coding_workspace_key, allow_root=True),
        analysis_model_key=project.coding_analysis_model_key,
        analysis_model_variant=project.coding_analysis_model_variant,
        execution_model_key=project.coding_execution_model_key,
        execution_model_variant=project.coding_execution_model_variant,
        queue_pause_reason=project.coding_queue_pause_reason,
    )


def _file_entry(item: CodingFileEntry) -> CodingFileEntryResponse:
    return CodingFileEntryResponse(
        path=_relative(item.path),
        name=item.name,
        type=item.type,
        ignored=item.ignored,
    )


def _file_content(
    project: Project, path: str, item: CodingFileContent
) -> CodingFileContentResponse:
    if item.type == "binary":
        if item.encoding != "base64":
            raise ApiError(502, "integration.invalidResponse")
        try:
            base64.b64decode(item.content, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ApiError(502, "integration.invalidResponse") from exc
        mime_type = (
            item.mime_type.lower()
            if isinstance(item.mime_type, str)
            and item.mime_type.lower() in _SAFE_BINARY_MIMES
            else "application/octet-stream"
        )
        encoding: Literal["base64"] | None = "base64"
    else:
        if item.encoding is not None:
            raise ApiError(502, "integration.invalidResponse")
        mime_type = "text/plain; charset=utf-8"
        encoding = None
    return CodingFileContentResponse(
        workspace_key=_workspace(project),
        path=_relative(path),
        type=item.type,
        content=item.content,
        encoding=encoding,
        mime_type=mime_type,
    )


def _file_status(item: CodingFileStatus) -> CodingFileStatusResponse:
    return CodingFileStatusResponse(
        path=_relative(item.path),
        added=item.added,
        removed=item.removed,
        status=item.status,
    )


def _plan(project: Project, item: ProjectPlan) -> CodingPlanResponse:
    return CodingPlanResponse(
        id=item.id,
        project_id=item.project_id,
        workspace_key=_workspace(project),
        originating_session_id=item.originating_session_id,
        external_session_id=item.external_session_id,
        analysis_model_key=item.analysis_model_key,
        analysis_model_variant=item.analysis_model_variant,
        title=item.title,
        markdown=item.markdown,
        created_at=item.created_at,
        updated_at=item.updated_at,
    )


def _safe_text(value: Any, limit: int) -> str | None:
    if not isinstance(value, str) or len(value) > limit:
        return None
    if any(ord(character) < 32 and character not in "\n\r\t" for character in value):
        return None
    return value


def _permission(value: Any) -> CodingPermissionResponse | None:
    if not isinstance(value, Mapping):
        return None
    permission_id = _safe_text(value.get("id"), 300)
    title = _safe_text(value.get("title"), 1000)
    permission_type = _safe_text(value.get("type"), 200)
    raw_patterns = value.get("patterns")
    if (
        permission_id is None
        or title is None
        or permission_type is None
        or not isinstance(raw_patterns, Sequence)
        or isinstance(raw_patterns, (str, bytes))
        or len(raw_patterns) > 500
    ):
        return None
    patterns = [_safe_text(pattern, 4096) for pattern in raw_patterns]
    if any(pattern is None for pattern in patterns):
        return None
    return CodingPermissionResponse(
        id=permission_id,
        title=title,
        type=permission_type,
        patterns=patterns,
    )


def _execution(
    project: Project, item: ProjectPlanExecution
) -> CodingExecutionResponse:
    error_code = item.error_code
    if error_code is not None and not _ERROR_CODE.fullmatch(error_code):
        error_code = "project.codingExecutionFailed"
    return CodingExecutionResponse(
        id=item.id,
        project_id=item.project_id,
        plan_id=item.plan_id,
        workspace_key=_workspace(project),
        queue_position=item.queue_position,
        status=item.status,
        model_key=item.model_key,
        model_variant=item.model_variant,
        external_session_id=item.external_session_id,
        pending_permission=_permission(item.pending_permission),
        result_summary=_safe_text(item.result_summary, 200_000),
        error_code=error_code,
        created_at=item.created_at,
        started_at=item.started_at,
        completed_at=item.completed_at,
    )


def _executions(
    project: Project, items: Sequence[ProjectPlanExecution]
) -> CodingExecutionsResponse:
    return CodingExecutionsResponse(
        workspace_key=_workspace(project),
        queue_pause_reason=project.coding_queue_pause_reason,
        items=[_execution(project, item) for item in items],
    )


@router.get(
    "/coding/integrations/{integration_id}/projects",
    response_model=CodingProjectOptionsResponse,
)
async def list_integration_projects(
    integration_id: UUID,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> CodingProjectOptionsResponse:
    items = await _call(
        ProjectCodingService.list_integrations_projects(db, user.id, integration_id)
    )
    return CodingProjectOptionsResponse(items=[_project_option(item) for item in items])


@router.put("/{project_id}/coding/binding", response_model=CodingBindingResponse)
async def set_binding(
    project_id: UUID,
    payload: CodingBindingRequest,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> CodingBindingResponse:
    project = await _owned_project(db, project_id, user.id)
    project = await _call(
        ProjectCodingService.set_binding(
            db,
            user.id,
            project,
            payload.integration_id,
            payload.project_key,
            payload.workspace_key,
        )
    )
    return _binding(project)


@router.delete("/{project_id}/coding/binding", status_code=status.HTTP_204_NO_CONTENT)
async def clear_binding(
    project_id: UUID,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> None:
    project = await _owned_project(db, project_id, user.id)
    await _call(ProjectCodingService.clear_binding(db, user.id, project))


@router.get("/{project_id}/coding/models", response_model=CodingModelsResponse)
async def list_models(
    project_id: UUID,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> CodingModelsResponse:
    project = await _owned_project(db, project_id, user.id)
    items = await _call(ProjectCodingService.list_models(db, user.id, project))
    return CodingModelsResponse(
        workspace_key=_workspace(project),
        analysis_model_key=project.coding_analysis_model_key,
        analysis_model_variant=project.coding_analysis_model_variant,
        execution_model_key=project.coding_execution_model_key,
        execution_model_variant=project.coding_execution_model_variant,
        items=[_model_option(item) for item in items],
    )


@router.put(
    "/{project_id}/coding/models", response_model=CodingModelSelectionResponse
)
async def set_models(
    project_id: UUID,
    payload: CodingModelsRequest,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> CodingModelSelectionResponse:
    project = await _owned_project(db, project_id, user.id)
    project = await _call(
        ProjectCodingService.set_models(
            db,
            user.id,
            project,
            payload.analysis_model_key,
            payload.analysis_model_variant,
            payload.execution_model_key,
            payload.execution_model_variant,
        )
    )
    return CodingModelSelectionResponse(
        workspace_key=_workspace(project),
        analysis_model_key=project.coding_analysis_model_key,
        analysis_model_variant=project.coding_analysis_model_variant,
        execution_model_key=project.coding_execution_model_key,
        execution_model_variant=project.coding_execution_model_variant,
    )


@router.get("/{project_id}/coding/files", response_model=CodingFilesResponse)
async def list_files(
    project_id: UUID,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
    path: Annotated[str, Query(min_length=1, max_length=4096)] = ".",
) -> CodingFilesResponse:
    project = await _owned_project(db, project_id, user.id)
    items = await _call(
        ProjectCodingService.list_files(db, user.id, project, path)
    )
    return CodingFilesResponse(
        workspace_key=_workspace(project),
        path=_relative(path, allow_root=True),
        items=[_file_entry(item) for item in items],
    )


@router.get(
    "/{project_id}/coding/files/content", response_model=CodingFileContentResponse
)
async def read_file(
    project_id: UUID,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
    path: Annotated[str, Query(min_length=1, max_length=4096)],
) -> CodingFileContentResponse:
    project = await _owned_project(db, project_id, user.id)
    item = await _call(ProjectCodingService.read_file(db, user.id, project, path))
    return _file_content(project, path, item)


@router.get(
    "/{project_id}/coding/files/status", response_model=CodingFileStatusesResponse
)
async def file_status(
    project_id: UUID,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> CodingFileStatusesResponse:
    project = await _owned_project(db, project_id, user.id)
    items = await _call(ProjectCodingService.file_status(db, user.id, project))
    return CodingFileStatusesResponse(
        workspace_key=_workspace(project),
        items=[_file_status(item) for item in items],
    )


@router.post("/{project_id}/coding/analyze", response_model=CodingAnalysisResponse)
async def analyze(
    project_id: UUID,
    payload: CodingAnalyzeRequest,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> CodingAnalysisResponse:
    project = await _owned_project(db, project_id, user.id)
    identity = await SettingsStore.load(db, AgentIdentitySettings, user_id=user.id)
    result = await _call(
        ProjectCodingService.analyze(
            db,
            user.id,
            project,
            payload.brief,
            payload.title,
            identity.instructions,
            project.instructions,
        )
    )
    return CodingAnalysisResponse(
        workspace_key=_workspace(project),
        analysis_model_key=project.coding_analysis_model_key or "",
        analysis_model_variant=project.coding_analysis_model_variant,
        title=payload.title.strip(),
        text=result.text,
        external_session_id=result.external_session_id,
    )


@router.get("/{project_id}/coding/plans", response_model=CodingPlansResponse)
async def list_plans(
    project_id: UUID,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> CodingPlansResponse:
    project = await _owned_project(db, project_id, user.id)
    items = await _call(ProjectCodingService.list_plans(db, user.id, project))
    return CodingPlansResponse(
        workspace_key=_workspace(project),
        items=[_plan(project, item) for item in items],
    )


@router.post(
    "/{project_id}/coding/plans",
    response_model=CodingPlanResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_plan(
    project_id: UUID,
    payload: CodingPlanRequest,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> CodingPlanResponse:
    project = await _owned_project(db, project_id, user.id)
    item = await _call(
        ProjectCodingService.create_plan(
            db,
            user.id,
            project,
            payload.originating_session_id,
            payload.title,
            payload.markdown,
            payload.external_session_id,
        )
    )
    return _plan(project, item)


@router.post(
    "/{project_id}/coding/plans/enqueue",
    response_model=CodingExecutionsResponse,
    status_code=status.HTTP_201_CREATED,
)
async def enqueue(
    project_id: UUID,
    payload: CodingEnqueueRequest,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> CodingExecutionsResponse:
    project = await _owned_project(db, project_id, user.id)
    items = await _call(
        ProjectCodingService.enqueue(db, user.id, project, payload.plan_ids)
    )
    return _executions(project, items)


@router.get(
    "/{project_id}/coding/executions", response_model=CodingExecutionsResponse
)
async def list_executions(
    project_id: UUID,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> CodingExecutionsResponse:
    project = await _owned_project(db, project_id, user.id)
    items = await _call(ProjectCodingService.list_executions(db, user.id, project))
    return _executions(project, items)


@router.post("/{project_id}/coding/queue/pause", response_model=CodingQueueResponse)
async def pause_queue(
    project_id: UUID,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> CodingQueueResponse:
    project = await _owned_project(db, project_id, user.id)
    project = await _call(ProjectCodingService.pause(db, user.id, project))
    return CodingQueueResponse(
        workspace_key=_workspace(project),
        paused=True,
        queue_pause_reason=project.coding_queue_pause_reason,
    )


@router.post("/{project_id}/coding/queue/resume", response_model=CodingQueueResponse)
async def resume_queue(
    project_id: UUID,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> CodingQueueResponse:
    project = await _owned_project(db, project_id, user.id)
    project = await _call(ProjectCodingService.resume(db, user.id, project))
    return CodingQueueResponse(
        workspace_key=_workspace(project),
        paused=False,
        queue_pause_reason=project.coding_queue_pause_reason,
    )


@router.post(
    "/{project_id}/coding/executions/{execution_id}/cancel",
    response_model=CodingExecutionResponse,
)
async def cancel_execution(
    project_id: UUID,
    execution_id: UUID,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> CodingExecutionResponse:
    project = await _owned_project(db, project_id, user.id)
    item = await _call(
        ProjectCodingService.cancel(db, user.id, project, execution_id)
    )
    return _execution(project, item)


@router.post(
    "/{project_id}/coding/executions/{execution_id}/permission",
    response_model=CodingExecutionResponse,
)
async def reply_permission(
    project_id: UUID,
    execution_id: UUID,
    payload: CodingPermissionRequest,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> CodingExecutionResponse:
    project = await _owned_project(db, project_id, user.id)
    item = await _call(
        ProjectCodingService.reply_permission(
            db, user.id, project, execution_id, payload.response
        )
    )
    return _execution(project, item)
