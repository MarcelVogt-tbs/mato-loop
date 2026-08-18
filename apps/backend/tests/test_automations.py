from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.api.routes.notifications import _notification_payload
from mato_loop_backend.api.routes.sessions import _automation_impact_token
from mato_loop_backend.automations import (
    AutomationError,
    AutomationService,
    next_run_at,
    runtime_snapshot,
)
from mato_loop_backend.models import (
    Automation,
    AutomationRun,
    AutomationSchedule,
    ChatSession,
    Notification,
)
from mato_loop_backend.notifications import NotificationService


def test_cron_uses_debian_dst_semantics_and_rejects_six_fields() -> None:
    spring = next_run_at(
        "30 2 * * *", "Europe/Berlin", datetime(2026, 3, 28, 12, tzinfo=UTC)
    )
    first_fall = next_run_at(
        "30 2 * * *", "Europe/Berlin", datetime(2026, 10, 24, 12, tzinfo=UTC)
    )
    after_fall = next_run_at("30 2 * * *", "Europe/Berlin", first_fall)

    assert spring == datetime(2026, 3, 29, 1, tzinfo=UTC)
    assert first_fall == datetime(2026, 10, 25, 0, 30, tzinfo=UTC)
    assert after_fall == datetime(2026, 10, 26, 1, 30, tzinfo=UTC)
    with pytest.raises(AutomationError) as exc_info:
        next_run_at("0 0 0 * * *", "UTC", datetime.now(UTC))
    assert exc_info.value.code == "automation.scheduleInvalid"


def test_runtime_snapshot_contains_only_validated_session_binding() -> None:
    session = ChatSession(
        id=uuid4(),
        user_id=uuid4(),
        preferred_execution_agent_id=uuid4(),
        preferred_root_id=uuid4(),
        working_directory="project",
        current_directory="/advertised/project",
        workspace_access="read-only",
    )

    snapshot = runtime_snapshot(session)

    assert snapshot["preferredExecutionAgentId"] == str(
        session.preferred_execution_agent_id
    )
    assert snapshot["preferredRootId"] == str(session.preferred_root_id)
    assert snapshot["workingDirectory"] == "project"
    assert "workspacePath" not in snapshot


async def test_due_schedules_are_bundled_into_one_skipped_run() -> None:
    now = datetime(2026, 8, 18, 12, tzinfo=UTC)
    automation = Automation(
        id=uuid4(),
        user_id=uuid4(),
        execution_session_id=uuid4(),
        name="Bericht",
        task="Erstelle einen Bericht",
        revision=4,
        enabled=True,
    )
    automation.schedules = [
        AutomationSchedule(
            id=uuid4(),
            cron_expression="0 * * * *",
            timezone="UTC",
            enabled=True,
            next_run_at=now,
        ),
        AutomationSchedule(
            id=uuid4(),
            cron_expression="0 12 * * *",
            timezone="UTC",
            enabled=True,
            next_run_at=now,
        ),
    ]
    execution_session = ChatSession(
        id=automation.execution_session_id,
        user_id=automation.user_id,
        session_type="automation",
        model_config_id=uuid4(),
    )
    rows = MagicMock()
    rows.all.return_value = [automation]
    db = AsyncMock(spec=AsyncSession)
    db.scalars.return_value = rows
    db.scalar.return_value = uuid4()
    db.get.return_value = execution_session

    count = await AutomationService.enqueue_due(db, now=now)

    run = db.add.call_args.args[0]
    assert count == 1
    assert run.status == "skipped"
    assert run.error_code == "automation.alreadyRunning"
    assert len(run.schedule_snapshots) == 2
    assert all(schedule.next_run_at > now for schedule in automation.schedules)
    db.commit.assert_awaited_once()


async def test_manual_enqueue_rejects_a_changed_revision(monkeypatch) -> None:
    automation = Automation(
        id=uuid4(),
        user_id=uuid4(),
        execution_session_id=uuid4(),
        name="Bericht",
        task="Erstelle einen Bericht",
        revision=2,
    )
    monkeypatch.setattr(
        AutomationService, "owned", AsyncMock(return_value=automation)
    )
    db = AsyncMock(spec=AsyncSession)

    with pytest.raises(AutomationError) as exc_info:
        await AutomationService.enqueue_manual(
            db,
            automation.id,
            automation.user_id,
            expected_revision=1,
        )

    assert exc_info.value.code == "automation.changed"
    assert exc_info.value.params == {"currentRevision": 2}
    db.add.assert_not_called()


def test_session_delete_impact_changes_with_linked_automation_revision() -> None:
    automation = Automation(
        id=uuid4(),
        user_id=uuid4(),
        execution_session_id=uuid4(),
        name="Bericht",
        task="Erstelle einen Bericht",
        revision=1,
        output_session_required=False,
    )

    original = _automation_impact_token([automation])
    automation.revision = 2

    assert _automation_impact_token([automation]) != original


async def test_start_failure_cancels_pending_delivery(monkeypatch) -> None:
    run = AutomationRun(
        id=uuid4(),
        automation_id=uuid4(),
        automation_revision=1,
        trigger="manual",
        status="queued",
        definition_snapshot={},
        delivery_status="pending",
    )
    db = AsyncMock(spec=AsyncSession)
    db.get.side_effect = [run, None]

    await AutomationService._fail_start(db, run.id, "automation.snapshotInvalid")

    assert run.status == "failed"
    assert run.delivery_status == "cancelled"
    db.commit.assert_awaited_once()


async def test_third_delivery_failure_is_terminal_and_notifies_once(
    monkeypatch,
) -> None:
    automation = Automation(
        id=uuid4(),
        user_id=uuid4(),
        execution_session_id=uuid4(),
        name="Bericht",
        task="Erstelle einen Bericht",
    )
    run = AutomationRun(
        id=uuid4(),
        automation_id=automation.id,
        automation_revision=1,
        trigger="manual",
        status="completed",
        definition_snapshot={},
        delivery_status="pending",
        delivery_attempts=2,
    )
    create = AsyncMock()
    monkeypatch.setattr(NotificationService, "create", create)

    await AutomationService._delivery_failure(
        AsyncMock(spec=AsyncSession), run, automation
    )

    assert run.delivery_status == "failed"
    assert run.delivery_attempts == 3
    assert run.delivery_next_attempt_at is None
    draft = create.await_args.args[1]
    assert draft.kind == "automation.deliveryFailed"
    assert draft.automation_run_id == run.id


def test_notification_exposes_automation_run_target() -> None:
    automation = Automation(
        id=uuid4(),
        user_id=uuid4(),
        execution_session_id=uuid4(),
        name="Bericht",
        task="Erstelle einen Bericht",
    )
    run = AutomationRun(
        id=uuid4(),
        automation_id=automation.id,
        automation_revision=1,
        trigger="manual",
        status="completed",
        definition_snapshot={},
        automation=automation,
    )
    notification = Notification(
        id=uuid4(),
        user_id=automation.user_id,
        automation_run_id=run.id,
        automation_run=run,
        kind="automation.completed",
        title="Automation abgeschlossen",
        deduplication_key=f"automation-run:{run.id}:completed",
        created_at=datetime.now(UTC),
    )

    payload = _notification_payload(notification).model_dump(mode="json", by_alias=True)

    assert payload["target"] == {
        "type": "automationRun",
        "automationId": str(automation.id),
        "runId": str(run.id),
    }
