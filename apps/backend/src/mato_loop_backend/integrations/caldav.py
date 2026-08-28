from __future__ import annotations

import hashlib
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta
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
    CalDavTask,
    CalDavTaskDraft,
)
from mato_loop_backend.integrations.contracts import (
    IntegrationAdapterDefinition,
    IntegrationContributions,
    IntegrationResource,
    ToolContribution,
)
from mato_loop_backend.models import Credential
from mato_loop_backend.security import CredentialCipher, load_credential, read_credential
from mato_loop_backend.settings import (
    IntegrationInstance,
    SettingsStore,
    ToolExecutionPolicy,
    ToolSettings,
    UserToolSettings,
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
_TASKS_READ = ToolAction(
    "tasks.read",
    "Aufgaben lesen",
    ToolExecutionPolicy.ASK,
)
_TASKS_WRITE = ToolAction(
    "tasks.write",
    "Aufgaben ändern",
    ToolExecutionPolicy.ASK,
    mutates=True,
)
_TASKS_DELETE = ToolAction(
    "tasks.delete",
    "Aufgaben löschen",
    ToolExecutionPolicy.ASK,
    allowed_policies=(ToolExecutionPolicy.DISABLED, ToolExecutionPolicy.ASK),
    mutates=True,
)
_CALENDAR_CAPABILITIES = frozenset(
    {"calendar.read", "calendar.write", "calendar.delete"}
)
_TASK_CAPABILITIES = frozenset({"tasks.read", "tasks.write", "tasks.delete"})


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
        description="type required; timed values need timezone offsets; allDay end is exclusive.",
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


class TaskListAccountsArguments(_Arguments):
    pass


class TaskIntegrationArguments(_Arguments):
    integration_id: UUID


class TaskResourceArguments(TaskIntegrationArguments):
    task_list_key: str = Field(pattern=r"^tasklist:[a-f0-9]{32}$")


class TaskQueryArguments(TaskResourceArguments):
    include_completed: bool = False
    limit: int = Field(default=100, ge=1, le=100)


class TaskCreateArguments(TaskResourceArguments):
    title: str = Field(min_length=1, max_length=500)
    description: str | None = Field(default=None, max_length=10_000)
    due: date | datetime | None = None

    @field_validator("due")
    @classmethod
    def validate_due(cls, value: date | datetime | None) -> date | datetime | None:
        if isinstance(value, datetime) and (
            value.tzinfo is None or value.utcoffset() is None
        ):
            raise ValueError("due datetime must be aware")
        return value


class TaskBatchItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=500)
    description: str | None = Field(default=None, max_length=10_000)
    due: date | datetime | None = None

    @field_validator("due")
    @classmethod
    def validate_due(cls, value: date | datetime | None) -> date | datetime | None:
        return TaskCreateArguments.validate_due(value)


class TaskCreateManyArguments(TaskResourceArguments):
    tasks: list[TaskBatchItem] = Field(min_length=1, max_length=50)


class TaskTokenArguments(TaskResourceArguments):
    task_token: str = Field(min_length=1, max_length=50_000)

    @field_validator("task_token")
    @classmethod
    def validate_task_token(cls, value: str) -> str:
        try:
            _TaskToken.model_validate(CredentialCipher().decrypt(value.encode("ascii")))
        except Exception as exc:
            raise ValueError("invalid task token") from exc
        return value


class TaskUpdateArguments(TaskTokenArguments):
    title: str | None = Field(default=None, min_length=1, max_length=500)
    description: str | None = Field(default=None, max_length=10_000)
    due: date | datetime | None = None

    @field_validator("due")
    @classmethod
    def validate_due(cls, value: date | datetime | None) -> date | datetime | None:
        return TaskCreateArguments.validate_due(value)

    @model_validator(mode="after")
    def require_patch(self) -> TaskUpdateArguments:
        fields = self.model_fields_set.intersection({"title", "description", "due"})
        if not fields:
            raise ValueError("at least one task field is required")
        if "title" in fields and self.title is None:
            raise ValueError("title cannot be null")
        return self


class TaskSetCompletedArguments(TaskTokenArguments):
    completed: bool


class TaskDeleteArguments(TaskTokenArguments):
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


class _TaskToken(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1]
    integration_id: UUID
    binding_revision: str = Field(max_length=50_000)
    task_list_key: str = Field(pattern=r"^tasklist:[a-f0-9]{32}$")
    href: str = Field(max_length=4_000)
    uid: str = Field(max_length=1_000)
    etag: str | None = Field(max_length=1_000)
    title: str = Field(max_length=500)
    description: str | None = Field(max_length=10_000)
    due: date | datetime | None
    completed: bool
    completed_at: datetime | None
    created: datetime | None
    sequence: int = Field(ge=0)
    recurring: bool
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
    record = await load_credential(
        context.db, instance.credential_id, owner_id=instance.owner_id
    )
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
    if calendar is None or "VEVENT" not in calendar.supported_components:
        raise CalDavError("caldav.notFound")
    return calendar


async def _task_list(
    client: CalDavClient, task_list_key: str, context: Any | None = None
) -> CalDavCalendar:
    if context is not None:
        resource = getattr(context, "bound_resource", None)
        if (
            isinstance(resource, IntegrationResource)
            and resource.key == task_list_key
            and isinstance(resource.binding, CalDavCalendar)
            and "VTODO" in resource.binding.supported_components
        ):
            return resource.binding
    opaque_key = task_list_key.removeprefix("tasklist:")
    discovery = await client.discover()
    task_list = next(
        (
            item
            for item in discovery.calendars
            if item.key == opaque_key and "VTODO" in item.supported_components
        ),
        None,
    )
    if task_list is None:
        raise CalDavError("caldav.notFound")
    return task_list


def _label(value: str) -> str:
    return (value.strip() or "Kalender")[:160]


def _task_label(value: str) -> str:
    return (value.strip() or "Aufgaben")[:160]


def _unavailable(
    calendar: CalDavCalendar, capabilities: frozenset[str] = _CALENDAR_CAPABILITIES
) -> frozenset[str]:
    prefix = "tasks" if "tasks.read" in capabilities else "calendar"
    by_privilege = {
        "read": f"{prefix}.read",
        "write": f"{prefix}.write",
        "delete": f"{prefix}.delete",
    }
    return frozenset(
        capability
        for privilege, capability in by_privilege.items()
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
    resources = []
    for calendar in discovery.calendars:
        if "VEVENT" in calendar.supported_components:
            resources.append(
                IntegrationResource(
                    key=f"calendar:{calendar.key}",
                    label=_label(calendar.name),
                    hard_unavailable_capabilities=_unavailable(calendar),
                    applicable_capabilities=_CALENDAR_CAPABILITIES,
                    binding=calendar,
                    group_key=calendar.key,
                )
            )
        if "VTODO" in calendar.supported_components:
            resources.append(
                IntegrationResource(
                    key=f"tasklist:{calendar.key}",
                    label=_task_label(calendar.name),
                    hard_unavailable_capabilities=_unavailable(
                        calendar, _TASK_CAPABILITIES
                    ),
                    applicable_capabilities=_TASK_CAPABILITIES,
                    binding=calendar,
                    group_key=calendar.key,
                )
            )
    return tuple(
        sorted(
            resources,
            key=lambda resource: (resource.key.split(":", 1)[0], resource.label, resource.key),
        )
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


def _task_token(
    task: CalDavTask, integration_id: UUID, revision: str, key: str
) -> str:
    payload = _TaskToken(
        version=1,
        integration_id=integration_id,
        binding_revision=revision,
        task_list_key=key,
        href=task.href,
        uid=task.uid,
        etag=task.etag,
        title=_bounded(task.title, 500) or "",
        description=_bounded(task.description, 10_000),
        due=task.due,
        completed=task.completed,
        completed_at=task.completed_at,
        created=task.created,
        sequence=task.sequence,
        recurring=task.recurring,
        mutation_safe=task.mutation_safe,
    )
    return CredentialCipher().encrypt(payload.model_dump(mode="json")).decode("ascii")


def _task_from_token(
    value: str,
    integration_id: UUID,
    revision: str,
    task_list_key: str,
) -> tuple[_TaskToken, CalDavTask]:
    try:
        payload = _TaskToken.model_validate(
            CredentialCipher().decrypt(value.encode("ascii"))
        )
    except Exception as exc:
        raise ValueError("invalid task token") from exc
    if (
        payload.integration_id != integration_id
        or payload.binding_revision != revision
        or payload.task_list_key != task_list_key
    ):
        raise ValueError("task token binding mismatch")
    return payload, CalDavTask(
        uid=payload.uid,
        href=payload.href,
        etag=payload.etag,
        title=payload.title,
        description=payload.description,
        due=payload.due,
        completed=payload.completed,
        completed_at=payload.completed_at,
        created=payload.created,
        sequence=payload.sequence,
        recurring=payload.recurring,
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


def _task_payload(
    task: CalDavTask, integration_id: UUID, revision: str, task_list_key: str
) -> dict[str, Any]:
    return {
        "taskToken": _task_token(task, integration_id, revision, task_list_key),
        "integrationId": str(integration_id),
        "taskListKey": task_list_key,
        "title": _bounded(task.title, 500),
        "description": _bounded(task.description, 10_000),
        "due": task.due.isoformat() if task.due is not None else None,
        "dueIsFloating": (
            isinstance(task.due, datetime)
            and (task.due.tzinfo is None or task.due.utcoffset() is None)
        ),
        "completed": task.completed,
        "completedAt": (
            task.completed_at.isoformat() if task.completed_at is not None else None
        ),
        "recurring": task.recurring,
    }


def _task_draft(
    *,
    title: str,
    description: str | None,
    due: date | datetime | None,
    completed: bool = False,
    completed_at: datetime | None = None,
    created: datetime | None = None,
    sequence: int = 0,
) -> CalDavTaskDraft:
    return CalDavTaskDraft(
        title=title,
        description=description,
        due=due,
        completed=completed,
        completed_at=completed_at,
        created=created,
        sequence=sequence,
    )


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


async def _remember_resource_labels(
    integration_id: UUID, discovery: Any, context: Any
) -> None:
    labels = {}
    for calendar in discovery.calendars:
        if "VEVENT" in calendar.supported_components:
            labels[f"calendar:{calendar.key}"] = _label(calendar.name)
        if "VTODO" in calendar.supported_components:
            labels[f"tasklist:{calendar.key}"] = _task_label(calendar.name)
    integration = next(
        item
        for item in context.integration_settings.instances
        if item.id == integration_id
    )
    owner_id = integration.owner_id
    if owner_id is None:
        tools = await SettingsStore.load(context.db, ToolSettings, for_update=True)
    else:
        tools = await SettingsStore.load_optional(
            context.db, UserToolSettings, user_id=owner_id, for_update=True
        )
        if tools is None:
            instance_tools = await SettingsStore.load(context.db, ToolSettings)
            tools = UserToolSettings.model_validate(
                {
                    **instance_tools.model_dump(exclude={"personal_settings_enabled"}),
                    "personal_tool_defaults_enabled": False,
                }
            )
    key = str(integration_id)
    if tools.resource_labels.get(key, {}) != labels:
        if labels:
            tools.resource_labels[key] = labels
        else:
            tools.resource_labels.pop(key, None)
        await SettingsStore.save(context.db, tools, user_id=owner_id)
    context.tool_settings.resource_labels = dict(tools.resource_labels)


async def calendar_list_calendars(
    arguments: CalendarIntegrationArguments, context: Any
) -> Any:
    try:
        async with _selected_client(arguments.integration_id, context) as (client, _revision):
            discovery = await client.discover()
        await _remember_resource_labels(arguments.integration_id, discovery, context)
        return _result(
            {
                "calendars": [
                    {
                        "calendarKey": f"calendar:{calendar.key}",
                        "name": _label(calendar.name),
                        "unavailableCapabilities": sorted(_unavailable(calendar)),
                    }
                    for calendar in discovery.calendars
                    if "VEVENT" in calendar.supported_components
                ]
            }
        )
    except CalDavError as exc:
        return _error(exc)


async def tasks_list_lists(arguments: TaskIntegrationArguments, context: Any) -> Any:
    try:
        async with _selected_client(arguments.integration_id, context) as (client, _revision):
            discovery = await client.discover()
        await _remember_resource_labels(arguments.integration_id, discovery, context)
        return _result(
            {
                "taskLists": [
                    {
                        "taskListKey": f"tasklist:{calendar.key}",
                        "name": _task_label(calendar.name),
                        "unavailableCapabilities": sorted(
                            _unavailable(calendar, _TASK_CAPABILITIES)
                        ),
                    }
                    for calendar in discovery.calendars
                    if "VTODO" in calendar.supported_components
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
                "calendar": {
                    "integrationId": str(arguments.integration_id),
                    "calendarKey": arguments.calendar_key,
                    "name": _label(calendar.name),
                },
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

        return ToolResult.error("integration.staleReference", field="event_token")
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

        return ToolResult.error("integration.staleReference", field="event_token")
    except CalDavError as exc:
        return _error(exc)


async def tasks_query(arguments: TaskQueryArguments, context: Any) -> Any:
    try:
        async with _selected_client(arguments.integration_id, context) as (client, revision):
            task_list = await _task_list(client, arguments.task_list_key, context)
            tasks = await client.query_tasks(
                task_list, include_completed=arguments.include_completed
            )
        return _result(
            {
                "tasks": [
                    _task_payload(
                        task,
                        arguments.integration_id,
                        revision,
                        arguments.task_list_key,
                    )
                    for task in tasks[: arguments.limit]
                ],
                "truncated": len(tasks) > arguments.limit,
            }
        )
    except CalDavError as exc:
        return _error(exc)


async def tasks_create(arguments: TaskCreateArguments, context: Any) -> Any:
    try:
        async with _selected_client(arguments.integration_id, context) as (client, revision):
            task_list = await _task_list(client, arguments.task_list_key, context)
            task = await client.create_task(
                task_list,
                _task_draft(
                    title=arguments.title,
                    description=arguments.description,
                    due=arguments.due,
                ),
                uuid5(
                    context.assistant_message_id or context.run_id,
                    f"{context.tool_call.name}:{context.tool_call.id}",
                ),
            )
        return _result(
            {
                "task": _task_payload(
                    task,
                    arguments.integration_id,
                    revision,
                    arguments.task_list_key,
                )
            }
        )
    except CalDavError as exc:
        return _error(exc)


def _normalized_task_title(value: str) -> str:
    return " ".join(value.split()).casefold()


async def tasks_create_many(
    arguments: TaskCreateManyArguments, context: Any
) -> Any:
    created = []
    created_titles = []
    skipped = []
    failed_title = None
    try:
        async with _selected_client(arguments.integration_id, context) as (
            client,
            revision,
        ):
            task_list = await _task_list(client, arguments.task_list_key, context)
            existing = await client.query_tasks(task_list, include_completed=False)
            known_titles = {_normalized_task_title(task.title) for task in existing}
            namespace = context.assistant_message_id or context.run_id
            for index, item in enumerate(arguments.tasks):
                normalized = _normalized_task_title(item.title)
                if normalized in known_titles:
                    skipped.append(item.title)
                    continue
                failed_title = item.title
                task = await client.create_task(
                    task_list,
                    _task_draft(
                        title=item.title,
                        description=item.description,
                        due=item.due,
                    ),
                    uuid5(
                        namespace,
                        f"{context.tool_call.name}:{context.tool_call.id}:{index}",
                    ),
                )
                known_titles.add(normalized)
                created_titles.append(item.title)
                created.append(
                    _task_payload(
                        task,
                        arguments.integration_id,
                        revision,
                        arguments.task_list_key,
                    )
                )
                failed_title = None
        return _result({"created": created, "skippedExistingTitles": skipped})
    except CalDavError as exc:
        if created_titles:
            from mato_loop_backend.agent.tools import ToolResult

            return ToolResult.error(
                "integration.outcomeUnknown",
                params={
                    "createdTitles": created_titles,
                    "skippedExistingTitles": skipped,
                    "failedTitle": failed_title,
                    "causeCode": caldav_error_code(exc),
                },
            )
        return _error(exc)


async def tasks_update(arguments: TaskUpdateArguments, context: Any) -> Any:
    try:
        _instance, revision = _binding(context, arguments.integration_id)
        token, existing = _task_from_token(
            arguments.task_token,
            arguments.integration_id,
            revision,
            arguments.task_list_key,
        )
        async with _selected_client(arguments.integration_id, context) as (client, _revision):
            await _task_list(client, arguments.task_list_key, context)
            task = await client.update_task(
                existing,
                _task_draft(
                    title=(
                        arguments.title
                        if "title" in arguments.model_fields_set
                        else token.title
                    ),
                    description=(
                        arguments.description
                        if "description" in arguments.model_fields_set
                        else token.description
                    ),
                    due=(arguments.due if "due" in arguments.model_fields_set else token.due),
                    completed=token.completed,
                    completed_at=token.completed_at,
                    created=token.created,
                    sequence=token.sequence + 1,
                ),
            )
        return _result(
            {
                "task": _task_payload(
                    task,
                    arguments.integration_id,
                    revision,
                    arguments.task_list_key,
                )
            }
        )
    except ValueError:
        from mato_loop_backend.agent.tools import ToolResult

        return ToolResult.error("tool.invalidArguments", field="task_token")
    except CalDavError as exc:
        return _error(exc)


async def tasks_set_completed(
    arguments: TaskSetCompletedArguments, context: Any
) -> Any:
    try:
        _instance, revision = _binding(context, arguments.integration_id)
        token, existing = _task_from_token(
            arguments.task_token,
            arguments.integration_id,
            revision,
            arguments.task_list_key,
        )
        completed_at = None
        if arguments.completed:
            completed_at = token.completed_at or datetime.now(UTC)
        async with _selected_client(arguments.integration_id, context) as (client, _revision):
            await _task_list(client, arguments.task_list_key, context)
            task = await client.update_task(
                existing,
                _task_draft(
                    title=token.title,
                    description=token.description,
                    due=token.due,
                    completed=arguments.completed,
                    completed_at=completed_at,
                    created=token.created,
                    sequence=token.sequence + 1,
                ),
            )
        return _result(
            {
                "task": _task_payload(
                    task,
                    arguments.integration_id,
                    revision,
                    arguments.task_list_key,
                )
            }
        )
    except ValueError:
        from mato_loop_backend.agent.tools import ToolResult

        return ToolResult.error("tool.invalidArguments", field="task_token")
    except CalDavError as exc:
        return _error(exc)


async def tasks_delete(arguments: TaskDeleteArguments, context: Any) -> Any:
    try:
        _instance, revision = _binding(context, arguments.integration_id)
        _token_value, existing = _task_from_token(
            arguments.task_token,
            arguments.integration_id,
            revision,
            arguments.task_list_key,
        )
        async with _selected_client(arguments.integration_id, context) as (client, _revision):
            await _task_list(client, arguments.task_list_key, context)
            await client.delete_task(existing)
        return _result({"deleted": True})
    except ValueError:
        from mato_loop_backend.agent.tools import ToolResult

        return ToolResult.error("tool.invalidArguments", field="task_token")
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


def _task_permission(
    capability: str,
    operation_key: str,
    arguments: TaskIntegrationArguments,
    context: Any,
    *,
    title: str,
    summary: str,
    resource: bool = False,
    use_integration_policy: bool = True,
    display_items: tuple[Any, ...] = (),
) -> tuple[Any, ...]:
    from mato_loop_backend.agent.permissions import integration_operation_permission

    instance, revision = _binding(context, arguments.integration_id)
    resource_key = arguments.task_list_key if resource else None
    resource_display = f'Konto "{_display_text(instance.name)}"'
    if resource_key is not None:
        labels = getattr(getattr(context, "tool_settings", None), "resource_labels", {})
        label = labels.get(str(arguments.integration_id), {}).get(resource_key)
        task_list = f'"{_display_text(label)}"' if label else resource_key
        resource_display = f'{task_list} · Konto "{_display_text(instance.name)}"'
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
            display_items=display_items,
            available_decisions=(
                ("allow_once", "allow_session", "deny")
                if capability == "tasks.read"
                else ("allow_once", "deny")
            ),
        ),
    )


def _task_reference(task: _TaskToken) -> str:
    title = _display_text(task.title) or "Ohne Titel"
    if isinstance(task.due, datetime):
        due = f" mit Fälligkeit {task.due:%d.%m.%Y, %H:%M} Uhr{_format_offset(task.due)}"
    elif isinstance(task.due, date):
        due = f" mit Fälligkeit {task.due:%d.%m.%Y}"
    else:
        due = ""
    return f'die Aufgabe "{title}"{due}'


def _task_due_display(value: date | datetime | None) -> str:
    if isinstance(value, datetime):
        return f"{value:%d.%m.%Y, %H:%M} Uhr{_format_offset(value)}"
    if isinstance(value, date):
        return f"{value:%d.%m.%Y}"
    return "Keine Fälligkeit"


def _task_for_permission(
    arguments: TaskUpdateArguments | TaskSetCompletedArguments | TaskDeleteArguments,
    context: Any,
) -> _TaskToken:
    _instance, revision = _binding(context, arguments.integration_id)
    task, _existing = _task_from_token(
        arguments.task_token,
        arguments.integration_id,
        revision,
        arguments.task_list_key,
    )
    return task


def _task_lists_permission(
    arguments: TaskIntegrationArguments, context: Any
) -> tuple[Any, ...]:
    from mato_loop_backend.agent.permissions import PermissionDisplayItem

    return _task_permission(
        "tasks.read",
        "list-task-lists",
        arguments,
        context,
        title="Aufgabenlisten auflisten?",
        summary="Der Agent möchte Namen und Kennungen der verfügbaren Aufgabenlisten lesen.",
        use_integration_policy=False,
        display_items=(PermissionDisplayItem("Aktion", "Aufgabenlisten auflisten"),),
    )


def _tasks_query_permission(
    arguments: TaskQueryArguments, context: Any
) -> tuple[Any, ...]:
    from mato_loop_backend.agent.permissions import PermissionDisplayItem

    completed = " einschließlich erledigter Aufgaben" if arguments.include_completed else ""
    return _task_permission(
        "tasks.read",
        "query-tasks",
        arguments,
        context,
        title="Aufgaben lesen?",
        summary=f"Der Agent möchte bis zu {arguments.limit} Aufgaben{completed} lesen.",
        resource=True,
        display_items=(
            PermissionDisplayItem("Aktion", "Aufgaben lesen"),
            PermissionDisplayItem("Anzahl", f"Bis zu {arguments.limit}"),
            PermissionDisplayItem(
                "Status",
                "Offene und erledigte Aufgaben"
                if arguments.include_completed
                else "Nur offene Aufgaben",
            ),
        ),
    )


def _task_create_permission(
    arguments: TaskCreateArguments, context: Any
) -> tuple[Any, ...]:
    from mato_loop_backend.agent.permissions import PermissionDisplayItem

    summary = f'Der Agent möchte die Aufgabe "{_display_text(arguments.title)}" erstellen.'
    if arguments.description:
        summary += f' Notiz: "{_display_text(arguments.description, 240)}".'
    return _task_permission(
        "tasks.write",
        _operation_key("create-task", arguments),
        arguments,
        context,
        title="Aufgabe erstellen?",
        summary=summary,
        resource=True,
        display_items=(
            PermissionDisplayItem("Aktion", "Aufgabe erstellen"),
            PermissionDisplayItem("Aufgabe", _display_text(arguments.title)),
            PermissionDisplayItem("Fälligkeit", _task_due_display(arguments.due)),
            *(
                (
                    PermissionDisplayItem(
                        "Notiz", _display_text(arguments.description, 1_000)
                    ),
                )
                if arguments.description
                else ()
            ),
        ),
    )


def _tasks_create_many_permission(
    arguments: TaskCreateManyArguments, context: Any
) -> tuple[Any, ...]:
    from mato_loop_backend.agent.permissions import PermissionDisplayItem

    titles = tuple(_display_text(item.title) for item in arguments.tasks)
    return _task_permission(
        "tasks.write",
        _operation_key("create-tasks", arguments),
        arguments,
        context,
        title="Aufgaben gesammelt erstellen?",
        summary=(
            f"Der Agent möchte bis zu {len(arguments.tasks)} Aufgaben erstellen. "
            "Gleichnamige offene Aufgaben werden übersprungen."
        ),
        resource=True,
        display_items=(
            PermissionDisplayItem("Aktion", "Fehlende Aufgaben erstellen"),
            PermissionDisplayItem("Aufgaben", titles, format="list"),
        ),
    )


def _task_update_permission(
    arguments: TaskUpdateArguments, context: Any
) -> tuple[Any, ...]:
    from mato_loop_backend.agent.permissions import PermissionDisplayItem

    display_items: tuple[Any, ...] = ()
    try:
        task = _task_for_permission(arguments, context)
        changes = []
        if "title" in arguments.model_fields_set:
            changes.append(f'Titel: "{_display_text(arguments.title)}"')
        if "description" in arguments.model_fields_set:
            changes.append(
                f'Notiz: "{_display_text(arguments.description, 240)}"'
                if arguments.description
                else "Notiz entfernen"
            )
        if "due" in arguments.model_fields_set:
            changes.append(
                f"Fälligkeit: {_task_due_display(arguments.due)}"
                if arguments.due
                else "Fälligkeit entfernen"
            )
        summary = f"Der Agent möchte {_task_reference(task)} ändern: {'; '.join(changes)}."
        display_items = (
            PermissionDisplayItem("Aktion", "Aufgabe ändern"),
            PermissionDisplayItem("Aufgabe", _display_text(task.title) or "Ohne Titel"),
            PermissionDisplayItem("Änderungen", tuple(changes), format="list"),
        )
    except ValueError:
        summary = "Der Agent möchte eine Aufgabe in dieser Aufgabenliste ändern."
    return _task_permission(
        "tasks.write",
        _operation_key("update-task", arguments),
        arguments,
        context,
        title="Aufgabe ändern?",
        summary=summary,
        resource=True,
        display_items=display_items,
    )


def _task_completed_permission(
    arguments: TaskSetCompletedArguments, context: Any
) -> tuple[Any, ...]:
    from mato_loop_backend.agent.permissions import PermissionDisplayItem

    display_items: tuple[Any, ...] = ()
    try:
        task = _task_for_permission(arguments, context)
        summary = (
            f"Der Agent möchte {_task_reference(task)} als "
            f"{'erledigt' if arguments.completed else 'offen'} markieren."
        )
        display_items = (
            PermissionDisplayItem("Aktion", "Aufgabenstatus ändern"),
            PermissionDisplayItem("Aufgabe", _display_text(task.title) or "Ohne Titel"),
            PermissionDisplayItem(
                "Neuer Status", "Erledigt" if arguments.completed else "Offen"
            ),
        )
    except ValueError:
        summary = "Der Agent möchte den Status einer Aufgabe ändern."
    return _task_permission(
        "tasks.write",
        _operation_key("set-task-completed", arguments),
        arguments,
        context,
        title="Aufgabenstatus ändern?",
        summary=summary,
        resource=True,
        display_items=display_items,
    )


def _task_delete_permission(
    arguments: TaskDeleteArguments, context: Any
) -> tuple[Any, ...]:
    from mato_loop_backend.agent.permissions import PermissionDisplayItem

    display_items: tuple[Any, ...] = ()
    try:
        task = _task_for_permission(arguments, context)
        summary = f"Der Agent möchte {_task_reference(task)} löschen."
        display_items = (
            PermissionDisplayItem("Aktion", "Aufgabe löschen"),
            PermissionDisplayItem("Aufgabe", _display_text(task.title) or "Ohne Titel"),
        )
    except ValueError:
        summary = "Der Agent möchte eine Aufgabe aus dieser Aufgabenliste löschen."
    return _task_permission(
        "tasks.delete",
        _operation_key("delete-task", arguments),
        arguments,
        context,
        title="Aufgabe löschen?",
        summary=summary,
        resource=True,
        display_items=display_items,
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


def _task_accounts_permission(
    _arguments: TaskListAccountsArguments, _context: Any
) -> tuple[Any, ...]:
    from mato_loop_backend.agent.permissions import (
        PermissionDisplayItem,
        PermissionRequest,
    )

    return (
        PermissionRequest(
            capability="tasks.read",
            resource_type="network_service",
            resource_scope="task_accounts",
            action_kind="tasks_list_accounts",
            risk_level="medium",
            title="Aufgabenkonten auflisten?",
            summary="Der Agent möchte die konfigurierten CalDAV-Konten für Aufgaben lesen.",
            resource_display="Konfigurierte CalDAV-Konten",
            display_items=(PermissionDisplayItem("Aktion", "Aufgabenkonten auflisten"),),
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
    ToolContribution(
        name="tasks_list_accounts",
        description=(
            "List active CalDAV accounts only when the user asks about task accounts or needs "
            "to select an account before listing task lists."
        ),
        arguments_model=TaskListAccountsArguments,
        handler=calendar_list_accounts,
        label="Aufgabenkonten auflisten",
        icon="account_circle",
        actions=(_TASKS_READ,),
        instance_argument=None,
        authorization_factory=_task_accounts_permission,
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
            "Create one calendar event. Only summary and timing are required; location and description "
            "are optional. Timed events require start and end; all-day end is exclusive."
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
        description=(
            "Update one returned calendar event at least one field. Timed events require start and end; "
            "all-day end is exclusive."
        ),
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
    ToolContribution(
        name="tasks_list_lists",
        description="List VTODO task lists in one CalDAV account before selecting a task list.",
        arguments_model=TaskIntegrationArguments,
        handler=tasks_list_lists,
        label="Aufgabenlisten auflisten",
        icon="checklist",
        actions=(_TASKS_READ,),
        instance_argument="integration_id",
        authorization_factory=_task_lists_permission,
        releases_private_data=True,
    ),
    ToolContribution(
        name="tasks_query",
        description=(
            "Read tasks from one CalDAV task list. Completed tasks are excluded unless "
            "include_completed is true."
        ),
        arguments_model=TaskQueryArguments,
        handler=tasks_query,
        label="Aufgaben lesen",
        icon="task_alt",
        actions=(_TASKS_READ,),
        instance_argument="integration_id",
        resource_argument="task_list_key",
        authorization_factory=_tasks_query_permission,
        releases_private_data=True,
    ),
    ToolContribution(
        name="tasks_create",
        description="Create one CalDAV task with a title and optional note and due date or time.",
        arguments_model=TaskCreateArguments,
        handler=tasks_create,
        label="Aufgabe erstellen",
        icon="add_task",
        actions=(_TASKS_WRITE,),
        instance_argument="integration_id",
        resource_argument="task_list_key",
        authorization_factory=_task_create_permission,
        releases_private_data=True,
    ),
    ToolContribution(
        name="tasks_create_many",
        description=(
            "Create up to 50 CalDAV tasks in one list after skipping requested titles that "
            "already exist as open tasks. Use this for a generated shopping list."
        ),
        arguments_model=TaskCreateManyArguments,
        handler=tasks_create_many,
        label="Fehlende Aufgaben gesammelt erstellen",
        icon="playlist_add",
        actions=(_TASKS_WRITE,),
        instance_argument="integration_id",
        resource_argument="task_list_key",
        authorization_factory=_tasks_create_many_permission,
        releases_private_data=True,
    ),
    ToolContribution(
        name="tasks_update",
        description="Update supported fields of one previously returned CalDAV task.",
        arguments_model=TaskUpdateArguments,
        handler=tasks_update,
        label="Aufgabe ändern",
        icon="edit_note",
        actions=(_TASKS_WRITE,),
        instance_argument="integration_id",
        resource_argument="task_list_key",
        authorization_factory=_task_update_permission,
        releases_private_data=True,
    ),
    ToolContribution(
        name="tasks_set_completed",
        description="Mark one previously returned CalDAV task as completed or open.",
        arguments_model=TaskSetCompletedArguments,
        handler=tasks_set_completed,
        label="Aufgabenstatus ändern",
        icon="task_alt",
        actions=(_TASKS_WRITE,),
        instance_argument="integration_id",
        resource_argument="task_list_key",
        authorization_factory=_task_completed_permission,
        releases_private_data=True,
    ),
    ToolContribution(
        name="tasks_delete",
        description="Delete one previously returned CalDAV task.",
        arguments_model=TaskDeleteArguments,
        handler=tasks_delete,
        label="Aufgabe löschen",
        icon="delete_outline",
        actions=(_TASKS_DELETE,),
        instance_argument="integration_id",
        resource_argument="task_list_key",
        authorization_factory=_task_delete_permission,
        releases_private_data=True,
    ),
)

CALDAV_ADAPTER = IntegrationAdapterDefinition(
    integration_type="calendar",
    adapter_type="caldav",
    config_model=CalDavConfig,
    contribution_factory=lambda _instance: IntegrationContributions(),
    resource_actions=(
        _READ,
        _WRITE,
        _DELETE,
        _TASKS_READ,
        _TASKS_WRITE,
        _TASKS_DELETE,
    ),
    resource_loader=load_caldav_resources,
    shared_tools=CALDAV_AGGREGATE_TOOLS + CALDAV_SHARED_TOOLS,
)
