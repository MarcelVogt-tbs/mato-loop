from dataclasses import replace
from typing import Any

from pydantic import Field

from mato_loop_backend.execution import ShellInspectResult
from mato_loop_backend.files.access import filesystem_permission
from mato_loop_backend.permissions.policy import (
    PermissionDecision,
    session_browse_permission,
    session_permission,
    session_search_permission,
    shell_command_permission,
)
from mato_loop_backend.providers.base import ToolCall
from mato_loop_backend.settings import ToolExecutionPolicy
from mato_loop_backend.tools.access import profile_tool_settings
from mato_loop_backend.tools.actions import ToolAction
from mato_loop_backend.tools.arguments import ToolArguments
from mato_loop_backend.tools.context import ToolContext
from mato_loop_backend.tools.contracts import ToolDefinition, ToolResult


class PermissionTarget(ToolArguments):
    tool: str = Field(min_length=1, max_length=200)
    arguments: dict[str, Any]


class RequestSessionPermissionsArguments(ToolArguments):
    requests: list[PermissionTarget] = Field(min_length=1, max_length=8)


async def builtin_permissions(arguments, context: ToolContext, name: str):
    capabilities = {
        "read": "filesystem.read", "glob": "filesystem.read", "grep": "filesystem.read",
        "write_file": "filesystem.write", "edit_file": "filesystem.write",
        "create_directory": "filesystem.write", "delete_path": "filesystem.delete",
    }
    if name in capabilities and not getattr(arguments, "elevated", False) and not getattr(arguments, "session_id", None):
        capability = capabilities[name]
        if arguments.root == "session" and capability != "filesystem.read":
            capability = "session.files.write"
        return (filesystem_permission(
            context, root=arguments.root, path=arguments.path, capability=capability,
            action_kind=name, action_label="Session-Zugriff vorbereiten",
        ),)
    if name == "session_read":
        return (session_permission(arguments.session_id or context.session.id),)
    if name == "shell_run" and not arguments.elevated and not arguments.environment:
        client = context.execution_client
        if client is None or client.current_directory is None:
            return ()
        inspection = await client.request("shell.inspect", {
            "command": arguments.command, "currentDirectory": client.current_directory,
        }, ShellInspectResult)
        return (shell_command_permission(client.agent_id, client.current_directory, arguments.command, inspection),)
    if name == "session_search":
        return (session_search_permission(),)
    if name == "session_browse":
        return (session_browse_permission(),)
    return ()


async def authorize_session_requests(context: ToolContext, requests) -> ToolResult:
    if not requests:
        return ToolResult.error("permission.preflightUnavailable")
    for request in requests:
        decision = await context.ask_handler(request, check_only=True)
        if decision == PermissionDecision.ALLOW:
            continue
        if decision != PermissionDecision.ASK:
            return ToolResult.error("permission.denied")
        if "allow_session" not in request.available_decisions or not request.effective_session_grants:
            return ToolResult.error("permission.preflightUnavailable")
        if await context.ask(replace(request, available_decisions=("allow_session", "deny"),
            summary=request.summary + " Es wird jetzt keine Aktion ausgeführt.")) != PermissionDecision.ALLOW:
            return ToolResult.error("permission.denied")
    return ToolResult.json({"permissionsReady": True, "executed": False})


async def request_session_permissions(arguments: RequestSessionPermissionsArguments, context: ToolContext) -> ToolResult:
    from mato_loop_backend.tools.catalog import tool_registry
    from mato_loop_backend.tools.executor import execute_definition

    settings = await profile_tool_settings(context)
    tools = tool_registry.effective(
        await context.current_integrations(), tool_settings=settings,
        memory_enabled=context.memory_read_enabled, memory_write_enabled=context.memory_write_enabled,
        execution_operations=context.execution_client.operations if context.execution_client else frozenset(),
    )
    if settings._delegation_ceiling is not None:
        tools = tools.only(*settings._delegation_ceiling["toolNames"])
    for target in arguments.requests:
        bound = tools.tools.get(target.tool)
        if bound is None:
            return ToolResult.error("tool.unavailable")
        call = ToolCall(context.tool_call.id, target.tool, target.arguments)
        prepared = replace(context, tool_call=call, tool_settings=settings, bound_options={})
        result = await execute_definition(tool_registry, call, prepared, bound, permissions_only=True)
        context.user_denied = context.user_denied or prepared.user_denied
        if result.is_error:
            return result
    return ToolResult.json({"permissionsReady": True, "executed": False})


REQUEST_SESSION_PERMISSIONS = ToolDefinition(
    "request_session_permissions",
    "Prepare session permissions for proposed tool calls without executing them. Pass tool names and their "
    "normal arguments. Supports file access, isolated shell inspection without environment overrides, "
    "session reading/search and integrations with declared permissions. "
    "Already allowed access needs no prompt. Disabled actions remain blocked. Action-specific or one-time-only "
    "approvals must be requested during actual execution. Use for known required or recommended automation access.",
    RequestSessionPermissionsArguments, request_session_permissions,
    label="Session-Freigaben anfragen", icon="verified_user",
    actions=(ToolAction("permissions.request", "Freigaben anfragen", ToolExecutionPolicy.AUTO,
                        ToolExecutionPolicy.AUTO, configurable=False, allowed_policies=(ToolExecutionPolicy.AUTO,)),),
)
