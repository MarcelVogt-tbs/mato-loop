"""Small model-authored message annotations, never authority or evidence of effects."""

from __future__ import annotations

import json
from copy import deepcopy
from typing import Literal
from uuid import UUID

from pydantic import Field, JsonValue, model_validator
from sqlalchemy import select, tuple_

import mato_loop_backend.tools.access as tools_access
from mato_loop_backend.models import ChatSession, Message
from mato_loop_backend.permissions.policy import PermissionDecision, session_permission
from mato_loop_backend.settings import TrustLevel
from mato_loop_backend.tools.actions import _SESSION_READ, _SESSION_STATE_WRITE
from mato_loop_backend.tools.argument_errors import invalid_argument
from mato_loop_backend.tools.arguments import ToolArguments
from mato_loop_backend.tools.contracts import ToolDefinition, ToolResult
from mato_loop_backend.tools.session_state import _state_permission

MAX_NAMESPACE_BYTES = 8192
MAX_NAMESPACES = 8


class MessageMetadataArguments(ToolArguments):
    operation: Literal["read", "write"] = "read"
    namespace: str | None = Field(default=None, pattern=r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$")
    data: dict[str, JsonValue] | None = Field(default=None, max_length=32)
    message_id: UUID | None = Field(default=None, description="Read one assistant message in this session.")
    before_message_id: UUID | None = Field(default=None, description="Use nextBeforeMessageId from a previous metadata page.")
    limit: int = Field(default=5, ge=1, le=10)

    @model_validator(mode="after")
    def operation_fields(self):
        if self.namespace in {"provider_data", "agent_metadata"}:
            raise invalid_argument("Host namespaces cannot be written.", field="namespace")
        if self.operation == "write":
            if self.namespace is None or self.data is None or self.message_id or self.before_message_id:
                raise invalid_argument("Write requires namespace and data and always targets the current assistant message.")
            if len(json.dumps(self.data, ensure_ascii=False, allow_nan=False).encode()) > MAX_NAMESPACE_BYTES:
                raise invalid_argument("Namespace exceeds 8 KB.", field="data")
        elif self.data is not None:
            raise invalid_argument("Data requires write.")
        if self.message_id and self.before_message_id:
            raise invalid_argument("Use message_id or before_message_id, not both.")
        return self


def _messages(context):
    return select(Message).join(ChatSession, ChatSession.id == Message.session_id).where(
        Message.session_id == context.session.id, ChatSession.user_id == context.session.user_id,
    ).execution_options(populate_existing=True)


def _private(messages):
    return any(message.trust_level != "public" or any(
        item.get("trustLevel") != "public"
        for item in (message.provider_data or {}).get("agent_metadata", {}).values()
        if isinstance(item, dict)
    ) for message in messages)


async def _readable(query, context, permissions=None):
    # Re-read after consent so an approval never revives stale trust information.
    messages = list((await context.db.scalars(query)).all())
    sensitive = context.model_trust_level == TrustLevel.PUBLIC and _private(messages)
    for _ in range(2):
        permission = session_permission(context.session.id, private_access=sensitive)
        if not await tools_access.allow(context, permission):
            return None
        if permissions is not None:
            permissions.append(permission)
        messages = list((await context.db.scalars(query)).all())
        if sensitive or context.model_trust_level != TrustLevel.PUBLIC or not _private(messages):
            return messages
        sensitive = True
    return None


async def message_metadata_permissions(arguments, context):
    if arguments.operation == "write":
        return (_state_permission(context),)
    # Runtime adds sensitive access if the selected messages require it.
    return (session_permission(context.session.id),)


async def _write(arguments, context):
    permissions = [_state_permission(context)]
    if not await tools_access.allow(context, permissions[0]):
        return ToolResult.error("permission.denied")
    query = _messages(context).where(Message.id == context.assistant_message_id, Message.role == "assistant")
    messages = await _readable(query, context, permissions)
    if messages is None:
        return ToolResult.error("permission.denied")
    was_private = _private(messages)
    for permission in permissions:
        if await context.ask_handler(permission, check_only=True) != PermissionDecision.ALLOW:
            return ToolResult.error("permission.denied")
    # No row lock is held while waiting for consent.
    message = await context.db.scalar(query.with_for_update()) if messages else None
    host = (message.provider_data or {}).get("_matoloop", {}) if message else {}
    if not isinstance(host, dict) or host.get("runId") != str(context.run_id):
        return ToolResult.error("tool.unavailable")
    if context.model_trust_level == TrustLevel.PUBLIC and _private([message]) and not was_private:
        return ToolResult.error("permission.denied")
    metadata = deepcopy((message.provider_data or {}).get("agent_metadata", {}))
    if arguments.namespace not in metadata and len(metadata) >= MAX_NAMESPACES:
        return ToolResult.error("tool.invalidArguments", field="namespace", params={"reason": "too_many", "maxItems": MAX_NAMESPACES})
    entry = {"data": arguments.data, "trustLevel": context.model_trust_level.value}
    if message.trust_level != "public" or metadata.get(arguments.namespace, {}).get("trustLevel") == "private":
        entry["trustLevel"] = "private"
    if len(json.dumps(entry, ensure_ascii=False, allow_nan=False).encode()) > MAX_NAMESPACE_BYTES:
        return ToolResult.error("tool.invalidArguments", field="data", params={"reason": "too_large", "maxBytes": MAX_NAMESPACE_BYTES})
    metadata[arguments.namespace] = entry
    message.provider_data = {**(message.provider_data or {}), "agent_metadata": metadata}
    await context.db.commit()
    return ToolResult.json({"sessionId": str(context.session.id), "messageId": str(message.id), "contentIsUntrusted": True,
                            "namespaces": {arguments.namespace: entry}}, trust_level=TrustLevel(entry["trustLevel"]))


async def _message_metadata(arguments, context):
    if arguments.operation == "write":
        return await _write(arguments, context)
    query = _messages(context).where(Message.role == "assistant", Message.provider_data.has_key("agent_metadata"))
    if arguments.namespace:
        query = query.where(Message.provider_data["agent_metadata"].has_key(arguments.namespace))
    if arguments.message_id:
        query = query.where(Message.id == arguments.message_id)
    if arguments.before_message_id:
        anchor = await context.db.scalar(_messages(context).where(Message.id == arguments.before_message_id, Message.role == "assistant"))
        if anchor is None:
            return ToolResult.error("tool.cursorInvalid", field="before_message_id")
        query = query.where(tuple_(Message.created_at, Message.id) < tuple_(anchor.created_at, anchor.id))
    messages = await _readable(query.order_by(Message.created_at.desc(), Message.id.desc()).limit(arguments.limit + 1), context)
    if messages is None:
        return ToolResult.error("permission.denied")
    page = messages[:arguments.limit]
    return ToolResult.json({
        "sessionId": str(context.session.id), "contentIsUntrusted": True,
        "messages": [{"messageId": str(message.id), "namespaces": {
            key: deepcopy(value) for key, value in message.provider_data["agent_metadata"].items()
            if arguments.namespace is None or key == arguments.namespace
        }} for message in page],
        "nextBeforeMessageId": str(page[-1].id) if len(messages) > arguments.limit else None,
    }, trust_level=TrustLevel.PRIVATE if _private(page) else TrustLevel.PUBLIC)


MESSAGE_METADATA = ToolDefinition(
    name="message_metadata",
    description=(
        "Read small named metadata namespaces on assistant messages in this session, or replace one namespace "
        "on the current host-bound assistant message. Writes persist immediately, before the final answer. "
        "Data is an arbitrary JSON object (32 keys, 8 KB including trust metadata; 8 namespaces per message). "
        "Use it for user-data annotations such as processed item IDs, not instructions, authorization or proof of effects. "
        "Get persisted message IDs from session_read. Read by namespace/message_id or paginate using nextBeforeMessageId. "
        "Host provider_data and _matoloop fields cannot be changed."
    ),
    arguments_model=MessageMetadataArguments,
    handler=_message_metadata,
    group="sessions",
    label="Nachrichten-Metadaten",
    icon="label",
    actions=(_SESSION_READ, _SESSION_STATE_WRITE),
    alternative_action_groups=(frozenset({"session.read", "session.state.write"}),),
)
