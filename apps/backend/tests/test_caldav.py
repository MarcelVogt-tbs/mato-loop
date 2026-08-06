import json
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, date, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

import mato_loop_backend.integrations.caldav as caldav_module
from mato_loop_backend.integrations.caldav import (
    CALDAV_ADAPTER,
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
from mato_loop_backend.settings import IntegrationInstance, IntegrationSettings


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
    assert create_schema["properties"]["timing"]["discriminator"]["propertyName"] == "type"


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
    assert "href" not in result.content
    assert "etag" not in result.content.lower()
    assert "internal-uid" not in result.content

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
