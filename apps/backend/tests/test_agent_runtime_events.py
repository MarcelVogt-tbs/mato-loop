import asyncio
import json
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.agent import runtime as runtime_module
from mato_loop_backend.agent.context import _model_tool_calls
from mato_loop_backend.agent.run_timing import run_timing_payload, transition_run_status
from mato_loop_backend.agent.runtime import (
    RUN_PHASES,
    AgentRunError,
    AgentRuntime,
    _public_reasoning_payload,
    _public_run_error,
    _publish_run_phase,
    _safe_provider_data,
    _safe_tool_arguments,
)
from mato_loop_backend.attachments import AttachmentInput
from mato_loop_backend.events import RunEventBroker, run_events
from mato_loop_backend.models import AgentRun, ChatSession, Message, TitleGenerationStatus
from mato_loop_backend.providers.base import (
    ProviderError,
    ProviderResult,
    PublicReasoning,
    ToolCall,
)
from mato_loop_backend.settings import (
    AgentRuntimeSettings,
    AiSettings,
    SettingsStore,
    TrustLevel,
)
from mato_loop_backend.settings.models import LlmProviderConfig, ModelConfig


def test_run_timing_sums_active_segments_and_excludes_user_waits() -> None:
    started_at = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
    run = AgentRun(id=uuid4(), session_id=uuid4(), status="queued")

    transition_run_status(run, "running", now=started_at)
    transition_run_status(run, "waiting_approval", now=started_at + timedelta(seconds=2))
    transition_run_status(run, "running", now=started_at + timedelta(hours=2))
    assert run_timing_payload(
        run, now=started_at + timedelta(hours=2, seconds=1)
    ) == {
        "activeDurationMs": 3_000,
        "activeDurationRunning": True,
        "activeTimingRevision": 3,
    }
    transition_run_status(run, "waiting_input", now=started_at + timedelta(hours=2, seconds=3))
    transition_run_status(run, "running", now=started_at + timedelta(hours=3))
    transition_run_status(run, "completed", now=started_at + timedelta(hours=3, seconds=4))

    assert run.active_duration_ms == 9_000
    assert run.active_started_at is None
    assert run.active_timing_revision == 6


async def test_run_duration_is_merged_into_result_message_metadata() -> None:
    message = Message(
        id=uuid4(),
        session_id=uuid4(),
        role="assistant",
        provider_data={
            "provider": "private",
            "_matoloop": {"performance": {"providerDurationMs": 400}},
        },
    )
    run = AgentRun(
        id=uuid4(),
        session_id=message.session_id,
        result_message_id=message.id,
        active_duration_ms=9_000,
    )
    db = AsyncMock(spec=AsyncSession)
    db.get.return_value = message

    await AgentRuntime._persist_run_duration(db, run)

    assert message.provider_data == {
        "provider": "private",
        "_matoloop": {
            "performance": {"providerDurationMs": 400, "activeDurationMs": 9_000}
        },
    }


async def test_run_phase_event_has_stable_language_neutral_payload(monkeypatch) -> None:
    published = []

    async def publish(run_id, event) -> None:
        published.append((run_id, event))

    monkeypatch.setattr(run_events, "publish", publish)
    run_id = uuid4()

    await _publish_run_phase(
        run_id,
        "processing_tool",
        tool="read_file",
        toolCallId="call-1",
        index=0,
        total=2,
    )

    started_at = published[0][1].pop("startedAt")
    assert isinstance(started_at, str)
    assert started_at.endswith("+00:00")
    assert published == [
        (
            run_id,
                {
                    "type": "run.phase",
                    "phase": "processing_tool",
                    "activityLabel": "Datei wird gelesen",
                    "tool": "read_file",
                "toolCallId": "call-1",
                "index": 0,
                "total": 2,
            },
        )
    ]
    assert RUN_PHASES == {
        "preparing_context",
        "compacting_context",
        "waiting_model",
        "processing_tool",
        "finalizing",
    }


async def test_unknown_run_phase_is_rejected() -> None:
    with pytest.raises(ValueError, match="Unknown run phase"):
        await _publish_run_phase(uuid4(), "thinking_text")


async def test_runtime_rejects_inactive_user() -> None:
    db = AsyncMock(spec=AsyncSession)
    db.scalar.return_value = False

    with pytest.raises(AgentRunError, match="auth.accountDisabled"):
        await AgentRuntime._ensure_user_active(db, uuid4())


@pytest.mark.parametrize(
    ("provider_trust", "model_trust", "expected_trust", "expected_pseudonymization"),
    [
        (TrustLevel.PRIVATE, TrustLevel.PUBLIC, TrustLevel.PUBLIC, True),
        (TrustLevel.PRIVATE, None, TrustLevel.PRIVATE, False),
    ],
)
async def test_start_persists_effective_run_policy_snapshot(
    monkeypatch,
    provider_trust: TrustLevel,
    model_trust: TrustLevel | None,
    expected_trust: TrustLevel,
    expected_pseudonymization: bool,
) -> None:
    provider = LlmProviderConfig(
        name="Provider",
        adapter_type="openai-compatible",
        trust_level=provider_trust,
        pseudonymization_enabled=True,
    )
    model = ModelConfig(
        provider_id=provider.id,
        model_id="model",
        name="Anzeigename",
        trust_level=model_trust,
    )
    settings = AiSettings(
        providers=[provider], models=[model], default_model_id=model.id
    )
    monkeypatch.setattr(SettingsStore, "load", AsyncMock(return_value=settings))
    task = Mock()

    def create_task(coroutine):
        coroutine.close()
        return task

    monkeypatch.setattr(asyncio, "create_task", create_task)
    session = ChatSession(
        id=uuid4(),
        user_id=uuid4(),
        title="Test",
        model_config_id=model.id,
    )
    db = AsyncMock(spec=AsyncSession)
    db.scalar.side_effect = [session, None, True, True]

    requested_run_id = uuid4()
    run = await AgentRuntime.start(db, session, "Nachricht", run_id=requested_run_id)

    message, persisted_run = db.add_all.call_args.args[0]
    assert message.trust_level == expected_trust.value
    assert persisted_run is run
    assert run.id == requested_run_id
    assert run.model_config_id == model.id
    assert run.model_name == "Anzeigename"
    assert run.trust_level == expected_trust.value
    assert run.pseudonymization_enabled is expected_pseudonymization
    db.commit.assert_awaited_once()
    task.add_done_callback.assert_called_once()
    AgentRuntime._tasks.pop(run.id, None)


async def test_start_rejects_unavailable_model_before_persisting(monkeypatch) -> None:
    missing_model_id = uuid4()
    settings = AiSettings(models=[], default_model_id=missing_model_id)
    monkeypatch.setattr(SettingsStore, "load", AsyncMock(return_value=settings))
    task = Mock()

    def create_task(coroutine):
        coroutine.close()
        return task

    monkeypatch.setattr(asyncio, "create_task", create_task)
    session = ChatSession(
        id=uuid4(),
        user_id=uuid4(),
        title="Test",
        model_config_id=missing_model_id,
    )
    db = AsyncMock(spec=AsyncSession)
    db.scalar.side_effect = [session, None, True]

    with pytest.raises(AgentRunError) as exc_info:
        await AgentRuntime.start(db, session, "Nachricht")

    assert exc_info.value.code == "session.modelUnavailable"
    db.add_all.assert_not_called()
    db.commit.assert_not_awaited()


async def test_start_schedules_title_generation_after_agent_run(monkeypatch) -> None:
    provider = LlmProviderConfig(name="Provider", adapter_type="openai-compatible")
    model = ModelConfig(provider_id=provider.id, model_id="model")
    settings = AiSettings(
        providers=[provider], models=[model], default_model_id=model.id
    )
    monkeypatch.setattr(SettingsStore, "load", AsyncMock(return_value=settings))
    run_worker = AsyncMock()
    title_worker = AsyncMock()
    monkeypatch.setattr(AgentRuntime, "_run", run_worker)
    monkeypatch.setattr(AgentRuntime, "_generate_title", title_worker)
    attachment_service = Mock()
    attachment_service.create_attachment = AsyncMock(
        return_value=Mock(id=uuid4(), filename="urlaub.png")
    )
    attachment_service.discard = AsyncMock()
    monkeypatch.setattr(
        runtime_module, "AttachmentService", lambda **_kwargs: attachment_service
    )
    scheduled = []
    tasks = []

    def create_task(coroutine):
        scheduled.append(coroutine)
        coroutine.close()
        task = Mock()
        tasks.append(task)
        return task

    monkeypatch.setattr(asyncio, "create_task", create_task)
    session = ChatSession(
        id=uuid4(),
        user_id=uuid4(),
        title="Neue Session",
        title_generation_status=TitleGenerationStatus.PENDING.value,
        model_config_id=model.id,
    )
    run_id = uuid4()
    db = AsyncMock(spec=AsyncSession)
    db.scalar.side_effect = [session, None, True, True]

    def assign_ids(items):
        next(item for item in items if isinstance(item, AgentRun)).id = run_id
        next(item for item in items if isinstance(item, Message)).id = uuid4()

    db.add_all.side_effect = assign_ids

    await AgentRuntime.start(
        db,
        session,
        "",
        attachments=[AttachmentInput(b"image", "image/png", "urlaub.png", "image")],
    )

    message = next(item for item in db.add_all.call_args.args[0] if isinstance(item, Message))
    assert len(scheduled) == 2
    assert session.title == "urlaub.png"
    assert session.title_generation_status == TitleGenerationStatus.RUNNING.value
    run_worker.assert_called_once_with(run_id)
    title_worker.assert_called_once_with(
        run_id,
        session.id,
        message.id,
        "urlaub.png",
        after=tasks[0],
    )
    assert list(AgentRuntime._title_tasks.values()) == [session.user_id]
    AgentRuntime._tasks.pop(run_id, None)
    AgentRuntime._title_tasks.clear()


async def test_repeated_run_cancel_does_not_interrupt_cleanup() -> None:
    run_id = uuid4()
    cleanup_started = asyncio.Event()
    release_cleanup = asyncio.Event()
    cancellations = 0

    async def worker() -> None:
        nonlocal cancellations
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            cancellations += 1
            cleanup_started.set()
            try:
                await release_cleanup.wait()
            except asyncio.CancelledError:
                cancellations += 1
            raise

    task = asyncio.create_task(worker())
    AgentRuntime._tasks[run_id] = task
    AgentRuntime._cancellable_runs.add(run_id)
    try:
        first = asyncio.create_task(AgentRuntime.cancel_and_wait(run_id))
        await cleanup_started.wait()
        second = asyncio.create_task(AgentRuntime.cancel_and_wait(run_id))
        await asyncio.sleep(0)
        release_cleanup.set()

        assert await asyncio.gather(first, second) == [True, True]
        assert cancellations == 1
    finally:
        AgentRuntime._tasks.pop(run_id, None)
        AgentRuntime._cancellable_runs.discard(run_id)
        AgentRuntime._user_cancelled_runs.discard(run_id)
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


async def test_cancel_waits_when_run_is_already_finalizing() -> None:
    run_id = uuid4()
    release = asyncio.Event()

    async def worker() -> None:
        await release.wait()

    task = asyncio.create_task(worker())
    AgentRuntime._tasks[run_id] = task
    try:
        cancellation = asyncio.create_task(AgentRuntime.cancel_and_wait(run_id))
        await asyncio.sleep(0)

        assert task.cancelling() == 0
        assert not cancellation.done()

        release.set()
        assert await cancellation is True
    finally:
        AgentRuntime._tasks.pop(run_id, None)
        AgentRuntime._user_cancelled_runs.discard(run_id)
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


async def test_cancel_and_wait_user_title_tasks_only_cancels_owned_tasks() -> None:
    user_id = uuid4()
    other_user_id = uuid4()
    owned = asyncio.create_task(asyncio.Event().wait())
    other = asyncio.create_task(asyncio.Event().wait())
    AgentRuntime._title_tasks[owned] = user_id
    AgentRuntime._title_tasks[other] = other_user_id
    try:
        assert await AgentRuntime.cancel_and_wait_user_title_tasks(user_id) is True
        assert owned.cancelled()
        assert not other.done()
    finally:
        AgentRuntime._title_tasks.clear()
        other.cancel()
        await asyncio.gather(other, return_exceptions=True)


class _SessionContext:
    def __init__(self, db) -> None:
        self.db = db

    async def __aenter__(self):
        return self.db

    async def __aexit__(self, *_args) -> None:
        return None


async def test_title_generation_rechecks_user_before_each_provider_request(
    monkeypatch,
) -> None:
    user_id = uuid4()
    provider = LlmProviderConfig(name="Provider", adapter_type="openai-compatible")
    model = ModelConfig(provider_id=provider.id, model_id="model")
    settings = AiSettings(
        providers=[provider], models=[model], title_generation_model_id=model.id
    )
    session = ChatSession(
        id=uuid4(),
        user_id=user_id,
        title="Initial title",
        title_generation_status=TitleGenerationStatus.RUNNING.value,
        model_config_id=model.id,
    )
    db = AsyncMock(spec=AsyncSession)
    message = Message(id=uuid4(), session_id=session.id, role="user", content="Message")
    db.get.side_effect = [session, message]
    ensure_active = AsyncMock(
        side_effect=[None, AgentRunError("auth.accountDisabled")]
    )
    complete = AsyncMock(return_value=ProviderResult())
    fail = AsyncMock()
    monkeypatch.setattr(runtime_module, "AsyncSessionFactory", lambda: _SessionContext(db))
    monkeypatch.setattr(runtime_module, "load_effective_ai", AsyncMock(return_value=settings))
    monkeypatch.setattr(
        SettingsStore, "load", AsyncMock(return_value=AgentRuntimeSettings())
    )
    monkeypatch.setattr(AgentRuntime, "_ensure_user_active", ensure_active)
    monkeypatch.setattr(runtime_module.ProviderService, "complete", complete)
    monkeypatch.setattr(AgentRuntime, "_fail_title_generation", fail)

    await AgentRuntime._generate_title(
        uuid4(), session.id, message.id, session.title
    )

    assert ensure_active.await_count == 2
    assert all(call.args == (db, user_id) for call in ensure_active.await_args_list)
    complete.assert_awaited_once()
    fail.assert_awaited_once_with(db, session.id)


def test_unknown_runtime_exception_has_safe_descriptor() -> None:
    secret = "secret upstream response"

    error = _public_run_error(RuntimeError(secret))

    assert error.payload() == {
        "code": "agent.failed",
        "message": "The agent run failed.",
    }
    assert secret not in str(error.payload())


async def test_run_failure_persists_structured_message_before_terminal_event(monkeypatch) -> None:
    run = AgentRun(
        id=uuid4(),
        session_id=uuid4(),
        status="queued",
        model_name="Antwortmodell",
        trust_level="private",
    )
    db = AsyncMock(spec=AsyncSession)
    db.get.return_value = run
    db.scalar.return_value = None
    db.add.side_effect = lambda message: setattr(message, "id", uuid4())
    events = []

    async def fail(*_args) -> None:
        raise ProviderError(
            "secret provider response",
            code="provider.rawSecretResponse",
        )

    async def publish(_run_id, event) -> None:
        events.append(event)

    monkeypatch.setattr(runtime_module, "AsyncSessionFactory", lambda: _SessionContext(db))
    monkeypatch.setattr(AgentRuntime, "_execute_loop", fail)
    monkeypatch.setattr(AgentRuntime, "_terminalize_incomplete_tool_calls", AsyncMock())
    monkeypatch.setattr(AgentRuntime, "_deliver_run_push", AsyncMock())
    monkeypatch.setattr(run_events, "publish", publish)

    await AgentRuntime._run(run.id)

    message = db.add.call_args.args[0]
    assert message.error_run_id == run.id
    assert message.error_code == "provider.requestFailed"
    assert message.error_params is None
    assert message.content == ""
    assert run.status == "failed"
    assert run.error == "provider.requestFailed"
    assert [event["type"] for event in events] == [
        "run.started",
        "message.completed",
        "run.failed",
    ]
    assert events[1]["message"]["content"] == "The model provider request failed."
    assert events[1]["message"]["createdAt"] == message.created_at.isoformat()
    assert "secret" not in str(events)


@pytest.mark.parametrize(
    ("failed", "expected_title", "expected_body"),
    [
        (False, "Neue Antwort: Test", "Fertige Antwort"),
        (True, "Antwort fehlgeschlagen", "Der Agent-Auftrag konnte nicht abgeschlossen werden."),
    ],
)
async def test_normal_run_push_uses_canonical_session_target(
    monkeypatch, failed: bool, expected_title: str, expected_body: str
) -> None:
    session = ChatSession(id=uuid4(), user_id=uuid4(), title="Test")
    message = Message(id=uuid4(), session_id=session.id, role="assistant", content="Fertige Antwort")
    run = AgentRun(id=uuid4(), session_id=session.id, result_message_id=message.id)
    db = AsyncMock(spec=AsyncSession)
    db.scalar.return_value = None

    async def get(model_type, item_id):
        return {(ChatSession, session.id): session, (Message, message.id): message}.get(
            (model_type, item_id)
        )

    db.get.side_effect = get
    deliver = AsyncMock()
    monkeypatch.setattr(runtime_module.WebPushService, "deliver_after_commit", deliver)

    await AgentRuntime._deliver_run_push(db, run, failed=failed)

    user_id, payload = deliver.await_args.args
    assert user_id == session.user_id
    assert payload.notification.title == expected_title
    assert payload.notification.body == expected_body
    assert payload.notification.target.session_id == session.id


async def test_automation_run_does_not_emit_a_second_session_push(monkeypatch) -> None:
    run = AgentRun(id=uuid4(), session_id=uuid4())
    db = AsyncMock(spec=AsyncSession)
    db.scalar.return_value = uuid4()
    deliver = AsyncMock()
    monkeypatch.setattr(runtime_module.WebPushService, "deliver_after_commit", deliver)

    await AgentRuntime._deliver_run_push(db, run, failed=False)

    deliver.assert_not_awaited()


async def test_user_cancel_persists_visible_response_before_terminal_event(monkeypatch) -> None:
    run = AgentRun(
        id=uuid4(),
        session_id=uuid4(),
        status="running",
        model_name="Antwortmodell",
        trust_level="private",
    )
    db = AsyncMock(spec=AsyncSession)
    db.get.return_value = run
    events = []

    async def execute_loop(_db, _run, visible_response) -> None:
        visible_response.content_parts.extend(["Teil", "antwort"])
        visible_response.reasoning = {
            "kind": "content",
            "text": "Zwischenstand",
            "status": "streaming",
        }
        AgentRuntime._user_cancelled_runs.add(run.id)
        raise asyncio.CancelledError

    async def publish(_run_id, event) -> None:
        events.append(event)

    monkeypatch.setattr(runtime_module, "AsyncSessionFactory", lambda: _SessionContext(db))
    monkeypatch.setattr(AgentRuntime, "_execute_loop", execute_loop)
    monkeypatch.setattr(AgentRuntime, "_terminalize_incomplete_tool_calls", AsyncMock())
    monkeypatch.setattr(run_events, "publish", publish)
    with pytest.raises(asyncio.CancelledError):
        await AgentRuntime._run(run.id)

    interrupted = db.add.call_args.args[0]
    assert isinstance(interrupted, Message)
    assert interrupted.content == "Teilantwort"
    assert interrupted.generation_status == "interrupted"
    assert interrupted.model_name == "Antwortmodell"
    assert interrupted.trust_level == "private"
    assert interrupted.public_reasoning == {
        "kind": "content",
        "text": "Zwischenstand",
        "status": "interrupted",
    }
    assert [event["type"] for event in events] == [
        "run.started",
        "message.interrupted",
        "run.cancelled",
    ]
    assert events[1]["message"]["content"] == "Teilantwort"
    assert events[1]["message"]["createdAt"] == interrupted.created_at.isoformat()
    assert events[1]["message"]["modelName"] == "Antwortmodell"
    assert run.status == "cancelled"


async def test_immediate_user_cancel_persists_empty_interruption_marker(monkeypatch) -> None:
    run = AgentRun(id=uuid4(), session_id=uuid4(), status="queued")
    db = AsyncMock(spec=AsyncSession)
    db.get.return_value = run
    events = []

    async def publish(_run_id, event) -> None:
        events.append(event)

    execute_loop = AsyncMock()
    monkeypatch.setattr(runtime_module, "AsyncSessionFactory", lambda: _SessionContext(db))
    monkeypatch.setattr(AgentRuntime, "_execute_loop", execute_loop)
    monkeypatch.setattr(AgentRuntime, "_terminalize_incomplete_tool_calls", AsyncMock())
    monkeypatch.setattr(run_events, "publish", publish)
    AgentRuntime._user_cancelled_runs.add(run.id)

    with pytest.raises(asyncio.CancelledError):
        await AgentRuntime._run(run.id)

    interrupted = db.add.call_args.args[0]
    assert interrupted.content == ""
    assert interrupted.generation_status == "interrupted"
    assert [event["type"] for event in events] == [
        "message.interrupted",
        "run.cancelled",
    ]
    execute_loop.assert_not_awaited()


async def test_shutdown_cancel_does_not_persist_user_interruption(monkeypatch) -> None:
    run = AgentRun(id=uuid4(), session_id=uuid4(), status="running")
    db = AsyncMock(spec=AsyncSession)
    db.get.return_value = run
    events = []

    async def execute_loop(_db, _run, _visible_response) -> None:
        raise asyncio.CancelledError

    async def publish(_run_id, event) -> None:
        events.append(event)

    monkeypatch.setattr(runtime_module, "AsyncSessionFactory", lambda: _SessionContext(db))
    monkeypatch.setattr(AgentRuntime, "_execute_loop", execute_loop)
    monkeypatch.setattr(AgentRuntime, "_terminalize_incomplete_tool_calls", AsyncMock())
    monkeypatch.setattr(run_events, "publish", publish)

    with pytest.raises(asyncio.CancelledError):
        await AgentRuntime._run(run.id)

    db.add.assert_not_called()
    assert [event["type"] for event in events] == ["run.started", "run.cancelled"]


async def test_cancel_after_completed_response_does_not_add_interruption(monkeypatch) -> None:
    run = AgentRun(id=uuid4(), session_id=uuid4(), status="running")
    db = AsyncMock(spec=AsyncSession)
    db.get.return_value = run
    events = []

    async def publish(_run_id, event) -> None:
        events.append(event)

    monkeypatch.setattr(runtime_module, "AsyncSessionFactory", lambda: _SessionContext(db))
    monkeypatch.setattr(AgentRuntime, "_execute_loop", AsyncMock())

    async def cancel_after_completion(run_id, event) -> None:
        await publish(run_id, event)
        if event["type"] == "run.completed":
            AgentRuntime._user_cancelled_runs.add(run.id)
            raise asyncio.CancelledError

    monkeypatch.setattr(run_events, "publish", cancel_after_completion)

    with pytest.raises(asyncio.CancelledError):
        await AgentRuntime._run(run.id)

    db.add.assert_not_called()
    assert run.status == "completed"
    assert [event["type"] for event in events] == ["run.started", "run.completed"]


async def test_reasoning_history_keeps_only_latest_snapshot() -> None:
    broker = RunEventBroker()
    run_id = uuid4()

    await broker.publish(run_id, {"type": "reasoning.updated", "reasoning": {"text": "A"}})
    await broker.publish(run_id, {"type": "message.delta", "delta": "Antwort"})
    await broker.publish(run_id, {"type": "reasoning.updated", "reasoning": {"text": "AB"}})

    assert broker._history[run_id] == [
        {"type": "message.delta", "delta": "Antwort"},
        {"type": "reasoning.updated", "reasoning": {"text": "AB"}},
    ]


async def test_context_summary_history_keeps_only_latest_snapshot() -> None:
    broker = RunEventBroker()
    run_id = uuid4()

    await broker.publish(
        run_id,
        {"type": "context.summary.updated", "summary": {"content": "A"}},
    )
    await broker.publish(run_id, {"type": "run.phase", "phase": "compacting_context"})
    await broker.publish(
        run_id,
        {"type": "context.summary.updated", "summary": {"content": "AB"}},
    )

    assert broker._history[run_id] == [
        {"type": "run.phase", "phase": "compacting_context"},
        {"type": "context.summary.updated", "summary": {"content": "AB"}},
    ]


def test_public_reasoning_payload_preserves_long_content() -> None:
    text = "x" * 150_000

    payload = _public_reasoning_payload(PublicReasoning(text=text))

    assert payload is not None
    assert payload["text"] == text
    assert "truncated" not in payload


def test_webfetch_url_query_is_redacted_before_persistence() -> None:
    arguments = _safe_tool_arguments(
        "webfetch", {"url": "https://example.com/page?token=secret", "format": "text"}
    )

    assert arguments == {
        "url": "https://example.com/page?REDACTED",
        "format": "text",
    }


def test_write_file_content_is_fully_redacted_before_persistence() -> None:
    secret = "vertraulicher Dateiinhalt"

    arguments = _safe_tool_arguments(
        "write_file",
        {"root": "session", "path": "files/note.txt", "content": secret},
    )

    assert arguments["content"] == {"characters": len(secret), "redacted": True}
    assert secret not in str(arguments)


def test_edit_file_text_is_fully_redacted_before_persistence() -> None:
    arguments = _safe_tool_arguments(
        "edit_file",
        {
            "root": "session",
            "path": "note.txt",
            "oldText": "private old text",
            "newText": "private new text",
        },
    )

    assert arguments["oldText"] == {"characters": 16, "redacted": True}
    assert arguments["newText"] == {"characters": 16, "redacted": True}
    assert "private" not in str(arguments)


def test_qr_code_content_is_fully_redacted_before_persistence() -> None:
    secret = "WIFI:T:WPA;S:Guest;P:not-for-tool-history;;"

    arguments = _safe_tool_arguments("generate_qr_code", {"content": secret})

    assert arguments["content"] == {"characters": len(secret), "redacted": True}
    assert secret not in str(arguments)


def test_mutation_tokens_are_redacted_before_persistence() -> None:
    arguments = _safe_tool_arguments(
        "tasks_update", {"task_token": "encrypted-secret", "title": "Neu"}
    )

    assert arguments == {"task_token": "[Redacted]", "title": "Neu"}


def test_memory_write_content_is_preserved_for_user_review() -> None:
    content = "Stabiler Selbstkontext " * 1000

    arguments = _safe_tool_arguments(
        "memory_write",
        {"action": "create", "content": content, "category": "context"},
    )

    assert arguments["content"] == content


def test_responses_replay_write_file_content_is_fully_redacted() -> None:
    secret = "nicht persistieren"
    call = ToolCall(
        id="write-1",
        name="write_file",
        arguments={
            "root": "session",
            "path": "files/note.txt",
            "content": secret,
        },
    )

    result = _safe_provider_data(
        {
            "responses_output_items": [
                {
                    "type": "function_call",
                    "call_id": "write-1",
                    "name": "write_file",
                    "arguments": (
                        '{"root":"session","path":"files/note.txt",'
                        f'"content":"{secret}"}}'
                    ),
                }
            ]
        },
        [call],
    )

    persisted = result["responses_output_items"][0]["arguments"]
    assert secret not in persisted
    assert '"redacted": true' in persisted


def test_responses_replay_webfetch_arguments_are_redacted() -> None:
    call = ToolCall(
        id="call-1",
        name="webfetch",
        arguments={"url": "https://example.com/page?token=secret"},
    )

    result = _safe_provider_data(
        {
            "responses_output_items": [
                {
                    "type": "function_call",
                    "call_id": "call-1",
                    "name": "webfetch",
                    "arguments": '{"url":"https://example.com/page?token=secret"}',
                }
            ]
        },
        [call],
    )

    assert "secret" not in result["responses_output_items"][0]["arguments"]


def test_responses_replay_mutation_tokens_are_redacted() -> None:
    call = ToolCall(
        id="task-1",
        name="tasks_update",
        arguments={"task_token": "encrypted-secret", "title": "Neu"},
    )

    result = _safe_provider_data(
        {
            "responses_output_items": [
                {
                    "type": "function_call",
                    "call_id": call.id,
                    "name": call.name,
                    "arguments": json.dumps(call.arguments),
                }
            ]
        },
        [call],
    )

    persisted = result["responses_output_items"][0]["arguments"]
    assert "encrypted-secret" not in persisted
    assert "[Redacted]" in persisted


def test_agent_identity_instructions_are_redacted_and_reprojected_as_text() -> None:
    instructions = "Sei direkt. " * 100
    call = ToolCall(
        id="identity-1",
        name="update_agent_identity",
        arguments={"instructions": instructions},
    )

    safe_arguments = _safe_tool_arguments(call.name, call.arguments)
    replay = _safe_provider_data(
        {
            "responses_output_items": [
                {
                    "type": "function_call",
                    "call_id": call.id,
                    "name": call.name,
                    "arguments": {"instructions": instructions},
                }
            ]
        },
        [call],
    )
    projected = _model_tool_calls(
        [{"id": call.id, "name": call.name, "arguments": safe_arguments}]
    )

    assert instructions not in replay["responses_output_items"][0]["arguments"]
    assert projected is not None
    assert isinstance(projected[0]["arguments"]["instructions"], str)
    assert "were truncated" in projected[0]["arguments"]["instructions"]


def test_object_responses_replay_webfetch_arguments_are_redacted() -> None:
    result = _safe_provider_data(
        {
            "responses_output_items": [
                {
                    "type": "function_call",
                    "call_id": "call-1",
                    "name": "webfetch",
                    "arguments": {"url": "https://example.com/?secret"},
                }
            ]
        },
        [ToolCall(id="call-1", name="webfetch", arguments={})],
    )

    assert result["responses_output_items"][0]["arguments"] == (
        '{"url": "https://example.com/?REDACTED"}'
    )


def test_provider_cannot_set_internal_runtime_state() -> None:
    result = _safe_provider_data(
        {
            "_matoloop": {
                "memoryOverview": {"hasMore": True},
                "fileActions": [{"downloadUrl": "https://evil.test"}],
            }
        },
        [],
    )

    assert "_matoloop" not in result
