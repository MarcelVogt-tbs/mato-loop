from __future__ import annotations

import asyncio
import hashlib
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.agent import AgentRuntime
from mato_loop_backend.agent.session_secrets import session_secrets
from mato_loop_backend.api.errors import ApiError
from mato_loop_backend.automations import ACTIVE_STATUSES, automation_impact_token
from mato_loop_backend.db.session import get_db_session
from mato_loop_backend.models import (
    AgentRun,
    Automation,
    AutomationRun,
    ChatSession,
    Message,
    MessageAttachment,
    Project,
    ProjectPlanExecution,
    UserAccount,
)
from mato_loop_backend.security import require_user
from mato_loop_backend.session_storage import SessionStorage, SessionStorageError
from mato_loop_backend.sessions.cleanup import descendant_sessions
from mato_loop_backend.sessions.service import (
    SessionServiceError,
    validate_execution_preferences,
)

router = APIRouter(prefix="/projects", dependencies=[Depends(require_user)])
logger = logging.getLogger(__name__)


class ProjectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    name: str = Field(min_length=1, max_length=240)
    instructions: str = Field(default="", max_length=4000)
    default_execution_agent_id: UUID | None = Field(
        default=None,
        validation_alias=AliasChoices("defaultExecutionAgentId", "default_execution_agent_id"),
    )
    default_current_directory: str | None = Field(
        default=None,
        max_length=4096,
        validation_alias=AliasChoices("defaultCurrentDirectory", "default_current_directory"),
    )

    @field_validator("name", mode="before")
    @classmethod
    def normalize_name(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator("instructions", mode="before")
    @classmethod
    def normalize_instructions(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


class ProjectCreateRequest(ProjectRequest):
    pass


class ProjectResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    name: str
    instructions: str
    default_execution_agent_id: UUID | None = Field(alias="defaultExecutionAgentId")
    default_current_directory: str | None = Field(alias="defaultCurrentDirectory")
    session_count: int = Field(alias="sessionCount")
    created_at: datetime = Field(alias="createdAt")
    updated_at: datetime = Field(alias="updatedAt")
    coding_service: dict | None = Field(alias="codingService")


class ProjectListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[ProjectResponse]


def _payload(project: Project, session_count: int) -> ProjectResponse:
    return ProjectResponse(
        id=project.id,
        name=project.name,
        instructions=project.instructions,
        defaultExecutionAgentId=project.default_execution_agent_id,
        defaultCurrentDirectory=project.default_current_directory,
        sessionCount=session_count,
        createdAt=project.created_at,
        updatedAt=project.updated_at,
        codingService=(
            {
                "adapterType": "opencode",
                "integrationId": str(project.coding_integration_id),
                "projectKey": project.coding_project_key,
                "workspaceKey": project.coding_workspace_key,
                "analysisModelKey": project.coding_analysis_model_key,
                "analysisModelVariant": project.coding_analysis_model_variant,
                "executionModelKey": project.coding_execution_model_key,
                "executionModelVariant": project.coding_execution_model_variant,
                "queuePauseReason": project.coding_queue_pause_reason,
            }
            if project.coding_integration_id is not None
            else None
        ),
    )


async def _owned_project(
    db: AsyncSession, project_id: UUID, user_id: UUID, *, for_update: bool = False
) -> Project:
    statement = select(Project).where(Project.id == project_id, Project.user_id == user_id)
    if for_update:
        statement = statement.with_for_update()
    project = await db.scalar(statement)
    if project is None:
        raise ApiError(status.HTTP_404_NOT_FOUND, "project.notFound")
    return project


async def _session_count(db: AsyncSession, project: Project) -> int:
    count = await db.scalar(
        select(func.count(ChatSession.id)).where(
            ChatSession.project_id == project.id,
            ChatSession.user_id == project.user_id,
            ChatSession.parent_session_id.is_(None),
        )
    )
    return count or 0


def _project_session_ids(project_id: UUID, user_id: UUID):
    return select(ChatSession.id).where(
        ChatSession.project_id == project_id,
        ChatSession.user_id == user_id,
        ChatSession.parent_session_id.is_(None),
    )


async def _external_file_reference_count(
    db: AsyncSession, session_ids: list[UUID]
) -> int:
    if not session_ids:
        return 0
    return int(
        await db.scalar(
            select(func.count())
            .select_from(MessageAttachment)
            .join(Message, Message.id == MessageAttachment.message_id)
            .where(
                MessageAttachment.file_root_id.in_(session_ids),
                Message.session_id.not_in(session_ids),
            )
        )
        or 0
    )


async def _linked_automations(
    db: AsyncSession,
    project_id: UUID,
    user_id: UUID,
    *,
    for_update: bool = False,
) -> list[Automation]:
    statement = select(Automation).where(
        Automation.user_id == user_id,
        Automation.output_session_id.in_(_project_session_ids(project_id, user_id)),
    )
    if for_update:
        statement = statement.execution_options(populate_existing=True).with_for_update()
    return list((await db.scalars(statement)).all())


def project_delete_impact_token(
    session_ids: list[UUID], automations: list[Automation]
) -> str:
    value = "|".join(str(item) for item in sorted(session_ids))
    return hashlib.sha256(
        f"sessions:{value}\nautomations:{automation_impact_token(automations)}".encode()
    ).hexdigest()


def _validate_automation_policy(
    automations: list[Automation],
    automation_mode: Literal["unlink", "deleteAutomations"] | None,
) -> None:
    required_count = sum(item.output_session_required for item in automations)
    if required_count and automation_mode != "deleteAutomations":
        raise ApiError(
            409,
            "session.automationOutputRequired",
            params={
                "automationCount": required_count,
                "totalAutomationCount": len(automations),
            },
        )
    if automations and automation_mode is None:
        raise ApiError(
            409,
            "session.automationOutputLinked",
            params={"automationCount": len(automations)},
        )


@router.get("", response_model=ProjectListResponse)
async def list_projects(
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> ProjectListResponse:
    rows = (
        await db.execute(
            select(Project, func.count(ChatSession.id))
            .outerjoin(
                ChatSession,
                (ChatSession.project_id == Project.id)
                & (ChatSession.user_id == Project.user_id)
                & ChatSession.parent_session_id.is_(None),
            )
            .where(Project.user_id == user.id)
            .group_by(Project.id)
            .order_by(Project.updated_at.desc(), Project.id.desc())
        )
    ).all()
    return ProjectListResponse(
        items=[_payload(project, session_count) for project, session_count in rows]
    )


@router.post("", response_model=ProjectResponse, status_code=status.HTTP_201_CREATED)
async def create_project(
    payload: ProjectCreateRequest,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> ProjectResponse:
    try:
        current_directory, _ = await validate_execution_preferences(
            db,
            user.id,
            payload.default_execution_agent_id,
            payload.default_current_directory,
        )
    except SessionServiceError as exc:
        raise ApiError(exc.status_code, exc.code, field=exc.field, params=exc.params) from exc
    project = Project(
        user_id=user.id,
        name=payload.name,
        instructions=payload.instructions,
        default_execution_agent_id=payload.default_execution_agent_id,
        default_current_directory=current_directory,
    )
    db.add(project)
    await db.commit()
    return _payload(project, 0)


@router.patch("/{project_id}", response_model=ProjectResponse)
async def update_project(
    project_id: UUID,
    payload: ProjectRequest,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> ProjectResponse:
    project = await _owned_project(db, project_id, user.id)
    session_count = await _session_count(db, project)
    project.name = payload.name
    if "instructions" in payload.model_fields_set:
        project.instructions = payload.instructions
    execution_fields = {
        "default_execution_agent_id",
        "default_current_directory",
    }
    if payload.model_fields_set & execution_fields:
        agent_id = (
            payload.default_execution_agent_id
            if "default_execution_agent_id" in payload.model_fields_set
            else project.default_execution_agent_id
        )
        current_directory = (
            payload.default_current_directory
            if "default_current_directory" in payload.model_fields_set
            else (
                None
                if agent_id != project.default_execution_agent_id
                else project.default_current_directory
            )
        )
        if (
            agent_id != project.default_execution_agent_id
            or current_directory != project.default_current_directory
        ):
            try:
                current_directory, _ = await validate_execution_preferences(
                    db, user.id, agent_id, current_directory
                )
            except SessionServiceError as exc:
                raise ApiError(
                    exc.status_code, exc.code, field=exc.field, params=exc.params
                ) from exc
            project.default_execution_agent_id = agent_id
            project.default_current_directory = current_directory
    project.updated_at = datetime.now(UTC)
    await db.commit()
    return _payload(project, session_count)


@router.get("/{project_id}/delete-impact")
async def project_delete_impact(
    project_id: UUID,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict[str, int | str | list[str]]:
    project = await _owned_project(db, project_id, user.id)
    project_db_id = project.id
    owner_id = project.user_id
    if await db.scalar(
        select(ProjectPlanExecution.id)
        .where(
            ProjectPlanExecution.project_id == project_db_id,
            ProjectPlanExecution.status.in_(("queued", "running", "waiting_permission")),
        )
        .limit(1)
    ) is not None:
        raise ApiError(409, "project.codingExecutionsActive")
    session_ids = list(
        (await db.scalars(_project_session_ids(project_db_id, owner_id))).all()
    )
    storage_session_ids = list(
        (
            await db.scalars(
                select(ChatSession.id).where(
                    ChatSession.project_id == project_db_id,
                    ChatSession.user_id == owner_id,
                )
            )
        ).all()
    )
    linked = await _linked_automations(db, project_db_id, owner_id)
    sorted_session_ids = sorted(session_ids)
    return {
        "sessionCount": len(sorted_session_ids),
        "sessionIds": [str(item) for item in sorted_session_ids],
        "automationCount": len(linked),
        "requiredAutomationCount": sum(
            item.output_session_required for item in linked
        ),
        "fileReferenceCount": await _external_file_reference_count(
            db, storage_session_ids
        ),
        "automationImpact": project_delete_impact_token(session_ids, linked),
    }


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(
    project_id: UUID,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
    automation_impact: Annotated[
        str,
        Query(alias="automationImpact", min_length=64, max_length=64),
    ],
    automation_mode: Annotated[
        Literal["unlink", "deleteAutomations"] | None,
        Query(alias="automationMode"),
    ] = None,
) -> None:
    project = await _owned_project(db, project_id, user.id)
    project_db_id = project.id
    owner_id = project.user_id
    session_ids = list(
        (await db.scalars(_project_session_ids(project_db_id, owner_id))).all()
    )
    linked_automations = await _linked_automations(db, project_db_id, owner_id)
    if automation_impact != project_delete_impact_token(
        session_ids, linked_automations
    ):
        raise ApiError(409, "session.automationImpactChanged")
    _validate_automation_policy(linked_automations, automation_mode)

    async def active_run_ids(
        current_session_ids: list[UUID], automation_ids: list[UUID]
    ) -> list[UUID]:
        run_ids = list(
            (
                await db.scalars(
                    select(AgentRun.id).where(
                        AgentRun.session_id.in_(current_session_ids),
                        AgentRun.parent_run_id.is_(None),
                        AgentRun.status.in_(ACTIVE_STATUSES),
                    )
                )
            ).all()
        )
        if automation_mode == "deleteAutomations" and automation_ids:
            run_ids.extend(
                (
                    await db.scalars(
                        select(AgentRun.id)
                        .join(AutomationRun, AutomationRun.agent_run_id == AgentRun.id)
                        .where(
                            AutomationRun.automation_id.in_(automation_ids),
                            AgentRun.status.in_(ACTIVE_STATUSES),
                        )
                    )
                ).all()
            )
        return list(dict.fromkeys(run_ids))

    async def cancel_runs(run_ids: list[UUID]) -> None:
        not_local = []
        for run_id in run_ids:
            if not await AgentRuntime.cancel_and_wait(run_id):
                not_local.append(run_id)
        if not_local and await db.scalar(
            select(AgentRun.id).where(
                AgentRun.id.in_(not_local),
                AgentRun.status.in_(ACTIVE_STATUSES),
            )
        ):
            raise ApiError(409, "session.runAlreadyActive")

    automation_ids = [item.id for item in linked_automations]
    cancellation_roots = [*session_ids]
    if automation_mode == "deleteAutomations":
        cancellation_roots.extend(item.execution_session_id for item in linked_automations)
    if run_ids := await active_run_ids(session_ids, automation_ids):
        await db.rollback()
        await cancel_runs(run_ids)
    await db.rollback()
    for session_id in cancellation_roots:
        await AgentRuntime.cancel_descendants(session_id)

    for attempt in range(2):
        project = await _owned_project(db, project_db_id, owner_id, for_update=True)
        sessions = list(
            (
                await db.scalars(
                    select(ChatSession)
                    .where(
                        ChatSession.project_id == project_db_id,
                        ChatSession.user_id == owner_id,
                    )
                    .with_for_update()
                )
            ).all()
        )
        linked_automations = await _linked_automations(
            db, project_db_id, owner_id, for_update=True
        )
        session_ids = [item.id for item in sessions if item.parent_session_id is None]
        if automation_impact != project_delete_impact_token(
            session_ids, linked_automations
        ):
            raise ApiError(409, "session.automationImpactChanged")
        _validate_automation_policy(linked_automations, automation_mode)
        automation_ids = [item.id for item in linked_automations]
        deletion_roots = [item.id for item in sessions]
        if automation_mode == "deleteAutomations":
            deletion_roots.extend(item.execution_session_id for item in linked_automations)
        descendants = await descendant_sessions(db, deletion_roots)
        storage_session_ids = [
            *deletion_roots,
            *(child_id for child_id, _ in descendants),
        ]
        await db.execute(
            select(ChatSession.id)
            .where(ChatSession.id.in_(storage_session_ids))
            .with_for_update()
        )
        reference_count = await _external_file_reference_count(db, storage_session_ids)
        if reference_count:
            raise ApiError(
                409,
                "session.filesReferenced",
                params={"referenceCount": reference_count},
            )
        late_run_ids = await active_run_ids(
            storage_session_ids, automation_ids
        )
        coding_execution_id = await db.scalar(
            select(ProjectPlanExecution.id)
            .where(
                ProjectPlanExecution.project_id == project_db_id,
                ProjectPlanExecution.status.in_(
                    ("queued", "running", "waiting_permission")
                ),
            )
            .limit(1)
        )
        if coding_execution_id is not None:
            raise ApiError(409, "project.codingExecutionsActive")
        if not late_run_ids:
            break
        await db.rollback()
        await cancel_runs(late_run_ids)
        if attempt == 1:
            raise ApiError(409, "session.runAlreadyActive")

    execution_sessions = []
    if automation_mode == "deleteAutomations" and linked_automations:
        execution_session_ids = [
            item.execution_session_id for item in linked_automations
        ]
        execution_sessions = list(
            (
                await db.scalars(
                    select(ChatSession).where(
                        ChatSession.id.in_(execution_session_ids),
                        ChatSession.user_id == owner_id,
                    )
                )
            ).all()
        )
    extra_storage = dict(
        [(item.id, item.project_id) for item in execution_sessions]
        + [(child_id, child_project) for child_id, child_project in descendants
           if child_project != project_db_id]
    )

    storage = SessionStorage()
    project_trashed: Path | None = None
    execution_trashed: list[tuple[UUID, UUID | None, Path | None]] = []

    async def trash_storage() -> None:
        nonlocal project_trashed
        project_trashed = await asyncio.to_thread(
            storage.trash_project, project_db_id
        )
        for execution_session_id, storage_project_id in extra_storage.items():
            trashed = await asyncio.to_thread(
                storage.trash_session, execution_session_id, storage_project_id
            )
            execution_trashed.append((execution_session_id, storage_project_id, trashed))

    async def restore_storage() -> None:
        for execution_session_id, storage_project_id, trashed in reversed(execution_trashed):
            if trashed is None:
                continue
            try:
                await asyncio.to_thread(
                    storage.restore_session, execution_session_id, trashed, storage_project_id
                )
            except Exception:
                logger.exception("Automation session storage could not be restored")
        if project_trashed is not None:
            try:
                await asyncio.to_thread(
                    storage.restore_project, project_db_id, project_trashed
                )
            except Exception:
                logger.exception("Project storage could not be restored")

    async def restore_and_rollback() -> None:
        try:
            await restore_storage()
        finally:
            await db.rollback()

    async def purge_storage() -> None:
        for _execution_session_id, _storage_project_id, trashed in execution_trashed:
            try:
                await asyncio.to_thread(storage.purge_trashed, trashed)
            except Exception:
                logger.exception("Deleted automation session storage could not be purged")
        try:
            await asyncio.to_thread(storage.purge_trashed, project_trashed)
        except Exception:
            logger.exception("Deleted project storage could not be purged")

    trash_task = asyncio.create_task(trash_storage())
    try:
        await asyncio.shield(trash_task)
    except asyncio.CancelledError:
        try:
            await trash_task
        except BaseException:
            pass
        await restore_and_rollback()
        raise
    except (OSError, SessionStorageError) as exc:
        await restore_and_rollback()
        logger.exception("Project storage could not be moved to trash")
        raise ApiError(500, "session.storageUnavailable") from exc
    except BaseException:
        await restore_and_rollback()
        raise

    commit_task: asyncio.Task[None] | None = None
    try:
        if automation_mode == "unlink":
            for automation in linked_automations:
                automation.output_session_id = None
                automation.revision += 1
        if automation_mode == "deleteAutomations":
            for automation in linked_automations:
                await db.delete(automation)
            await db.flush()
        if deletion_roots:
            await db.execute(
                delete(ChatSession)
                .where(ChatSession.id.in_(deletion_roots), ChatSession.user_id == owner_id)
                .execution_options(synchronize_session=False)
            )
        await db.flush()
        await db.delete(project)
        commit_task = asyncio.create_task(db.commit())
        await asyncio.shield(commit_task)
    except asyncio.CancelledError:
        if commit_task is None:
            await restore_and_rollback()
            raise
        try:
            await commit_task
        except BaseException:
            await restore_and_rollback()
        else:
            session_secrets.clear_sessions(storage_session_ids)
            await purge_storage()
        raise
    except BaseException:
        await restore_and_rollback()
        raise
    session_secrets.clear_sessions(storage_session_ids)
    await purge_storage()
