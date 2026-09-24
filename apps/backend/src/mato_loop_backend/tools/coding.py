from __future__ import annotations

import json

from sqlalchemy import select

import mato_loop_backend.tools.access as tools_access
from mato_loop_backend.models import Project
from mato_loop_backend.permissions.policy import PermissionRequest, SessionGrantScope
from mato_loop_backend.projects.coding import ProjectCodingError, ProjectCodingService
from mato_loop_backend.settings import load_agent_profile, load_effective_agent_identity
from mato_loop_backend.tools.actions import (
    _CODING_ANALYZE,
    _CODING_EXECUTE,
    _CODING_PLAN_CREATE,
    _CODING_PLAN_READ,
)
from mato_loop_backend.tools.arguments import (
    CodingAnalyzeArguments,
    CodingCreatePlanArguments,
    CodingEnqueueArguments,
    CodingListPlansArguments,
)
from mato_loop_backend.tools.context import ToolContext as ToolContext
from mato_loop_backend.tools.contracts import ToolDefinition as ToolDefinition
from mato_loop_backend.tools.contracts import ToolResult as ToolResult


async def _coding_project(context: ToolContext) -> Project | None:
    if context.session.project_id is None:
        return None
    project = await context.db.scalar(
        select(Project).where(
            Project.id == context.session.project_id,
            Project.user_id == context.session.user_id,
            Project.coding_integration_id.is_not(None),
        ).execution_options(populate_existing=True)
    )
    integrations = await context.current_integrations()
    if project is None or not any(
        item.id == project.coding_integration_id and item.enabled
        and item.integration_type == "coding" and item.adapter_type == "opencode"
        for item in integrations.instances
    ):
        return None
    return project


async def _authorize_coding(
    context: ToolContext,
    project: Project,
    capability: str,
    title: str,
    summary: str,
    *,
    risk_level: str = "medium",
) -> bool:
    integration_id = project.coding_integration_id
    if await _coding_project(context) is None:
        return False
    allowed = await tools_access.allow(
        context,
        PermissionRequest(
            capability=capability,
            resource_type="project",
            resource_scope=str(project.id),
            action_kind=capability,
            risk_level=risk_level,
            title=title,
            summary=summary,
            resource_display=project.name,
            session_grants=(SessionGrantScope(
                (
                    "read"
                    if capability in {"coding.analyze", "coding.plan.read"}
                    else "write"
                ),
                "project",
                json.dumps(
                    {"capability": capability, "projectId": str(project.id)},
                    ensure_ascii=True,
                    separators=(",", ":"),
                    sort_keys=True,
                ),
                project.name,
            ),),
        ),
    )
    if not allowed:
        return False
    current = await _coding_project(context)
    return current is not None and current.coding_integration_id == integration_id


def _coding_error(exc: ProjectCodingError) -> ToolResult:
    field = (exc.params or {}).get("field")
    params = {
        key: value
        for key, value in (exc.params or {}).items()
        if key in {"keys", "maxLength", "minLength"}
    }
    return ToolResult.error(
        exc.code,
        field=field if isinstance(field, str) else None,
        params=params or None,
    )


async def _coding_analyze(
    arguments: CodingAnalyzeArguments, context: ToolContext
) -> ToolResult:
    project = await _coding_project(context)
    if project is None:
        return ToolResult.error("tool.unavailable")
    if not await _authorize_coding(
        context,
        project,
        "coding.analyze",
        "Projekt analysieren?",
        "Der Agent möchte das gebundene Projekt mit OpenCode schreibgeschützt analysieren.",
    ):
        return ToolResult.error("permission.denied")
    try:
        profile = await load_agent_profile(
            context.db,
            context.session.user_id,
            getattr(context, "agent_profile_id", None)
            or getattr(context.session, "agent_profile_id", None),
        )
        identity = await load_effective_agent_identity(
            context.db, context.session.user_id, profile
        )
        result = await ProjectCodingService.analyze(
            context.db,
            context.session.user_id,
            project,
            arguments.brief,
            arguments.title,
            identity.persona.instructions,
            project.instructions,
        )
    except ProjectCodingError as exc:
        return _coding_error(exc)
    return ToolResult.json(
        {
            "analysis": result.text,
            "externalSessionId": result.external_session_id,
        }
    )


async def _coding_create_plan(
    arguments: CodingCreatePlanArguments, context: ToolContext
) -> ToolResult:
    project = await _coding_project(context)
    if project is None:
        return ToolResult.error("tool.unavailable")
    if not await _authorize_coding(
        context,
        project,
        "coding.plan.create",
        "Plan speichern?",
        "Der Agent möchte einen kanonischen Ausführungsplan im Projekt speichern.",
    ):
        return ToolResult.error("permission.denied")
    try:
        plan = await ProjectCodingService.create_plan(
            context.db,
            context.session.user_id,
            project,
            context.session.id,
            arguments.title,
            arguments.markdown,
            arguments.external_session_id,
        )
    except ProjectCodingError as exc:
        return _coding_error(exc)
    return ToolResult.json({"id": str(plan.id), "title": plan.title})


async def _coding_list_plans(
    arguments: CodingListPlansArguments, context: ToolContext
) -> ToolResult:
    project = await _coding_project(context)
    if project is None:
        return ToolResult.error("tool.unavailable")
    if not await _authorize_coding(
        context,
        project,
        "coding.plan.read",
        "Pläne anzeigen?",
        "Der Agent möchte die gespeicherten Projektpläne anzeigen.",
        risk_level="low",
    ):
        return ToolResult.error("permission.denied")
    try:
        plans = await ProjectCodingService.list_plans(
            context.db, context.session.user_id, project
        )
    except ProjectCodingError as exc:
        return _coding_error(exc)
    return ToolResult.json(
        {
            "plans": [
                {"id": str(plan.id), "title": plan.title}
                for plan in reversed(plans[-arguments.limit :])
            ]
        }
    )


async def _coding_enqueue(
    arguments: CodingEnqueueArguments, context: ToolContext
) -> ToolResult:
    project = await _coding_project(context)
    if project is None:
        return ToolResult.error("tool.unavailable")
    if not await _authorize_coding(
        context,
        project,
        "coding.execute.enqueue",
        "Pläne ausführen?",
        "Der Agent möchte die ausgewählten Pläne zur OpenCode-Ausführung einreihen.",
        risk_level="high",
    ):
        return ToolResult.error("permission.denied")
    try:
        executions = await ProjectCodingService.enqueue(
            context.db,
            context.session.user_id,
            project,
            arguments.plan_ids,
        )
    except ProjectCodingError as exc:
        return _coding_error(exc)
    return ToolResult.json(
        {
            "executions": [
                {
                    "id": str(execution.id),
                    "planId": str(execution.plan_id),
                    "position": execution.queue_position,
                    "status": execution.status,
                }
                for execution in executions
            ]
        }
    )


CODING_ANALYZE = ToolDefinition(
            "coding_analyze",
            "Analyze the bound project with OpenCode using read-only tools. Use the returned findings "
            "as evidence, then author the canonical MatoLoop plan yourself.",
            CodingAnalyzeArguments,
            _coding_analyze,
            group="coding",
            label="Projekt analysieren",
            icon="search",
            actions=(_CODING_ANALYZE,),
            required_capability="project.coding",
        )

CODING_CREATE_PLAN = ToolDefinition(
            "coding_create_plan",
            "Persist one canonical MatoLoop implementation plan after analysis. The markdown must be an "
            "actionable plan authored by you, not raw OpenCode analysis.",
            CodingCreatePlanArguments,
            _coding_create_plan,
            group="coding",
            label="Plan speichern",
            icon="note_add",
            actions=(_CODING_PLAN_CREATE,),
            required_capability="project.coding",
        )

CODING_LIST_PLANS = ToolDefinition(
            "coding_list_plans",
            "List recent canonical plans for the bound project.",
            CodingListPlansArguments,
            _coding_list_plans,
            group="coding",
            label="Pläne anzeigen",
            icon="list_alt",
            actions=(_CODING_PLAN_READ,),
            required_capability="project.coding",
        )

CODING_ENQUEUE = ToolDefinition(
            "coding_enqueue",
            "Queue exact saved plan snapshots for serial OpenCode execution only when the user asks to "
            "implement them.",
            CodingEnqueueArguments,
            _coding_enqueue,
            group="coding",
            label="Pläne ausführen",
            icon="playlist_play",
            actions=(_CODING_EXECUTE,),
            required_capability="project.coding",
        )
