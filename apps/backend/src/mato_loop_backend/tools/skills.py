from __future__ import annotations

import asyncio
import hashlib
from uuid import uuid4

import mato_loop_backend.permissions.resolution as permissions_resolution
import mato_loop_backend.tools.access as tools_access
from mato_loop_backend.execution.client import ExecutionClientError
from mato_loop_backend.files.service import RootFileError, RootFileService
from mato_loop_backend.models import AgentRun
from mato_loop_backend.permissions.policy import (
    skill_edit_permission,
    skill_load_permission,
    skill_write_permission,
)
from mato_loop_backend.settings import SkillDefinition, ToolExecutionPolicy
from mato_loop_backend.skill_packages import SkillError
from mato_loop_backend.skill_resources import (
    materialize,
    prepare_computer,
    read_draft,
    session_package_path,
)
from mato_loop_backend.skills import SkillScope, SkillService
from mato_loop_backend.tools.actions import _SKILL_LOAD, _SKILL_WRITE
from mato_loop_backend.tools.arguments import (
    BuildSkillArguments,
    EditSkillArguments,
    LoadSkillArguments,
)
from mato_loop_backend.tools.context import ToolContext
from mato_loop_backend.tools.contracts import ToolDefinition, ToolResult


def session_files(context: ToolContext) -> RootFileService:
    return RootFileService(
        context.root_files.session_id, project_id=context.root_files.project_id,
        session_storage=context.root_files.session_storage,
        session_quota_bytes=context.root_files.SESSION_QUOTA_BYTES,
        max_editable_text_bytes=context.root_files.max_editable_text_bytes,
    )


async def _save_skill_draft(context: ToolContext, brief: str, *, name_hint: str | None = None, current_skill: SkillDefinition | None = None, scope: SkillScope = "personal") -> ToolResult:
    try:
        await SkillService.require_write(context.db, context.session.user_id, scope)
    except SkillError as exc:
        return ToolResult.error(exc.code)
    context.tool_settings = await tools_access.profile_tool_settings(context)
    policy = permissions_resolution.tool_execution_policy(context.tool_settings, "skills.write", ToolExecutionPolicy.ASK)
    if policy == ToolExecutionPolicy.DISABLED:
        return ToolResult.error("tool.unavailable")
    if context.skill_draft_handler is None:
        return ToolResult.error("skill.creationModelFailed")
    try:
        draft = await context.skill_draft_handler(brief, name_hint, current_skill)
        base = await SkillService.package(current_skill) if current_skill else None
        package = draft.package(base)
        draft_id = uuid4()
        path = f"skill-drafts/{draft_id}/{package.name}"
        files = session_files(context)
        await asyncio.to_thread(materialize, files, package, path)
    except (SkillError, RootFileError) as exc:
        return ToolResult.error(exc.code)
    executable = [path for path, entry in package.files.items() if entry.executable]
    details = {
        "skillPreview": {"name": draft.name, "description": draft.description, "instructions": draft.instructions, "scope": scope, "files": sorted(package.files)},
        "skillDraft": {"path": path, "executablePaths": executable},
        "draftFile": {"root": "session", "path": f"{path}/SKILL.md", "filename": "SKILL.md", "sizeBytes": len(package.files["SKILL.md"].content), "initialRevision": package.revision, "fileRevision": hashlib.sha256(package.files["SKILL.md"].content).hexdigest()},
    }
    if current_skill:
        details["skillTarget"] = {"id": str(current_skill.id), "name": current_skill.name, "scope": scope}
    approved = False
    if policy == ToolExecutionPolicy.ASK:
        context.approval_resolution.clear()
        permission = (
            skill_edit_permission(current_skill.id, current_skill.name, approval_details=details, scope=scope)
            if current_skill else skill_write_permission(context.session.user_id, draft.name, approval_details=details, scope=scope)
        )
        if not await tools_access.allow(context, permission):
            return ToolResult.error("permission.denied")
        approved = True
    context.tool_settings = await tools_access.profile_tool_settings(context)
    current_policy = permissions_resolution.tool_execution_policy(context.tool_settings, "skills.write", ToolExecutionPolicy.ASK)
    if current_policy == ToolExecutionPolicy.DISABLED or (current_policy == ToolExecutionPolicy.ASK and not approved):
        return ToolResult.error("tool.unavailable")
    try:
        reviewed = await asyncio.to_thread(read_draft, files, path, executable)
        if reviewed.revision != context.approval_resolution.get("expected_revision", package.revision):
            return ToolResult.error("file.changed")
        skill = await SkillService.save_package(
            context.db, context.session.user_id, current_skill.id if current_skill else draft_id,
            reviewed, scope=scope, create=current_skill is None,
            expected_revision=current_skill.revision if current_skill else None,
        )
    except (SkillError, RootFileError) as exc:
        return ToolResult.error(exc.code)
    return ToolResult.json({"id": str(skill.id), "name": skill.name, "scope": scope, "updated": current_skill is not None, "effectiveFrom": "next_run"})


async def _build_skill(arguments: BuildSkillArguments, context: ToolContext) -> ToolResult:
    return await _save_skill_draft(context, arguments.brief, name_hint=arguments.name_hint, scope=arguments.scope)


async def _edit_skill(arguments: EditSkillArguments, context: ToolContext) -> ToolResult:
    skill = context.skill_state.catalog.get(arguments.skill_id)
    if skill is None:
        return ToolResult.error("skill.notFound")
    if not skill.editable:
        return ToolResult.error("skill.notEditable")
    # Resolve the requested ownership scope; a personal override and the global original
    # may intentionally have the same stable default-skill ID.
    try:
        current = await SkillService.get(context.db, context.session.user_id, skill.id, arguments.scope)
        if current.revision != skill.revision or current.owner_id != skill.owner_id:
            return ToolResult.error("skill.changed")
    except SkillError as exc:
        return ToolResult.error(exc.code)
    return await _save_skill_draft(context, arguments.brief, current_skill=current, scope=arguments.scope)


async def _load_skill(arguments: LoadSkillArguments, context: ToolContext) -> ToolResult:
    skill = context.skill_state.active.get(arguments.skill_id) or context.skill_state.catalog.get(arguments.skill_id)
    if skill is None:
        return ToolResult.error("skill.notFound")
    context.tool_settings = await tools_access.profile_tool_settings(context)
    policy = permissions_resolution.tool_execution_policy(context.tool_settings, "skills.load", ToolExecutionPolicy.AUTO)
    if policy == ToolExecutionPolicy.DISABLED:
        return ToolResult.error("tool.unavailable")
    approved = False
    if policy == ToolExecutionPolicy.ASK:
        if not await tools_access.allow(context, skill_load_permission(context.session.user_id, skill.name)):
            return ToolResult.error("permission.denied")
        approved = True
    context.tool_settings = await tools_access.profile_tool_settings(context)
    current_policy = permissions_resolution.tool_execution_policy(context.tool_settings, "skills.load", ToolExecutionPolicy.AUTO)
    if current_policy == ToolExecutionPolicy.DISABLED or (current_policy == ToolExecutionPolicy.ASK and not approved):
        return ToolResult.error("tool.unavailable")
    try:
        # Ownership/enablement is live; the selected content revision remains fixed.
        available = await SkillService.list(context.db, context.session.user_id)
        if not any(item.id == skill.id and item.owner_id == skill.owner_id for item in available):
            return ToolResult.error("skill.notFound")
        package = await SkillService.package(skill)
        loaded = skill.model_copy(deep=True)
        if loaded.files_path is None:
            loaded.files_path = await asyncio.to_thread(materialize, session_files(context), package, session_package_path(context.run_id, package))
        if arguments.on_computer:
            client = context.execution_client
            if client is None:
                return ToolResult.error("file.workspaceNotSelected")
            run = await context.db.get(AgentRun, context.run_id)
            if run is None or run.session_id != context.session.id or run.execution_agent_id != client.agent_id:
                return ToolResult.error("executionAgent.unavailable")
            loaded.computer_path = await prepare_computer(client, run, package)
            loaded.computer_id = client.agent_id
        context.skill_state.active[loaded.id] = loaded
    except (SkillError, RootFileError, ExecutionClientError) as exc:
        return ToolResult.error(exc.code)
    return ToolResult.json({
        "id": str(loaded.id), "name": loaded.name, "loaded": True,
        "revision": loaded.revision, "filesBasePath": loaded.files_path,
        "computerBasePath": loaded.computer_path if context.execution_client and loaded.computer_id == context.execution_client.agent_id else None,
        "resources": loaded.resource_paths, "effectiveFrom": "next_model_request",
    })


LOAD_SKILL = ToolDefinition(
    "load_skill", "Load a matching catalog skill; call alone.",
    LoadSkillArguments, _load_skill, group="agent", label="Skill laden", icon="school", actions=(_SKILL_LOAD,),
)
BUILD_SKILL = ToolDefinition(
    "build_skill", "Create a skill package only on an explicit latest-message request.",
    BuildSkillArguments, _build_skill, group="agent", label="Skill speichern", icon="construction", actions=(_SKILL_WRITE,),
)
EDIT_SKILL = ToolDefinition(
    "edit_skill", "Edit a catalog skill only on an explicit latest-message request.",
    EditSkillArguments, _edit_skill, group="agent", label="Skill bearbeiten", icon="edit_note", actions=(_SKILL_WRITE,),
)
