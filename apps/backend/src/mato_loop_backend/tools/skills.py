from __future__ import annotations

import hashlib
from io import BytesIO
from typing import Any
from uuid import uuid4

import mato_loop_backend.permissions.resolution as permissions_resolution
import mato_loop_backend.tools.access as tools_access
from mato_loop_backend.files.service import FileRoot, RootFileError, RootFileService
from mato_loop_backend.permissions.policy import (
    skill_edit_permission,
    skill_load_permission,
    skill_write_permission,
)
from mato_loop_backend.settings import SkillDefinition, ToolExecutionPolicy
from mato_loop_backend.skills import (
    SkillError,
    SkillService,
    parse_skill_draft,
    serialize_skill_draft,
)
from mato_loop_backend.tools.actions import _SKILL_LOAD, _SKILL_WRITE
from mato_loop_backend.tools.arguments import (
    BuildSkillArguments,
    EditSkillArguments,
    LoadSkillArguments,
)
from mato_loop_backend.tools.context import ToolContext as ToolContext
from mato_loop_backend.tools.contracts import ToolDefinition as ToolDefinition
from mato_loop_backend.tools.contracts import ToolResult as ToolResult

SKILL_DRAFT_MAX_BYTES = 40 * 1024


async def _save_skill_draft(
    context: ToolContext,
    brief: str,
    *,
    name_hint: str | None = None,
    current_skill: SkillDefinition | None = None,
) -> ToolResult:
    context.tool_settings = await tools_access.profile_tool_settings(context)
    policy = permissions_resolution.tool_execution_policy(
        context.tool_settings, "skills.write", ToolExecutionPolicy.ASK
    )
    if policy == ToolExecutionPolicy.DISABLED:
        return ToolResult.error("tool.unavailable")
    if context.skill_draft_handler is None:
        return ToolResult.error("skill.creationModelFailed")
    try:
        draft = await context.skill_draft_handler(brief, name_hint, current_skill)
    except SkillError as error:
        return ToolResult.error(error.code)

    draft_id = uuid4()
    skill_id = current_skill.id if current_skill is not None else draft_id
    draft_bytes = serialize_skill_draft(draft).encode("utf-8")
    initial_revision = hashlib.sha256(draft_bytes).hexdigest()
    draft_path = f"skill-draft-{draft_id}.json"
    # Host approval documents must be editable through the normal API before the run ends.
    draft_files = RootFileService(
        context.root_files.session_id,
        project_id=context.root_files.project_id,
        session_storage=context.root_files.session_storage,
        session_quota_bytes=context.root_files.SESSION_QUOTA_BYTES,
        max_editable_text_bytes=context.root_files.max_editable_text_bytes,
    )
    try:
        entry = draft_files.create_file(
            FileRoot.SESSION,
            draft_path,
            BytesIO(draft_bytes),
            max_bytes=SKILL_DRAFT_MAX_BYTES,
        )
    except RootFileError as error:
        return ToolResult.error(error.code)

    approval_details = {
        "skillPreview": draft.model_dump(),
        "draftFile": {
            "root": "session",
            "path": entry.path,
            "filename": entry.name,
            "sizeBytes": entry.size,
            "initialRevision": initial_revision,
        },
    }
    if current_skill is not None:
        approval_details["skillTarget"] = {
            "id": str(current_skill.id),
            "name": current_skill.name,
        }
    approval_received = False
    if policy == ToolExecutionPolicy.ASK:
        context.approval_resolution.clear()
        permission = (
            skill_edit_permission(
                current_skill.id,
                current_skill.name,
                approval_details=approval_details,
            )
            if current_skill is not None
            else skill_write_permission(
                context.session.user_id,
                draft.name,
                approval_details=approval_details,
            )
        )
        if not await tools_access.allow(
            context,
            permission,
        ):
            return ToolResult.error("permission.denied")
        approval_received = True

    context.tool_settings = await tools_access.profile_tool_settings(context)
    current_policy = permissions_resolution.tool_execution_policy(
        context.tool_settings, "skills.write", ToolExecutionPolicy.ASK
    )
    if current_policy == ToolExecutionPolicy.DISABLED or (
        current_policy == ToolExecutionPolicy.ASK and not approval_received
    ):
        return ToolResult.error("tool.unavailable")
    expected_revision = context.approval_resolution.get(
        "expected_revision", initial_revision
    )
    try:
        current_bytes = draft_files.read_bytes(
            FileRoot.SESSION,
            draft_path,
            limit=SKILL_DRAFT_MAX_BYTES,
        )
    except RootFileError:
        return ToolResult.error("skill.draftInvalid")
    if len(current_bytes) > SKILL_DRAFT_MAX_BYTES:
        return ToolResult.error("skill.draftInvalid")
    if hashlib.sha256(current_bytes).hexdigest() != expected_revision:
        return ToolResult.error("file.changed")
    try:
        current_draft = parse_skill_draft(current_bytes)
        skill = (
            await SkillService.replace(
                context.db,
                context.session.user_id,
                current_skill.id,
                current_draft,
                expected=current_skill,
            )
            if current_skill is not None
            else await SkillService.create_definition(
                context.db,
                user_id=context.session.user_id,
                definition=SkillDefinition(id=skill_id, **current_draft.model_dump()),
            )
        )
    except SkillError as error:
        return ToolResult.error(
            error.code,
            field="name" if error.code == "skill.duplicate" else None,
            params={"maxSkills": 20} if error.code == "skill.limitReached" else None,
        )
    payload: dict[str, Any] = {
        "id": str(skill.id),
        "name": skill.name,
        "effectiveFrom": "next_run",
    }
    if current_skill is not None:
        payload["updated"] = True
    return ToolResult.json(payload)


async def _build_skill(
    arguments: BuildSkillArguments, context: ToolContext
) -> ToolResult:
    return await _save_skill_draft(
        context,
        arguments.brief,
        name_hint=arguments.name_hint,
    )


async def _edit_skill(arguments: EditSkillArguments, context: ToolContext) -> ToolResult:
    skill = context.skill_state.catalog.get(arguments.skill_id)
    if skill is None:
        return ToolResult.error("skill.notFound")
    if not skill.editable:
        return ToolResult.error("skill.notEditable")
    if context.skill_state.active is not None:
        return ToolResult.error("tool.unavailable")
    result = await _save_skill_draft(
        context,
        arguments.brief,
        current_skill=skill,
    )
    if not result.is_error:
        context.skill_state.edit_completed = True
    return result


async def _load_skill(arguments: LoadSkillArguments, context: ToolContext) -> ToolResult:
    skill = context.skill_state.catalog.get(arguments.skill_id)
    if skill is None:
        return ToolResult.error("skill.notFound")
    if context.skill_state.active is not None:
        if context.skill_state.active.id == skill.id:
            return ToolResult.json(
                {"id": str(skill.id), "name": skill.name, "loaded": True}
            )
        return ToolResult.error("skill.alreadyActive")

    context.tool_settings = await tools_access.profile_tool_settings(context)
    policy = permissions_resolution.tool_execution_policy(
        context.tool_settings, "skills.load", ToolExecutionPolicy.AUTO
    )
    if policy == ToolExecutionPolicy.DISABLED:
        return ToolResult.error("tool.unavailable")
    approval_received = False
    if policy == ToolExecutionPolicy.ASK:
        if not await tools_access.allow(
            context,
            skill_load_permission(context.session.user_id, skill.name),
        ):
            return ToolResult.error("permission.denied")
        approval_received = True
    context.tool_settings = await tools_access.profile_tool_settings(context)
    current_policy = permissions_resolution.tool_execution_policy(
        context.tool_settings, "skills.load", ToolExecutionPolicy.AUTO
    )
    if current_policy == ToolExecutionPolicy.DISABLED or (
        current_policy == ToolExecutionPolicy.ASK and not approval_received
    ):
        return ToolResult.error("tool.unavailable")

    context.skill_state.active = skill.model_copy(deep=True)
    return ToolResult.json(
        {
            "id": str(skill.id),
            "name": skill.name,
            "loaded": True,
            "effectiveFrom": "next_model_request",
        }
    )


LOAD_SKILL = ToolDefinition(
            "load_skill",
            "Load one catalog skill before following it. Call alone for a named or clear latest-request "
            "match; a skill cannot add tools.",
            LoadSkillArguments,
            _load_skill,
            group="agent",
            label="Skill laden",
            icon="school",
            actions=(_SKILL_LOAD,),
        )

BUILD_SKILL = ToolDefinition(
            "build_skill",
            "Create one reusable skill only on an explicit latest-message request; pass a compact brief "
            "and optional user-suggested name.",
            BuildSkillArguments,
            _build_skill,
            group="agent",
            label="Skill speichern",
            icon="construction",
            actions=(_SKILL_WRITE,),
        )

EDIT_SKILL = ToolDefinition(
            "edit_skill",
            "Edit one catalog skill only on an explicit latest-message request; pass its exact ID and a "
            "compact change brief without loading it.",
            EditSkillArguments,
            _edit_skill,
            group="agent",
            label="Skill bearbeiten",
            icon="edit_note",
            actions=(_SKILL_WRITE,),
        )
