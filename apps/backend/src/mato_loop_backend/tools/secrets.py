from __future__ import annotations

from uuid import UUID

import mato_loop_backend.permissions.resolution as permissions_resolution
import mato_loop_backend.tools.access as tools_access
from mato_loop_backend.agent.session_secrets import session_secrets
from mato_loop_backend.permissions.policy import personal_variables_permission
from mato_loop_backend.personal_variables import get_personal_variable, personal_variable_value
from mato_loop_backend.settings import ToolExecutionPolicy
from mato_loop_backend.tools.arguments import ProtectedText, ProtectedTextRef
from mato_loop_backend.tools.context import ToolContext
from mato_loop_backend.tools.contracts import ToolResult


async def resolve_secret_refs(
    context: ToolContext, references: dict[str, UUID | str]
) -> dict[str, str] | ToolResult:
    """Resolve owned variables and protected-input references inside the backend."""
    variables = {}
    for reference in dict.fromkeys(ref for ref in references.values() if isinstance(ref, UUID)):
        variable = await get_personal_variable(context.db, context.session.user_id, reference)
        if variable is None:
            return ToolResult.error("personalVariable.notFound")
        variables[reference] = variable
    if variables and not await tools_access.allow(
        context,
        personal_variables_permission(
            context.session.user_id,
            names=tuple(variable.label for variable in variables.values()),
        ),
    ):
        return ToolResult.error("permission.denied")
    if variables:
        context.tool_settings = await tools_access.profile_tool_settings(context)
        if permissions_resolution.tool_execution_policy(
            context.tool_settings, "secrets.access", ToolExecutionPolicy.ASK
        ) == ToolExecutionPolicy.DISABLED:
            return ToolResult.error("tool.unavailable")
    values = {}
    for key, reference in references.items():
        if isinstance(reference, UUID):
            variable = await get_personal_variable(
                context.db, context.session.user_id, reference, populate_existing=True
            )
            if variable is None:
                return ToolResult.error("personalVariable.notFound")
            values[key] = personal_variable_value(variable)
            continue
        prefix, separator, raw_id = reference.partition(":")
        try:
            secret_id = UUID(raw_id)
        except (TypeError, ValueError):
            return ToolResult.error("secret.valueRefUnavailable")
        if not separator or prefix not in {"run-secret", "session-secret"}:
            return ToolResult.error("secret.valueRefUnavailable")
        value = (
            context.one_time_secrets.get(secret_id)
            if prefix == "run-secret"
            else session_secrets.resolve(context.session.id, secret_id)
        )
        if value is None:
            return ToolResult.error("secret.valueRefUnavailable")
        values[key] = value
    return values


async def resolve_protected_text(
    context: ToolContext, text: str | ProtectedText,
) -> tuple[str, bool] | ToolResult:
    """Assemble explicit text parts without releasing referenced values to the model."""
    if isinstance(text, str):
        return text, False
    references = {
        str(index): part.value_ref
        for index, part in enumerate(text.parts)
        if isinstance(part, ProtectedTextRef)
    }
    values = await resolve_secret_refs(context, references)
    if isinstance(values, ToolResult):
        return values
    return "".join(
        values[str(index)] if isinstance(part, ProtectedTextRef) else part
        for index, part in enumerate(text.parts)
    ), True


async def resolve_sudo_password(
    context: ToolContext, reference: UUID | str | None, agent_id: UUID
) -> str | None | ToolResult:
    name = f"SUDO_PASSWORD_{agent_id.hex.upper()}"
    if reference is None:
        reference = session_secrets.references(context.session.id).get(name) or context.run_secret_refs.get(name)
    if reference is None:
        return None
    if isinstance(reference, str) and reference.startswith("session-secret:"):
        if session_secrets.references(context.session.id).get(name) != reference:
            return ToolResult.error("secret.valueRefUnavailable")
    values = await resolve_secret_refs(context, {name: reference})
    return values if isinstance(values, ToolResult) else values[name]
