from __future__ import annotations

import asyncio
import json
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

import mato_loop_backend.tools.access as tools_access
from mato_loop_backend.automations import (
    AutomationError,
    AutomationScheduler,
    AutomationService,
    runtime_snapshot,
)
from mato_loop_backend.models import Automation, ChatSession
from mato_loop_backend.permissions.policy import (
    PermissionDisplayItem,
    PermissionRequest,
    automation_collection_scope,
    automation_scope,
)
from mato_loop_backend.tools.actions import (
    _AUTOMATION_CREATE,
    _AUTOMATION_DELETE,
    _AUTOMATION_READ,
    _AUTOMATION_RUN,
    _AUTOMATION_UPDATE,
)
from mato_loop_backend.tools.arguments import (
    AutomationScheduleArguments,
    CreateAutomationArguments,
    DeleteAutomationArguments,
    EditAutomationArguments,
    GetAutomationArguments,
    ListAutomationsArguments,
    RunAutomationArguments,
)
from mato_loop_backend.tools.context import ToolContext as ToolContext
from mato_loop_backend.tools.contracts import ToolDefinition as ToolDefinition
from mato_loop_backend.tools.contracts import ToolResult as ToolResult


def _automation_collection_permission(
    context: ToolContext,
    capability: str,
    *,
    action_kind: str,
    title: str,
    summary: str,
    display_items: tuple[PermissionDisplayItem, ...] = (),
    approval_details: dict[str, Any] | None = None,
) -> PermissionRequest:
    return PermissionRequest(
        capability=capability,
        resource_type="automation_collection",
        resource_scope=automation_collection_scope(
            context.session.user_id, context.session.id
        ),
        action_kind=action_kind,
        risk_level="medium",
        title=title,
        summary=summary,
        resource_display="Eigene Automationen",
        display_items=display_items,
        approval_details=approval_details,
    )


def _automation_permission(
    context: ToolContext,
    automation: Automation,
    capability: str,
    *,
    action_kind: str,
    title: str,
    summary: str,
    risk_level: str = "medium",
    display_items: tuple[PermissionDisplayItem, ...] = (),
    approval_details: dict[str, Any] | None = None,
) -> PermissionRequest:
    return PermissionRequest(
        capability=capability,
        resource_type="automation",
        resource_scope=automation_scope(
            automation.id,
            automation.revision,
            context.session.user_id,
            context.session.id,
        ),
        action_kind=action_kind,
        risk_level=risk_level,
        title=title,
        summary=summary,
        resource_display=f"{automation.name} (Revision {automation.revision})",
        display_items=display_items,
        approval_details=approval_details,
    )


def _automation_schedules(
    schedules: list[AutomationScheduleArguments],
) -> list[dict[str, Any]]:
    return [item.model_dump(exclude_none=True) for item in schedules]


def _automation_schedule_json(schedules: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            key: value.isoformat() if isinstance(value, datetime) else value
            for key, value in item.items()
        }
        for item in schedules
    ]


def _automation_schedule_lines(schedules: list[dict[str, Any]]) -> tuple[str, ...]:
    return tuple(
        f'{item.get("run_at") or "{} | {}".format(item["cron"], item["timezone"])} | '
        f'{"aktiv" if item["enabled"] else "inaktiv"}'
        f'{" | gültig bis " + item["valid_until"] if item.get("valid_until") else ""}'
        for item in _automation_schedule_json(schedules)
    ) or ("Nur manuell",)


async def _automation_definition_payload(
    db: AsyncSession, automation: Automation
) -> dict[str, Any]:
    execution_session = await db.get(ChatSession, automation.execution_session_id)
    if execution_session is None:
        raise AutomationError("automation.executionSessionMissing")
    return {
        "id": str(automation.id),
        "name": automation.name,
        "task": automation.task,
        "skillId": str(automation.skill_id) if automation.skill_id else None,
        "enabled": automation.enabled,
        "archiveAfterSuccess": automation.archive_after_success,
        "archivedAt": (
            automation.archived_at.isoformat() if automation.archived_at else None
        ),
        "revision": automation.revision,
        "schedules": [
            {
                "cron": item.cron_expression,
                "timezone": item.timezone,
                "runAt": (
                    item.next_run_at.isoformat()
                    if item.cron_expression is None
                    else None
                ),
                "validUntil": (
                    item.valid_until.isoformat() if item.valid_until else None
                ),
                "enabled": item.enabled,
                "nextRunAt": item.next_run_at.isoformat(),
            }
            for item in automation.schedules
        ],
        "outputSessionId": (
            str(automation.output_session_id) if automation.output_session_id else None
        ),
        "outputSessionRequired": automation.output_session_required,
        "runtime": runtime_snapshot(execution_session),
        "contentIsUntrusted": True,
    }


def _automation_tool_error(exc: AutomationError) -> ToolResult:
    return ToolResult.error(exc.code, field=exc.field, params=exc.params)


async def _list_automations(
    arguments: ListAutomationsArguments, context: ToolContext
) -> ToolResult:
    permission = _automation_collection_permission(
        context,
        "automation.read",
        action_kind="list_automations",
        title="Automationen anzeigen?",
        summary="Der Agent möchte die eigenen Automationen kompakt anzeigen.",
    )
    if not await tools_access.allow(context, permission):
        return ToolResult.error("permission.denied")
    rows = list(
        (
            await context.db.scalars(
                select(Automation)
                .options(selectinload(Automation.schedules))
                .where(
                    Automation.user_id == context.session.user_id,
                    Automation.archived_at.is_(None),
                )
                .order_by(Automation.updated_at.desc(), Automation.id.desc())
                .limit(arguments.limit + 1)
            )
        ).all()
    )
    items = rows[: arguments.limit]
    return ToolResult.json(
        {
            "automations": [
                {
                    "id": str(item.id),
                    "name": item.name,
                    "enabled": item.enabled,
                    "revision": item.revision,
                    "scheduleCount": len(item.schedules),
                    "nextRunAt": (
                        min(
                            schedule.next_run_at
                            for schedule in item.schedules
                            if schedule.enabled
                        ).isoformat()
                        if any(schedule.enabled for schedule in item.schedules)
                        else None
                    ),
                }
                for item in items
            ],
            "limit": arguments.limit,
            "hasMore": len(rows) > arguments.limit,
            "contentIsUntrusted": True,
        }
    )


async def _get_automation(
    arguments: GetAutomationArguments, context: ToolContext
) -> ToolResult:
    try:
        automation = await AutomationService.owned(
            context.db, arguments.automation_id, context.session.user_id
        )
        expected_revision = automation.revision
        if not await tools_access.allow(
            context,
            _automation_permission(
                context,
                automation,
                "automation.read",
                action_kind="get_automation",
                title="Automation anzeigen?",
                summary=(
                    f'Der Agent möchte „{automation.name}“ in Revision '
                    f"{automation.revision} vollständig anzeigen."
                ),
            ),
        ):
            return ToolResult.error("permission.denied")
        automation = await AutomationService.owned(
            context.db, arguments.automation_id, context.session.user_id
        )
        if automation.revision != expected_revision:
            raise AutomationError(
                "automation.changed", params={"currentRevision": automation.revision}
            )
        return ToolResult.json(await _automation_definition_payload(context.db, automation))
    except AutomationError as exc:
        return _automation_tool_error(exc)


async def _create_automation(
    arguments: CreateAutomationArguments, context: ToolContext
) -> ToolResult:
    schedules = _automation_schedules(arguments.schedules)
    deliver = arguments.deliver_to_current_session or arguments.output_session_required
    try:
        skill = await AutomationService.skill(
            context.db, context.session.user_id, arguments.skill_id
        )
        preview = {
            "name": arguments.name,
            "task": arguments.task,
            "skillId": str(arguments.skill_id) if arguments.skill_id else None,
            "enabled": arguments.enabled,
            "archiveAfterSuccess": arguments.archive_after_success,
            "schedules": _automation_schedule_json(schedules),
            "output": "current" if deliver else "none",
            "outputSessionRequired": arguments.output_session_required,
        }
        permission = _automation_collection_permission(
            context,
            "automation.create",
            action_kind="create_automation",
            title="Automation erstellen?",
            summary=(
                f'Der Agent möchte die Automation „{arguments.name}“ erstellen und '
                f'{"aktivieren" if arguments.enabled else "deaktiviert speichern"}.'
            ),
            display_items=(
                PermissionDisplayItem("Aufgabe", arguments.task, "code"),
                PermissionDisplayItem(
                    "Zeitpläne", _automation_schedule_lines(schedules), "list"
                ),
                PermissionDisplayItem("Skill", skill.name if skill else "Keiner"),
                PermissionDisplayItem(
                    "Nach Erfolg",
                    "Archivieren" if arguments.archive_after_success else "Aktiv lassen",
                ),
                PermissionDisplayItem(
                    "Ausgabe",
                    "Aktuelle Session" if deliver else "Keine Session",
                ),
            ),
            approval_details={"automationPreview": preview},
        )
        if not await tools_access.allow(context, permission):
            return ToolResult.error("permission.denied")
        automation = await AutomationService.create(
            context.db,
            user_id=context.session.user_id,
            name=arguments.name,
            task=arguments.task,
            skill_id=arguments.skill_id,
            enabled=arguments.enabled,
            source_session_id=context.session.id,
            output_session_id=context.session.id if deliver else None,
            output_session_required=arguments.output_session_required,
            schedules=schedules,
            archive_after_success=arguments.archive_after_success,
        )
        await AutomationService.provision_and_commit(context.db, automation)
        return ToolResult.json(
            {
                "id": str(automation.id),
                "name": automation.name,
                "revision": automation.revision,
                "enabled": automation.enabled,
                "created": True,
            }
        )
    except AutomationError as exc:
        await context.db.rollback()
        return _automation_tool_error(exc)


def _automation_edit_diff(
    automation: Automation,
    arguments: EditAutomationArguments,
    schedules: list[dict[str, Any]],
    output_session_id: UUID | None,
) -> tuple[str, ...]:
    before_schedules = [
        {
            **(
                {"run_at": item.next_run_at.isoformat()}
                if item.cron_expression is None
                else {
                    "cron": item.cron_expression,
                    "timezone": item.timezone,
                    **(
                        {"valid_until": item.valid_until.isoformat()}
                        if item.valid_until
                        else {}
                    ),
                }
            ),
            "enabled": item.enabled,
        }
        for item in automation.schedules
    ]
    changes = []
    comparisons = (
        ("Name", automation.name, arguments.name),
        ("Aufgabe", automation.task, arguments.task),
        (
            "Skill",
            str(automation.skill_id) if automation.skill_id else "Keiner",
            str(arguments.skill_id) if arguments.skill_id else "Keiner",
        ),
        ("Aktiv", str(automation.enabled), str(arguments.enabled)),
        (
            "Nach Erfolg archivieren",
            str(automation.archive_after_success),
            str(arguments.archive_after_success),
        ),
        (
            "Zeitpläne",
            json.dumps(before_schedules) if before_schedules else "Nur manuell",
            json.dumps(_automation_schedule_json(schedules)) if schedules else "Nur manuell",
        ),
        (
            "Ausgabe",
            str(automation.output_session_id) if automation.output_session_id else "Keine",
            str(output_session_id) if output_session_id else "Keine",
        ),
        (
            "Ausgabe erforderlich",
            str(automation.output_session_required),
            str(arguments.output_session_required),
        ),
    )
    for label, before, after in comparisons:
        if before != after:
            changes.append(f"{label}: {before} -> {after}")
    if arguments.copy_runtime_from_current_session:
        changes.append("Runtime: aus aktueller Session übernehmen")
    return tuple(changes or ("Keine inhaltliche Änderung",))


async def _edit_automation(
    arguments: EditAutomationArguments, context: ToolContext
) -> ToolResult:
    schedules = _automation_schedules(arguments.schedules)
    try:
        automation = await AutomationService.owned(
            context.db, arguments.automation_id, context.session.user_id
        )
        if automation.archived_at is not None:
            raise AutomationError("automation.archived")
        if automation.revision != arguments.expected_revision:
            raise AutomationError(
                "automation.changed", params={"currentRevision": automation.revision}
            )
        output_session_id = {
            "keep": automation.output_session_id,
            "current": context.session.id,
            "none": None,
        }[arguments.output_mode]
        if arguments.output_session_required and output_session_id is None:
            return ToolResult.error(
                "tool.invalidArguments", field="output_session_required"
            )
        await AutomationService.skill(
            context.db, context.session.user_id, arguments.skill_id
        )
        changes = _automation_edit_diff(
            automation, arguments, schedules, output_session_id
        )
        if not await tools_access.allow(
            context,
            _automation_permission(
                context,
                automation,
                "automation.update",
                action_kind="edit_automation",
                title="Automation ändern?",
                summary=(
                    f'Der Agent möchte „{automation.name}“ in Revision '
                    f"{automation.revision} ändern."
                ),
                display_items=(PermissionDisplayItem("Änderungen", changes, "list"),),
                approval_details={
                    "automationChange": {
                        "automationId": str(automation.id),
                        "expectedRevision": automation.revision,
                        "changes": list(changes),
                    }
                },
            ),
        ):
            return ToolResult.error("permission.denied")
        automation = await AutomationService.owned(
            context.db,
            arguments.automation_id,
            context.session.user_id,
            for_update=True,
        )
        automation = await AutomationService.replace(
            context.db,
            automation,
            expected_revision=arguments.expected_revision,
            name=arguments.name,
            task=arguments.task,
            skill_id=arguments.skill_id,
            enabled=arguments.enabled,
            source_session_id=(
                context.session.id
                if arguments.copy_runtime_from_current_session
                else None
            ),
            output_session_id=output_session_id,
            output_session_required=arguments.output_session_required,
            delivery_channel_type=(
                automation.delivery_channel_type if output_session_id else None
            ),
            delivery_endpoint_id=(
                automation.delivery_endpoint_id if output_session_id else None
            ),
            schedules=schedules,
            archive_after_success=arguments.archive_after_success,
        )
        await context.db.commit()
        return ToolResult.json(
            {
                "id": str(automation.id),
                "name": automation.name,
                "revision": automation.revision,
                "enabled": automation.enabled,
                "updated": True,
            }
        )
    except AutomationError as exc:
        await context.db.rollback()
        return _automation_tool_error(exc)


async def _run_automation(
    arguments: RunAutomationArguments, context: ToolContext
) -> ToolResult:
    try:
        automation = await AutomationService.owned(
            context.db, arguments.automation_id, context.session.user_id
        )
        if automation.archived_at is not None:
            raise AutomationError("automation.archived")
        if automation.revision != arguments.expected_revision:
            raise AutomationError(
                "automation.changed", params={"currentRevision": automation.revision}
            )
        if not await tools_access.allow(
            context,
            _automation_permission(
                context,
                automation,
                "automation.run",
                action_kind="run_automation",
                title="Automation jetzt starten?",
                summary=(
                    f'Der Agent möchte „{automation.name}“ in Revision '
                    f"{automation.revision} manuell einreihen."
                ),
                display_items=(
                    PermissionDisplayItem("Auswirkung", "Ein Lauf wird eingereiht."),
                ),
            ),
        ):
            return ToolResult.error("permission.denied")
        run = await AutomationService.enqueue_manual(
            context.db,
            automation.id,
            context.session.user_id,
            expected_revision=arguments.expected_revision,
        )
        asyncio.get_running_loop().call_soon(AutomationScheduler.start)
        return ToolResult.json(
            {
                "automationId": str(automation.id),
                "revision": run.automation_revision,
                "runId": str(run.id),
                "status": run.status,
                "queued": True,
            }
        )
    except AutomationError as exc:
        await context.db.rollback()
        return _automation_tool_error(exc)


async def _delete_automation(
    arguments: DeleteAutomationArguments, context: ToolContext
) -> ToolResult:
    try:
        automation = await AutomationService.owned(
            context.db, arguments.automation_id, context.session.user_id
        )
        if automation.revision != arguments.expected_revision:
            raise AutomationError(
                "automation.changed", params={"currentRevision": automation.revision}
            )
        if not await tools_access.allow(
            context,
            _automation_permission(
                context,
                automation,
                "automation.delete",
                action_kind="delete_automation",
                title="Automation endgültig löschen?",
                summary=(
                    f'Der Agent möchte „{automation.name}“ in Revision '
                    f"{automation.revision} samt Laufhistorie löschen."
                ),
                risk_level="high",
                display_items=(
                    PermissionDisplayItem(
                        "Auswirkung",
                        "Aktive Läufe werden beendet; Ausführungssession und Historie werden gelöscht.",
                    ),
                ),
            ),
        ):
            return ToolResult.error("permission.denied")
        await AutomationService.delete(
            context.db,
            automation.id,
            context.session.user_id,
            expected_revision=arguments.expected_revision,
        )
        return ToolResult.json(
            {
                "id": str(arguments.automation_id),
                "revision": arguments.expected_revision,
                "deleted": True,
            }
        )
    except AutomationError as exc:
        await context.db.rollback()
        return _automation_tool_error(exc)


LIST_AUTOMATIONS = ToolDefinition(
            "list_automations",
            "List.",
            ListAutomationsArguments,
            _list_automations,
            group="automations",
            label="Automationen anzeigen",
            icon="view_list",
            actions=(_AUTOMATION_READ,),
        )

GET_AUTOMATION = ToolDefinition(
            "get_automation",
            "Get by ID.",
            GetAutomationArguments,
            _get_automation,
            group="automations",
            label="Automation anzeigen",
            icon="schedule",
            actions=(_AUTOMATION_READ,),
        )

CREATE_AUTOMATION = ToolDefinition(
            "create_automation",
            "Create if asked. Same task uses one automation. Chat reminders set deliver_to_current_session=true; calendar event reminders use the calendar event tools. Use cron+timezone or offset run_at; valid_until is cron-only.",
            CreateAutomationArguments,
            _create_automation,
            group="automations",
            label="Automation erstellen",
            icon="add_task",
            actions=(_AUTOMATION_CREATE,),
        )

EDIT_AUTOMATION = ToolDefinition(
            "edit_automation",
            "Edit exact revision. Use cron+timezone or offset run_at; valid_until is cron-only.",
            EditAutomationArguments,
            _edit_automation,
            group="automations",
            label="Automation ändern",
            icon="edit_calendar",
            actions=(_AUTOMATION_UPDATE,),
        )

RUN_AUTOMATION = ToolDefinition(
            "run_automation",
            "Run an exact revision only if latest asks.",
            RunAutomationArguments,
            _run_automation,
            group="automations",
            label="Automation starten",
            icon="play_arrow",
            actions=(_AUTOMATION_RUN,),
        )

DELETE_AUTOMATION = ToolDefinition(
            "delete_automation",
            "Delete with history only if latest asks; exact revision.",
            DeleteAutomationArguments,
            _delete_automation,
            group="automations",
            label="Automation löschen",
            icon="delete_forever",
            actions=(_AUTOMATION_DELETE,),
        )
