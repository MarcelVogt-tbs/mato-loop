from __future__ import annotations

from dataclasses import replace

from mato_loop_backend.execution import ExecutionClientError
from mato_loop_backend.execution.client import ExecutionResult
from mato_loop_backend.permissions.policy import (
    PermissionDecision,
    PermissionDisplayItem,
    PermissionRequest,
)
from mato_loop_backend.tools import access
from mato_loop_backend.tools.context import ToolContext
from mato_loop_backend.tools.contracts import ToolResult
from mato_loop_backend.tools.secrets import (
    discard_sudo_reference,
    effective_sudo_reference,
    resolve_sudo_password,
)


class AdministratorAuthenticationResult(ExecutionResult):
    passwordless: bool


async def authorize(context: ToolContext, request: PermissionRequest, reference=None) -> bool:
    if context.user_denied:
        return False
    decision = await context.ask_handler(request, check_only=True)
    if decision in {PermissionDecision.DENY, PermissionDecision.USER_DENIED}:
        return False
    reference = effective_sudo_reference(context.session.id, context.run_secret_refs, context.execution_client.agent_id, reference)
    if reference is None:
        authentication = await context.execution_client.request(
            "administrator.run", {"operation": "authenticate", "arguments": {}}, AdministratorAuthenticationResult,
        )
        if not authentication.passwordless:
            raise ExecutionClientError("process.sudoAuthRequired")
    previews = []
    fields = (
        (("oldText", "Vorher"), ("newText", "Nachher")) if context.tool_call.name == "edit_file"
        else (("content", "Dateiinhalt"),) if context.tool_call.name == "write_file" else ()
    )
    for field, label in fields:
        value = context.tool_call.arguments.get(field)
        preview = value[:8000] + ("\n[Vorschau gekürzt]" if len(value) > 8000 else "") if isinstance(value, str) else "Geschützter Text"
        previews.append(PermissionDisplayItem(label, preview, "code"))
    return await access.allow(context, replace(
        request, action_kind=context.tool_call.name,
        display_items=(*request.display_items, *previews),
    ))


async def request_arguments(context: ToolContext, operation: str, arguments: dict, reference=None) -> dict | ToolResult:
    client = context.execution_client
    reference = effective_sudo_reference(context.session.id, context.run_secret_refs, client.agent_id, reference)
    password = await resolve_sudo_password(context, reference, client.agent_id)
    if isinstance(password, ToolResult):
        return password
    return {"operation": operation, "arguments": arguments, **({"sudoPassword": password} if password else {})}


async def invoke(context: ToolContext, operation: str, arguments: dict, result_type, reference=None):
    payload = await request_arguments(context, operation, arguments, reference)
    if isinstance(payload, ToolResult):
        return payload
    try:
        return await context.execution_client.request("administrator.run", payload, result_type)
    except ExecutionClientError as exc:
        if exc.code in {"process.sudoAuthFailed", "process.sudoAuthRequired"}:
            reference = effective_sudo_reference(context.session.id, context.run_secret_refs, context.execution_client.agent_id, reference)
            discard_sudo_reference(context, reference)
        raise
