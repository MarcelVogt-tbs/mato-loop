from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

import mato_loop_backend.permissions.resolution as permissions_resolution
import mato_loop_backend.tools.access as tools_access
from mato_loop_backend.agent.session_secrets import session_secrets
from mato_loop_backend.models import ChatSession, ExecutionAgent
from mato_loop_backend.permissions.policy import personal_variables_permission
from mato_loop_backend.personal_variables import get_personal_variable, personal_variable_value
from mato_loop_backend.settings import ToolExecutionPolicy
from mato_loop_backend.tools.arguments import ProtectedText, ProtectedTextRef
from mato_loop_backend.tools.context import ToolContext
from mato_loop_backend.tools.contracts import ToolResult


async def resolve_secret_refs(
    context: ToolContext, references: dict[str, UUID | str], *, secret_only: bool = False
) -> dict[str, str] | ToolResult:
    """Resolve owned variables and protected-input references inside the backend."""
    variables = {}
    for reference in dict.fromkeys(ref for ref in references.values() if isinstance(ref, UUID)):
        variable = await get_personal_variable(context.db, context.session.user_id, reference)
        if variable is None or (secret_only and variable.metadata_json.get("secret") is not True):
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
            if variable is None or (secret_only and variable.metadata_json.get("secret") is not True):
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


async def effective_sudo_reference(
    db: AsyncSession, session: ChatSession, run_secret_refs: dict[str, str], agent_id: UUID,
    reference: UUID | str | None = None,
) -> UUID | str | None:
    if reference is not None:
        return reference
    name = f"SUDO_PASSWORD_{agent_id.hex.upper()}"
    temporary = session_secrets.references(session.id).get(name) or run_secret_refs.get(name)
    if temporary is not None:
        return temporary
    agent = await db.get(ExecutionAgent, agent_id, populate_existing=True)
    if agent is not None and agent.user_id == session.user_id and agent.revoked_at is None:
        return agent.sudo_credential_id
    return None


def discard_sudo_reference(context: ToolContext, reference: UUID | str | None) -> None:
    if reference is None:
        return
    for name, value in session_secrets.references(context.session.id).items():
        if value == reference:
            session_secrets.discard(context.session.id, name, reference)
    for name, value in list(context.run_secret_refs.items()):
        if value == reference:
            context.run_secret_refs.pop(name)
    if isinstance(reference, str) and reference.startswith("run-secret:"):
        context.one_time_secrets.pop(UUID(reference.removeprefix("run-secret:")), None)


async def resolve_sudo_password(
    context: ToolContext, reference: UUID | str | None, agent_id: UUID
) -> str | None | ToolResult:
    name = f"SUDO_PASSWORD_{agent_id.hex.upper()}"
    reference = await effective_sudo_reference(context.db, context.session, context.run_secret_refs, agent_id, reference)
    if reference is None:
        return None
    values = await resolve_secret_refs(context, {name: reference}, secret_only=True)
    if isinstance(values, ToolResult):
        return values
    password = values[name]
    if not 1 <= len(password) <= 4096 or any(c in password for c in "\r\n\x00"):
        return ToolResult.error("personalVariable.valueInvalid")
    return password
