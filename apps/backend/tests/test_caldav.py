import hashlib
import json
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, date, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4, uuid5

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

import mato_loop_backend.integrations.caldav as caldav_module
from mato_loop_backend.agent.tools import tool_registry
from mato_loop_backend.integrations.caldav import (
    CALDAV_ADAPTER,
    CalendarIntegrationArguments,
    CalendarListAccountsArguments,
    CalendarUpdateEventArguments,
    calendar_list_accounts,
    calendar_update_event,
    load_caldav_resources,
)
from mato_loop_backend.integrations.caldav_client import (
    CalDavCalendar,
    CalDavDiscovery,
    CalDavEvent,
    CalDavTask,
)
from mato_loop_backend.integrations.registry import (
    effective_integration_contributions,
    integration_adapter,
)
from mato_loop_backend.settings import (
    IntegrationInstance,
    IntegrationSettings,
    ToolExecutionPolicy,
    ToolSettings,
)


def _account(*, name: str = "Kalender") -> IntegrationInstance:
    return IntegrationInstance(
        name=name,
        integration_type="calendar",
        adapter_type="caldav",
        config={"base_url": "https://calendar.example.com", "username": "user"},
    )


def _revision(account: IntegrationInstance) -> str:
    return json.dumps(
        account.model_dump(mode="json"),
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


def _task(**changes) -> CalDavTask:
    values = {
        "uid": "task-1",
        "href": "https://calendar.example.com/home/tasks/task-1.ics",
        "etag": '"etag"',
        "title": "Bericht abgeben",
    }
    return CalDavTask(**(values | changes))


def _task_context(
    account: IntegrationInstance, revision: str, **values
) -> SimpleNamespace:
    return SimpleNamespace(
        bound_options={
            "integration_id": str(account.id),
            "binding_revision": revision,
        },
        integration_settings=IntegrationSettings(instances=[account]),
        **values,
    )


def _mock_task_client(monkeypatch, client, revision: str) -> None:
    @asynccontextmanager
    async def selected_client(_integration_id, _context):
        yield client, revision

    monkeypatch.setattr(caldav_module, "_selected_client", selected_client)
    monkeypatch.setattr(caldav_module, "_task_list", AsyncMock())


async def _updated_task(existing, draft) -> CalDavTask:
    return replace(
        existing,
        title=draft.title,
        description=draft.description,
        due=draft.due,
        completed=draft.completed,
        completed_at=draft.completed_at,
        created=draft.created,
        sequence=draft.sequence,
    )


async def test_resource_loader_maps_components_and_remote_privilege_limits(
    monkeypatch,
) -> None:
    discovery = CalDavDiscovery(
        "https://calendar.example.com/principal",
        "https://calendar.example.com/home/",
        (
            CalDavCalendar(
                key="a" * 32,
                href="https://calendar.example.com/home/private/",
                name="  Privat  ",
                privileges=frozenset({"read"}),
                supported_components=frozenset({"VEVENT"}),
            ),
            CalDavCalendar(
                key="b" * 32,
                href="https://calendar.example.com/home/tasks/",
                name="  Aufgaben  ",
                privileges=frozenset({"write", "delete"}),
                supported_components=frozenset({"VTODO"}),
            ),
            CalDavCalendar(
                key="c" * 32,
                href="https://calendar.example.com/home/mixed/",
                name="X" * 200,
                privileges=frozenset({"read", "write", "delete"}),
                supported_components=frozenset({"VEVENT", "VTODO"}),
            ),
        ),
    )
    client = AsyncMock()
    client.discover.return_value = discovery
    context = AsyncMock()
    context.__aenter__.return_value = client
    constructor = Mock(return_value=context)
    monkeypatch.setattr(caldav_module, "CalDavClient", constructor)

    resources = await load_caldav_resources(_account(), {"password": "secret"})

    constructor.assert_called_once_with(
        "https://calendar.example.com/", "user", "secret"
    )
    by_key = {resource.key: resource for resource in resources}
    assert set(by_key) == {
        f"calendar:{'a' * 32}",
        f"tasklist:{'b' * 32}",
        f"calendar:{'c' * 32}",
        f"tasklist:{'c' * 32}",
    }
    private = by_key[f"calendar:{'a' * 32}"]
    assert private.label == "Privat"
    assert private.applicable_capabilities == {
        "calendar.read",
        "calendar.write",
        "calendar.delete",
    }
    assert private.hard_unavailable_capabilities == {
        "calendar.write",
        "calendar.delete",
    }
    tasks = by_key[f"tasklist:{'b' * 32}"]
    assert tasks.label == "Aufgaben"
    assert tasks.applicable_capabilities == {
        "tasks.read",
        "tasks.write",
        "tasks.delete",
    }
    assert tasks.hard_unavailable_capabilities == {"tasks.read"}
    mixed_calendar = by_key[f"calendar:{'c' * 32}"]
    mixed_tasks = by_key[f"tasklist:{'c' * 32}"]
    assert mixed_calendar.group_key == mixed_tasks.group_key == "c" * 32
    assert mixed_calendar.key != mixed_tasks.key
    assert mixed_calendar.applicable_capabilities == {
        "calendar.read",
        "calendar.write",
        "calendar.delete",
    }
    assert mixed_tasks.applicable_capabilities == {
        "tasks.read",
        "tasks.write",
        "tasks.delete",
    }
    for key in (f"calendar:{'c' * 32}", f"tasklist:{'c' * 32}"):
        assert len(by_key[key].label) == 160
        assert by_key[key].hard_unavailable_capabilities == set()


def test_adapter_declares_stable_shared_tools_and_policy_modes() -> None:
    first = _account(name="Privat")
    second = _account(name="Arbeit")
    contributions = effective_integration_contributions(
        IntegrationSettings(instances=[first, second])
    )
    tools = {tool.name: tool for tool in contributions.tools}

    assert integration_adapter("calendar", "caldav") is CALDAV_ADAPTER
    assert set(tools) == {
        "calendar_list_accounts",
        "calendar_list_calendars",
        "calendar_query_events",
        "calendar_create_event",
        "calendar_update_event",
        "calendar_delete_event",
        "tasks_list_accounts",
        "tasks_list_lists",
        "tasks_query",
        "tasks_create",
        "tasks_create_many",
        "tasks_update",
        "tasks_set_completed",
        "tasks_delete",
    }
    assert tools["calendar_list_accounts"].instance_argument is None
    assert tools["calendar_list_accounts"].integration_ids == (
        str(first.id),
        str(second.id),
    )
    assert tools["calendar_list_calendars"].instance_argument == "integration_id"
    assert tools["calendar_query_events"].resource_argument == "calendar_key"
    assert tools["calendar_create_event"].resource_argument == "calendar_key"
    assert tools["calendar_update_event"].resource_argument == "calendar_key"
    assert tools["calendar_delete_event"].resource_argument == "calendar_key"
    assert tools["tasks_list_accounts"].instance_argument is None
    assert tools["tasks_list_lists"].instance_argument == "integration_id"
    for name in (
        "tasks_query",
        "tasks_create",
        "tasks_create_many",
        "tasks_update",
        "tasks_set_completed",
        "tasks_delete",
    ):
        assert tools[name].resource_argument == "task_list_key"
    discovery_permission = tools["calendar_list_calendars"].authorization_factory(
        CalendarIntegrationArguments(integration_id=first.id),
        SimpleNamespace(
            bound_options={
                "integration_id": str(first.id),
                "binding_revision": _revision(first),
            },
            integration_settings=IntegrationSettings(instances=[first, second]),
        ),
    )[0]
    assert discovery_permission.use_integration_policy is False
    assert discovery_permission.policy_integration_id is None
    assert json.loads(discovery_permission.resource_scope)["integrationId"] == str(first.id)
    actions = {action.capability: action for action in CALDAV_ADAPTER.resource_actions}
    assert actions["calendar.read"].default_policy == ToolExecutionPolicy.AUTO
    assert set(actions) == {
        "calendar.read",
        "calendar.write",
        "calendar.delete",
        "tasks.read",
        "tasks.write",
        "tasks.delete",
    }
    assert actions["tasks.read"].default_policy == ToolExecutionPolicy.AUTO
    assert actions["calendar.delete"].default_policy == ToolExecutionPolicy.ASK
    assert actions["tasks.delete"].default_policy == ToolExecutionPolicy.ASK
    create_schema = tools["calendar_create_event"].arguments_model.model_json_schema()
    assert "idempotency_key" not in create_schema["properties"]
    assert create_schema["properties"]["timing"]["discriminator"]["propertyName"] == "type"
    assert "timed values need timezone offsets" in create_schema["properties"]["timing"][
        "description"
    ]
    assert "allDay end is exclusive" in create_schema["properties"]["timing"]["description"]
    assert set(create_schema["$defs"]["TimedEventTiming"]["required"]) == {
        "type",
        "start",
        "end",
    }
    assert set(create_schema["$defs"]["AllDayEventTiming"]["required"]) == {
        "type",
        "start",
        "end",
    }
    assert actions["calendar.write"].mutates is True
    assert actions["calendar.delete"].mutates is True
    assert actions["tasks.write"].mutates is True
    assert actions["tasks.delete"].mutates is True
    with pytest.raises(ValueError):
        tools["calendar_create_event"].arguments_model.model_validate(
            {
                "integration_id": str(first.id),
                "calendar_key": f"calendar:{'a' * 32}",
                "summary": "Planung",
                "timing": {
                    "start": "2026-08-07T14:00:00+02:00",
                    "end": "2026-08-07T15:00:00+02:00",
                },
            }
        )


def test_task_schemas_accept_dates_or_aware_datetimes_and_default_to_open() -> None:
    account = _account()
    key = f"tasklist:{'a' * 32}"
    tools = {
        tool.name: tool
        for tool in effective_integration_contributions(
            IntegrationSettings(instances=[account])
        ).tools
    }
    query_model = tools["tasks_query"].arguments_model
    create_model = tools["tasks_create"].arguments_model
    query_schema = query_model.model_json_schema()
    create_schema = create_model.model_json_schema()

    assert query_schema["properties"]["include_completed"]["default"] is False
    assert (
        query_model(integration_id=account.id, task_list_key=key).include_completed
        is False
    )
    assert {
        option.get("format")
        for option in create_schema["properties"]["due"]["anyOf"]
        if option.get("format")
    } == {"date", "date-time"}
    assert create_model(
        integration_id=account.id,
        task_list_key=key,
        title="Abgabe",
        due=date(2026, 8, 8),
    ).due == date(2026, 8, 8)
    aware = datetime(2026, 8, 8, 10, tzinfo=UTC)
    assert create_model(
        integration_id=account.id,
        task_list_key=key,
        title="Abgabe",
        due=aware,
    ).due == aware
    with pytest.raises(ValueError):
        create_model(
            integration_id=account.id,
            task_list_key=key,
            title="Abgabe",
            due=datetime(2026, 8, 8, 10),
        )


def test_active_default_calendar_is_bound_only_to_query_and_create() -> None:
    account = _account()
    key = f"calendar:{'a' * 32}"
    settings = ToolSettings.model_validate(
        {
            "defaultCalendar": {
                "integrationId": str(account.id),
                "calendarKey": key,
            }
        }
    )

    effective = tool_registry.effective(
        IntegrationSettings(instances=[account]),
        tool_settings=settings,
    )

    assert len(effective.prompt_fragments) == 3
    assert effective.prompt_fragments[0].startswith("A default calendar is configured.")
    assert "call calendar_query_events or calendar_create_event directly" in (
        effective.prompt_fragments[0]
    )
    assert "omit both integration_id and calendar_key" in effective.prompt_fragments[0]
    assert "For another calendar, include both" in effective.prompt_fragments[0]
    assert "copy eventToken, integrationId, and calendarKey" in effective.prompt_fragments[1]
    assert "mutation succeeds only" in effective.prompt_fragments[1]
    assert "copy taskToken, integrationId, and taskListKey" in effective.prompt_fragments[2]
    for name in ("calendar_query_events", "calendar_create_event"):
        tool = effective.tools[name]
        assert tool.default_arguments == {
            "integration_id": str(account.id),
            "calendar_key": key,
        }
        required = tool.schema()["parameters"]["required"]
        assert "integration_id" not in required
        assert "calendar_key" not in required
    assert "offset times and an exclusive range end" in effective.tools[
        "calendar_query_events"
    ].definition.description

    assert effective.tools["calendar_list_calendars"].default_arguments == {}
    assert effective.tools["calendar_list_calendars"].schema()["parameters"][
        "properties"
    ]["integration_id"]["enum"] == [str(account.id)]
    assert effective.tools["calendar_update_event"].default_arguments == {}
    assert effective.tools["calendar_delete_event"].default_arguments == {}

    settings.default_calendar.integration_id = uuid4()
    without_active_default = tool_registry.effective(
        IntegrationSettings(instances=[account]),
        tool_settings=settings,
    )
    required = without_active_default.tools["calendar_query_events"].schema()[
        "parameters"
    ]["required"]
    assert "integration_id" in required
    assert "calendar_key" in required
    assert len(without_active_default.prompt_fragments) == 2
    assert "copy eventToken, integrationId, and calendarKey" in (
        without_active_default.prompt_fragments[0]
    )
    assert "copy taskToken, integrationId, and taskListKey" in (
        without_active_default.prompt_fragments[1]
    )


def test_active_default_task_list_is_bound_to_query_and_create_tools() -> None:
    account = _account()
    disabled = _account(name="Aus")
    disabled.enabled = False
    key = f"tasklist:{'a' * 32}"
    settings = ToolSettings.model_validate(
        {
            "defaultTaskList": {
                "integrationId": str(account.id),
                "taskListKey": key,
            }
        }
    )
    integrations = IntegrationSettings(instances=[account, disabled])

    effective = tool_registry.effective(
        integrations,
        tool_settings=settings,
    )

    assert any(
        fragment.startswith("A default task list is configured.")
        for fragment in effective.prompt_fragments
    )
    task_fragment = next(
        fragment
        for fragment in effective.prompt_fragments
        if fragment.startswith("A default task list is configured.")
    )
    assert "omit both integration_id and task_list_key" in task_fragment
    assert "For another task list, include both" in task_fragment
    for name in ("tasks_query", "tasks_create", "tasks_create_many"):
        tool = effective.tools[name]
        assert tool.default_arguments == {
            "integration_id": str(account.id),
            "task_list_key": key,
        }
        required = tool.schema()["parameters"]["required"]
        assert "integration_id" not in required
        assert "task_list_key" not in required

    create_parameters = effective.tools["tasks_create"].schema()["parameters"]
    assert create_parameters["properties"]["title"]["minLength"] == 1
    assert create_parameters["properties"]["title"]["maxLength"] == 500
    assert "title" in create_parameters["required"]
    update_parameters = effective.tools["tasks_update"].schema()["parameters"]
    assert "title" in update_parameters["properties"]
    assert "title" not in update_parameters["required"]

    for name in (
        "tasks_list_accounts",
        "tasks_list_lists",
        "tasks_update",
        "tasks_set_completed",
        "tasks_delete",
    ):
        assert effective.tools[name].default_arguments == {}
    assert "integration_id" in effective.tools["tasks_list_lists"].schema()[
        "parameters"
    ]["required"]
    for name in ("tasks_update", "tasks_set_completed", "tasks_delete"):
        required = effective.tools[name].schema()["parameters"]["required"]
        assert "integration_id" in required
        assert "task_list_key" in required

    for integration_id in (disabled.id, uuid4()):
        settings.default_task_list.integration_id = integration_id
        unbound = tool_registry.effective(
            integrations,
            tool_settings=settings,
        )
        assert not any(
            fragment.startswith("A default task list is configured.")
            for fragment in unbound.prompt_fragments
        )
        for name in ("tasks_query", "tasks_create", "tasks_create_many"):
            assert unbound.tools[name].default_arguments == {}
            assert {"integration_id", "task_list_key"} <= set(
                unbound.tools[name].schema()["parameters"]["required"]
            )


def test_calendar_permissions_describe_the_requested_action() -> None:
    account = _account(name="Arbeit")
    revision = _revision(account)
    key = f"calendar:{'a' * 32}"
    context = SimpleNamespace(
        bound_options={
            "integration_id": str(account.id),
            "binding_revision": revision,
        },
        integration_settings=IntegrationSettings(instances=[account]),
        tool_settings=ToolSettings(
            resource_labels={str(account.id): {key: "Teamkalender"}}
        ),
    )
    timing = caldav_module.TimedEventTiming(
        type="timed",
        start=datetime(2026, 8, 7, 14, tzinfo=UTC),
        end=datetime(2026, 8, 7, 15, tzinfo=UTC),
    )
    create = caldav_module._create_permission(
        caldav_module.CalendarCreateEventArguments(
            integration_id=account.id,
            calendar_key=key,
            summary="Projektbesprechung",
            description="Nächste Schritte abstimmen",
            location="Raum 2",
            timing=timing,
        ),
        context,
    )[0]

    assert create.resource_display == '"Teamkalender" · Konto "Arbeit"'
    assert create.summary == (
        'Der Agent möchte den Termin "Projektbesprechung" am '
        "07.08.2026, 14:00 bis 15:00 Uhr (UTC+00:00) erstellen. "
        'Ort: "Raum 2". Beschreibung: "Nächste Schritte abstimmen".'
    )

    event = CalDavEvent(
        uid="uid",
        href="https://calendar.example.com/event.ics",
        etag='"etag"',
        summary="Alter Titel",
        start=date(2026, 8, 8),
        end=date(2026, 8, 9),
        all_day=True,
        description="Alt",
        location="Büro",
    )
    token = caldav_module._token(event, account.id, revision, key)
    update = caldav_module._update_permission(
        CalendarUpdateEventArguments(
            integration_id=account.id,
            calendar_key=key,
            event_token=token,
            location=None,
        ),
        context,
    )[0]
    delete = caldav_module._delete_permission(
        caldav_module.CalendarDeleteEventArguments(
            integration_id=account.id,
            calendar_key=key,
            event_token=token,
        ),
        context,
    )[0]

    assert update.summary == (
        'Der Agent möchte den Termin "Alter Titel" am 08.08.2026 (ganztägig) '
        "ändern: Ort entfernen."
    )
    assert delete.summary == (
        'Der Agent möchte den Termin "Alter Titel" am 08.08.2026 (ganztägig) löschen.'
    )


async def test_calendar_listing_remembers_resource_labels(monkeypatch) -> None:
    account = _account()
    revision = _revision(account)
    discovery = CalDavDiscovery(
        "https://calendar.example.com/principal",
        "https://calendar.example.com/home/",
        (
            CalDavCalendar(
                key="a" * 32,
                href="https://calendar.example.com/home/private/",
                name="  Privat  ",
                privileges=frozenset({"read"}),
            ),
        ),
    )
    client = AsyncMock()
    client.discover.return_value = discovery

    @asynccontextmanager
    async def selected_client(_integration_id, _context):
        yield client, revision

    tools = ToolSettings()
    load = AsyncMock(return_value=tools)
    save = AsyncMock()
    monkeypatch.setattr(caldav_module, "_selected_client", selected_client)
    monkeypatch.setattr(caldav_module.SettingsStore, "load", load)
    monkeypatch.setattr(caldav_module.SettingsStore, "save", save)
    context = SimpleNamespace(
        db=AsyncMock(spec=AsyncSession),
        bound_options={
            "integration_id": str(account.id),
            "binding_revision": revision,
        },
        integration_settings=IntegrationSettings(instances=[account]),
        tool_settings=ToolSettings(),
    )

    result = await caldav_module.calendar_list_calendars(
        CalendarIntegrationArguments(integration_id=account.id), context
    )

    payload = json.loads(result.content)
    assert payload["integrationId"] == str(account.id)
    assert payload["calendars"][0]["name"] == "Privat"
    assert tools.resource_labels == {
        str(account.id): {f"calendar:{'a' * 32}": "Privat"}
    }
    load.assert_awaited_once_with(context.db, ToolSettings, for_update=True)
    save.assert_awaited_once_with(context.db, tools, user_id=None)


def test_task_permissions_use_task_capabilities_and_exact_task_list_scope() -> None:
    account = _account(name="Arbeit")
    revision = _revision(account)
    key = f"tasklist:{'a' * 32}"
    task = _task(due=date(2026, 8, 8))
    token = caldav_module._task_token(task, account.id, revision, key)
    context = _task_context(
        account,
        revision,
        tool_settings=ToolSettings(
            resource_labels={str(account.id): {key: "Teamaufgaben"}}
        ),
    )
    tools = {
        tool.name: tool
        for tool in effective_integration_contributions(
            IntegrationSettings(instances=[account])
        ).tools
    }

    accounts = tools["tasks_list_accounts"].authorization_factory(None, context)[0]
    listing = tools["tasks_list_lists"].authorization_factory(
        caldav_module.TaskIntegrationArguments(integration_id=account.id), context
    )[0]
    query = tools["tasks_query"].authorization_factory(
        caldav_module.TaskQueryArguments(
            integration_id=account.id,
            task_list_key=key,
        ),
        context,
    )[0]
    create = tools["tasks_create"].authorization_factory(
        caldav_module.TaskCreateArguments(
            integration_id=account.id,
            task_list_key=key,
            title="Bericht abgeben",
        ),
        context,
    )[0]
    create_many = tools["tasks_create_many"].authorization_factory(
        caldav_module.TaskCreateManyArguments(
            integration_id=account.id,
            task_list_key=key,
            tasks=[
                caldav_module.TaskBatchItem(title="Milch"),
                caldav_module.TaskBatchItem(title="Brot"),
            ],
        ),
        context,
    )[0]
    update = tools["tasks_update"].authorization_factory(
        caldav_module.TaskUpdateArguments(
            integration_id=account.id,
            task_list_key=key,
            task_token=token,
            title="Bericht versenden",
            due=None,
        ),
        context,
    )[0]
    completed = tools["tasks_set_completed"].authorization_factory(
        caldav_module.TaskSetCompletedArguments(
            integration_id=account.id,
            task_list_key=key,
            task_token=token,
            completed=True,
        ),
        context,
    )[0]
    delete = tools["tasks_delete"].authorization_factory(
        caldav_module.TaskDeleteArguments(
            integration_id=account.id,
            task_list_key=key,
            task_token=token,
        ),
        context,
    )[0]

    assert accounts.capability == "tasks.read"
    assert accounts.resource_scope == "task_accounts"
    assert listing.use_integration_policy is False
    assert "resourceKey" not in json.loads(listing.resource_scope)
    expected_scope = {
        "bindingRevision": hashlib.sha256(revision.encode()).hexdigest(),
        "integrationId": str(account.id),
        "operationKey": "query-tasks",
        "resourceKey": key,
    }
    assert query.resource_scope == json.dumps(
        expected_scope, ensure_ascii=True, separators=(",", ":"), sort_keys=True
    )
    assert query.policy_resource_key == key
    assert query.resource_display == '"Teamaufgaben" · Konto "Arbeit"'
    assert query.available_decisions == ("allow_once", "allow_session", "deny")
    assert json.loads(query.effective_session_grants[0].resource_scope)["resourceKey"] == key
    assert create.capability == "tasks.write"
    assert delete.capability == "tasks.delete"
    for permission in (create, create_many, update, completed, delete):
        assert json.loads(permission.resource_scope)["resourceKey"] == key
        assert permission.policy_resource_key == key
        assert permission.available_decisions == ("allow_once", "allow_session", "deny")
        assert permission.effective_session_grants[0].access == "write"
    assert [(item.label, item.value) for item in create.display_items] == [
        ("Aktion", "Aufgabe erstellen"),
        ("Aufgabe", "Bericht abgeben"),
        ("Fälligkeit", "Keine Fälligkeit"),
    ]
    assert [(item.label, item.value) for item in create_many.display_items] == [
        ("Aktion", "Fehlende Aufgaben erstellen"),
        ("Aufgaben", ("Milch", "Brot")),
    ]
    assert [(item.label, item.value) for item in update.display_items] == [
        ("Aktion", "Aufgabe ändern"),
        ("Aufgabe", "Bericht abgeben"),
        (
            "Änderungen",
            ('Titel: "Bericht versenden"', "Fälligkeit entfernen"),
        ),
    ]
    assert [(item.label, item.value) for item in completed.display_items] == [
        ("Aktion", "Aufgabenstatus ändern"),
        ("Aufgabe", "Bericht abgeben"),
        ("Neuer Status", "Erledigt"),
    ]
    assert [(item.label, item.value) for item in delete.display_items] == [
        ("Aktion", "Aufgabe löschen"),
        ("Aufgabe", "Bericht abgeben"),
    ]


async def test_account_list_uses_only_bound_active_caldav_snapshot() -> None:
    first = _account(name="Privat")
    second = _account(name="Arbeit")
    disabled = _account(name="Aus")
    disabled.enabled = False
    context = SimpleNamespace(
        bound_options={"integration_ids": json.dumps([str(first.id), str(disabled.id)])},
        integration_settings=IntegrationSettings(
            instances=[first, second, disabled]
        ),
    )

    result = await calendar_list_accounts(CalendarListAccountsArguments(), context)

    assert json.loads(result.content) == {
        "accounts": [{"integrationId": str(first.id), "name": "Privat"}],
        "contentIsUntrusted": True,
    }


async def test_calendar_query_identifies_empty_selected_calendar(monkeypatch) -> None:
    account = _account()
    revision = _revision(account)
    key = f"calendar:{'a' * 32}"
    calendar = CalDavCalendar(
        "a" * 32,
        "https://calendar.example.com/home/private/",
        "Privat",
        frozenset({"read"}),
    )
    client = AsyncMock()
    client.query_events.return_value = ()

    @asynccontextmanager
    async def selected_client(_integration_id, _context):
        yield client, revision

    monkeypatch.setattr(caldav_module, "_selected_client", selected_client)
    monkeypatch.setattr(caldav_module, "_calendar", AsyncMock(return_value=calendar))

    result = await caldav_module.calendar_query_events(
        caldav_module.CalendarQueryEventsArguments(
            integration_id=account.id,
            calendar_key=key,
            start=datetime(2026, 8, 19, tzinfo=UTC),
            end=datetime(2026, 8, 20, tzinfo=UTC),
        ),
        SimpleNamespace(),
    )

    assert json.loads(result.content) == {
        "calendar": {
            "integrationId": str(account.id),
            "calendarKey": key,
            "name": "Privat",
        },
        "events": [],
        "contentIsUntrusted": True,
    }


async def test_create_uses_a_stable_host_generated_idempotency_key(monkeypatch) -> None:
    account = _account()
    revision = _revision(account)
    key = f"calendar:{'a' * 32}"
    assistant_message_id = uuid4()
    run_id = uuid4()
    client = AsyncMock()
    client.create_event.return_value = CalDavEvent(
        uid="uid",
        href="https://calendar.example.com/home/private/event.ics",
        etag='"etag"',
        summary="Planung",
        start=datetime(2026, 8, 7, 18, tzinfo=UTC),
        end=datetime(2026, 8, 7, 19, tzinfo=UTC),
        all_day=False,
    )

    @asynccontextmanager
    async def selected_client(_integration_id, _context):
        yield client, revision

    monkeypatch.setattr(caldav_module, "_selected_client", selected_client)
    monkeypatch.setattr(
        caldav_module,
        "_calendar",
        AsyncMock(
            return_value=CalDavCalendar(
                "a" * 32,
                "https://calendar.example.com/home/private/",
                "Privat",
                frozenset({"read", "write", "delete"}),
            )
        ),
    )
    context = SimpleNamespace(
        assistant_message_id=assistant_message_id,
        run_id=run_id,
        tool_call=SimpleNamespace(name="calendar_create_event", id="call-1"),
    )
    arguments = caldav_module.CalendarCreateEventArguments(
        integration_id=account.id,
        calendar_key=key,
        summary="Planung",
        timing=caldav_module.TimedEventTiming(
            type="timed",
            start=datetime(2026, 8, 7, 20, tzinfo=UTC),
            end=datetime(2026, 8, 7, 21, tzinfo=UTC),
        ),
    )

    result = await caldav_module.calendar_create_event(arguments, context)

    assert result.is_error is False
    assert client.create_event.await_args.args[2] == uuid5(
        assistant_message_id, "calendar_create_event:call-1"
    )
    payload = json.loads(result.content)
    assert payload["event"]["timing"]["start"] == "2026-08-07T18:00:00Z"
    assert payload["event"]["localTiming"] == {
        "timeZone": "Europe/Berlin",
        "start": "2026-08-07T20:00:00+02:00",
        "end": "2026-08-07T21:00:00+02:00",
        "startWeekday": "Friday",
        "endWeekday": "Friday",
    }


async def test_task_query_hides_remote_fields_and_returns_binding_token(
    monkeypatch,
) -> None:
    account = _account()
    revision = _revision(account)
    key = f"tasklist:{'a' * 32}"
    task = _task(
        uid="remote-secret-task-uid",
        href="https://calendar.example.com/home/tasks/remote-secret-task.ics",
        etag='"remote-secret-etag"',
        description="Bis Mittag",
        due=datetime(2026, 8, 8, 10, tzinfo=UTC),
    )
    client = AsyncMock()
    client.query_tasks.return_value = (task,)
    _mock_task_client(monkeypatch, client, revision)
    result = await caldav_module.tasks_query(
        caldav_module.TaskQueryArguments(
            integration_id=account.id,
            task_list_key=key,
        ),
        _task_context(account, revision),
    )

    payload = json.loads(result.content)
    returned = payload["tasks"][0]
    assert set(returned) == {
        "taskToken",
        "integrationId",
        "taskListKey",
        "title",
        "description",
        "due",
        "dueIsFloating",
        "completed",
        "completedAt",
        "recurring",
    }
    assert "remote-secret" not in result.content
    assert client.query_tasks.await_args.kwargs == {"include_completed": False}
    decrypted = caldav_module.CredentialCipher().decrypt(
        returned["taskToken"].encode("ascii")
    )
    assert decrypted["integration_id"] == str(account.id)
    assert decrypted["binding_revision"] == revision
    assert decrypted["task_list_key"] == key
    assert decrypted["uid"] == task.uid
    assert decrypted["href"] == task.href
    assert decrypted["etag"] == task.etag


async def test_task_create_uses_one_stable_host_idempotency_key(monkeypatch) -> None:
    account = _account()
    revision = _revision(account)
    key = f"tasklist:{'a' * 32}"
    assistant_message_id = uuid4()
    client = AsyncMock()
    client.create_task.return_value = _task(due=date(2026, 8, 8))
    _mock_task_client(monkeypatch, client, revision)
    context = _task_context(
        account,
        revision,
        assistant_message_id=assistant_message_id,
        run_id=uuid4(),
        tool_call=SimpleNamespace(name="tasks_create", id="call-1"),
    )
    result = await caldav_module.tasks_create(
        caldav_module.TaskCreateArguments(
            integration_id=account.id,
            task_list_key=key,
            title="Bericht abgeben",
            due=date(2026, 8, 8),
        ),
        context,
    )

    assert result.is_error is False
    client.create_task.assert_awaited_once()
    assert client.create_task.await_args.args[2] == uuid5(
        assistant_message_id, "tasks_create:call-1"
    )
    assert client.create_task.await_args.args[1].due == date(2026, 8, 8)


async def test_task_create_many_skips_existing_and_duplicate_open_titles(
    monkeypatch,
) -> None:
    account = _account()
    revision = _revision(account)
    key = f"tasklist:{'a' * 32}"
    assistant_message_id = uuid4()
    client = AsyncMock()
    client.query_tasks.return_value = (_task(title="Milch"),)
    client.create_task.return_value = _task(uid="bread", title="Brot")
    _mock_task_client(monkeypatch, client, revision)
    context = _task_context(
        account,
        revision,
        assistant_message_id=assistant_message_id,
        run_id=uuid4(),
        tool_call=SimpleNamespace(name="tasks_create_many", id="call-2"),
    )

    result = await caldav_module.tasks_create_many(
        caldav_module.TaskCreateManyArguments(
            integration_id=account.id,
            task_list_key=key,
            tasks=[
                caldav_module.TaskBatchItem(title="  MILCH "),
                caldav_module.TaskBatchItem(title="Brot"),
                caldav_module.TaskBatchItem(title=" brot "),
            ],
        ),
        context,
    )

    payload = json.loads(result.content)
    assert [task["title"] for task in payload["created"]] == ["Brot"]
    assert payload["skippedExistingTitles"] == ["  MILCH ", " brot "]
    client.query_tasks.assert_awaited_once_with(
        caldav_module._task_list.return_value, include_completed=False
    )
    client.create_task.assert_awaited_once()
    assert client.create_task.await_args.args[2] == uuid5(
        assistant_message_id, "tasks_create_many:call-2:1"
    )


async def test_task_create_many_reports_known_partial_effects(monkeypatch) -> None:
    account = _account()
    revision = _revision(account)
    key = f"tasklist:{'a' * 32}"
    client = AsyncMock()
    client.query_tasks.return_value = ()
    client.create_task.side_effect = [
        _task(uid="milk", title="Milch"),
        caldav_module.CalDavError("caldav.outcomeUnknown"),
    ]
    _mock_task_client(monkeypatch, client, revision)
    context = _task_context(
        account,
        revision,
        assistant_message_id=uuid4(),
        run_id=uuid4(),
        tool_call=SimpleNamespace(name="tasks_create_many", id="call-3"),
    )

    result = await caldav_module.tasks_create_many(
        caldav_module.TaskCreateManyArguments(
            integration_id=account.id,
            task_list_key=key,
            tasks=[
                caldav_module.TaskBatchItem(title="Milch"),
                caldav_module.TaskBatchItem(title="Brot"),
            ],
        ),
        context,
    )

    error = json.loads(result.content)["error"]
    assert error["code"] == "integration.outcomeUnknown"
    assert error["params"] == {
        "createdTitles": ["Milch"],
        "skippedExistingTitles": [],
        "failedTitle": "Brot",
        "causeCode": "integration.outcomeUnknown",
    }


async def test_update_token_is_binding_scoped_and_preserves_omitted_fields(
    monkeypatch,
) -> None:
    account = _account()
    revision = _revision(account)
    key = f"calendar:{'a' * 32}"
    event = CalDavEvent(
        uid="internal-uid",
        href="https://calendar.example.com/home/private/event.ics",
        etag='"etag"',
        summary="Alt",
        start=date(2026, 8, 6),
        end=date(2026, 8, 7),
        all_day=True,
        description="Beschreibung",
        location="Büro",
    )
    token = caldav_module._token(event, account.id, revision, key)
    client = AsyncMock()
    client.update_event.return_value = replace(event, description=None)

    @asynccontextmanager
    async def selected_client(_integration_id, _context):
        yield client, revision

    monkeypatch.setattr(caldav_module, "_selected_client", selected_client)
    monkeypatch.setattr(
        caldav_module,
        "_calendar",
        AsyncMock(
            return_value=CalDavCalendar(
                "a" * 32,
                "https://calendar.example.com/home/private/",
                "Privat",
                frozenset({"read", "write", "delete"}),
            )
        ),
    )
    context = SimpleNamespace(
        bound_options={
            "integration_id": str(account.id),
            "binding_revision": revision,
        },
        integration_settings=IntegrationSettings(instances=[account]),
    )
    arguments = CalendarUpdateEventArguments(
        integration_id=account.id,
        calendar_key=key,
        event_token=token,
        description=None,
    )

    result = await calendar_update_event(arguments, context)

    draft = client.update_event.await_args.args[1]
    assert draft.summary == "Alt"
    assert draft.description is None
    assert draft.location == "Büro"
    payload = json.loads(result.content)
    assert payload["contentIsUntrusted"] is True
    assert payload["event"]["integrationId"] == str(account.id)
    assert payload["event"]["calendarKey"] == key
    assert "href" not in result.content
    assert "etag" not in result.content.lower()
    assert "internal-uid" not in result.content

    decrypted = caldav_module.CredentialCipher().decrypt(token.encode("ascii"))
    assert decrypted["version"] == 2
    decrypted.pop("version")
    legacy_token = caldav_module.CredentialCipher().encrypt(decrypted).decode("ascii")
    legacy = await calendar_update_event(
        arguments.model_copy(update={"event_token": legacy_token}), context
    )
    assert legacy.is_error is True
    assert legacy.metadata["errorCode"] == "integration.staleReference"

    wrong_binding = SimpleNamespace(
        bound_options={
            "integration_id": str(uuid4()),
            "binding_revision": revision,
        },
        integration_settings=context.integration_settings,
    )
    invalid = await calendar_update_event(arguments, wrong_binding)
    assert invalid.is_error is True


async def test_task_update_preserves_omitted_and_clears_explicit_fields(
    monkeypatch,
) -> None:
    account = _account()
    revision = _revision(account)
    key = f"tasklist:{'a' * 32}"
    task = _task(
        title="Alt",
        description="Beschreibung",
        due=date(2026, 8, 8),
        created=datetime(2026, 8, 1, 8, tzinfo=UTC),
        sequence=4,
    )
    token = caldav_module._task_token(task, account.id, revision, key)
    client = AsyncMock()
    client.update_task.side_effect = _updated_task

    _mock_task_client(monkeypatch, client, revision)
    context = _task_context(account, revision)

    preserved = await caldav_module.tasks_update(
        caldav_module.TaskUpdateArguments(
            integration_id=account.id,
            task_list_key=key,
            task_token=token,
            title="Neu",
        ),
        context,
    )
    preserved_draft = client.update_task.await_args.args[1]
    cleared = await caldav_module.tasks_update(
        caldav_module.TaskUpdateArguments(
            integration_id=account.id,
            task_list_key=key,
            task_token=json.loads(preserved.content)["task"]["taskToken"],
            description=None,
            due=None,
        ),
        context,
    )
    cleared_draft = client.update_task.await_args.args[1]

    assert preserved_draft.title == "Neu"
    assert preserved_draft.description == "Beschreibung"
    assert preserved_draft.due == date(2026, 8, 8)
    assert preserved_draft.created == datetime(2026, 8, 1, 8, tzinfo=UTC)
    assert preserved_draft.sequence == 5
    assert cleared.is_error is False
    assert cleared_draft.title == "Neu"
    assert cleared_draft.description is None
    assert cleared_draft.due is None


async def test_task_completion_sets_timestamp_and_reopen_clears_it(monkeypatch) -> None:
    account = _account()
    revision = _revision(account)
    key = f"tasklist:{'a' * 32}"
    task = _task()
    token = caldav_module._task_token(task, account.id, revision, key)
    client = AsyncMock()
    client.update_task.side_effect = _updated_task

    _mock_task_client(monkeypatch, client, revision)
    context = _task_context(account, revision)
    before = datetime.now(UTC)

    completed = await caldav_module.tasks_set_completed(
        caldav_module.TaskSetCompletedArguments(
            integration_id=account.id,
            task_list_key=key,
            task_token=token,
            completed=True,
        ),
        context,
    )
    completed_draft = client.update_task.await_args.args[1]
    reopened = await caldav_module.tasks_set_completed(
        caldav_module.TaskSetCompletedArguments(
            integration_id=account.id,
            task_list_key=key,
            task_token=json.loads(completed.content)["task"]["taskToken"],
            completed=False,
        ),
        context,
    )
    reopened_draft = client.update_task.await_args.args[1]

    assert completed_draft.completed is True
    assert before <= completed_draft.completed_at <= datetime.now(UTC)
    assert reopened.is_error is False
    assert reopened_draft.completed is False
    assert reopened_draft.completed_at is None


async def test_task_delete_uses_token_target(monkeypatch) -> None:
    account = _account()
    revision = _revision(account)
    key = f"tasklist:{'a' * 32}"
    task = _task()
    token = caldav_module._task_token(task, account.id, revision, key)
    client = AsyncMock()
    _mock_task_client(monkeypatch, client, revision)
    context = _task_context(account, revision)

    result = await caldav_module.tasks_delete(
        caldav_module.TaskDeleteArguments(
            integration_id=account.id,
            task_list_key=key,
            task_token=token,
        ),
        context,
    )

    assert json.loads(result.content) == {
        "deleted": True,
        "contentIsUntrusted": True,
    }
    client.delete_task.assert_awaited_once_with(task)


async def test_task_mutation_rejects_invalid_or_wrong_binding_tokens() -> None:
    account = _account()
    revision = _revision(account)
    key = f"tasklist:{'a' * 32}"
    task = _task()
    context = _task_context(account, revision)

    invalid = await caldav_module.tasks_delete(
        caldav_module.TaskDeleteArguments.model_construct(
            integration_id=account.id,
            task_list_key=key,
            task_token="invalid",
        ),
        context,
    )
    wrong_binding = await caldav_module.tasks_delete(
        caldav_module.TaskDeleteArguments(
            integration_id=account.id,
            task_list_key=key,
            task_token=caldav_module._task_token(task, uuid4(), revision, key),
        ),
        context,
    )

    assert invalid.metadata == {"errorCode": "tool.invalidArguments"}
    assert wrong_binding.metadata == {"errorCode": "tool.invalidArguments"}


def test_timed_event_schema_requires_aware_datetimes() -> None:
    with pytest.raises(ValueError):
        caldav_module.TimedEventTiming(
            type="timed",
            start=datetime(2026, 8, 6, 9),
            end=datetime(2026, 8, 6, 10),
        )
    timing = caldav_module.TimedEventTiming(
        type="timed",
        start=datetime(2026, 8, 6, 9, tzinfo=UTC),
        end=datetime(2026, 8, 6, 10, tzinfo=UTC),
    )
    assert timing.end > timing.start
