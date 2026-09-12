from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.api.errors import register_error_handlers
from mato_loop_backend.api.routes import automations as routes
from mato_loop_backend.api.routes.automations import AutomationRequest, ScheduleRequest
from mato_loop_backend.api.routes.notifications import _notification_payload
from mato_loop_backend.automations import (
    AutomationError,
    AutomationService,
    advance_schedule,
    automation_impact_token,
    copy_runtime,
    next_run_at,
    runtime_snapshot,
)
from mato_loop_backend.db.session import get_db_session
from mato_loop_backend.models import (
    AgentRun,
    Automation,
    AutomationRun,
    AutomationSchedule,
    ChatSession,
    Message,
    Notification,
    UserAccount,
)
from mato_loop_backend.notifications import NotificationService
from mato_loop_backend.security import require_user
from mato_loop_backend.settings import DEFAULT_AGENT_PROFILE_ID


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


def test_schedule_request_accepts_exactly_one_schedule_kind() -> None:
    once = ScheduleRequest.model_validate_json(
        '{"runAt":"2026-08-21T10:30:00+02:00","enabled":true}'
    )

    assert once.run_at == datetime(2026, 8, 21, 8, 30, tzinfo=UTC)
    with pytest.raises(ValidationError):
        ScheduleRequest.model_validate_json(
            '{"cron":"0 8 * * *","timezone":"UTC",'
            '"runAt":"2026-08-21T10:30:00+02:00"}'
        )
    with pytest.raises(ValidationError):
        ScheduleRequest.model_validate_json('{"enabled":true}')

    recurring = ScheduleRequest.model_validate_json(
        '{"cron":"0 8 * * *","timezone":"Europe/Berlin",'
        '"validUntil":"2026-08-28T08:00:00+02:00"}'
    )
    assert recurring.valid_until == datetime(2026, 8, 28, 6, tzinfo=UTC)
    with pytest.raises(ValidationError):
        ScheduleRequest.model_validate_json(
            '{"runAt":"2026-08-21T10:30:00+02:00",'
            '"validUntil":"2026-08-28T10:30:00+02:00"}'
        )


def test_automation_request_accepts_browser_uuid_and_datetime_values() -> None:
    skill_id = uuid4()
    source_session_id = uuid4()
    output_session_id = uuid4()

    request = AutomationRequest.model_validate(
        {
            "name": "Bericht",
            "task": "Erstelle einen Bericht",
            "skillId": str(skill_id),
            "sourceSessionId": str(source_session_id),
            "outputSessionId": str(output_session_id),
            "schedules": [
                {"runAt": "2026-08-21T10:30:00+02:00", "enabled": True}
            ],
        }
    )

    assert request.skill_id == skill_id
    assert request.source_session_id == source_session_id
    assert request.output_session_id == output_session_id
    assert request.schedules[0].run_at == datetime(2026, 8, 21, 8, 30, tzinfo=UTC)


def test_manual_request_requires_explicit_bounded_schedules() -> None:
    draft = {"name": "Manual", "task": "Do work"}
    assert AutomationRequest(**draft, schedules=[]).schedules == []
    with pytest.raises(ValidationError):
        AutomationRequest(**draft)
    with pytest.raises(ValidationError):
        AutomationRequest(**draft, schedules=[{"cron": "0 8 * * *", "timezone": "UTC"}] * 51)
    schema = AutomationRequest.model_json_schema()
    assert "schedules" in schema["required"]
    assert schema["properties"]["schedules"]["maxItems"] == 50
    assert schema["properties"]["schedules"].get("minItems", 0) == 0


async def test_manual_automation_is_never_scheduled_but_can_start(monkeypatch) -> None:
    automation = Automation(
        id=uuid4(), user_id=uuid4(), execution_session_id=uuid4(),
        name="Manual", task="Do work", revision=1, enabled=True, schedules=[],
    )
    db = AsyncMock(spec=AsyncSession)
    db.scalars.return_value = MagicMock(all=MagicMock(return_value=[automation]))
    assert await AutomationService.enqueue_due(db) == 0
    db.add.assert_not_called()
    statement = str(db.scalars.await_args.args[0])
    assert "EXISTS" in statement and "automation_schedules.next_run_at <=" in statement
    monkeypatch.setattr(AutomationService, "owned", AsyncMock(return_value=automation))
    monkeypatch.setattr(AutomationService, "_snapshot", AsyncMock(return_value={"task": "Do work"}))
    mirror = AsyncMock()
    monkeypatch.setattr(AutomationService, "mirror_agent_runs", mirror)
    db.scalar.return_value = None

    def add(run):
        run.id = uuid4()
        run.delivery_attempts = 0

    db.add.side_effect = add
    result = await routes.start_automation(
        automation.id, routes.AutomationRunRequest(expectedRevision=1),
        UserAccount(id=automation.user_id), db,
    )
    assert result["trigger"] == "manual"
    assert result["status"] == "queued"
    assert result["automationRevision"] == 1
    mirror.assert_awaited_once_with(db)
    db.add.assert_called_once()
    assert db.commit.await_count == 2


@pytest.fixture
def automation_api():
    user = UserAccount(id=uuid4())
    db = AsyncMock(spec=AsyncSession)
    app = FastAPI()
    app.include_router(routes.router)
    register_error_handlers(app)
    app.dependency_overrides[get_db_session] = lambda: db
    app.dependency_overrides[require_user] = lambda: user
    with TestClient(app) as client:
        yield client, db, user


@pytest.mark.parametrize("method", ["post", "put"])
def test_manual_api_rejects_archiving_and_omitted_schedules(automation_api, method, monkeypatch):
    client, db, user = automation_api
    automation = Automation(id=uuid4(), user_id=user.id, revision=1)
    db.scalar.return_value = automation
    monkeypatch.setattr(AutomationService, "skill", AsyncMock(return_value=None))
    path = "/automations" if method == "post" else f"/automations/{automation.id}"
    draft = {"name": "Manual", "task": "Do work", "expectedRevision": 1}
    missing = client.request(method, path, json=draft)
    assert missing.status_code == 422
    assert missing.json()["error"]["code"] == "validation.required"
    assert missing.json()["error"]["field"] == "schedules"
    invalid = client.request(method, path, json={**draft, "schedules": [], "archiveAfterSuccess": True})
    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "automation.archiveRequiresOneTimeSchedule"
    db.add.assert_not_called()
    db.commit.assert_not_awaited()


def test_status_and_list_summary_are_compact_and_batch_active_statuses(automation_api):
    client, db, user = automation_api
    now = datetime(2026, 9, 1, tzinfo=UTC)
    items = [
        Automation(id=uuid4(), user_id=user.id, name="Manual", task="x" * 300,
                   enabled=True, revision=1, schedules=[]),
        Automation(id=uuid4(), user_id=user.id, name="Paused", task="Task",
                   enabled=False, revision=2, schedules=[AutomationSchedule(enabled=True, next_run_at=now)]),
    ]
    db.scalars.return_value = MagicMock(all=MagicMock(return_value=items))
    db.execute.return_value = MagicMock(all=MagicMock(return_value=[(items[0].id, "queued")]))
    db.get.side_effect = AssertionError("Compact reads must not load sessions or results")
    response = client.get("/automations?summary=true")
    assert response.status_code == 200
    payload = response.json()["items"]
    status_keys = {"id", "revision", "enabled", "archivedAt", "scheduleCount", "effectiveState"}
    assert all(set(item) == status_keys | {"name", "taskExcerpt"} for item in payload)
    assert payload[0]["taskExcerpt"] == "x" * 240
    assert payload[0]["scheduleCount"] == 0
    assert payload[0]["effectiveState"] == {
        "enabled": False, "activeRunStatus": "queued", "nextRunAt": None,
    }
    assert payload[1]["effectiveState"] == {
        "enabled": False, "activeRunStatus": None, "nextRunAt": None,
    }
    db.execute.assert_awaited_once()
    db.scalars.assert_awaited_once()
    query = db.scalars.await_args.args[0].compile()
    assert user.id in query.params.values()
    assert "automations.archived_at IS NULL" in str(query)
    active_query = db.execute.await_args.args[0].compile()
    assert [item.id for item in items] in active_query.params.values()
    assert "definition_snapshot" not in str(active_query)
    for item, summary in zip(items, payload, strict=True):
        db.scalar.return_value = item
        response = client.get(f"/automations/{item.id}/status")
        assert response.status_code == 200
        assert response.json() == {key: summary[key] for key in status_keys}
    client.get("/automations?summary=true&archived=true")
    assert "automations.archived_at IS NOT NULL" in str(db.scalars.await_args.args[0])
    db.get.assert_not_awaited()


@pytest.mark.parametrize("enabled,archived", [(True, False), (False, False), (True, True)])
def test_status_next_run_requires_effective_schedule(enabled, archived):
    now = datetime(2026, 9, 1, tzinfo=UTC)
    item = Automation(
        id=uuid4(), revision=1, enabled=enabled, archived_at=now if archived else None,
        schedules=[AutomationSchedule(enabled=True, next_run_at=now)],
    )
    state = routes._automation_status(item, None).effective_state
    assert state.enabled == (enabled and not archived)
    assert state.next_run_at == (now if enabled and not archived else None)


@pytest.mark.parametrize("suffix", ["/status", "?includeHistory=false", "/runs?summary=true"])
def test_compact_endpoints_are_owner_bound(automation_api, suffix):
    client, db, user = automation_api
    automation_id = uuid4()
    db.scalar.return_value = None
    response = client.get(f"/automations/{automation_id}{suffix}")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "automation.notFound"
    statement = db.scalar.await_args.args[0].compile()
    assert user.id in statement.params.values()
    assert automation_id in statement.params.values()
    assert "automations.user_id =" in str(statement)
    db.get.assert_not_awaited()
    db.scalars.assert_not_awaited()
    db.execute.assert_not_awaited()


def test_detail_history_is_opt_out_and_default_list_stays_full(automation_api):
    client, db, user = automation_api
    item = Automation(
        id=uuid4(), user_id=user.id, name="Manual", task="Do work", enabled=True,
        execution_session_id=uuid4(), revision=1, schedules=[],
    )
    db.get.return_value = ChatSession(id=item.execution_session_id)
    db.scalar.side_effect = [item, None, item, None, None]
    db.scalars.return_value = MagicMock(all=MagicMock(return_value=[]))
    light = client.get(f"/automations/{item.id}?includeHistory=false")
    assert light.status_code == 200
    assert "executionHistory" not in light.json()
    assert {"runtime", "task", "schedules"} <= light.json().keys()
    db.scalars.assert_not_awaited()
    full = client.get(f"/automations/{item.id}")
    assert full.json() == {**light.json(), "executionHistory": []}
    db.scalars.assert_awaited_once()
    assert "messages" in str(db.scalars.await_args.args[0])
    db.scalars.return_value = MagicMock(all=MagicMock(return_value=[item]))
    listed = client.get("/automations")
    assert listed.status_code == 200
    assert listed.json() == {"items": [light.json()]}


def test_run_summary_skips_snapshots_and_result_lookups_but_full_reads_keep_them(automation_api):
    client, db, user = automation_api
    automation_id = uuid4()
    run = AutomationRun(
        id=uuid4(), automation_id=automation_id, automation_revision=3, status="completed",
        trigger="manual", agent_run_id=uuid4(), definition_snapshot={"task": "Secret"},
        schedule_snapshots=[], delivery_status="not_requested", delivery_attempts=0,
    )
    db.scalar.return_value = Automation(id=automation_id, user_id=user.id)
    db.scalars.return_value = MagicMock(all=MagicMock(return_value=[run, run]))
    db.get.side_effect = AssertionError("Summary must not look up results")
    response = client.get(f"/automations/{automation_id}/runs?summary=true&page=2&pageSize=1")
    assert response.status_code == 200
    data = response.json()
    assert data["page"] == 2 and data["hasMore"] is True
    assert len(data["items"]) == 1
    assert set(data["items"][0]) == {
        "id", "automationId", "status", "trigger", "automationRevision", "scheduledFor",
        "createdAt", "startedAt", "completedAt", "delivery",
    }
    assert data["items"][0]["delivery"] == {
        "status": "not_requested", "attempts": 0, "nextAttemptAt": None,
    }
    db.get.assert_not_awaited()
    db.scalars.assert_awaited_once()
    query = db.scalars.await_args.args[0].compile()
    assert "definition_snapshot" not in str(query)
    assert "schedule_snapshots" not in str(query)
    assert query.params["param_1"] == 2 and query.params["param_2"] == 1
    message = Message(id=uuid4(), content="Result", role="assistant")
    agent_run = AgentRun(
        id=run.agent_run_id, result_message_id=message.id, agent_profile_id=uuid4()
    )
    db.get.side_effect = [agent_run, message, agent_run, message]
    full = client.get(f"/automations/{automation_id}/runs?pageSize=1")
    assert full.status_code == 200
    item = full.json()["items"][0]
    assert item["result"] == {"messageId": str(message.id), "content": "Result"}
    assert item["agentProfileId"] == str(agent_run.agent_profile_id)
    assert item["definition"] == {"task": "Secret"}
    db.scalar.return_value = run
    single = client.get(f"/automations/{automation_id}/runs/{run.id}")
    assert single.json() == item


def test_one_time_schedule_uses_existing_next_run_at() -> None:
    automation = Automation(
        id=uuid4(),
        user_id=uuid4(),
        execution_session_id=uuid4(),
        name="Erinnerung",
        task="Erinnere mich",
    )
    run_at = datetime(2026, 8, 21, 10, 30, tzinfo=UTC)

    AutomationService.set_schedules(
        automation,
        [{"run_at": run_at, "enabled": True}],
        now=datetime(2026, 8, 20, 12, tzinfo=UTC),
    )

    assert len(automation.schedules) == 1
    assert automation.schedules[0].cron_expression is None
    assert automation.schedules[0].timezone is None
    assert automation.schedules[0].next_run_at == run_at


def test_recurring_schedule_advances_until_inclusive_cutoff() -> None:
    cutoff = datetime(2026, 8, 21, 8, tzinfo=UTC)
    automation = Automation(
        id=uuid4(),
        user_id=uuid4(),
        execution_session_id=uuid4(),
        name="Erinnerung",
        task="Erinnere mich",
    )
    AutomationService.set_schedules(
        automation,
        [
            {
                "cron": "0 8 * * *",
                "timezone": "UTC",
                "valid_until": datetime.fromisoformat("2026-08-21T10:00:00+02:00"),
                "enabled": True,
            }
        ],
        now=datetime(2026, 8, 20, 8, tzinfo=UTC),
    )

    schedule = automation.schedules[0]
    assert schedule.next_run_at == cutoff
    assert schedule.valid_until == cutoff
    assert schedule.enabled is True

    advance_schedule(schedule, cutoff)
    assert schedule.next_run_at == datetime(2026, 8, 22, 8, tzinfo=UTC)
    assert schedule.enabled is False


@pytest.mark.parametrize("invalid", [[], [{"cron": "0 8 * * *", "timezone": "UTC"}],
    [{"run_at": datetime(2026, 8, 21, 10, 30, tzinfo=UTC)}] * 2])
def test_archive_policy_requires_one_one_time_schedule(invalid) -> None:
    AutomationService.validate_archive_policy(
        [{"run_at": datetime(2026, 8, 21, 10, 30, tzinfo=UTC)}], True
    )

    with pytest.raises(AutomationError) as exc_info:
        AutomationService.validate_archive_policy(invalid, True)

    assert exc_info.value.code == "automation.archiveRequiresOneTimeSchedule"


@pytest.mark.parametrize("manual", [False, True])
async def test_create_flushes_execution_session_before_automation(monkeypatch, manual) -> None:
    events = []
    added = []
    db = AsyncMock(spec=AsyncSession)

    def add(item) -> None:
        added.append(item)
        events.append(("add", type(item)))

    db.add.side_effect = add

    async def flush() -> None:
        events.append(("flush", None))

    db.flush.side_effect = flush
    monkeypatch.setattr(AutomationService, "skill", AsyncMock(return_value=None))

    automation = await AutomationService.create(
        db,
        user_id=uuid4(),
        name="Erinnerung",
        task="Erinnere mich",
        skill_id=None,
        enabled=True,
        source_session_id=None,
        output_session_id=None,
        output_session_required=False,
        schedules=[] if manual else [
            {
                "run_at": datetime(2026, 8, 21, 10, 30, tzinfo=UTC),
                "enabled": True,
            }
        ],
        now=datetime(2026, 8, 20, 12, tzinfo=UTC),
    )

    assert events == [
        ("add", ChatSession),
        ("flush", None),
        ("add", Automation),
        ("flush", None),
    ]
    assert automation.execution_session_id is not None
    assert added[0].agent_profile_id == DEFAULT_AGENT_PROFILE_ID
    assert len(automation.schedules) == (0 if manual else 1)


@pytest.mark.parametrize("manual", [False, True])
async def test_replace_deletes_existing_schedules_before_inserting_replacements(
    monkeypatch, manual,
) -> None:
    automation = Automation(
        id=uuid4(),
        user_id=uuid4(),
        execution_session_id=uuid4(),
        name="Bericht",
        task="Erstelle einen Bericht",
        revision=1,
    )
    automation.schedules = [
        AutomationSchedule(
            id=uuid4(),
            cron_expression="0 8 * * *",
            timezone="UTC",
            enabled=True,
            next_run_at=datetime(2026, 8, 21, 8, tzinfo=UTC),
        )
    ]
    db = AsyncMock(spec=AsyncSession)
    db.get.return_value = ChatSession(
        id=automation.execution_session_id,
        user_id=automation.user_id,
        session_type="automation",
    )
    schedule_snapshots = []

    async def flush() -> None:
        schedule_snapshots.append(
            [(item.cron_expression, item.timezone) for item in automation.schedules]
        )

    db.flush.side_effect = flush
    monkeypatch.setattr(AutomationService, "skill", AsyncMock(return_value=None))

    await AutomationService.replace(
        db,
        automation,
        expected_revision=1,
        name=automation.name,
        task=automation.task,
        skill_id=None,
        enabled=True,
        source_session_id=None,
        output_session_id=None,
        output_session_required=False,
        schedules=[] if manual else [{"cron": "0 8 * * *", "timezone": "UTC", "enabled": True}],
        now=datetime(2026, 8, 20, 12, tzinfo=UTC),
    )

    assert schedule_snapshots == [[], [] if manual else [("0 8 * * *", "UTC")]]
    assert automation.revision == 2
    db.refresh.assert_awaited_once_with(automation, attribute_names=["updated_at"])


def test_runtime_snapshot_contains_only_validated_session_binding() -> None:
    agent_profile_id = uuid4()
    session = ChatSession(
        id=uuid4(),
        user_id=uuid4(),
        preferred_execution_agent_id=uuid4(),
        current_directory="/advertised/project",
        agent_profile_id=agent_profile_id,
    )

    snapshot = runtime_snapshot(session)

    assert snapshot["preferredExecutionAgentId"] == str(
        session.preferred_execution_agent_id
    )
    assert snapshot["currentDirectory"] == "/advertised/project"
    assert snapshot["agentProfileId"] == str(agent_profile_id)
    assert "workspacePath" not in snapshot


def test_copy_runtime_includes_agent_profile() -> None:
    source = ChatSession(agent_profile_id=uuid4())
    target = ChatSession(agent_profile_id=DEFAULT_AGENT_PROFILE_ID)

    copy_runtime(source, target)

    assert target.agent_profile_id == source.agent_profile_id


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
            valid_until=now,
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
    assert run.schedule_snapshots[0]["validUntil"] == now.isoformat()
    assert automation.schedules[0].enabled is False
    assert automation.schedules[1].enabled is True
    assert all(schedule.next_run_at > now for schedule in automation.schedules)
    db.commit.assert_awaited_once()


async def test_due_one_time_schedule_is_enqueued_once_and_disabled() -> None:
    now = datetime(2026, 8, 20, 12, tzinfo=UTC)
    automation = Automation(
        id=uuid4(),
        user_id=uuid4(),
        execution_session_id=uuid4(),
        name="Erinnerung",
        task="Erinnere mich",
        revision=1,
        enabled=True,
    )
    schedule = AutomationSchedule(
        id=uuid4(),
        cron_expression=None,
        timezone=None,
        enabled=True,
        next_run_at=now,
    )
    automation.schedules = [schedule]
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
    db.scalar.return_value = None
    db.get.return_value = execution_session

    first = await AutomationService.enqueue_due(db, now=now)
    second = await AutomationService.enqueue_due(db, now=now)

    run = db.add.call_args.args[0]
    assert first == 1
    assert second == 0
    assert schedule.enabled is False
    assert run.status == "queued"
    assert run.schedule_snapshots == [
        {
            "id": str(schedule.id),
            "cron": None,
            "timezone": None,
            "runAt": now.isoformat(),
            "scheduledFor": now.isoformat(),
        }
    ]
    db.add.assert_called_once()


async def test_recovery_disables_missed_one_time_schedule() -> None:
    schedule = AutomationSchedule(
        id=uuid4(),
        automation_id=uuid4(),
        cron_expression=None,
        timezone=None,
        enabled=True,
        next_run_at=datetime(2026, 8, 19, 12, tzinfo=UTC),
    )
    rows = MagicMock()
    rows.all.return_value = [schedule]
    active_runs = MagicMock()
    active_runs.all.return_value = []
    db = AsyncMock(spec=AsyncSession)
    db.execute.return_value = active_runs
    db.scalars.return_value = rows

    await AutomationService.recover(db)

    assert schedule.enabled is False
    db.commit.assert_awaited()


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


async def test_completed_scheduled_one_time_run_archives_automation(monkeypatch) -> None:
    completed_at = datetime(2026, 8, 20, 14, 20, tzinfo=UTC)
    automation = Automation(
        id=uuid4(),
        user_id=uuid4(),
        execution_session_id=uuid4(),
        name="Erinnerung",
        task="Erinnere mich",
        revision=2,
        archive_after_success=True,
    )
    agent_run = AgentRun(
        id=uuid4(),
        session_id=automation.execution_session_id,
        status="completed",
        completed_at=completed_at,
    )
    run = AutomationRun(
        id=uuid4(),
        automation_id=automation.id,
        automation_revision=2,
        trigger="scheduled",
        status="running",
        schedule_snapshots=[{"cron": None, "runAt": completed_at.isoformat()}],
        definition_snapshot={},
        agent_run_id=agent_run.id,
        delivery_status="pending",
    )
    rows = MagicMock()
    rows.all.return_value = [(run, automation, agent_run)]
    db = AsyncMock(spec=AsyncSession)
    db.execute.return_value = rows
    monkeypatch.setattr(AutomationService, "_notify_terminal", AsyncMock())

    changed = await AutomationService.mirror_agent_runs(db)

    assert changed == 1
    assert run.status == "completed"
    assert automation.archived_at == completed_at
    db.commit.assert_awaited_once()


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

    original = automation_impact_token([automation])
    automation.revision = 2

    assert automation_impact_token([automation]) != original


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
    notification_id = uuid4()
    create = AsyncMock(return_value=notification_id)
    monkeypatch.setattr(NotificationService, "create", create)

    push = await AutomationService._delivery_failure(
        AsyncMock(spec=AsyncSession), run, automation
    )

    assert run.delivery_status == "failed"
    assert run.delivery_attempts == 3
    assert run.delivery_next_attempt_at is None
    draft = create.await_args.args[1]
    assert draft.kind == "automation.deliveryFailed"
    assert draft.automation_run_id == run.id
    assert push.notification.tag == f"notification:{notification_id}"
    assert push.notification.target.run_id == run.id


async def test_delivery_retry_rejects_delivery_still_in_progress() -> None:
    run = AutomationRun(
        id=uuid4(),
        automation_id=uuid4(),
        automation_revision=1,
        trigger="manual",
        status="completed",
        definition_snapshot={},
        output_session_id=uuid4(),
        delivery_status="pending",
    )
    db = AsyncMock(spec=AsyncSession)
    db.scalar.return_value = run

    with pytest.raises(AutomationError) as exc_info:
        await AutomationService.retry_delivery(
            db, run.automation_id, run.id, uuid4()
        )

    assert exc_info.value.code == "automation.deliveryUnavailable"
    db.scalar.assert_awaited_once()


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
