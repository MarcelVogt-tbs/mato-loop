from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.agent.runtime import AgentRuntime
from mato_loop_backend.agent.tools import QuestionArguments, validate_question_answers
from mato_loop_backend.events import run_events
from mato_loop_backend.models import QuestionRequest


class QuestionDecisionError(RuntimeError):
    def __init__(
        self, status_code: int, code: str, *, field: str | None = None
    ) -> None:
        super().__init__(code)
        self.status_code = status_code
        self.code = code
        self.field = field


async def answer_question(
    db: AsyncSession,
    request: QuestionRequest,
    answers: dict[str, list[str]],
    *,
    idempotent_same_result: bool = False,
) -> dict[str, str]:
    if request.status != "pending":
        if not (idempotent_same_result and request.status == "answered"):
            raise QuestionDecisionError(409, "question.alreadyAnswered")
        try:
            questions = QuestionArguments.model_validate({"questions": request.questions})
            validated_answers = validate_question_answers(questions, answers)
        except ValueError:
            raise QuestionDecisionError(409, "question.alreadyAnswered") from None
        if request.answers == validated_answers:
            return {"status": "answered"}
        raise QuestionDecisionError(409, "question.alreadyAnswered")

    try:
        questions = QuestionArguments.model_validate({"questions": request.questions})
        validated_answers = validate_question_answers(questions, answers)
    except ValueError as exc:
        raise QuestionDecisionError(
            422, "question.invalidAnswer", field="answers"
        ) from exc

    if not AgentRuntime.claim_question_resolution(request.id):
        request.status = "expired"
        await db.commit()
        raise QuestionDecisionError(409, "question.runInactive")

    request_id = request.id
    run_id = request.run_id
    request.status = "answered"
    request.answers = validated_answers
    request.answered_at = datetime.now(UTC)
    try:
        await db.commit()
    except BaseException:
        try:
            await db.rollback()
        finally:
            AgentRuntime.release_question_resolution(request_id)
        raise
    if not AgentRuntime.resolve_question(request_id, validated_answers):
        request.status = "expired"
        request.answers = None
        request.answered_at = None
        await db.commit()
        raise QuestionDecisionError(409, "question.runInactive")
    await run_events.publish(
        run_id,
        {"type": "question.answered", "questionId": str(request_id)},
    )
    return {"status": "answered"}


async def reject_question(
    db: AsyncSession,
    request: QuestionRequest,
    *,
    idempotent_same_result: bool = False,
) -> dict[str, str]:
    if request.status != "pending":
        if idempotent_same_result and request.status == "rejected":
            return {"status": "rejected"}
        raise QuestionDecisionError(409, "question.alreadyAnswered")
    if not AgentRuntime.claim_question_resolution(request.id):
        request.status = "expired"
        await db.commit()
        raise QuestionDecisionError(409, "question.runInactive")

    request_id = request.id
    run_id = request.run_id
    request.status = "rejected"
    request.answered_at = datetime.now(UTC)
    try:
        await db.commit()
    except BaseException:
        try:
            await db.rollback()
        finally:
            AgentRuntime.release_question_resolution(request_id)
        raise
    if not AgentRuntime.resolve_question(request_id, "rejected"):
        request.status = "expired"
        request.answered_at = None
        await db.commit()
        raise QuestionDecisionError(409, "question.runInactive")
    await run_events.publish(
        run_id,
        {"type": "question.rejected", "questionId": str(request_id)},
    )
    return {"status": "rejected"}
