from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.messaging.approvals import (
    MAX_APPROVAL_PROMPT_TEXT,
    MessagingApprovalAction,
    MessagingApprovalError,
    MessagingApprovalPayload,
    build_approval_actions,
    build_approval_fallback_text,
    build_approval_prompt_text,
    decode_approval_callback,
    decode_approval_token,
    encode_approval_callback,
    encode_approval_token,
    enqueue_approval_prompt,
    parse_approval_command,
    parse_approval_payload,
    resolve_approval_action,
)
from mato_loop_backend.models import (
    AgentRun,
    ApprovalRequest,
    Message,
    MessagingEndpoint,
    MessagingInbox,
    MessagingOutbox,
)


def _approval() -> ApprovalRequest:
    return ApprovalRequest(
        id=uuid4(),
        request_type="agent_action",
        user_id=uuid4(),
        session_id=uuid4(),
        run_id=uuid4(),
        action_kind="file_write",
        risk_level="medium",
        title="Datei schreiben?",
        reason="Vom Agent angeforderte Tool-Aktion",
        summary="Der Agent möchte eine Datei schreiben.",
        capability="filesystem.write",
        resource_type="file",
        resource_scope="secret scope",
        payload={},
        available_decisions=["allow_once", "allow_session", "deny"],
        status="pending",
    )


def _provenance(approval: ApprovalRequest):
    endpoint = MessagingEndpoint(
        id=uuid4(),
        user_id=approval.user_id,
        provider="test-provider",
        name="Test",
        enabled=True,
        external_identity="bot",
        paired_user_id="user-1",
        paired_chat_id="chat-1",
    )
    message = Message(
        id=uuid4(),
        session_id=approval.session_id,
        role="user",
        content="hello",
        source_type="endpoint",
        source_provider=endpoint.provider,
        source_endpoint_id=endpoint.id,
    )
    run = AgentRun(
        id=approval.run_id,
        session_id=approval.session_id,
        input_message_id=message.id,
        status="waiting_approval",
    )
    inbox = MessagingInbox(
        id=uuid4(),
        endpoint_id=endpoint.id,
        external_event_id="event-1",
        external_message_id="input-1",
        payload={},
        status="completed",
        session_id=approval.session_id,
        input_message_id=message.id,
        agent_run_id=run.id,
    )
    return endpoint, message, run, inbox


def _payload(approval: ApprovalRequest) -> dict:
    actions = build_approval_actions(approval)
    return MessagingApprovalPayload(
        kind="approval",
        version=1,
        text="Freigabe erforderlich",
        approvalId=approval.id,
        sessionId=approval.session_id,
        runId=approval.run_id,
        actions=actions,
    ).model_dump(mode="json", by_alias=True)


def test_payload_is_strict_typed_and_requires_kind() -> None:
    approval = _approval()
    payload = _payload(approval)

    parsed = parse_approval_payload(payload)

    assert parsed is not None
    assert parsed.approval_id == approval.id
    assert set(payload) == {
        "kind",
        "version",
        "text",
        "approvalId",
        "sessionId",
        "runId",
        "actions",
    }
    assert parse_approval_payload({"text": "ordinary message"}) is None
    with pytest.raises(ValidationError):
        MessagingApprovalPayload.model_validate({**payload, "unexpected": True})
    with pytest.raises(ValidationError):
        MessagingApprovalAction(decision="allow_once", label="Ablehnen")
    with pytest.raises(ValidationError):
        MessagingApprovalAction.model_validate(
            {"decision": "approve", "label": "Genehmigen"}
        )


def test_uuid_token_callback_and_exact_command_codecs() -> None:
    approval_id = UUID(int=0)
    token = encode_approval_token(approval_id)

    assert token == "AAAAAAAAAAAAAAAAAAAAAA"
    assert len(token) == 22
    assert decode_approval_token(token) == approval_id
    with pytest.raises(ValueError):
        decode_approval_token(token + "=")

    for decision in ("allow_once", "allow_session", "deny"):
        callback = encode_approval_callback(approval_id, decision)
        assert len(callback.encode()) <= 64
        assert decode_approval_callback(callback) == (approval_id, decision)
    with pytest.raises(ValueError):
        decode_approval_callback(f"a2:{token}:o")

    assert parse_approval_command(f"/approval {token} once") == (
        approval_id,
        "allow_once",
    )
    assert parse_approval_command(f"/approval@my_bot {token} session") == (
        approval_id,
        "allow_session",
    )
    assert parse_approval_command(f"/approval {token} deny") == (approval_id, "deny")
    for text in (
        f"Bitte /approval {token} once",
        f"/approval {token} once bitte",
        f" /approval {token} once",
        f"/approval {token} allow_once",
        f"/Approval {token} once",
    ):
        assert parse_approval_command(text) is None


def test_fallback_lists_only_available_exact_commands() -> None:
    approval_id = uuid4()
    token = encode_approval_token(approval_id)

    text = build_approval_fallback_text(approval_id, ("allow_once", "deny"))

    assert text.splitlines()[1:] == [
        f"/approval {token} once",
        f"/approval {token} deny",
    ]
    assert " session" not in text


def test_prompt_is_bounded_and_does_not_leak_unapproved_fields() -> None:
    approval = _approval()
    approval.title = " Title\n" * 200
    approval.summary = "Summary " * 1_000
    approval.reason = "Reason " * 1_000
    approval.resource_scope = "LEAK_SCOPE"
    approval.payload = {
        "arguments": {"token": "LEAK_ARGUMENT"},
        "toolOutput": "LEAK_OUTPUT",
        "metadata": "LEAK_METADATA",
        "displayItems": [
            {"label": "Pfad", "value": "/safe/path", "metadata": "LEAK_NESTED"},
            {"label": "Tags", "value": ["eins", "zwei"]},
            {"label": "Nested", "value": {"secret": "LEAK_OBJECT"}},
        ]
        + [{"label": f"Extra {index}", "value": "value"} for index in range(20)],
    }

    text = build_approval_prompt_text(approval)

    assert len(text) <= MAX_APPROVAL_PROMPT_TEXT
    assert "Pfad: /safe/path" in text
    assert "Tags: eins, zwei" in text
    assert "LEAK_" not in text
    assert "Nested" not in text
    assert "Extra 19" not in text


@pytest.mark.parametrize("action_kind", ["build_skill", "edit_skill"])
def test_skill_write_is_deny_only_and_directs_user_to_matoloop(action_kind) -> None:
    approval = _approval()
    approval.action_kind = action_kind
    approval.capability = "skills.write"

    actions = build_approval_actions(approval)

    assert [(action.decision, action.label) for action in actions] == [
        ("deny", "Ablehnen")
    ]
    assert "Prüfe diese Skill-Freigabe in MatoLoop." in build_approval_prompt_text(
        approval
    )


def test_agent_actions_intersect_supported_decisions_in_stable_order() -> None:
    approval = _approval()
    approval.available_decisions = ["deny", "approve", "allow_once"]

    assert [action.decision for action in build_approval_actions(approval)] == [
        "allow_once",
        "deny",
    ]
    approval.request_type = "memory_change"
    assert build_approval_actions(approval) == []


async def test_enqueue_loads_and_validates_provenance_without_committing() -> None:
    approval = _approval()
    endpoint, message, run, _inbox = _provenance(approval)
    db = AsyncMock(spec=AsyncSession)
    db.get.side_effect = [run, message, endpoint]

    outbox = await enqueue_approval_prompt(db, approval)

    assert db.get.await_args_list[-1].kwargs == {"with_for_update": True}
    assert db.add.call_args.args == (outbox,)
    assert outbox.endpoint_id == endpoint.id
    assert outbox.inbox_id is None
    assert outbox.destination == endpoint.paired_chat_id
    assert outbox.deduplication_key == f"approval:{approval.id}"
    assert outbox.payload["approvalId"] == str(approval.id)
    assert outbox.payload["sessionId"] == str(approval.session_id)
    assert outbox.payload["runId"] == str(approval.run_id)
    assert outbox.payload["kind"] == "approval"
    assert "/approval" not in outbox.payload["text"]
    db.commit.assert_not_awaited()


async def test_enqueue_fails_closed_on_provenance_mismatch() -> None:
    approval = _approval()
    endpoint, message, run, _inbox = _provenance(approval)
    message.source_provider = "other-provider"
    db = AsyncMock(spec=AsyncSession)
    db.get.side_effect = [run, message, endpoint]

    with pytest.raises(MessagingApprovalError) as exc_info:
        await enqueue_approval_prompt(db, approval)

    assert exc_info.value.code == "approval.notFound"
    db.add.assert_not_called()
    db.commit.assert_not_awaited()


async def test_resolution_authorizes_relations_and_delegates_idempotently(
    monkeypatch,
) -> None:
    approval = _approval()
    endpoint, message, run, inbox = _provenance(approval)
    outbox = MessagingOutbox(
        id=uuid4(),
        endpoint_id=endpoint.id,
        inbox_id=None,
        destination=endpoint.paired_chat_id,
        deduplication_key=f"approval:{approval.id}",
        payload=_payload(approval),
        status="delivered",
        external_message_ids=["prompt-1"],
    )
    db = AsyncMock(spec=AsyncSession)
    db.get.side_effect = [approval, run, message]
    db.scalar.side_effect = [inbox, outbox]
    decide = AsyncMock(return_value={"status": "approved", "decision": "allow_once"})
    monkeypatch.setattr(
        "mato_loop_backend.agent.approval_decisions.decide_agent_action", decide
    )

    result = await resolve_approval_action(
        db, endpoint, approval.id, "allow_once", "prompt-1"
    )

    assert result == {"status": "approved", "decision": "allow_once"}
    assert db.get.await_args_list[0].kwargs == {"with_for_update": True}
    decide.assert_awaited_once_with(
        db, approval, "allow_once", idempotent_same_decision=True
    )


async def test_resolution_allows_same_decision_retry_after_run_resumed(monkeypatch) -> None:
    approval = _approval()
    approval.status = "approved"
    approval.decision = "allow_once"
    endpoint, message, run, inbox = _provenance(approval)
    run.status = "completed"
    outbox = MessagingOutbox(
        endpoint_id=endpoint.id,
        inbox_id=None,
        destination=endpoint.paired_chat_id,
        deduplication_key=f"approval:{approval.id}",
        payload=_payload(approval),
        status="delivered",
        external_message_ids=["prompt-1"],
    )
    db = AsyncMock(spec=AsyncSession)
    db.get.side_effect = [approval, run, message]
    db.scalar.side_effect = [inbox, outbox]
    decide = AsyncMock(return_value={"status": "approved", "decision": "allow_once"})
    monkeypatch.setattr(
        "mato_loop_backend.agent.approval_decisions.decide_agent_action", decide
    )

    result = await resolve_approval_action(db, endpoint, approval.id, "allow_once")

    assert result["status"] == "approved"
    decide.assert_awaited_once()


@pytest.mark.parametrize("failure", ["owner", "run_status", "external_message"])
async def test_resolution_fails_closed_without_disclosing_invariant(
    monkeypatch, failure
) -> None:
    approval = _approval()
    endpoint, message, run, inbox = _provenance(approval)
    outbox = MessagingOutbox(
        endpoint_id=endpoint.id,
        inbox_id=None,
        destination=endpoint.paired_chat_id,
        deduplication_key=f"approval:{approval.id}",
        payload=_payload(approval),
        status="delivered",
        external_message_ids=["prompt-1"],
    )
    source_message_id = "prompt-1"
    if failure == "owner":
        endpoint.user_id = uuid4()
    elif failure == "run_status":
        run.status = "running"
    else:
        source_message_id = "other-message"
    db = AsyncMock(spec=AsyncSession)
    db.get.side_effect = [approval, run, message]
    db.scalar.side_effect = [inbox, outbox]
    decide = AsyncMock()
    monkeypatch.setattr(
        "mato_loop_backend.agent.approval_decisions.decide_agent_action", decide
    )

    with pytest.raises(MessagingApprovalError) as exc_info:
        await resolve_approval_action(
            db, endpoint, approval.id, "allow_once", source_message_id
        )

    assert exc_info.value.code == "approval.notFound"
    decide.assert_not_awaited()
