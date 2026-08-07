from __future__ import annotations

import hashlib
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta
from typing import Annotated, Any, Literal
from uuid import UUID, uuid5
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator, model_validator

from mato_loop_backend.agent.tool_contracts import ToolAction
from mato_loop_backend.integrations.caldav_client import (
    CalDavCalendar,
    CalDavClient,
    CalDavError,
    CalDavEvent,
    CalDavEventDraft,
)
from mato_loop_backend.integrations.contracts import (
    IntegrationAdapterDefinition,
    IntegrationContributions,
    IntegrationResource,
    ToolContribution,
)
from mato_loop_backend.models import Credential
from mato_loop_backend.security import CredentialCipher, read_credential
from mato_loop_backend.settings import (
    IntegrationInstance,
    SettingsStore,
    ToolExecutionPolicy,
    ToolSettings,
)

CALDAV_CREDENTIAL_KIND = "caldav.password"
_LOCAL_TIMEZONE = ZoneInfo("Europe/Berlin")
_WEEKDAYS = (
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
)
_READ = ToolAction(
    "calendar.read",
    "Kalender lesen",
    ToolExecutionPolicy.ASK,
)
_WRITE = ToolAction(
    "calendar.write",
    "Kalender ändern",
    ToolExecutionPolicy.ASK,
    mutates=True,
)
_DELETE = ToolAction(
    "calendar.delete",
    "Kalendereinträge löschen",
    ToolExecutionPolicy.ASK,
    allowed_policies=(ToolExecutionPolicy.DISABLED, ToolExecutionPolicy.ASK),
    mutates=True,
)


class CalDavConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_url: HttpUrl
    username: str = Field(min_length=1, max_length=320)

    @model_validator(mode="after")
    def require_secure_url(self) -> CalDavConfig:
        if (
            self.base_url.scheme != "https"
            or self.base_url.username is not None
            or self.base_url.password is not None
            or self.base_url.query is not None
            or self.base_url.fragment is not None
        ):
            raise ValueError("CalDAV URL must be a secure endpoint without credentials")
        return self


class _Arguments(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CalendarListAccountsArguments(_Arguments):
    pass


class CalendarIntegrationArguments(_Arguments):
    integration_id: UUID


class CalendarResourceArguments(CalendarIntegrationArguments):
    calendar_key: str = Field(pattern=r"^calendar:[a-f0-9]{32}$")


class CalendarQueryEventsArguments(CalendarResourceArguments):
    start: datetime
    end: datetime
    limit: int = Field(default=50, ge=1, le=50)

    @model_validator(mode="after")
    def validate_range(self) -> CalendarQueryEventsArguments:
        if (
            self.start.tzinfo is None
            or self.start.utcoffset() is None
            or self.end.tzinfo is None
            or self.end.utcoffset() is None
            or self.end <= self.start
        ):
            raise ValueError("start and end must be aware and increasing")
        return self


class TimedEventTiming(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["timed"]
    start: datetime
    end: datetime

    @model_validator(mode="after")
    def validate_range(self) -> TimedEventTiming:
        if (
            self.start.tzinfo is None
            or self.start.utcoffset() is None
            or self.end.tzinfo is None
            or self.end.utcoffset() is None
            or self.end <= self.start
        ):
            raise ValueError("start and end must be aware and increasing")
        return self


class AllDayEventTiming(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["allDay"]
    start: date
    end: date

    @model_validator(mode="after")
    def validate_range(self) -> AllDayEventTiming:
        if self.end <= self.start:
            raise ValueError("end must be after start")
        return self


EventTiming = Annotated[
    TimedEventTiming | AllDayEventTiming,
    Field(
        discriminator="type",
        description=(
            'Every timing object requires type. Timed example: {"type":"timed",'
            '"start":"2026-08-07T14:00:00+02:00","end":"2026-08-07T15:00:00+02:00"}. '
            'All-day example: {"type":"allDay","start":"2026-08-07",'
            '"end":"2026-08-08"}; the all-day end date is exclusive.'
        ),
    ),
]


class CalendarCreateEventArguments(CalendarResourceArguments):
    summary: str = Field(min_length=1, max_length=500)
    description: str | None = Field(default=None, max_length=10_000)
    location: str | None = Field(default=None, max_length=1_000)
    timing: EventTiming


class CalendarEventArguments(CalendarResourceArguments):
    event_token: str = Field(min_length=1, max_length=50_000)

    @field_validator("event_token")
    @classmethod
    def validate_event_token(cls, value: str) -> str:
        try:
            _EventToken.model_validate(CredentialCipher().decrypt(value.encode("ascii")))
        except Exception as exc:
            raise ValueError("invalid event token") from exc
        return value


class CalendarUpdateEventArguments(CalendarEventArguments):
    summary: str | None = Field(default=None, min_length=1, max_length=500)
    description: str | None = Field(default=None, max_length=10_000)
    location: str | None = Field(default=None, max_length=1_000)
    timing: EventTiming | None = None

    @model_validator(mode="after")
    def require_patch(self) -> CalendarUpdateEventArguments:
        fields = self.model_fields_set.intersection(
            {"summary", "description", "location", "timing"}
        )
        if not fields:
            raise ValueError("at least one event field is required")
        if "summary" in fields and self.summary is None:
            raise ValueError("summary cannot be null")
        if "timing" in fields and self.timing is None:
            raise ValueError("timing cannot be null")
        return self


class CalendarDeleteEventArguments(CalendarEventArguments):
    pass


class _EventToken(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[2]
    integration_id: UUID
    binding_revision: str = Field(max_length=50_000)
    calendar_key: str = Field(pattern=r"^calendar:[a-f0-9]{32}$")
    href: str = Field(max_length=4_000)
    uid: str = Field(max_length=1_000)
    etag: str | None = Field(max_length=1_000)
    summary: str = Field(max_length=500)
    timing: EventTiming | None
    description: str | None = Field(max_length=10_000)
    location: str | None = Field(max_length=1_000)
    recurring: bool
    scheduled: bool
    mutation_safe: bool


_CALDAV_ERROR_CODES = {
    "caldav.authenticationFailed": "integration.authenticationFailed",
    "caldav.conflict": "integration.remoteChanged",
    "caldav.crossOrigin": "integration.invalidResponse",
    "caldav.etagRequired": "integration.remoteChanged",
    "caldav.invalidEvent": "tool.invalidArguments",
    "caldav.invalidHref": "integration.invalidResponse",
    "caldav.invalidLimit": "integration.invalidConfiguration",
    "caldav.invalidTimeRange": "tool.invalidArguments",
    "caldav.invalidUrl": "integration.invalidConfiguration",
    "caldav.malformedCalendarData": "integration.invalidResponse",
    "caldav.malformedResponse": "integration.invalidResponse",
    "caldav.malformedXml": "integration.invalidResponse",
    "caldav.notFound": "integration.remoteNotFound",
    "caldav.outcomeUnknown": "integration.outcomeUnknown",
    "caldav.preconditionFailed": "integration.remoteChanged",
    "caldav.redirectRejected": "integration.unavailable",
    "caldav.responseTooLarge": "integration.responseTooLarge",
    "caldav.tooManyEvents": "integration.responseTooLarge",
    "caldav.unavailable": "integration.unavailable",
    "caldav.unexpectedStatus": "integration.unavailable",
    "caldav.unsupportedMutation": "integration.operationUnsupported",
}


def caldav_error_code(error: CalDavError) -> str:
    return _CALDAV_ERROR_CODES.get(error.code, "integration.unavailable")


def _bound_ids(context: Any) -> set[str]:
    value = context.bound_options.get("integration_ids")
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return set()
    if not isinstance(value, (list, tuple)) or not all(
        isinstance(item, str) for item in value
    ):
        return set()
    return set(value)


def _binding(context: Any, integration_id: UUID) -> tuple[IntegrationInstance, str]:
    selected_id = context.bound_options.get("integration_id")
    revision = context.bound_options.get("binding_revision")
    if selected_id != str(integration_id) or not isinstance(revision, str):
        raise ValueError("missing selected integration binding")
    instance = next(
        (
            item
            for item in context.integration_settings.instances
            if str(item.id) == selected_id
            and item.enabled
            and item.integration_type == "calendar"
            and item.adapter_type == "caldav"
        ),
        None,
    )
    if instance is None or json.dumps(
        instance.model_dump(mode="json"),
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ) != revision:
        raise ValueError("stale selected integration binding")
    return instance, revision


def _credential_payload(record: Credential | None) -> dict[str, Any]:
    if record is None or record.kind != CALDAV_CREDENTIAL_KIND:
        raise CalDavError("caldav.authenticationFailed")
    try:
        payload = read_credential(record)
    except Exception as exc:
        raise CalDavError("caldav.authenticationFailed") from exc
    if (
        set(payload) != {"password"}
        or not isinstance(payload["password"], str)
        or not payload["password"]
    ):
        raise CalDavError("caldav.authenticationFailed")
    return payload


@asynccontextmanager
async def _selected_client(
    integration_id: UUID, context: Any
) -> AsyncIterator[tuple[CalDavClient, str]]:
    instance, revision = _binding(context, integration_id)
    if instance.credential_id is None:
        raise CalDavError("caldav.authenticationFailed")
    record = await context.db.get(Credential, instance.credential_id)
    password = _credential_payload(record)["password"]
    config = CalDavConfig.model_validate(instance.config)
    async with CalDavClient(str(config.base_url), config.username, password) as client:
        yield client, revision


async def _calendar(
    client: CalDavClient, calendar_key: str, context: Any | None = None
) -> CalDavCalendar:
    if context is not None:
        resource = getattr(context, "bound_resource", None)
        if (
            isinstance(resource, IntegrationResource)
            and resource.key == calendar_key
            and isinstance(resource.binding, CalDavCalendar)
        ):
            return resource.binding
    opaque_key = calendar_key.removeprefix("calendar:")
    discovery = await client.discover()
    calendar = next(
        (item for item in discovery.calendars if item.key == opaque_key),
        None,
    )
    if calendar is None:
        raise CalDavError("caldav.notFound")
    return calendar


def _label(value: str) -> str:
    return (value.strip() or "Kalender")[:160]


def _unavailable(calendar: CalDavCalendar) -> frozenset[str]:
    capabilities = {
        "read": "calendar.read",
        "write": "calendar.write",
        "delete": "calendar.delete",
    }
    return frozenset(
        capability
        for privilege, capability in capabilities.items()
        if privilege not in calendar.privileges
    )


async def load_caldav_resources(
    instance: IntegrationInstance, credential: dict[str, Any] | None
) -> tuple[IntegrationResource, ...]:
    config = CalDavConfig.model_validate(instance.config)
    if (
        credential is None
        or set(credential) != {"password"}
        or not isinstance(credential["password"], str)
        or not credential["password"]
    ):
        raise CalDavError("caldav.authenticationFailed")
    async with CalDavClient(
        str(config.base_url), config.username, credential["password"]
    ) as client:
        discovery = await client.discover()
    return tuple(
        IntegrationResource(
            key=f"calendar:{calendar.key}",
            label=_label(calendar.name),
            hard_unavailable_capabilities=_unavailable(calendar),
            binding=calendar,
        )
        for calendar in discovery.calendars
    )


def _timing(event: CalDavEvent) -> EventTiming | None:
    if event.end is None:
        return None
    if event.all_day and isinstance(event.start, date) and isinstance(event.end, date):
        return AllDayEventTiming(type="allDay", start=event.start, end=event.end)
    if isinstance(event.start, datetime) and isinstance(event.end, datetime):
        return TimedEventTiming(type="timed", start=event.start, end=event.end)
    return None


def _draft(
    timing: EventTiming,
    summary: str,
    description: str | None,
    location: str | None,
) -> CalDavEventDraft:
    return CalDavEventDraft(
        summary=summary,
        start=timing.start,
        end=timing.end,
        description=description,
        location=location,
    )


def _token(event: CalDavEvent, integration_id: UUID, revision: str, key: str) -> str:
    payload = _EventToken(
        version=2,
        integration_id=integration_id,
        binding_revision=revision,
        calendar_key=key,
        href=event.href,
        uid=event.uid,
        etag=event.etag,
        summary=_bounded(event.summary, 500) or "",
        timing=_timing(event),
        description=_bounded(event.description, 10_000),
        location=_bounded(event.location, 1_000),
        recurring=event.recurring,
        scheduled=event.scheduled,
        mutation_safe=event.mutation_safe,
    )
    return CredentialCipher().encrypt(payload.model_dump(mode="json")).decode("ascii")


def _event_from_token(
    value: str,
    integration_id: UUID,
    revision: str,
    calendar_key: str,
) -> tuple[_EventToken, CalDavEvent]:
    try:
        payload = _EventToken.model_validate(
            CredentialCipher().decrypt(value.encode("ascii"))
        )
    except Exception as exc:
        raise ValueError("invalid event token") from exc
    if (
        payload.integration_id != integration_id
        or payload.binding_revision != revision
        or payload.calendar_key != calendar_key
    ):
        raise ValueError("event token binding mismatch")
    timing = payload.timing
    return payload, CalDavEvent(
        uid=payload.uid,
        href=payload.href,
        etag=payload.etag,
        summary=payload.summary,
        start=timing.start if timing is not None else date.min,
        end=timing.end if timing is not None else None,
        all_day=isinstance(timing, AllDayEventTiming),
        description=payload.description,
        location=payload.location,
        recurring=payload.recurring,
        scheduled=payload.scheduled,
        mutation_safe=payload.mutation_safe,
    )


def _bounded(value: str | None, limit: int) -> str | None:
    return value[:limit] if value is not None else None


def _display_text(value: str | None, limit: int = 180) -> str:
    text = " ".join((value or "").split())
    return f"{text[:limit]}..." if len(text) > limit else text


def _format_offset(value: datetime) -> str:
    offset = value.utcoffset()
    if offset is None:
        return ""
    minutes = int(offset.total_seconds() // 60)
    sign = "+" if minutes >= 0 else "-"
    hours, remainder = divmod(abs(minutes), 60)
    return f" (UTC{sign}{hours:02d}:{remainder:02d})"


def _format_event_timing(timing: EventTiming) -> str:
    if isinstance(timing, AllDayEventTiming):
        last_day = timing.end - timedelta(days=1)
        if timing.start == last_day:
            return f"{timing.start:%d.%m.%Y} (ganztägig)"
        return f"{timing.start:%d.%m.%Y} bis {last_day:%d.%m.%Y} (ganztägig)"
    if (
        timing.start.date() == timing.end.date()
        and timing.start.utcoffset() == timing.end.utcoffset()
    ):
        return (
            f"{timing.start:%d.%m.%Y}, {timing.start:%H:%M} bis "
            f"{timing.end:%H:%M} Uhr{_format_offset(timing.start)}"
        )
    return (
        f"{timing.start:%d.%m.%Y}, {timing.start:%H:%M} Uhr"
        f"{_format_offset(timing.start)} bis {timing.end:%d.%m.%Y}, "
        f"{timing.end:%H:%M} Uhr{_format_offset(timing.end)}"
    )


def _event_reference(event: _EventToken) -> str:
    title = _display_text(event.summary) or "Ohne Titel"
    timing = (
        f" am {_format_event_timing(event.timing)}"
        if event.timing is not None
        else ""
    )
    return f'den Termin "{title}"{timing}'


def _event_for_permission(
    arguments: CalendarUpdateEventArguments | CalendarDeleteEventArguments,
    context: Any,
) -> _EventToken:
    _instance, revision = _binding(context, arguments.integration_id)
    event, _existing = _event_from_token(
        arguments.event_token,
        arguments.integration_id,
        revision,
        arguments.calendar_key,
    )
    return event


def _event_payload(
    event: CalDavEvent, integration_id: UUID, revision: str, calendar_key: str
) -> dict[str, Any]:
    timing = _timing(event)
    local_timing = None
    if isinstance(timing, TimedEventTiming):
        local_start = timing.start.astimezone(_LOCAL_TIMEZONE)
        local_end = timing.end.astimezone(_LOCAL_TIMEZONE)
        local_timing = {
            "timeZone": _LOCAL_TIMEZONE.key,
            "start": local_start.isoformat(),
            "end": local_end.isoformat(),
            "startWeekday": _WEEKDAYS[local_start.weekday()],
            "endWeekday": _WEEKDAYS[local_end.weekday()],
        }
    return {
        "eventToken": _token(event, integration_id, revision, calendar_key),
        "integrationId": str(integration_id),
        "calendarKey": calendar_key,
        "summary": _bounded(event.summary, 500),
        "description": _bounded(event.description, 10_000),
        "location": _bounded(event.location, 1_000),
        "timing": timing.model_dump(mode="json") if timing is not None else None,
        "localTiming": local_timing,
        "recurring": event.recurring,
        "scheduled": event.scheduled,
    }


def _result(value: dict[str, Any]) -> Any:
    from mato_loop_backend.agent.tools import ToolResult

    return ToolResult.json({**value, "contentIsUntrusted": True})


def _error(error: CalDavError) -> Any:
    from mato_loop_backend.agent.tools import ToolResult

    return ToolResult.error(caldav_error_code(error))


async def calendar_list_accounts(
    _arguments: CalendarListAccountsArguments, context: Any
) -> Any:
    bound_ids = _bound_ids(context)
    if not bound_ids:
        from mato_loop_backend.agent.tools import ToolResult

        return ToolResult.error("tool.unavailable")
    accounts = [
        {"integrationId": str(instance.id), "name": instance.name[:160]}
        for instance in context.integration_settings.instances
        if str(instance.id) in bound_ids
        and instance.enabled
        and instance.integration_type == "calendar"
        and instance.adapter_type == "caldav"
    ]
    return _result({"accounts": accounts})


async def calendar_list_calendars(
    arguments: CalendarIntegrationArguments, context: Any
) -> Any:
    try:
        async with _selected_client(arguments.integration_id, context) as (client, _revision):
            discovery = await client.discover()
        labels = {
            f"calendar:{calendar.key}": _label(calendar.name)
            for calendar in discovery.calendars
        }
        tools = await SettingsStore.load(context.db, ToolSettings, for_update=True)
        integration_id = str(arguments.integration_id)
        current_labels = tools.resource_labels.get(integration_id, {})
        if current_labels != labels:
            if labels:
                tools.resource_labels[integration_id] = labels
            else:
                tools.resource_labels.pop(integration_id, None)
            await SettingsStore.save(context.db, tools)
        context.tool_settings.resource_labels = dict(tools.resource_labels)
        context.tool_settings = tools
        return _result(
            {
                "calendars": [
                    {
                        "calendarKey": f"calendar:{calendar.key}",
                        "name": _label(calendar.name),
                        "unavailableCapabilities": sorted(_unavailable(calendar)),
                    }
                    for calendar in discovery.calendars
                ]
            }
        )
    except CalDavError as exc:
        return _error(exc)


async def calendar_query_events(
    arguments: CalendarQueryEventsArguments, context: Any
) -> Any:
    try:
        async with _selected_client(arguments.integration_id, context) as (client, revision):
            calendar = await _calendar(client, arguments.calendar_key, context)
            events = await client.query_events(calendar, arguments.start, arguments.end)
        return _result(
            {
                "events": [
                    _event_payload(
                        event,
                        arguments.integration_id,
                        revision,
                        arguments.calendar_key,
                    )
                    for event in events[: arguments.limit]
                ]
            }
        )
    except CalDavError as exc:
        return _error(exc)


async def calendar_create_event(
    arguments: CalendarCreateEventArguments, context: Any
) -> Any:
    try:
        async with _selected_client(arguments.integration_id, context) as (client, revision):
            calendar = await _calendar(client, arguments.calendar_key, context)
            event = await client.create_event(
                calendar,
                _draft(
                    arguments.timing,
                    arguments.summary,
                    arguments.description,
                    arguments.location,
                ),
                uuid5(
                    context.assistant_message_id or context.run_id,
                    f"{context.tool_call.name}:{context.tool_call.id}",
                ),
            )
        return _result(
            {
                "event": _event_payload(
                    event,
                    arguments.integration_id,
                    revision,
                    arguments.calendar_key,
                )
            }
        )
    except CalDavError as exc:
        return _error(exc)


async def calendar_update_event(
    arguments: CalendarUpdateEventArguments, context: Any
) -> Any:
    try:
        _instance, revision = _binding(context, arguments.integration_id)
        token, existing = _event_from_token(
            arguments.event_token,
            arguments.integration_id,
            revision,
            arguments.calendar_key,
        )
        timing = arguments.timing if "timing" in arguments.model_fields_set else token.timing
        if timing is None:
            raise CalDavError("caldav.unsupportedMutation")
        async with _selected_client(arguments.integration_id, context) as (client, _revision):
            await _calendar(client, arguments.calendar_key, context)
            event = await client.update_event(
                existing,
                _draft(
                    timing,
                    arguments.summary
                    if "summary" in arguments.model_fields_set
                    else token.summary,
                    arguments.description
                    if "description" in arguments.model_fields_set
                    else token.description,
                    arguments.location
                    if "location" in arguments.model_fields_set
                    else token.location,
                ),
            )
        return _result(
            {
                "event": _event_payload(
                    event,
                    arguments.integration_id,
                    revision,
                    arguments.calendar_key,
                )
            }
        )
    except ValueError:
        from mato_loop_backend.agent.tools import ToolResult

        return ToolResult.error("tool.invalidArguments", field="event_token")
    except CalDavError as exc:
        return _error(exc)


async def calendar_delete_event(
    arguments: CalendarDeleteEventArguments, context: Any
) -> Any:
    try:
        _instance, revision = _binding(context, arguments.integration_id)
        _token_value, existing = _event_from_token(
            arguments.event_token,
            arguments.integration_id,
            revision,
            arguments.calendar_key,
        )
        async with _selected_client(arguments.integration_id, context) as (client, _revision):
            await _calendar(client, arguments.calendar_key, context)
            await client.delete_event(existing)
        return _result({"deleted": True})
    except ValueError:
        from mato_loop_backend.agent.tools import ToolResult

        return ToolResult.error("tool.invalidArguments", field="event_token")
    except CalDavError as exc:
        return _error(exc)


def _operation_key(name: str, arguments: BaseModel) -> str:
    material = json.dumps(
        arguments.model_dump(mode="json", exclude_unset=True),
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return f"{name}:{hashlib.sha256(material.encode()).hexdigest()}"


def _permission(
    capability: str,
    operation_key: str,
    arguments: CalendarIntegrationArguments,
    context: Any,
    *,
    title: str,
    summary: str,
    resource: bool = False,
    use_integration_policy: bool = True,
) -> tuple[Any, ...]:
    from mato_loop_backend.agent.permissions import integration_operation_permission

    instance, revision = _binding(context, arguments.integration_id)
    resource_key = arguments.calendar_key if resource else None
    resource_display = f'Konto "{_display_text(instance.name)}"'
    if resource_key is not None:
        labels = getattr(getattr(context, "tool_settings", None), "resource_labels", {})
        label = labels.get(str(arguments.integration_id), {}).get(resource_key)
        calendar = f'"{_display_text(label)}"' if label else resource_key
        resource_display = f'{calendar} · Konto "{_display_text(instance.name)}"'
    return (
        integration_operation_permission(
            capability=capability,
            integration_id=arguments.integration_id,
            binding_revision=revision,
            operation_key=operation_key,
            resource_key=resource_key,
            resource_display=resource_display,
            use_integration_policy=use_integration_policy,
            title=title,
            summary=summary,
            available_decisions=(
                ("allow_once", "allow_session", "deny")
                if capability == "calendar.read"
                else ("allow_once", "deny")
            ),
        ),
    )


def _list_permission(arguments: CalendarIntegrationArguments, context: Any) -> tuple[Any, ...]:
    return _permission(
        "calendar.read",
        "list-calendars",
        arguments,
        context,
        title="Kalender auflisten?",
        summary="Der Agent möchte Namen und Kennungen der verfügbaren Kalender lesen.",
        use_integration_policy=False,
    )


def _query_permission(arguments: CalendarQueryEventsArguments, context: Any) -> tuple[Any, ...]:
    return _permission(
        "calendar.read",
        "query-events",
        arguments,
        context,
        title="Kalender lesen?",
        summary=(
            "Der Agent möchte Termine im Zeitraum "
            f"{arguments.start:%d.%m.%Y, %H:%M} Uhr{_format_offset(arguments.start)} bis "
            f"{arguments.end:%d.%m.%Y, %H:%M} Uhr{_format_offset(arguments.end)} lesen."
        ),
        resource=True,
    )


def _create_permission(arguments: CalendarCreateEventArguments, context: Any) -> tuple[Any, ...]:
    summary = (
        f'Der Agent möchte den Termin "{_display_text(arguments.summary)}" am '
        f"{_format_event_timing(arguments.timing)} erstellen."
    )
    if arguments.location:
        summary += f' Ort: "{_display_text(arguments.location)}".'
    if arguments.description:
        summary += f' Beschreibung: "{_display_text(arguments.description, 240)}".'
    return _permission(
        "calendar.write",
        _operation_key("create", arguments),
        arguments,
        context,
        title="Termin erstellen?",
        summary=summary,
        resource=True,
    )


def _update_permission(arguments: CalendarUpdateEventArguments, context: Any) -> tuple[Any, ...]:
    try:
        event = _event_for_permission(arguments, context)
    except ValueError:
        summary = "Der Agent möchte einen Termin in diesem Kalender ändern."
    else:
        changes = []
        if "summary" in arguments.model_fields_set:
            changes.append(f'Titel auf "{_display_text(arguments.summary)}" ändern')
        if "timing" in arguments.model_fields_set and arguments.timing is not None:
            changes.append(f"Zeit auf {_format_event_timing(arguments.timing)} ändern")
        if "location" in arguments.model_fields_set:
            changes.append(
                f'Ort auf "{_display_text(arguments.location)}" ändern'
                if arguments.location
                else "Ort entfernen"
            )
        if "description" in arguments.model_fields_set:
            changes.append(
                f'Beschreibung auf "{_display_text(arguments.description, 240)}" ändern'
                if arguments.description
                else "Beschreibung entfernen"
            )
        summary = f"Der Agent möchte {_event_reference(event)} ändern: {'; '.join(changes)}."
    return _permission(
        "calendar.write",
        _operation_key("update", arguments),
        arguments,
        context,
        title="Termin ändern?",
        summary=summary,
        resource=True,
    )


def _delete_permission(arguments: CalendarDeleteEventArguments, context: Any) -> tuple[Any, ...]:
    try:
        event = _event_for_permission(arguments, context)
        summary = f"Der Agent möchte {_event_reference(event)} löschen."
    except ValueError:
        summary = "Der Agent möchte einen Termin aus diesem Kalender löschen."
    return _permission(
        "calendar.delete",
        _operation_key("delete", arguments),
        arguments,
        context,
        title="Termin löschen?",
        summary=summary,
        resource=True,
    )


def _accounts_permission(
    _arguments: CalendarListAccountsArguments, _context: Any
) -> tuple[Any, ...]:
    from mato_loop_backend.agent.permissions import PermissionRequest

    return (
        PermissionRequest(
            capability="calendar.read",
            resource_type="network_service",
            resource_scope="calendar_accounts",
            action_kind="calendar_list_accounts",
            risk_level="medium",
            title="Kalenderkonten auflisten?",
            summary="Der Agent möchte die konfigurierten Kalenderkonten lesen.",
            resource_display="Konfigurierte Kalenderkonten",
        ),
    )


CALDAV_AGGREGATE_TOOLS = (
    ToolContribution(
        name="calendar_list_accounts",
        description=(
            "List active CalDAV accounts only when the user asks about accounts or explicitly "
            "requests a non-default calendar."
        ),
        arguments_model=CalendarListAccountsArguments,
        handler=calendar_list_accounts,
        label="Kalenderkonten auflisten",
        icon="calendar_month",
        actions=(_READ,),
        instance_argument=None,
        authorization_factory=_accounts_permission,
        releases_private_data=True,
    ),
)

CALDAV_SHARED_TOOLS = (
    ToolContribution(
        name="calendar_list_calendars",
        description=(
            "List calendars in one CalDAV account only when the user asks about available "
            "calendars or explicitly requests a non-default calendar."
        ),
        arguments_model=CalendarIntegrationArguments,
        handler=calendar_list_calendars,
        label="Kalender auflisten",
        icon="calendar_month",
        actions=(_READ,),
        instance_argument="integration_id",
        authorization_factory=_list_permission,
        releases_private_data=True,
    ),
    ToolContribution(
        name="calendar_query_events",
        description="Read events from one calendar in an aware time range.",
        arguments_model=CalendarQueryEventsArguments,
        handler=calendar_query_events,
        label="Termine lesen",
        icon="event",
        actions=(_READ,),
        instance_argument="integration_id",
        resource_argument="calendar_key",
        authorization_factory=_query_permission,
        releases_private_data=True,
    ),
    ToolContribution(
        name="calendar_create_event",
        description=(
            "Create one timed or all-day calendar event. Timed events require both start and end; "
            "all-day events require start and exclusive end dates."
        ),
        arguments_model=CalendarCreateEventArguments,
        handler=calendar_create_event,
        label="Termin erstellen",
        icon="event_available",
        actions=(_WRITE,),
        instance_argument="integration_id",
        resource_argument="calendar_key",
        authorization_factory=_create_permission,
        releases_private_data=True,
    ),
    ToolContribution(
        name="calendar_update_event",
        description="Update supported fields of a previously returned calendar event.",
        arguments_model=CalendarUpdateEventArguments,
        handler=calendar_update_event,
        label="Termin ändern",
        icon="edit_calendar",
        actions=(_WRITE,),
        instance_argument="integration_id",
        resource_argument="calendar_key",
        authorization_factory=_update_permission,
        releases_private_data=True,
    ),
    ToolContribution(
        name="calendar_delete_event",
        description="Delete a previously returned calendar event.",
        arguments_model=CalendarDeleteEventArguments,
        handler=calendar_delete_event,
        label="Termin löschen",
        icon="event_busy",
        actions=(_DELETE,),
        instance_argument="integration_id",
        resource_argument="calendar_key",
        authorization_factory=_delete_permission,
        releases_private_data=True,
    ),
)

CALDAV_ADAPTER = IntegrationAdapterDefinition(
    integration_type="calendar",
    adapter_type="caldav",
    config_model=CalDavConfig,
    contribution_factory=lambda _instance: IntegrationContributions(),
    resource_actions=(_READ, _WRITE, _DELETE),
    resource_loader=load_caldav_resources,
    shared_tools=CALDAV_AGGREGATE_TOOLS + CALDAV_SHARED_TOOLS,
)
