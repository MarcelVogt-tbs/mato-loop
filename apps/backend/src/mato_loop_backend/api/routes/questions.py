from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.agent.runtime import AgentRuntime, question_payload
from mato_loop_backend.agent.tools import QuestionArguments, validate_question_answers
from mato_loop_backend.api.errors import ApiError
from mato_loop_backend.db.session import get_db_session
from mato_loop_backend.events import run_events
from mato_loop_backend.models import ChatSession, QuestionRequest, UserAccount
from mato_loop_backend.security import require_user

router = APIRouter(prefix="/questions", dependencies=[Depends(require_user)])


class QuestionAnswerRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    answers: dict[
        str,
        Annotated[list[Annotated[str, Field(max_length=20_000)]], Field(max_length=13)],
    ] = Field(max_length=5)


@router.get("/pending")
async def pending_questions(
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> list[dict]:
    items = list(
        (
            await db.scalars(
                select(QuestionRequest)
                .join(ChatSession, ChatSession.id == QuestionRequest.session_id)
                .where(QuestionRequest.status == "pending")
                .where(ChatSession.user_id == user.id)
                .order_by(QuestionRequest.created_at)
            )
        ).all()
    )
    return [question_payload(item) for item in items]


@router.post("/{question_id}/answers")
async def answer_question(
    question_id: UUID,
    payload: QuestionAnswerRequest,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict[str, str]:
    request = await db.get(QuestionRequest, question_id, with_for_update=True)
    if request is None:
        raise ApiError(404, "question.notFound")
    session = await db.get(ChatSession, request.session_id)
    if session is None or session.user_id != user.id:
        raise ApiError(404, "question.notFound")
    if request.status != "pending":
        raise ApiError(409, "question.alreadyAnswered")
    try:
        questions = QuestionArguments.model_validate({"questions": request.questions})
        answers = validate_question_answers(questions, payload.answers)
    except ValueError as exc:
        raise ApiError(422, "question.invalidAnswer", field="answers") from exc
    if not AgentRuntime.claim_question_resolution(request.id):
        request.status = "expired"
        await db.commit()
        raise ApiError(409, "question.runInactive")
    request.status = "answered"
    request.answers = answers
    request.answered_at = datetime.now(UTC)
    try:
        await db.commit()
    except (Exception, asyncio.CancelledError):
        try:
            await db.rollback()
        finally:
            AgentRuntime.release_question_resolution(request.id)
        raise
    if not AgentRuntime.resolve_question(request.id, answers):
        request.status = "expired"
        request.answers = None
        request.answered_at = None
        await db.commit()
        raise ApiError(409, "question.runInactive")
    await run_events.publish(
        request.run_id,
        {"type": "question.answered", "questionId": str(request.id)},
    )
    return {"status": request.status}


@router.post("/{question_id}/reject")
async def reject_question(
    question_id: UUID,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict[str, str]:
    request = await db.get(QuestionRequest, question_id, with_for_update=True)
    if request is None:
        raise ApiError(404, "question.notFound")
    session = await db.get(ChatSession, request.session_id)
    if session is None or session.user_id != user.id:
        raise ApiError(404, "question.notFound")
    if request.status != "pending":
        raise ApiError(409, "question.alreadyAnswered")
    if not AgentRuntime.claim_question_resolution(request.id):
        request.status = "expired"
        await db.commit()
        raise ApiError(409, "question.runInactive")
    request.status = "rejected"
    request.answered_at = datetime.now(UTC)
    try:
        await db.commit()
    except (Exception, asyncio.CancelledError):
        try:
            await db.rollback()
        finally:
            AgentRuntime.release_question_resolution(request.id)
        raise
    if not AgentRuntime.resolve_question(request.id, "rejected"):
        request.status = "expired"
        request.answered_at = None
        await db.commit()
        raise ApiError(409, "question.runInactive")
    await run_events.publish(
        request.run_id,
        {"type": "question.rejected", "questionId": str(request.id)},
    )
    return {"status": request.status}
