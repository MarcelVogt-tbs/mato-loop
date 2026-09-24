from __future__ import annotations

import base64
import binascii
import re
from typing import Annotated, Literal, cast
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.models import (
    AgentRun,
    ChatSession,
    Message,
    MessagingEndpoint,
    MessagingInbox,
    MessagingOutbox,
    QuestionRequest,
)
from mato_loop_backend.tools.arguments import (
    QuestionArguments,
    QuestionItem,
    validate_question_answers,
)

QuestionInteractionAction = Literal[
    "select", "toggle", "next", "back", "custom", "text", "reject", "submit"
]

_TOKEN_RE = re.compile(r"[A-Za-z0-9_-]{22}")
_CALLBACK_CODES: dict[QuestionInteractionAction, str] = {
    "select": "s",
    "toggle": "t",
    "next": "n",
    "back": "b",
    "custom": "c",
    "reject": "r",
    "submit": "u",
}
_CODE_ACTIONS = {value: key for key, value in _CALLBACK_CODES.items()}


class MessagingQuestionEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    kind: Literal["question"]
    version: Literal[1]
    question_id: UUID = Field(alias="questionId", strict=False)
    session_id: UUID = Field(alias="sessionId", strict=False)
    run_id: UUID = Field(alias="runId", strict=False)
    questions: Annotated[list[QuestionItem], Field(min_length=1, max_length=5)]
    current_index: int = Field(default=0, alias="currentIndex", ge=0, le=4)
    answers: dict[
        str,
        Annotated[list[Annotated[str, Field(max_length=20_000)]], Field(max_length=13)],
    ] = Field(default_factory=dict, max_length=5)
    awaiting_text: bool = Field(default=False, alias="awaitingText")

    @model_validator(mode="after")
    def state_is_valid(self) -> MessagingQuestionEnvelope:
        arguments = QuestionArguments.model_validate({"questions": self.questions})
        if self.current_index >= len(arguments.questions):
            raise ValueError("current question index is out of range")
        by_id = {question.id: question for question in arguments.questions}
        if not set(self.answers) <= set(by_id):
            raise ValueError("answer references an unknown question")
        for question_id, answer in self.answers.items():
            single = QuestionArguments(questions=[by_id[question_id]])
            validate_question_answers(single, {question_id: answer})
        current = arguments.questions[self.current_index]
        if self.awaiting_text and (
            current.type == "free_text" or not current.allow_custom
        ):
            raise ValueError("current question does not accept custom text")
        return self


class QuestionInteraction(BaseModel):
    model_config = ConfigDict(
        extra="forbid", strict=True, frozen=True, populate_by_name=True
    )

    action: QuestionInteractionAction
    option_index: int | None = Field(
        default=None, alias="optionIndex", strict=True, ge=0, le=11
    )
    text: Annotated[str, Field(min_length=1, max_length=20_000)] | None = None

    @model_validator(mode="after")
    def arguments_match_action(self) -> QuestionInteraction:
        if (self.action in {"select", "toggle"}) != (self.option_index is not None):
            raise ValueError("optionIndex is required only for option actions")
        if (self.action == "text") != (self.text is not None):
            raise ValueError("text is required only for text actions")
        return self


class QuestionInteractionResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    status: Literal["pending", "answered", "rejected"]
    envelope: MessagingQuestionEnvelope | None = None
    changed: bool
    finalized: bool

    @model_validator(mode="after")
    def envelope_matches_status(self) -> QuestionInteractionResult:
        if (self.status == "pending") != (self.envelope is not None):
            raise ValueError("only pending results contain an envelope")
        if self.finalized != (self.status != "pending"):
            raise ValueError("finalized does not match status")
        return self


class MessagingQuestionError(RuntimeError):
    code = "question.notFound"

    def __init__(self) -> None:
        super().__init__(self.code)


def parse_question_envelope(value: object) -> MessagingQuestionEnvelope | None:
    if not isinstance(value, dict) or value.get("kind") != "question":
        return None
    return MessagingQuestionEnvelope.model_validate(value)


def encode_question_token(question_id: UUID) -> str:
    return base64.urlsafe_b64encode(question_id.bytes).rstrip(b"=").decode("ascii")


def decode_question_token(token: str) -> UUID:
    if _TOKEN_RE.fullmatch(token) is None:
        raise ValueError("invalid question token")
    try:
        raw = base64.b64decode(token + "==", altchars=b"-_", validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("invalid question token") from exc
    if len(raw) != 16 or encode_question_token(UUID(bytes=raw)) != token:
        raise ValueError("invalid question token")
    return UUID(bytes=raw)


def encode_question_callback(
    question_id: UUID, interaction: QuestionInteraction
) -> str:
    if interaction.action == "text":
        raise ValueError("text interactions cannot be callbacks")
    code = _CALLBACK_CODES[interaction.action]
    suffix = (
        f":{interaction.option_index}"
        if interaction.option_index is not None
        else ""
    )
    value = f"q1:{encode_question_token(question_id)}:{code}{suffix}"
    if len(value.encode("utf-8")) > 64:
        raise ValueError("question callback is too long")
    return value


def decode_question_callback(value: str) -> tuple[UUID, QuestionInteraction]:
    parts = value.split(":")
    if len(parts) not in {3, 4} or parts[0] != "q1":
        raise ValueError("invalid question callback")
    action = _CODE_ACTIONS.get(parts[2])
    if action is None or (action in {"select", "toggle"}) != (len(parts) == 4):
        raise ValueError("invalid question callback")
    try:
        option_index = int(parts[3]) if len(parts) == 4 else None
        if len(parts) == 4 and parts[3] != str(option_index):
            raise ValueError("non-canonical option index")
        interaction = QuestionInteraction(action=action, option_index=option_index)
    except (TypeError, ValueError, ValidationError) as exc:
        raise ValueError("invalid question callback") from exc
    return decode_question_token(parts[1]), interaction


def _valid_endpoint(
    endpoint: MessagingEndpoint, session: ChatSession
) -> bool:
    return bool(
        endpoint.id
        and endpoint.user_id == session.user_id
        and endpoint.enabled
        and endpoint.deleted_at is None
        and endpoint.paired_user_id
        and endpoint.paired_chat_id
    )


def _valid_provenance(
    request: QuestionRequest,
    session: ChatSession,
    run: AgentRun,
    message: Message,
    endpoint: MessagingEndpoint,
    *,
    require_waiting: bool,
) -> bool:
    return bool(
        request.status in {"pending", "answered", "rejected"}
        and request.session_id == session.id
        and request.run_id == run.id
        and request.tool_call_id
        and run.session_id == session.id
        and run.input_message_id == message.id
        and (not require_waiting or run.status == "waiting_input")
        and message.session_id == session.id
        and message.role == "user"
        and message.source_type == "endpoint"
        and message.source_endpoint_id == endpoint.id
        and message.source_provider == endpoint.provider
        and _valid_endpoint(endpoint, session)
    )


async def enqueue_question_prompt(
    db: AsyncSession, request: QuestionRequest
) -> MessagingOutbox:
    run = await db.get(AgentRun, request.run_id)
    message = (
        await db.get(Message, run.input_message_id)
        if run is not None and run.input_message_id is not None
        else None
    )
    session = await db.get(ChatSession, request.session_id)
    endpoint = (
        await db.get(MessagingEndpoint, message.source_endpoint_id, with_for_update=True)
        if message is not None and message.source_endpoint_id is not None
        else None
    )
    if (
        run is None
        or message is None
        or session is None
        or endpoint is None
        or request.status != "pending"
        or not _valid_provenance(
            request, session, run, message, endpoint, require_waiting=True
        )
    ):
        raise MessagingQuestionError

    try:
        questions = QuestionArguments.model_validate({"questions": request.questions})
        envelope = MessagingQuestionEnvelope(
            kind="question",
            version=1,
            questionId=request.id,
            sessionId=request.session_id,
            runId=request.run_id,
            questions=questions.questions,
        )
    except ValidationError as exc:
        raise MessagingQuestionError from exc
    outbox = MessagingOutbox(
        endpoint_id=endpoint.id,
        inbox_id=None,
        destination=cast(str, endpoint.paired_chat_id),
        deduplication_key=f"question:{request.id}",
        payload=envelope.model_dump(mode="json", by_alias=True),
    )
    db.add(outbox)
    return outbox


async def resolve_question_interaction(
    db: AsyncSession,
    endpoint: MessagingEndpoint,
    question_id: UUID,
    interaction: QuestionInteraction,
    source_external_message_id: str,
) -> QuestionInteractionResult:
    request = await db.get(QuestionRequest, question_id, with_for_update=True)
    if request is None or request.id != question_id:
        raise MessagingQuestionError
    session = await db.get(ChatSession, request.session_id)
    run = await db.get(AgentRun, request.run_id)
    message = (
        await db.get(Message, run.input_message_id)
        if run is not None and run.input_message_id is not None
        else None
    )
    if (
        session is None
        or run is None
        or message is None
        or request.status not in {"pending", "answered", "rejected"}
        or not _valid_provenance(
            request,
            session,
            run,
            message,
            endpoint,
            require_waiting=request.status == "pending",
        )
    ):
        raise MessagingQuestionError

    inbox = await db.scalar(
        select(MessagingInbox).where(
            MessagingInbox.endpoint_id == endpoint.id,
            MessagingInbox.agent_run_id == run.id,
            MessagingInbox.input_message_id == message.id,
            MessagingInbox.session_id == session.id,
        )
    )
    outbox = await db.scalar(
        select(MessagingOutbox)
        .where(
            MessagingOutbox.endpoint_id == endpoint.id,
            MessagingOutbox.deduplication_key == f"question:{request.id}",
        )
        .with_for_update()
    )
    if (
        inbox is None
        or inbox.endpoint_id != endpoint.id
        or inbox.agent_run_id != run.id
        or inbox.input_message_id != message.id
        or inbox.session_id != session.id
        or inbox.status != "completed"
        or outbox is None
        or outbox.endpoint_id != endpoint.id
        or outbox.inbox_id is not None
        or outbox.destination != endpoint.paired_chat_id
        or outbox.deduplication_key != f"question:{request.id}"
        or outbox.status != "delivered"
        or not source_external_message_id
        or source_external_message_id not in outbox.external_message_ids
    ):
        raise MessagingQuestionError

    try:
        envelope = parse_question_envelope(outbox.payload)
        request_questions = QuestionArguments.model_validate(
            {"questions": request.questions}
        )
    except (ValidationError, ValueError) as exc:
        raise MessagingQuestionError from exc
    if (
        envelope is None
        or envelope.question_id != request.id
        or envelope.session_id != session.id
        or envelope.run_id != run.id
        or envelope.questions != request_questions.questions
    ):
        raise MessagingQuestionError

    # Imported lazily because the runtime imports messaging prompt enqueue helpers.
    from mato_loop_backend.agent.question_decisions import (
        QuestionDecisionError,
        answer_question,
        reject_question,
    )

    was_pending = request.status == "pending"
    if not was_pending:
        if request.status == "answered" and interaction.action == "submit":
            answers = {
                question.id: envelope.answers.get(question.id, [])
                for question in envelope.questions
            }
            await answer_question(
                db, request, answers, idempotent_same_result=True
            )
            return QuestionInteractionResult(
                status="answered", changed=False, finalized=True
            )
        if request.status == "rejected" and interaction.action == "reject":
            await reject_question(db, request, idempotent_same_result=True)
            return QuestionInteractionResult(
                status="rejected", changed=False, finalized=True
            )
        raise QuestionDecisionError(409, "question.alreadyAnswered")

    if interaction.action == "submit":
        answers = {
            question.id: envelope.answers.get(question.id, [])
            for question in envelope.questions
        }
        await answer_question(db, request, answers, idempotent_same_result=True)
        return QuestionInteractionResult(
            status="answered", changed=True, finalized=True
        )
    if interaction.action == "reject":
        await reject_question(db, request, idempotent_same_result=True)
        return QuestionInteractionResult(
            status="rejected", changed=True, finalized=True
        )

    current = envelope.questions[envelope.current_index]
    before = envelope.model_dump(mode="json", by_alias=True)
    answers = {key: list(value) for key, value in envelope.answers.items()}
    current_index = envelope.current_index
    awaiting_text = envelope.awaiting_text
    if interaction.action == "select":
        if current.type != "single_choice" or interaction.option_index is None:
            raise MessagingQuestionError
        try:
            answers[current.id] = [current.options[interaction.option_index].label]
        except IndexError as exc:
            raise MessagingQuestionError from exc
        awaiting_text = False
    elif interaction.action == "toggle":
        if current.type != "multiple_choice" or interaction.option_index is None:
            raise MessagingQuestionError
        try:
            label = current.options[interaction.option_index].label
        except IndexError as exc:
            raise MessagingQuestionError from exc
        selected = answers.setdefault(current.id, [])
        selected.remove(label) if label in selected else selected.append(label)
        awaiting_text = False
    elif interaction.action == "custom":
        if current.type == "free_text" or not current.allow_custom:
            raise MessagingQuestionError
        awaiting_text = True
    elif interaction.action == "text":
        if current.type != "free_text" and not awaiting_text:
            raise MessagingQuestionError
        if interaction.text is None:
            raise MessagingQuestionError
        if current.type == "multiple_choice":
            option_labels = {option.label for option in current.options}
            selected = [
                value for value in answers.get(current.id, []) if value in option_labels
            ]
            candidate = [*selected, interaction.text]
        else:
            candidate = [interaction.text]
        single = QuestionArguments(questions=[current])
        answers[current.id] = validate_question_answers(
            single, {current.id: candidate}
        )[current.id]
        current_index = min(current_index + 1, len(envelope.questions) - 1)
        awaiting_text = False
    elif interaction.action == "next":
        current_index = min(current_index + 1, len(envelope.questions) - 1)
        awaiting_text = False
    elif interaction.action == "back":
        current_index = max(current_index - 1, 0)
        awaiting_text = False
    else:
        raise MessagingQuestionError

    envelope = MessagingQuestionEnvelope.model_validate(
        {
            **before,
            "answers": answers,
            "currentIndex": current_index,
            "awaitingText": awaiting_text,
        }
    )
    changed = before != envelope.model_dump(mode="json", by_alias=True)
    outbox.payload = envelope.model_dump(mode="json", by_alias=True)
    await db.commit()
    return QuestionInteractionResult(
        status="pending",
        envelope=envelope,
        changed=changed,
        finalized=False,
    )
