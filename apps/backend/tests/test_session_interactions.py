import asyncio
import json
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from mato_loop_backend.agent import session_execution
from mato_loop_backend.agent.context import _model_order
from mato_loop_backend.agent.interaction_context import (
    existing_action,
    interaction_context,
    run_records,
)
from mato_loop_backend.agent.projection import _projection_messages
from mato_loop_backend.models import (
    AgentRun,
    ApprovalRequest,
    ChatSession,
    Message,
    QuestionRequest,
)
from mato_loop_backend.settings import TrustLevel


async def test_review_runs_while_waiting_but_resolution_cannot_execute_concurrently():
    session_id, first, second = (uuid4() for _ in range(3))
    waiting, resolved, resumed = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def original():
        await session_execution.acquire(first, session_id)
        try:
            async with session_execution.interaction_wait(first):
                waiting.set()
                await resolved.wait()
            resumed.set()
        finally:
            session_execution.release(first)

    task = asyncio.create_task(original())
    try:
        await asyncio.wait_for(waiting.wait(), 1)
        assert first in session_execution.parked_runs()
        await asyncio.wait_for(session_execution.acquire(second, session_id), 1)
        resolved.set()
        await asyncio.sleep(0)
        assert not resumed.is_set()
        session_execution.release(second)
        await asyncio.wait_for(task, 1)
        assert resumed.is_set() and first not in session_execution.parked_runs()
    finally:
        session_execution.release(second)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("trust", ["private", "public"])
async def test_pending_requests_are_context_not_interrupted_tool_execution(trust):
    session = ChatSession(id=uuid4(), user_id=uuid4())
    run = AgentRun(id=uuid4(), session_id=session.id, trust_level=trust)
    pending = ApprovalRequest(id=uuid4(), run_id=uuid4(), title="Move event", summary="Tuesday to Wednesday",
                              status="pending", payload={"toolCallId": "move"})
    question = QuestionRequest(id=uuid4(), run_id=pending.run_id, tool_call_id="secret", status="pending",
                               questions=[{"type": "password", "question": "Protected prompt"}])
    call = Message(id=uuid4(), role="assistant", content="", tool_calls=[
        {"id": "done", "name": "calendar_query_events", "arguments": {}},
        {"id": "move", "name": "calendar_update_event", "arguments": {}},
        {"id": "not_started", "name": "calendar_update_event", "arguments": {}},
    ], provider_data={"_matoloop": {"runId": str(pending.run_id)}})
    result = Message(id=uuid4(), role="tool", tool_call_id="done", content='{"count":2}', trust_level="public",
                     provider_data={"_matoloop": {"runId": str(pending.run_id)}})
    db = AsyncMock()
    db.scalars.side_effect = [MagicMock(all=lambda: [pending]), MagicMock(all=lambda: [question]),
                             MagicMock(all=lambda: [pending.run_id])]
    view, context = await interaction_context(db, session, run, [call, result])
    assert "Move event" in context and "Protected prompt" not in context
    assert len(view[0].tool_calls) == 1 and len(call.tool_calls) == 3
    projected = _projection_messages("System", None, view, TrustLevel(trust))
    assert "count" in str(projected) and "tool.executionInterrupted" not in str(projected)
    if trust == "public":
        assert "agent_runs.trust_level" in str(db.scalars.await_args_list[0].args[0])


def test_resumed_review_retains_original_turn_and_real_crossed_tool_result():
    first, second = uuid4(), uuid4()
    user = Message(id=uuid4(), role="user", content="Original", provider_data={"_matoloop": {"requestId": str(first)}})
    call = Message(id=uuid4(), role="assistant", content="", tool_calls=[{"id": "move", "name": "calendar_update_event", "arguments": {}}], provider_data={"_matoloop": {"runId": str(first)}})
    other = Message(id=uuid4(), role="user", content="Next review", provider_data={"_matoloop": {"requestId": str(second)}})
    response = Message(id=uuid4(), role="assistant", content="Review done", provider_data={"_matoloop": {"runId": str(second)}})
    result = Message(id=uuid4(), role="tool", tool_call_id="move", content=json.dumps({"success": True}), trust_level="public", provider_data={"_matoloop": {"runId": str(first)}})
    records = [user, call, other, response, result]
    view = run_records(records, AgentRun(id=first, input_message_id=user.id))
    assert [item.content for item in view if item.role == "user"] == ["Next review", "Original"]
    projected = _projection_messages("System", None, _model_order(view), TrustLevel.PUBLIC)
    assert any(item.get("tool_call_id") == "move" and "success" in item["content"] for item in projected)
    assert "tool.executionInterrupted" not in str(projected)
    assert records[0] is user and records[-1] is result


def test_ongoing_voice_does_not_change_the_executing_tasks_serialized_turn():
    from mato_loop_backend.providers.openai_compatible import _responses_input

    run_id = uuid4()
    user = Message(id=uuid4(), role="user", content="Search the web.", trust_level="public")
    call = Message(id=uuid4(), role="assistant", content="", trust_level="public",
                   tool_calls=[{"id": "search", "name": "webfetch", "arguments": {"url": "https://example.com"}}],
                   provider_data={"_matoloop": {"runId": str(run_id)}})
    result = Message(id=uuid4(), role="tool", tool_call_id="search", content="Search result", trust_level="public",
                     provider_data={"_matoloop": {"runId": str(run_id)}})
    voice = Message(id=uuid4(), role="user", content="A different conversational topic.",
                    provider_data={"_matoloop": {"voice": True}}, trust_level="public")
    run = AgentRun(id=run_id, input_message_id=user.id)

    def serialized(records):
        messages = _projection_messages("System", None, _model_order(run_records(records, run)), TrustLevel.PUBLIC)
        return json.dumps(_responses_input(messages)[1], sort_keys=True)

    before = serialized([user, call, result])
    persisted = [Message(**{column.key: getattr(item, column.key) for column in item.__table__.columns})
                 for item in (user, call, voice, result)]
    assert serialized(persisted) == serialized([user, call, voice, result]) == before


@pytest.mark.parametrize("same_run", [False, True])
def test_model_order_pairs_reused_call_ids_by_run_and_batch(same_run):
    first, second = str(uuid4()), str(uuid4())
    if same_run:
        second = first

    def message(role, run_id, content):
        return Message(id=uuid4(), role=role, content=content, tool_call_id="reused" if role == "tool" else None,
                       tool_calls=[{"id": "reused", "name": "read", "arguments": {}}] if role == "assistant" else None,
                       provider_data={"_matoloop": {"runId": run_id}})

    call = message("assistant", first, "first call")
    result = message("tool", first, "first result")
    next_call = message("assistant", second, "second call")
    next_result = message("tool", second, "second result")
    user = Message(id=uuid4(), role="user", content="Review")
    records = [call, user, result, next_call, next_result] if same_run else [call, next_call, user, next_result, result]

    assert [item.content for item in _model_order(records)] == (
        ["first call", "first result", "Review", "second call", "second result"] if same_run else
        ["first call", "first result", "second call", "second result", "Review"]
    )


@pytest.mark.parametrize("kind", ["approval", "question", None])
async def test_pending_call_ids_do_not_hide_completed_other_batches(kind):
    session = ChatSession(id=uuid4(), user_id=uuid4())
    run = AgentRun(id=uuid4(), trust_level="private")
    waiting = uuid4()
    records = []
    for owner in (waiting, run.id, waiting):
        records.append(Message(id=uuid4(), role="assistant", tool_calls=[{"id": "reused"}],
                               provider_data={"_matoloop": {"runId": str(owner)}}))
        if len(records) < 4:
            records.append(Message(id=uuid4(), role="tool", tool_call_id="reused",
                                   provider_data={"_matoloop": {"runId": str(owner)}}))
    request = (ApprovalRequest(id=uuid4(), run_id=waiting, payload={"toolCallId": "reused"},
                               title="Pending", summary="Pending", status="pending") if kind == "approval" else
               QuestionRequest(id=uuid4(), run_id=waiting, tool_call_id="reused", questions=[], status="pending"))
    db = AsyncMock()
    db.scalars.side_effect = [MagicMock(all=lambda: [request] if kind == "approval" else []),
                             MagicMock(all=lambda: [request] if kind == "question" else []),
                             MagicMock(all=lambda: [waiting])]

    view, _ = await interaction_context(db, session, run, records)

    assert [len(item.tool_calls) for item in view if item.role == "assistant"] == [1, 1, 0]


async def test_cancel_parked_review_does_not_wait_for_executing_review():
    session_id, first, second = (uuid4() for _ in range(3))
    waiting = asyncio.Event()

    async def original():
        await session_execution.acquire(first, session_id)
        try:
            async with session_execution.interaction_wait(first):
                waiting.set()
                await asyncio.Event().wait()
        finally:
            session_execution.release(first)

    task = asyncio.create_task(original())
    try:
        await asyncio.wait_for(waiting.wait(), 1)
        await session_execution.acquire(second, session_id)
        task.cancel()
        await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), 1)
        assert first not in session_execution.parked_runs()
    finally:
        session_execution.release(second)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("status,changed", [("pending", False), ("approved", False), ("pending", True)])
async def test_existing_active_action_is_not_repeated_after_grant_resolution(status, changed):
    from mato_loop_backend.agent.run_support import _run_action_scope
    from mato_loop_backend.permissions.policy import PermissionRequest
    from mato_loop_backend.providers.base import ToolCall

    call = ToolCall("new", "calendar_update_event", {"event_id": "event", "start": "14:00"})
    permission = PermissionRequest(capability="calendar.write", resource_type="event", resource_scope="event",
                                   action_kind="update", risk_level="medium", title="Update", summary="Update")
    request = ApprovalRequest(id=uuid4(), status=status, payload={
        "tool": call.name, "arguments": call.arguments,
        "runActionScope": "changed-binding" if changed else _run_action_scope(permission, call),
    })
    db = AsyncMock()
    db.scalars.return_value = MagicMock(all=lambda: [request])
    assert await existing_action(db, ChatSession(id=uuid4(), user_id=uuid4()), AgentRun(id=uuid4()),
                                 call, permission) is (None if changed else request)


@pytest.mark.parametrize("kind", ["approval", "question"])
@pytest.mark.parametrize("automation", [False, True])
async def test_live_request_keeps_its_waiter_across_another_run(monkeypatch, kind, automation):
    from mato_loop_backend.agent.runtime import AgentRuntime
    from mato_loop_backend.events import run_events
    from mato_loop_backend.notifications import NotificationService
    from mato_loop_backend.permissions.coordinator import approval_coordinator, question_coordinator
    from mato_loop_backend.permissions.policy import PermissionRequest
    from mato_loop_backend.providers.base import ToolCall
    from mato_loop_backend.tools.arguments import QuestionArguments

    session = ChatSession(id=uuid4(), user_id=uuid4())
    run = AgentRun(id=uuid4(), session_id=session.id, automation_origin=automation, trust_level="private")
    db = AsyncMock()
    db.scalars.return_value = MagicMock(all=lambda: [])
    db.add.side_effect = lambda request: setattr(request, "id", uuid4())
    monkeypatch.setattr(run_events, "publish", AsyncMock())
    monkeypatch.setattr(NotificationService, f"create_for_{kind}", AsyncMock(return_value=None))
    coordinator = approval_coordinator if kind == "approval" else question_coordinator
    await session_execution.acquire(run.id, session.id)
    call = ToolCall("original-call", kind, {})
    if kind == "approval":
        operation = AgentRuntime._request_approval(db, run, session, call, PermissionRequest(
            capability="network.fetch", resource_type="url", resource_scope="https://example.org",
            action_kind="fetch", risk_level="low", title="Read", summary="Read"))
    else:
        operation = AgentRuntime._request_question(db, run, session, call, QuestionArguments(questions=[{
            "id": "choice", "type": "free_text", "header": "Choice", "question": "When?",
        }]))
    task = asyncio.create_task(operation)
    other = uuid4()
    try:
        for _ in range(30):
            if run.id in session_execution.parked_runs():
                break
            await asyncio.sleep(0)
        request = db.add.call_args.args[0]
        await asyncio.wait_for(session_execution.acquire(other, session.id), 1)
        assert coordinator.is_waiting(request.id)
        assert coordinator.resolve(request.id, "allow_once" if kind == "approval" else {"choice": ["tomorrow"]})
        await asyncio.sleep(0)
        assert not task.done()
        session_execution.release(other)
        await asyncio.wait_for(task, 1)
        assert request.status != "expired" and db.add.call_count == 1
    finally:
        session_execution.release(other)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        session_execution.release(run.id)
