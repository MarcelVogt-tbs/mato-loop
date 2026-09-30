from unittest.mock import AsyncMock, Mock
from uuid import uuid4

from mato_loop_backend.api.routes.questions import pending_questions
from mato_loop_backend.events import RunEventBroker
from mato_loop_backend.models import QuestionRequest, UserAccount


async def test_child_requests_reach_only_the_parent_without_forwarding_transcripts():
    broker = RunEventBroker()
    parent, child, sibling, child_session = (uuid4() for _ in range(4))
    summary = {"sessionId": str(child_session), "title": "Tests pruefen", "toolCallId": "task-1"}
    broker.register_subagent(child, parent, summary)
    await broker.publish(child, {"type": "message.delta", "delta": "child-only content"})
    await broker.publish(child, {"type": "run.started"})
    await broker.publish(child, {
        "type": "approval.created", "approval": {"id": "approval-1", "payload": {"tool": "read"}},
    })
    await broker.publish(child, {"type": "approval.resolved", "approvalId": "approval-1"})
    await broker.publish(child, {"type": "question.created", "question": {"id": "question-1"}})
    await broker.publish(child, {"type": "question.expired", "questionId": "question-1"})

    forwarded = [item for item in broker._history[parent] if item["type"] != "subagent.updated"]
    assert [item["type"] for item in forwarded] == [
        "approval.created", "approval.resolved", "question.created", "question.expired",
    ]
    assert all(item["subagentSessionId"] == str(child_session) for item in forwarded)
    assert all(item["subagentTitle"] == summary["title"] for item in forwarded)
    assert forwarded[0]["approval"]["payload"] == {
        "tool": "read", "subagentSessionId": str(child_session), "subagentTitle": summary["title"],
    }
    assert forwarded[2]["question"]["payload"]["subagentSessionId"] == str(child_session)
    assert sibling not in broker._history
    assert broker._snapshot(child)["content"] == "child-only content"
    assert broker._snapshot(parent)["content"] == ""
    assert "subagentSessionId" not in broker._history[child][-1]
    count = len(broker._history[parent])
    broker.unregister_subagent(child)
    await broker.publish(child, {"type": "run.timing"})
    assert len(broker._history[parent]) == count


async def test_pending_questions_restore_child_provenance_after_reload():
    user = UserAccount(id=uuid4())
    parent_id, child_id, child_run, parent_run = (uuid4() for _ in range(4))
    child_question = QuestionRequest(
        id=uuid4(), session_id=parent_id, run_id=child_run,
        tool_call_id="question", questions=[], status="pending",
    )
    parent_question = QuestionRequest(
        id=uuid4(), session_id=parent_id, run_id=parent_run,
        tool_call_id="question", questions=[], status="pending",
    )
    db = AsyncMock()
    db.scalars.return_value = Mock(all=lambda: [child_question, parent_question])
    db.execute.return_value = Mock(all=lambda: [(child_run, child_id, "Tests pruefen", parent_id)])
    result = await pending_questions(user, db)
    assert result[0]["sessionId"] == str(parent_id)
    assert result[0]["runId"] == str(child_run)
    assert result[0]["payload"] == {
        "subagentSessionId": str(child_id), "subagentTitle": "Tests pruefen",
    }
    assert "payload" not in result[1]
