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
)
from mato_loop_backend.integrations.registry import (
    effective_integration_contributions,
    integration_adapter,
)
from mato_loop_backend.settings import IntegrationInstance, IntegrationSettings, ToolSettings


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


async def test_resource_loader_maps_only_remote_privilege_limits(monkeypatch) -> None:
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
            CalDavCalendar(
                key="b" * 32,
                href="https://calendar.example.com/home/work/",
                name="X" * 200,
                privileges=frozenset({"read", "write", "delete"}),
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
    assert resources[0].key == f"calendar:{'a' * 32}"
    assert resources[0].label == "Privat"
    assert resources[0].hard_unavailable_capabilities == {
        "calendar.write",
        "calendar.delete",
    }
    assert len(resources[1].label) == 160
    assert resources[1].hard_unavailable_capabilities == set()


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
    }
    assert all(tool.write is False for tool in tools.values())
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
    assert [item.value for item in actions["calendar.read"].allowed_policies] == [
        "disabled",
        "ask",
        "auto",
    ]
    assert [item.value for item in actions["calendar.delete"].allowed_policies] == [
        "disabled",
        "ask",
    ]
    create_schema = tools["calendar_create_event"].arguments_model.model_json_schema()
    assert "idempotency_key" not in create_schema["properties"]
    assert create_schema["properties"]["timing"]["discriminator"]["propertyName"] == "type"
    assert '"type":"timed"' in create_schema["properties"]["timing"]["description"]
    assert '"type":"allDay"' in create_schema["properties"]["timing"]["description"]
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
        "read-only",
        tool_settings=settings,
    )

    assert len(effective.prompt_fragments) == 2
    assert effective.prompt_fragments[0].startswith("A default calendar is configured.")
    assert "call calendar_query_events or calendar_create_event directly" in (
        effective.prompt_fragments[0]
    )
    assert "copy eventToken, integrationId, and calendarKey" in effective.prompt_fragments[1]
    assert "mutation succeeds only" in effective.prompt_fragments[1]
    for name in ("calendar_query_events", "calendar_create_event"):
        tool = effective.tools[name]
        assert tool.default_arguments == {
            "integration_id": str(account.id),
            "calendar_key": key,
        }
        required = tool.schema()["parameters"]["required"]
        assert "integration_id" not in required
        assert "calendar_key" not in required

    assert effective.tools["calendar_list_calendars"].default_arguments == {}
    assert effective.tools["calendar_list_calendars"].schema()["parameters"][
        "properties"
    ]["integration_id"]["enum"] == [str(account.id)]
    assert effective.tools["calendar_update_event"].default_arguments == {}
    assert effective.tools["calendar_delete_event"].default_arguments == {}

    settings.default_calendar.integration_id = uuid4()
    without_active_default = tool_registry.effective(
        IntegrationSettings(instances=[account]),
        "read-only",
        tool_settings=settings,
    )
    required = without_active_default.tools["calendar_query_events"].schema()[
        "parameters"
    ]["required"]
    assert "integration_id" in required
    assert "calendar_key" in required
    assert len(without_active_default.prompt_fragments) == 1
    assert "copy eventToken, integrationId, and calendarKey" in (
        without_active_default.prompt_fragments[0]
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

    assert json.loads(result.content)["calendars"][0]["name"] == "Privat"
    assert tools.resource_labels == {
        str(account.id): {f"calendar:{'a' * 32}": "Privat"}
    }
    load.assert_awaited_once_with(context.db, ToolSettings, for_update=True)
    save.assert_awaited_once_with(context.db, tools)


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
    assert legacy.metadata["errorCode"] == "tool.invalidArguments"

    wrong_binding = SimpleNamespace(
        bound_options={
            "integration_id": str(uuid4()),
            "binding_revision": revision,
        },
        integration_settings=context.integration_settings,
    )
    invalid = await calendar_update_event(arguments, wrong_binding)
    assert invalid.is_error is True


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
