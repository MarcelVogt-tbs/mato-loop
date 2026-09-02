from unittest.mock import AsyncMock, Mock
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.agent.question_decisions import QuestionDecisionError
from mato_loop_backend.agent.runtime import AgentRuntime
from mato_loop_backend.agent.tools import QuestionArguments
from mato_loop_backend.events import run_events
from mato_loop_backend.messaging.questions import (
    MessagingQuestionEnvelope,
    MessagingQuestionError,
    QuestionInteraction,
    decode_question_callback,
    decode_question_token,
    encode_question_callback,
    encode_question_token,
    enqueue_question_prompt,
    parse_question_envelope,
    resolve_question_interaction,
)
from mato_loop_backend.models import (
    AgentRun,
    ChatSession,
    Message,
    MessagingEndpoint,
    MessagingInbox,
    MessagingOutbox,
    QuestionRequest,
)


def _arguments() -> QuestionArguments:
    return QuestionArguments.model_validate(
        {
            "questions": [
                {
                    "id": "format",
                    "type": "single_choice",
                    "header": "Format",
                    "question": "Welches Format?",
                    "options": [{"label": "Kurz"}, {"label": "Ausführlich"}],
                },
                {
                    "id": "topics",
                    "type": "multiple_choice",
                    "header": "Themen",
                    "question": "Welche Themen?",
                    "options": [{"label": "API"}, {"label": "UI"}],
                },
                {
                    "id": "note",
                    "type": "free_text",
                    "header": "Hinweise",
                    "question": "Weitere Hinweise?",
                    "default_answers": ["Keine"],
                },
            ]
        }
    )


def _request() -> QuestionRequest:
    return QuestionRequest(
        id=uuid4(),
        session_id=uuid4(),
        run_id=uuid4(),
        tool_call_id="call-1",
        questions=_arguments().model_dump(mode="json")["questions"],
        status="pending",
    )


def _provenance(request: QuestionRequest):
    session = ChatSession(id=request.session_id, user_id=uuid4(), title="Test")
    endpoint = MessagingEndpoint(
        id=uuid4(),
        user_id=session.user_id,
        provider="test-provider",
        name="Test",
        enabled=True,
        external_identity="bot",
        paired_user_id="user-1",
        paired_chat_id="chat-1",
    )
    message = Message(
        id=uuid4(),
        session_id=session.id,
        role="user",
        content="hello",
        source_type="endpoint",
        source_provider=endpoint.provider,
        source_endpoint_id=endpoint.id,
    )
    run = AgentRun(
        id=request.run_id,
        session_id=session.id,
        input_message_id=message.id,
        status="waiting_input",
    )
    inbox = MessagingInbox(
        id=uuid4(),
        endpoint_id=endpoint.id,
        external_event_id="event-1",
        external_message_id="input-1",
        payload={},
        status="completed",
        session_id=session.id,
        input_message_id=message.id,
        agent_run_id=run.id,
    )
    return session, endpoint, message, run, inbox


def _envelope(request: QuestionRequest, **updates) -> MessagingQuestionEnvelope:
    values = {
        "kind": "question",
        "version": 1,
        "questionId": request.id,
        "sessionId": request.session_id,
        "runId": request.run_id,
        "questions": request.questions,
        **updates,
    }
    return MessagingQuestionEnvelope.model_validate(values)


def _outbox(
    request: QuestionRequest,
    endpoint: MessagingEndpoint,
    envelope: MessagingQuestionEnvelope | None = None,
) -> MessagingOutbox:
    return MessagingOutbox(
        id=uuid4(),
        endpoint_id=endpoint.id,
        inbox_id=None,
        destination=endpoint.paired_chat_id,
        deduplication_key=f"question:{request.id}",
        payload=(envelope or _envelope(request)).model_dump(mode="json", by_alias=True),
        status="delivered",
        external_message_ids=["prompt-1"],
    )


def _resolution_db(request, session, run, message, inbox, outbox) -> AsyncMock:
    db = AsyncMock(spec=AsyncSession)
    db.get.side_effect = [request, session, run, message]
    db.scalar.side_effect = [inbox, outbox]
    return db


def test_question_envelope_is_strict_aliased_and_validates_state() -> None:
    request = _request()
    envelope = _envelope(request)
    payload = envelope.model_dump(mode="json", by_alias=True)

    assert set(payload) == {
        "kind",
        "version",
        "questionId",
        "sessionId",
        "runId",
        "questions",
        "currentIndex",
        "answers",
        "awaitingText",
    }
    assert payload["currentIndex"] == 0
    assert payload["answers"] == {}
    assert payload["awaitingText"] is False
    assert parse_question_envelope(payload) == envelope
    assert parse_question_envelope({"kind": "text"}) is None
    with pytest.raises(ValidationError):
        MessagingQuestionEnvelope.model_validate({**payload, "unexpected": True})
    with pytest.raises(ValidationError):
        MessagingQuestionEnvelope.model_validate({**payload, "currentIndex": 3})
    with pytest.raises(ValidationError):
        MessagingQuestionEnvelope.model_validate(
            {**payload, "answers": {"unknown": ["value"]}}
        )
    with pytest.raises(ValidationError):
        MessagingQuestionEnvelope.model_validate(
            {**payload, "currentIndex": 2, "awaitingText": True}
        )


def test_question_token_callback_and_interaction_validation() -> None:
    question_id = UUID(int=0)
    token = encode_question_token(question_id)

    assert token == "AAAAAAAAAAAAAAAAAAAAAA"
    assert decode_question_token(token) == question_id
    with pytest.raises(ValueError):
        decode_question_token(token + "=")

    interactions = [
        QuestionInteraction(action="select", optionIndex=11),
        QuestionInteraction(action="toggle", optionIndex=0),
        QuestionInteraction(action="next"),
        QuestionInteraction(action="back"),
        QuestionInteraction(action="custom"),
        QuestionInteraction(action="reject"),
        QuestionInteraction(action="submit"),
    ]
    for interaction in interactions:
        callback = encode_question_callback(question_id, interaction)
        assert len(callback.encode()) <= 64
        assert "Kurz" not in callback
        assert decode_question_callback(callback) == (question_id, interaction)

    with pytest.raises(ValueError):
        encode_question_callback(
            question_id, QuestionInteraction(action="text", text="exact text")
        )
    with pytest.raises(ValueError):
        decode_question_callback(f"q1:{token}:s:01")
    with pytest.raises(ValidationError):
        QuestionInteraction(action="select")
    with pytest.raises(ValidationError):
        QuestionInteraction(action="next", optionIndex=0)
    with pytest.raises(ValidationError):
        QuestionInteraction(action="text")


async def test_enqueue_question_prompt_uses_session_owner_and_does_not_commit() -> None:
    request = _request()
    session, endpoint, message, run, _inbox = _provenance(request)
    db = AsyncMock(spec=AsyncSession)
    db.get.side_effect = [run, message, session, endpoint]

    outbox = await enqueue_question_prompt(db, request)

    assert db.get.await_args_list[-1].kwargs == {"with_for_update": True}
    assert outbox.endpoint_id == endpoint.id
    assert outbox.inbox_id is None
    assert outbox.destination == endpoint.paired_chat_id
    assert outbox.deduplication_key == f"question:{request.id}"
    assert outbox.payload["questionId"] == str(request.id)
    assert outbox.payload["sessionId"] == str(session.id)
    assert outbox.payload["runId"] == str(run.id)
    assert outbox.payload["kind"] == "question"
    db.add.assert_called_once_with(outbox)
    db.commit.assert_not_awaited()


@pytest.mark.parametrize("failure", ["owner", "provider", "run", "status", "pairing"])
async def test_enqueue_question_prompt_fails_closed_on_provenance(failure) -> None:
    request = _request()
    session, endpoint, message, run, _inbox = _provenance(request)
    if failure == "owner":
        endpoint.user_id = uuid4()
    elif failure == "provider":
        message.source_provider = "other"
    elif failure == "run":
        run.session_id = uuid4()
    elif failure == "status":
        run.status = "running"
    else:
        endpoint.paired_chat_id = None
        endpoint.paired_user_id = None
    db = AsyncMock(spec=AsyncSession)
    db.get.side_effect = [run, message, session, endpoint]

    with pytest.raises(MessagingQuestionError):
        await enqueue_question_prompt(db, request)

    db.add.assert_not_called()
    db.commit.assert_not_awaited()


async def test_all_pending_question_interactions_update_valid_envelope_state() -> None:
    request = _request()
    session, endpoint, message, run, inbox = _provenance(request)
    outbox = _outbox(request, endpoint)

    async def interact(interaction: QuestionInteraction):
        db = _resolution_db(request, session, run, message, inbox, outbox)
        result = await resolve_question_interaction(
            db, endpoint, request.id, interaction, "prompt-1"
        )
        assert result.status == "pending"
        assert result.finalized is False
        assert parse_question_envelope(outbox.payload) == result.envelope
        db.commit.assert_awaited_once()
        return result

    result = await interact(QuestionInteraction(action="select", optionIndex=1))
    assert result.envelope.answers == {"format": ["Ausführlich"]}

    result = await interact(QuestionInteraction(action="custom"))
    assert result.envelope.awaiting_text is True
    result = await interact(QuestionInteraction(action="text", text=" Eigener Stil "))
    assert result.envelope.answers["format"] == ["Eigener Stil"]
    assert result.envelope.current_index == 1
    assert result.envelope.awaiting_text is False

    result = await interact(QuestionInteraction(action="toggle", optionIndex=0))
    assert result.envelope.answers["topics"] == ["API"]
    result = await interact(QuestionInteraction(action="toggle", optionIndex=1))
    assert result.envelope.answers["topics"] == ["API", "UI"]
    result = await interact(QuestionInteraction(action="toggle", optionIndex=0))
    assert result.envelope.answers["topics"] == ["UI"]

    result = await interact(QuestionInteraction(action="next"))
    assert result.envelope.current_index == 2
    result = await interact(QuestionInteraction(action="back"))
    assert result.envelope.current_index == 1
    result = await interact(QuestionInteraction(action="next"))
    result = await interact(QuestionInteraction(action="text", text=" Hinweis "))
    assert result.envelope.answers["note"] == ["Hinweis"]
    assert result.envelope.current_index == 2


async def test_text_and_option_actions_are_limited_to_current_mode() -> None:
    request = _request()
    session, endpoint, message, run, inbox = _provenance(request)

    for interaction in (
        QuestionInteraction(action="text", text="not requested"),
        QuestionInteraction(action="toggle", optionIndex=0),
        QuestionInteraction(action="select", optionIndex=11),
    ):
        outbox = _outbox(request, endpoint)
        db = _resolution_db(request, session, run, message, inbox, outbox)
        with pytest.raises(MessagingQuestionError):
            await resolve_question_interaction(
                db, endpoint, request.id, interaction, "prompt-1"
            )
        db.commit.assert_not_awaited()


@pytest.mark.parametrize("failure", ["owner", "source", "inbox", "payload", "run"])
async def test_resolution_binds_owner_source_and_original_provenance(failure) -> None:
    request = _request()
    session, endpoint, message, run, inbox = _provenance(request)
    outbox = _outbox(request, endpoint)
    source = "prompt-1"
    if failure == "owner":
        endpoint.user_id = uuid4()
    elif failure == "source":
        source = "other-prompt"
    elif failure == "inbox":
        inbox.status = "failed"
    elif failure == "payload":
        outbox.payload["runId"] = str(uuid4())
    else:
        run.status = "running"
    db = _resolution_db(request, session, run, message, inbox, outbox)

    with pytest.raises(MessagingQuestionError):
        await resolve_question_interaction(
            db, endpoint, request.id, QuestionInteraction(action="next"), source
        )

    db.commit.assert_not_awaited()


async def test_submit_applies_defaults_and_identical_retry_has_no_effects(
    monkeypatch,
) -> None:
    request = _request()
    session, endpoint, message, run, inbox = _provenance(request)
    outbox = _outbox(request, endpoint)
    claim = Mock(return_value=True)
    resolve = Mock(return_value=True)
    monkeypatch.setattr(AgentRuntime, "claim_question_resolution", claim)
    monkeypatch.setattr(AgentRuntime, "resolve_question", resolve)
    monkeypatch.setattr(run_events, "publish", AsyncMock())
    db = _resolution_db(request, session, run, message, inbox, outbox)

    result = await resolve_question_interaction(
        db, endpoint, request.id, QuestionInteraction(action="submit"), "prompt-1"
    )

    assert result.model_dump() == {
        "status": "answered",
        "envelope": None,
        "changed": True,
        "finalized": True,
    }
    assert request.answers == {"format": [], "topics": [], "note": ["Keine"]}
    resolve.assert_called_once_with(request.id, request.answers)

    run.status = "completed"
    retry_db = _resolution_db(request, session, run, message, inbox, outbox)
    retry = await resolve_question_interaction(
        retry_db,
        endpoint,
        request.id,
        QuestionInteraction(action="submit"),
        "prompt-1",
    )
    assert retry.status == "answered"
    assert retry.changed is False
    claim.assert_called_once()
    resolve.assert_called_once()
    retry_db.commit.assert_not_awaited()

    conflict_db = _resolution_db(request, session, run, message, inbox, outbox)
    with pytest.raises(QuestionDecisionError) as exc_info:
        await resolve_question_interaction(
            conflict_db,
            endpoint,
            request.id,
            QuestionInteraction(action="reject"),
            "prompt-1",
        )
    assert exc_info.value.code == "question.alreadyAnswered"


async def test_reject_is_final_and_repeated_reject_is_idempotent(monkeypatch) -> None:
    request = _request()
    session, endpoint, message, run, inbox = _provenance(request)
    outbox = _outbox(request, endpoint)
    claim = Mock(return_value=True)
    resolve = Mock(return_value=True)
    monkeypatch.setattr(AgentRuntime, "claim_question_resolution", claim)
    monkeypatch.setattr(AgentRuntime, "resolve_question", resolve)
    monkeypatch.setattr(run_events, "publish", AsyncMock())
    db = _resolution_db(request, session, run, message, inbox, outbox)

    result = await resolve_question_interaction(
        db, endpoint, request.id, QuestionInteraction(action="reject"), "prompt-1"
    )
    assert (result.status, result.changed, result.finalized) == (
        "rejected",
        True,
        True,
    )

    run.status = "completed"
    retry_db = _resolution_db(request, session, run, message, inbox, outbox)
    retry = await resolve_question_interaction(
        retry_db,
        endpoint,
        request.id,
        QuestionInteraction(action="reject"),
        "prompt-1",
    )
    assert (retry.status, retry.changed, retry.finalized) == (
        "rejected",
        False,
        True,
    )
    claim.assert_called_once()
    resolve.assert_called_once_with(request.id, "rejected")
    retry_db.commit.assert_not_awaited()
