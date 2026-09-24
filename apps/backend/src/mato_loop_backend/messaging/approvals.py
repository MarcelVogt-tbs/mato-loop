from __future__ import annotations

import base64
import binascii
import math
import re
from collections.abc import Iterable
from typing import Annotated, Literal, cast
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.models import (
    AgentRun,
    ApprovalRequest,
    Message,
    MessagingEndpoint,
    MessagingInbox,
    MessagingOutbox,
)

ApprovalDecision = Literal["allow_once", "allow_session", "deny"]

MAX_APPROVAL_PROMPT_TEXT = 3_500
MAX_APPROVAL_PAYLOAD_TEXT = 4_096
_MAX_DISPLAY_ITEMS = 6
_MAX_DISPLAY_LIST_ITEMS = 6
_ACTION_LABELS: dict[ApprovalDecision, str] = {
    "allow_once": "Einmal erlauben",
    "allow_session": "Für Session erlauben",
    "deny": "Ablehnen",
}
_COMMAND_DECISIONS: dict[str, ApprovalDecision] = {
    "once": "allow_once",
    "session": "allow_session",
    "deny": "deny",
}
_DECISION_COMMANDS = {value: key for key, value in _COMMAND_DECISIONS.items()}
_CALLBACK_CODES: dict[ApprovalDecision, str] = {
    "allow_once": "o",
    "allow_session": "s",
    "deny": "d",
}
_CODE_DECISIONS = {value: key for key, value in _CALLBACK_CODES.items()}
_TOKEN_RE = re.compile(r"[A-Za-z0-9_-]{22}")
_COMMAND_RE = re.compile(
    r"/approval(?:@[A-Za-z0-9_]{1,32})? ([A-Za-z0-9_-]{22}) (once|session|deny)"
)


class MessagingApprovalAction(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    decision: ApprovalDecision
    label: Literal["Einmal erlauben", "Für Session erlauben", "Ablehnen"]

    @model_validator(mode="after")
    def label_matches_decision(self) -> MessagingApprovalAction:
        if self.label != _ACTION_LABELS[self.decision]:
            raise ValueError("invalid approval action label")
        return self


class MessagingApprovalPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    kind: Literal["approval"]
    version: Literal[1]
    text: Annotated[str, Field(min_length=1, max_length=MAX_APPROVAL_PAYLOAD_TEXT)]
    approval_id: UUID = Field(alias="approvalId", strict=False)
    session_id: UUID = Field(alias="sessionId", strict=False)
    run_id: UUID = Field(alias="runId", strict=False)
    actions: Annotated[list[MessagingApprovalAction], Field(min_length=1, max_length=3)]

    @model_validator(mode="after")
    def actions_are_unique(self) -> MessagingApprovalPayload:
        decisions = [action.decision for action in self.actions]
        if len(decisions) != len(set(decisions)):
            raise ValueError("duplicate approval action")
        return self


class MessagingApprovalError(RuntimeError):
    code = "approval.notFound"

    def __init__(self) -> None:
        super().__init__(self.code)


def parse_approval_payload(value: object) -> MessagingApprovalPayload | None:
    if not isinstance(value, dict) or value.get("kind") != "approval":
        return None
    return MessagingApprovalPayload.model_validate(value)


def encode_approval_token(approval_id: UUID) -> str:
    return base64.urlsafe_b64encode(approval_id.bytes).rstrip(b"=").decode("ascii")


def decode_approval_token(token: str) -> UUID:
    if _TOKEN_RE.fullmatch(token) is None:
        raise ValueError("invalid approval token")
    try:
        raw = base64.b64decode(token + "==", altchars=b"-_", validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("invalid approval token") from exc
    if len(raw) != 16 or encode_approval_token(UUID(bytes=raw)) != token:
        raise ValueError("invalid approval token")
    return UUID(bytes=raw)


def encode_approval_callback(approval_id: UUID, decision: ApprovalDecision) -> str:
    try:
        code = _CALLBACK_CODES[decision]
    except KeyError as exc:
        raise ValueError("invalid approval decision") from exc
    value = f"a1:{encode_approval_token(approval_id)}:{code}"
    if len(value.encode("utf-8")) > 64:
        raise ValueError("approval callback is too long")
    return value


def decode_approval_callback(value: str) -> tuple[UUID, ApprovalDecision]:
    parts = value.split(":")
    if len(parts) != 3 or parts[0] != "a1" or parts[2] not in _CODE_DECISIONS:
        raise ValueError("invalid approval callback")
    return decode_approval_token(parts[1]), _CODE_DECISIONS[parts[2]]


def parse_approval_command(text: str) -> tuple[UUID, ApprovalDecision] | None:
    match = _COMMAND_RE.fullmatch(text)
    if match is None:
        return None
    try:
        approval_id = decode_approval_token(match.group(1))
    except ValueError:
        return None
    return approval_id, _COMMAND_DECISIONS[match.group(2)]


def build_approval_fallback_text(
    approval_id: UUID, decisions: Iterable[ApprovalDecision]
) -> str:
    available = set(decisions)
    commands = [
        f"/approval {encode_approval_token(approval_id)} {_DECISION_COMMANDS[decision]}"
        for decision in _ACTION_LABELS
        if decision in available
    ]
    return (
        "Antworte mit genau einem dieser Befehle:\n" + "\n".join(commands)
        if commands
        else ""
    )


def build_approval_actions(approval: ApprovalRequest) -> list[MessagingApprovalAction]:
    if approval.request_type != "agent_action":
        return []
    decisions: Iterable[ApprovalDecision]
    if approval.capability == "skills.write" and approval.action_kind in {
        "build_skill",
        "edit_skill",
    }:
        decisions = ("deny",)
    else:
        available = set(approval.available_decisions)
        decisions = (decision for decision in _ACTION_LABELS if decision in available)
    return [
        MessagingApprovalAction(decision=decision, label=_ACTION_LABELS[decision])
        for decision in decisions
    ]


def _bounded_text(value: object, limit: int) -> str | None:
    if type(value) is not str:
        return None
    normalized = " ".join(value.split())[:limit]
    return normalized or None


def _display_scalar(value: object, limit: int) -> str | None:
    if type(value) is str:
        return _bounded_text(value, limit)
    if type(value) is bool:
        return "Ja" if value else "Nein"
    if type(value) is int:
        return str(value)[:limit]
    if type(value) is float and math.isfinite(value):
        return str(value)[:limit]
    return None


def build_approval_prompt_text(approval: ApprovalRequest) -> str:
    lines = ["Freigabe erforderlich"]
    title = _bounded_text(approval.title, 240)
    summary = _bounded_text(approval.summary, 1_000)
    reason = _bounded_text(approval.reason, 500)
    if title:
        lines.extend(("", title))
    if approval.capability == "skills.write" and approval.action_kind in {
        "build_skill",
        "edit_skill",
    }:
        lines.extend(("", "Prüfe diese Skill-Freigabe in MatoLoop."))
    if summary:
        lines.extend(("", summary))
    if reason:
        lines.extend(("", f"Grund: {reason}"))

    display_items = approval.payload.get("displayItems")
    details: list[str] = []
    if type(display_items) is list:
        for item in display_items[:_MAX_DISPLAY_ITEMS]:
            if type(item) is not dict:
                continue
            label = _bounded_text(item.get("label"), 80)
            value = item.get("value")
            rendered = _display_scalar(value, 240)
            if type(value) is list:
                rendered_items = [
                    rendered_item
                    for entry in value[:_MAX_DISPLAY_LIST_ITEMS]
                    if (rendered_item := _display_scalar(entry, 80)) is not None
                ]
                rendered = ", ".join(rendered_items)[:240] or None
            if label and rendered:
                details.append(f"- {label}: {rendered}")
    if details:
        lines.extend(("", "Details:", *details))
    return "\n".join(lines)[:MAX_APPROVAL_PROMPT_TEXT].rstrip()


def _valid_endpoint(endpoint: MessagingEndpoint, approval: ApprovalRequest) -> bool:
    return bool(
        endpoint.id
        and endpoint.user_id == approval.user_id
        and endpoint.enabled
        and endpoint.deleted_at is None
        and endpoint.paired_user_id
        and endpoint.paired_chat_id
    )


def _valid_provenance(
    approval: ApprovalRequest,
    run: AgentRun,
    message: Message,
    endpoint: MessagingEndpoint,
) -> bool:
    return bool(
        approval.request_type == "agent_action"
        and approval.action_kind != "terminal_command"
        and approval.status == "pending"
        and approval.session_id is not None
        and approval.run_id == run.id
        and run.session_id == approval.session_id
        and run.status == "waiting_approval"
        and run.input_message_id == message.id
        and message.session_id == approval.session_id
        and message.role == "user"
        and message.source_type == "endpoint"
        and message.source_endpoint_id == endpoint.id
        and message.source_provider == endpoint.provider
        and _valid_endpoint(endpoint, approval)
    )


async def enqueue_approval_prompt(
    db: AsyncSession, approval: ApprovalRequest
) -> MessagingOutbox:
    run = await db.get(AgentRun, approval.run_id) if approval.run_id is not None else None
    message = (
        await db.get(Message, run.input_message_id)
        if run is not None and run.input_message_id is not None
        else None
    )
    endpoint = (
        await db.get(MessagingEndpoint, message.source_endpoint_id, with_for_update=True)
        if message is not None and message.source_endpoint_id is not None
        else None
    )
    if run is None or message is None or endpoint is None or not _valid_provenance(
        approval, run, message, endpoint
    ):
        raise MessagingApprovalError

    actions = build_approval_actions(approval)
    if not actions:
        raise MessagingApprovalError
    payload = MessagingApprovalPayload(
        kind="approval",
        version=1,
        text=build_approval_prompt_text(approval),
        approvalId=approval.id,
        sessionId=approval.session_id,
        runId=approval.run_id,
        actions=actions,
    )
    outbox = MessagingOutbox(
        endpoint_id=endpoint.id,
        inbox_id=None,
        destination=cast(str, endpoint.paired_chat_id),
        deduplication_key=f"approval:{approval.id}",
        payload=payload.model_dump(mode="json", by_alias=True),
    )
    db.add(outbox)
    return outbox


async def resolve_approval_action(
    db: AsyncSession,
    endpoint: MessagingEndpoint,
    approval_id: UUID,
    decision: ApprovalDecision,
    source_external_message_id: str | None = None,
) -> dict[str, str]:
    approval = await db.get(ApprovalRequest, approval_id)
    if (
        approval is None
        or approval.id != approval_id
        or approval.request_type != "agent_action"
        or approval.action_kind == "terminal_command"
        or approval.status not in {"pending", "approved", "denied"}
        or approval.session_id is None
        or approval.run_id is None
        or not _valid_endpoint(endpoint, approval)
        or decision not in _ACTION_LABELS
    ):
        raise MessagingApprovalError

    run = await db.get(AgentRun, approval.run_id)
    message = (
        await db.get(Message, run.input_message_id)
        if run is not None and run.input_message_id is not None
        else None
    )
    if (
        run is None
        or message is None
        or run.session_id != approval.session_id
        or run.input_message_id != message.id
        or message.session_id != approval.session_id
        or message.role != "user"
        or message.source_type != "endpoint"
        or message.source_endpoint_id != endpoint.id
        or message.source_provider != endpoint.provider
        or (approval.status == "pending" and run.status != "waiting_approval")
    ):
        raise MessagingApprovalError

    inbox = await db.scalar(
        select(MessagingInbox).where(
            MessagingInbox.endpoint_id == endpoint.id,
            MessagingInbox.agent_run_id == run.id,
            MessagingInbox.input_message_id == message.id,
            MessagingInbox.session_id == approval.session_id,
        )
    )
    outbox = await db.scalar(
        select(MessagingOutbox).where(
            MessagingOutbox.endpoint_id == endpoint.id,
            MessagingOutbox.deduplication_key == f"approval:{approval.id}",
        )
    )
    if (
        inbox is None
        or inbox.endpoint_id != endpoint.id
        or inbox.agent_run_id != run.id
        or inbox.input_message_id != message.id
        or inbox.session_id != approval.session_id
        or inbox.status != "completed"
        or outbox is None
        or outbox.endpoint_id != endpoint.id
        or outbox.inbox_id is not None
        or outbox.destination != endpoint.paired_chat_id
        or outbox.deduplication_key != f"approval:{approval.id}"
        or outbox.status != "delivered"
        or (
            source_external_message_id is not None
            and source_external_message_id not in outbox.external_message_ids
        )
    ):
        raise MessagingApprovalError

    try:
        payload = parse_approval_payload(outbox.payload)
    except ValidationError as exc:
        raise MessagingApprovalError from exc
    if (
        payload is None
        or payload.approval_id != approval.id
        or payload.session_id != approval.session_id
        or payload.run_id != approval.run_id
        or decision not in {action.decision for action in payload.actions}
    ):
        raise MessagingApprovalError

    from mato_loop_backend.permissions.decisions import decide_agent_action

    return await decide_agent_action(
        db, approval, decision, idempotent_same_decision=True
    )
