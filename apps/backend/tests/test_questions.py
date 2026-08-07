from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.agent.runtime import AgentRuntime
from mato_loop_backend.agent.tools import QuestionArguments, validate_question_answers
from mato_loop_backend.api.routes.questions import (
    QuestionAnswerRequest,
    answer_question,
    reject_question,
)
from mato_loop_backend.events import run_events
from mato_loop_backend.models import ChatSession, QuestionRequest, UserAccount


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
                },
            ]
        }
    )


def test_question_answers_match_types_and_options() -> None:
    arguments = _arguments()
    answers = {
        "format": ["Eigener Stil"],
        "topics": ["API", "UI", "Tests"],
        "note": [],
    }

    assert validate_question_answers(arguments, answers) == {
        "format": ["Eigener Stil"],
        "topics": ["API", "UI", "Tests"],
        "note": [],
    }
    with pytest.raises(ValueError, match="single-choice"):
        validate_question_answers(arguments, {**answers, "format": ["Kurz", "Ausführlich"]})


def test_question_contract_rejects_blank_text_and_oversized_answers() -> None:
    payload = _arguments().model_dump(mode="json")
    payload["questions"][0]["question"] = "   "

    with pytest.raises(ValueError, match="question text must not be blank"):
        QuestionArguments.model_validate(payload)
    with pytest.raises(ValueError, match="at most 13 items"):
        QuestionAnswerRequest(answers={"topics": [str(index) for index in range(14)]})


async def test_answer_is_persisted_before_agent_resumes(monkeypatch) -> None:
    arguments = _arguments()
    request = QuestionRequest(
        id=uuid4(),
        session_id=uuid4(),
        run_id=uuid4(),
        tool_call_id="call-1",
        questions=arguments.model_dump(mode="json")["questions"],
        status="pending",
    )
    user = UserAccount(id=uuid4(), username="test", password_hash="hash")
    session = ChatSession(id=request.session_id, user_id=user.id, title="Test")
    db = AsyncMock(spec=AsyncSession)
    db.get.side_effect = [request, session]
    claim = Mock(return_value=True)
    resolve = Mock(return_value=True)
    monkeypatch.setattr(AgentRuntime, "claim_question_resolution", claim)
    monkeypatch.setattr(AgentRuntime, "resolve_question", resolve)
    monkeypatch.setattr(run_events, "publish", AsyncMock())
    answers = {"format": ["Kurz"], "topics": ["API"], "note": []}

    result = await answer_question(
        request.id, QuestionAnswerRequest(answers=answers), user, db
    )

    assert request.status == "answered"
    assert request.answers == answers
    db.commit.assert_awaited_once()
    resolve.assert_called_once_with(request.id, answers)
    assert result == {"status": "answered"}


async def test_rejection_is_persisted_before_agent_resumes(monkeypatch) -> None:
    request = QuestionRequest(
        id=uuid4(),
        session_id=uuid4(),
        run_id=uuid4(),
        tool_call_id="call-1",
        questions=_arguments().model_dump(mode="json")["questions"],
        status="pending",
    )
    user = UserAccount(id=uuid4(), username="test", password_hash="hash")
    session = ChatSession(id=request.session_id, user_id=user.id, title="Test")
    db = AsyncMock(spec=AsyncSession)
    db.get.side_effect = [request, session]
    monkeypatch.setattr(
        AgentRuntime, "claim_question_resolution", Mock(return_value=True)
    )
    resolve = Mock(return_value=True)
    monkeypatch.setattr(AgentRuntime, "resolve_question", resolve)
    monkeypatch.setattr(run_events, "publish", AsyncMock())

    result = await reject_question(request.id, user, db)

    assert request.status == "rejected"
    db.commit.assert_awaited_once()
    resolve.assert_called_once_with(request.id, "rejected")
    assert result == {"status": "rejected"}


async def test_failed_answer_commit_releases_resolution(monkeypatch) -> None:
    request = QuestionRequest(
        id=uuid4(),
        session_id=uuid4(),
        run_id=uuid4(),
        tool_call_id="call-1",
        questions=_arguments().model_dump(mode="json")["questions"],
        status="pending",
    )
    user = UserAccount(id=uuid4(), username="test", password_hash="hash")
    session = ChatSession(id=request.session_id, user_id=user.id, title="Test")
    db = AsyncMock(spec=AsyncSession)
    db.get.side_effect = [request, session]
    db.commit.side_effect = RuntimeError("commit failed")
    db.rollback.side_effect = RuntimeError("rollback failed")
    monkeypatch.setattr(
        AgentRuntime, "claim_question_resolution", Mock(return_value=True)
    )
    release = Mock()
    monkeypatch.setattr(AgentRuntime, "release_question_resolution", release)

    with pytest.raises(RuntimeError, match="rollback failed"):
        await answer_question(
            request.id,
            QuestionAnswerRequest(
                answers={"format": ["Kurz"], "topics": ["API"], "note": []}
            ),
            user,
            db,
        )

    db.rollback.assert_awaited_once()
    release.assert_called_once_with(request.id)
