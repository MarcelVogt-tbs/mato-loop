from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from typing import Literal
from urllib.parse import urljoin
from uuid import UUID
from xml.etree import ElementTree

import httpx
from defusedxml import ElementTree as DefusedElementTree
from defusedxml.common import DefusedXmlException
from icalendar import Alarm as ICalendarAlarm
from icalendar import Calendar as ICalendar
from icalendar import Event as ICalendarEvent
from icalendar import Todo as ICalendarTodo

DAV = "DAV:"
CALDAV = "urn:ietf:params:xml:ns:caldav"
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_XML_ELEMENTS = 10_000
MAX_EVENTS = 1_000
MAX_QUERY_SPAN = timedelta(days=366)

CalDavPrivilege = Literal["read", "write", "delete"]
CalDavComponent = Literal["VEVENT", "VTODO"]


class CalDavError(RuntimeError):
    def __init__(self, code: str, *, status_code: int | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.status_code = status_code


@dataclass(frozen=True, slots=True)
class CalDavCalendar:
    key: str
    href: str
    name: str
    privileges: frozenset[CalDavPrivilege]
    supported_components: frozenset[CalDavComponent] = frozenset({"VEVENT"})


@dataclass(frozen=True, slots=True)
class CalDavDiscovery:
    principal_href: str
    calendar_home_href: str
    calendars: tuple[CalDavCalendar, ...]


@dataclass(frozen=True, slots=True)
class CalDavEventDraft:
    summary: str
    start: date | datetime
    end: date | datetime
    description: str | None = None
    location: str | None = None
    reminders_minutes_before: tuple[int, ...] = ()


@dataclass(frozen=True, slots=True)
class CalDavEvent:
    uid: str
    href: str
    etag: str | None
    summary: str
    start: date | datetime
    end: date | datetime | None
    all_day: bool
    description: str | None = None
    location: str | None = None
    reminders_minutes_before: tuple[int, ...] = ()
    recurring: bool = False
    scheduled: bool = False
    mutation_safe: bool = True


def _event_reminders(component: ICalendarEvent) -> tuple[tuple[int, ...], bool]:
    reminders = []
    for alarm in component.subcomponents:
        trigger = alarm.get("trigger")
        value = getattr(trigger, "dt", None)
        if (
            alarm.name != "VALARM"
            or str(alarm.get("action", "")).upper() != "DISPLAY"
            or str(alarm.get("description", "")) != str(component.get("summary", ""))
            or not isinstance(value, timedelta)
            or value > timedelta(0)
            or value % timedelta(minutes=1)
            or getattr(trigger, "params", {})
            or alarm.subcomponents
            or {str(key).upper() for key in alarm.keys()}
            > {"ACTION", "TRIGGER", "DESCRIPTION"}
        ):
            return (), False
        reminders.append(int(-value.total_seconds() // 60))
    if (
        len(reminders) > 10
        or len(reminders) != len(set(reminders))
        or any(minutes > 525_600 for minutes in reminders)
    ):
        return (), False
    return tuple(reminders), True


@dataclass(frozen=True, slots=True)
class CalDavTaskDraft:
    title: str
    description: str | None = None
    due: date | datetime | None = None
    completed: bool = False
    completed_at: datetime | None = None
    created: datetime | None = None
    sequence: int = 0


@dataclass(frozen=True, slots=True)
class CalDavTask:
    uid: str
    href: str
    etag: str | None
    title: str
    description: str | None = None
    due: date | datetime | None = None
    completed: bool = False
    completed_at: datetime | None = None
    created: datetime | None = None
    sequence: int = 0
    recurring: bool = False
    mutation_safe: bool = True


def _error(code: str, *, status_code: int | None = None) -> CalDavError:
    return CalDavError(f"caldav.{code}", status_code=status_code)


def _origin(url: httpx.URL) -> tuple[str, str, int]:
    default_port = 443 if url.scheme == "https" else 80
    return url.scheme, url.host.lower(), url.port or default_port


def _parse_url(value: str) -> httpx.URL:
    if (
        not isinstance(value, str)
        or not value
        or "?" in value
        or "#" in value
        or "\\" in value
        or any(character.isspace() or ord(character) < 32 for character in value)
    ):
        raise _error("invalidUrl")
    try:
        url = httpx.URL(value)
    except (httpx.InvalidURL, ValueError) as exc:
        raise _error("invalidUrl") from exc
    if url.scheme != "https" or not url.host or url.userinfo:
        raise _error("invalidUrl")
    return url


def _status_code(response: ElementTree.Element) -> int | None:
    text = response.findtext(f"{{{DAV}}}status")
    if not text:
        return None
    parts = text.split()
    try:
        return int(parts[1])
    except (IndexError, ValueError):
        return None


def _properties(response: ElementTree.Element) -> tuple[ElementTree.Element, ...]:
    properties: list[ElementTree.Element] = []
    for propstat in response.findall(f"{{{DAV}}}propstat"):
        status = _status_code(propstat)
        prop = propstat.find(f"{{{DAV}}}prop")
        if prop is not None and status is not None and 200 <= status < 300:
            properties.append(prop)
    return tuple(properties)


def _property(response: ElementTree.Element, name: str) -> ElementTree.Element | None:
    return next(
        (value for prop in _properties(response) if (value := prop.find(name)) is not None),
        None,
    )


def _task_properties_are_safe(component: ICalendarTodo) -> bool:
    for name in component.keys():
        value = component.get(name)
        if isinstance(value, list):
            return False
        parameters = {str(key).upper() for key in getattr(value, "params", {})}
        if parameters and (
            str(name).upper() != "DUE" or not parameters <= {"VALUE", "TZID"}
        ):
            return False
    return True


def _privileges(value: ElementTree.Element | None) -> frozenset[CalDavPrivilege]:
    if value is None:
        return frozenset()
    mapped: set[CalDavPrivilege] = set()
    for privilege in value.findall(f"{{{DAV}}}privilege"):
        child = next(iter(privilege), None)
        if child is None:
            continue
        name = child.tag.removeprefix(f"{{{DAV}}}")
        if name == "all":
            mapped.update(("read", "write", "delete"))
        elif name == "read":
            mapped.add("read")
        elif name in {"bind", "write", "write-content", "write-properties"}:
            mapped.add("write")
        elif name == "unbind":
            mapped.add("delete")
    return frozenset(mapped)


class CalDavClient:
    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        *,
        client: httpx.AsyncClient | None = None,
        max_response_bytes: int = MAX_RESPONSE_BYTES,
    ) -> None:
        self._base_url = _parse_url(base_url)
        self._origin = _origin(self._base_url)
        self._auth = httpx.BasicAuth(username, password)
        self._client = client or httpx.AsyncClient(timeout=30, trust_env=False)
        self._owns_client = client is None
        if max_response_bytes < 1 or max_response_bytes > MAX_RESPONSE_BYTES:
            raise _error("invalidLimit")
        self._max_response_bytes = max_response_bytes

    async def __aenter__(self) -> CalDavClient:
        return self

    async def __aexit__(self, *_args: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    def _url(self, href: str, *, relative_to: httpx.URL | None = None) -> httpx.URL:
        if (
            not isinstance(href, str)
            or not href
            or "?" in href
            or "#" in href
            or "\\" in href
            or any(character.isspace() or ord(character) < 32 for character in href)
        ):
            raise _error("invalidHref")
        try:
            url = httpx.URL(urljoin(str(relative_to or self._base_url), href))
        except (httpx.InvalidURL, ValueError) as exc:
            raise _error("invalidHref") from exc
        if url.scheme not in {"http", "https"} or not url.host or url.userinfo:
            raise _error("invalidHref")
        if _origin(url) != self._origin:
            raise _error("crossOrigin")
        return url

    def _collection_url(self, href: str, *, relative_to: httpx.URL | None = None) -> httpx.URL:
        url = self._url(href, relative_to=relative_to)
        return url if url.path.endswith("/") else url.copy_with(path=f"{url.path}/")

    async def _request(
        self,
        method: str,
        url: httpx.URL,
        *,
        headers: dict[str, str] | None = None,
        content: bytes | None = None,
        expected: set[int],
    ) -> tuple[bytes, httpx.Headers]:
        if _origin(url) != self._origin or url.userinfo or url.query or url.fragment:
            raise _error("crossOrigin")
        request = self._client.build_request(
            method,
            url,
            headers=headers,
            content=content,
        )
        try:
            response = await self._client.send(
                request,
                auth=self._auth,
                stream=True,
                follow_redirects=False,
            )
        except httpx.HTTPError as exc:
            raise _error(
                "outcomeUnknown" if method in {"PUT", "DELETE"} else "unavailable"
            ) from exc
        try:
            if 300 <= response.status_code < 400:
                raise _error("redirectRejected", status_code=response.status_code)
            if response.status_code not in expected:
                if response.status_code in {401, 403}:
                    code = "authenticationFailed"
                elif response.status_code == 404:
                    code = "notFound"
                elif response.status_code == 409:
                    code = "conflict"
                elif response.status_code == 412:
                    code = "preconditionFailed"
                else:
                    code = "unexpectedStatus"
                raise _error(code, status_code=response.status_code)
            length = response.headers.get("content-length")
            if length is not None:
                try:
                    if int(length) > self._max_response_bytes:
                        raise _error("responseTooLarge")
                except ValueError as exc:
                    raise _error("malformedResponse") from exc
            chunks: list[bytes] = []
            size = 0
            async for chunk in response.aiter_bytes():
                size += len(chunk)
                if size > self._max_response_bytes:
                    raise _error("responseTooLarge")
                chunks.append(chunk)
            return b"".join(chunks), response.headers
        finally:
            await response.aclose()

    def _xml(self, body: bytes) -> ElementTree.Element:
        if not body:
            raise _error("malformedXml")
        try:
            root = DefusedElementTree.fromstring(body)
        except (DefusedXmlException, ElementTree.ParseError, ValueError) as exc:
            raise _error("malformedXml") from exc
        if sum(1 for _element in root.iter()) > MAX_XML_ELEMENTS:
            raise _error("malformedXml")
        return root

    async def _propfind(self, url: httpx.URL, body: bytes, depth: int) -> ElementTree.Element:
        response, _headers = await self._request(
            "PROPFIND",
            url,
            headers={"Content-Type": "application/xml; charset=utf-8", "Depth": str(depth)},
            content=body,
            expected={207},
        )
        root = self._xml(response)
        if root.tag != f"{{{DAV}}}multistatus":
            raise _error("malformedResponse")
        return root

    async def discover(self) -> CalDavDiscovery:
        principal_request = ElementTree.tostring(
            ElementTree.fromstring(
                f'<propfind xmlns="{DAV}"><prop><current-user-principal/>'
                "<principal-URL/></prop></propfind>"
            ),
            encoding="utf-8",
            xml_declaration=True,
        )
        principal_root = await self._propfind(self._base_url, principal_request, 0)
        principal_href: str | None = None
        for response in principal_root.findall(f"{{{DAV}}}response"):
            principal = _property(response, f"{{{DAV}}}current-user-principal")
            if principal is None:
                principal = _property(response, f"{{{DAV}}}principal-URL")
            if principal is not None:
                principal_href = principal.findtext(f"{{{DAV}}}href")
                if principal_href:
                    break
        if not principal_href:
            raise _error("malformedResponse")
        principal_url = self._url(principal_href, relative_to=self._base_url)

        home_request = ElementTree.tostring(
            ElementTree.fromstring(
                f'<propfind xmlns="{DAV}" xmlns:c="{CALDAV}">'
                "<prop><c:calendar-home-set/></prop></propfind>"
            ),
            encoding="utf-8",
            xml_declaration=True,
        )
        home_root = await self._propfind(principal_url, home_request, 0)
        home_href: str | None = None
        for response in home_root.findall(f"{{{DAV}}}response"):
            home = _property(response, f"{{{CALDAV}}}calendar-home-set")
            if home is not None:
                home_href = home.findtext(f"{{{DAV}}}href")
                if home_href:
                    break
        if not home_href:
            raise _error("malformedResponse")
        home_url = self._collection_url(home_href, relative_to=principal_url)

        calendars_request = ElementTree.tostring(
            ElementTree.fromstring(
                f'<propfind xmlns="{DAV}" xmlns:c="{CALDAV}"><prop><resourcetype/>'
                "<displayname/><current-user-privilege-set/>"
                "<c:supported-calendar-component-set/></prop></propfind>"
            ),
            encoding="utf-8",
            xml_declaration=True,
        )
        calendars_root = await self._propfind(home_url, calendars_request, 1)
        calendars: list[CalDavCalendar] = []
        for response in calendars_root.findall(f"{{{DAV}}}response"):
            resource_type = _property(response, f"{{{DAV}}}resourcetype")
            if resource_type is None or resource_type.find(f"{{{CALDAV}}}calendar") is None:
                continue
            components = _property(response, f"{{{CALDAV}}}supported-calendar-component-set")
            supported_components = (
                frozenset(
                    name
                    for component in components.findall(f"{{{CALDAV}}}comp")
                    if (name := component.get("name", "").upper())
                    in {"VEVENT", "VTODO"}
                )
                if components is not None
                else frozenset({"VEVENT", "VTODO"})
            )
            if not supported_components:
                continue
            href = response.findtext(f"{{{DAV}}}href")
            if not href:
                raise _error("malformedResponse")
            calendar_url = self._collection_url(href, relative_to=home_url)
            canonical_href = str(calendar_url)
            display_name = _property(response, f"{{{DAV}}}displayname")
            name = (display_name.text or "").strip() if display_name is not None else ""
            calendars.append(
                CalDavCalendar(
                    key=hashlib.sha256(canonical_href.encode()).hexdigest()[:32],
                    href=canonical_href,
                    name=name,
                    privileges=_privileges(
                        _property(response, f"{{{DAV}}}current-user-privilege-set")
                    ),
                    supported_components=supported_components,
                )
            )
        calendars.sort(key=lambda calendar: calendar.key)
        return CalDavDiscovery(str(principal_url), str(home_url), tuple(calendars))

    async def query_events(
        self,
        calendar: CalDavCalendar,
        start: datetime,
        end: datetime,
    ) -> tuple[CalDavEvent, ...]:
        if (
            start.tzinfo is None
            or start.utcoffset() is None
            or end.tzinfo is None
            or end.utcoffset() is None
            or end <= start
            or end - start > MAX_QUERY_SPAN
        ):
            raise _error("invalidTimeRange")
        start_value = start.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
        end_value = end.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
        query = ElementTree.tostring(
            ElementTree.fromstring(
                f'<c:calendar-query xmlns="{DAV}" xmlns:c="{CALDAV}"><prop><getetag/>'
                f'<c:calendar-data><c:expand start="{start_value}" end="{end_value}"/>'
                "</c:calendar-data></prop><c:filter><c:comp-filter name=\"VCALENDAR\">"
                f'<c:comp-filter name="VEVENT"><c:time-range start="{start_value}" '
                f'end="{end_value}"/></c:comp-filter></c:comp-filter></c:filter>'
                "</c:calendar-query>"
            ),
            encoding="utf-8",
            xml_declaration=True,
        )
        calendar_url = self._collection_url(calendar.href)
        body, _headers = await self._request(
            "REPORT",
            calendar_url,
            headers={"Content-Type": "application/xml; charset=utf-8", "Depth": "1"},
            content=query,
            expected={207},
        )
        root = self._xml(body)
        if root.tag != f"{{{DAV}}}multistatus":
            raise _error("malformedResponse")
        events: list[CalDavEvent] = []
        for response in root.findall(f"{{{DAV}}}response"):
            if len(events) >= MAX_EVENTS:
                raise _error("tooManyEvents")
            href = response.findtext(f"{{{DAV}}}href")
            calendar_data = _property(response, f"{{{CALDAV}}}calendar-data")
            if not href or calendar_data is None or not calendar_data.text:
                continue
            event_url = self._url(href, relative_to=calendar_url)
            etag_value = _property(response, f"{{{DAV}}}getetag")
            etag = etag_value.text.strip() if etag_value is not None and etag_value.text else None
            try:
                parsed = ICalendar.from_ical(calendar_data.text)
                components = [component for component in parsed.walk() if component.name == "VEVENT"]
                multiple = len(components) > 1
                for component in components:
                    if len(events) >= MAX_EVENTS:
                        raise _error("tooManyEvents")
                    uid = str(component.get("uid", "")).strip()
                    start_value = component.decoded("dtstart")
                    end_property = component.get("dtend")
                    end_value = end_property.dt if end_property is not None else None
                    if end_value is None and component.get("duration") is not None:
                        end_value = start_value + component.decoded("duration")
                    if not uid or not isinstance(start_value, (date, datetime)) or (
                        end_value is not None and not isinstance(end_value, (date, datetime))
                    ):
                        raise ValueError
                    all_day = isinstance(start_value, date) and not isinstance(start_value, datetime)
                    if all_day:
                        if end_value is not None and isinstance(end_value, datetime):
                            raise ValueError
                    else:
                        if (
                            not isinstance(start_value, datetime)
                            or start_value.tzinfo is None
                            or start_value.utcoffset() is None
                            or (
                                end_value is not None
                                and (
                                    not isinstance(end_value, datetime)
                                    or end_value.tzinfo is None
                                    or end_value.utcoffset() is None
                                )
                            )
                        ):
                            raise ValueError
                        start_value = start_value.astimezone(UTC)
                        end_value = (
                            end_value.astimezone(UTC) if end_value is not None else None
                        )
                    reminders, alarms_safe = _event_reminders(component)
                    events.append(
                        CalDavEvent(
                            uid=uid,
                            href=str(event_url),
                            etag=etag,
                            summary=str(component.get("summary", "")),
                            start=start_value,
                            end=end_value,
                            all_day=all_day,
                            description=(
                                str(component.get("description"))
                                if component.get("description") is not None
                                else None
                            ),
                            location=(
                                str(component.get("location"))
                                if component.get("location") is not None
                                else None
                            ),
                            reminders_minutes_before=reminders,
                            recurring=multiple
                            or component.get("rrule") is not None
                            or component.get("recurrence-id") is not None,
                            scheduled=component.get("organizer") is not None
                            or component.get("attendee") is not None,
                            mutation_safe=(
                                alarms_safe
                                and {str(key).upper() for key in component.keys()}
                                <= {
                                    "UID",
                                    "DTSTAMP",
                                    "DTSTART",
                                    "DTEND",
                                    "DURATION",
                                    "SUMMARY",
                                    "DESCRIPTION",
                                    "LOCATION",
                                    "CREATED",
                                    "LAST-MODIFIED",
                                    "SEQUENCE",
                                    "STATUS",
                                    "TRANSP",
                                }
                            ),
                        )
                    )
            except CalDavError:
                raise
            except Exception as exc:
                raise _error("malformedCalendarData") from exc
        return tuple(events)

    async def query_tasks(
        self,
        calendar: CalDavCalendar,
        *,
        include_completed: bool = False,
    ) -> tuple[CalDavTask, ...]:
        query = ElementTree.tostring(
            ElementTree.fromstring(
                f'<c:calendar-query xmlns="{DAV}" xmlns:c="{CALDAV}"><prop>'
                "<getetag/><c:calendar-data/></prop><c:filter>"
                '<c:comp-filter name="VCALENDAR"><c:comp-filter name="VTODO"/>'
                "</c:comp-filter></c:filter></c:calendar-query>"
            ),
            encoding="utf-8",
            xml_declaration=True,
        )
        calendar_url = self._collection_url(calendar.href)
        body, _headers = await self._request(
            "REPORT",
            calendar_url,
            headers={"Content-Type": "application/xml; charset=utf-8", "Depth": "1"},
            content=query,
            expected={207},
        )
        root = self._xml(body)
        if root.tag != f"{{{DAV}}}multistatus":
            raise _error("malformedResponse")
        tasks: list[CalDavTask] = []
        task_count = 0
        for response in root.findall(f"{{{DAV}}}response"):
            href = response.findtext(f"{{{DAV}}}href")
            calendar_data = _property(response, f"{{{CALDAV}}}calendar-data")
            if not href or calendar_data is None or not calendar_data.text:
                continue
            task_url = self._url(href, relative_to=calendar_url)
            etag_value = _property(response, f"{{{DAV}}}getetag")
            etag = etag_value.text.strip() if etag_value is not None and etag_value.text else None
            try:
                parsed = ICalendar.from_ical(calendar_data.text)
                calendar_safe = {str(key).upper() for key in parsed.keys()} <= {
                    "PRODID",
                    "VERSION",
                    "CALSCALE",
                }
                components = [component for component in parsed.walk() if component.name == "VTODO"]
                primary_components = [
                    component
                    for component in parsed.subcomponents
                    if component.name != "VTIMEZONE"
                ]
                single_task = len(primary_components) == 1 and len(components) == 1
                for component in components:
                    task_count += 1
                    if task_count > MAX_EVENTS:
                        raise _error("tooManyEvents")
                    uid = str(component.get("uid", "")).strip()
                    due_property = component.get("due")
                    due = due_property.dt if due_property is not None else None
                    completed_property = component.get("completed")
                    completed_at = (
                        completed_property.dt if completed_property is not None else None
                    )
                    created_property = component.get("created")
                    created = created_property.dt if created_property is not None else None
                    sequence = int(component.get("sequence", 0))
                    if not uid or (due is not None and not isinstance(due, (date, datetime))):
                        raise ValueError
                    if isinstance(due, datetime):
                        if due.tzinfo is not None and due.utcoffset() is not None:
                            due = due.astimezone(UTC)
                    if completed_at is not None:
                        if (
                            not isinstance(completed_at, datetime)
                            or completed_at.tzinfo is None
                            or completed_at.utcoffset() is None
                        ):
                            raise ValueError
                        completed_at = completed_at.astimezone(UTC)
                    if created is not None:
                        if (
                            not isinstance(created, datetime)
                            or created.tzinfo is None
                            or created.utcoffset() is None
                        ):
                            raise ValueError
                        created = created.astimezone(UTC)
                    if sequence < 0:
                        raise ValueError
                    status = str(component.get("status", "")).upper()
                    completed = status == "COMPLETED"
                    if include_completed or not completed:
                        recurring = any(
                            component.get(name) is not None
                            for name in ("rrule", "recurrence-id", "rdate", "exdate")
                        )
                        tasks.append(
                            CalDavTask(
                                uid=uid,
                                href=str(task_url),
                                etag=etag,
                                title=str(component.get("summary", "")),
                                description=(
                                    str(component.get("description"))
                                    if component.get("description") is not None
                                    else None
                                ),
                                due=due,
                                completed=completed,
                                completed_at=completed_at,
                                created=created,
                                sequence=sequence,
                                recurring=recurring,
                                mutation_safe=(
                                    single_task
                                    and calendar_safe
                                    and _task_properties_are_safe(component)
                                    and not recurring
                                    and component.get("related-to") is None
                                    and not component.subcomponents
                                    and status in {"", "NEEDS-ACTION", "COMPLETED"}
                                    and (completed or completed_at is None)
                                    and len(str(component.get("summary", ""))) <= 500
                                    and len(str(component.get("description", ""))) <= 10_000
                                    and {str(key).upper() for key in component.keys()}
                                    <= {
                                        "UID",
                                        "DTSTAMP",
                                        "DUE",
                                        "SUMMARY",
                                        "DESCRIPTION",
                                        "STATUS",
                                        "COMPLETED",
                                        "CREATED",
                                        "LAST-MODIFIED",
                                        "SEQUENCE",
                                    }
                                ),
                            )
                        )
            except CalDavError:
                raise
            except Exception as exc:
                raise _error("malformedCalendarData") from exc
        return tuple(tasks)

    def _calendar_data(self, uid: str, draft: CalDavEventDraft) -> bytes:
        all_day = isinstance(draft.start, date) and not isinstance(draft.start, datetime)
        end_all_day = isinstance(draft.end, date) and not isinstance(draft.end, datetime)
        if all_day != end_all_day or draft.end <= draft.start:
            raise _error("invalidEvent")
        if not all_day and (
            draft.start.tzinfo is None
            or draft.start.utcoffset() is None
            or draft.end.tzinfo is None
            or draft.end.utcoffset() is None
        ):
            raise _error("invalidEvent")
        calendar = ICalendar()
        calendar.add("prodid", "-//MatoLoop//CalDAV Client//EN")
        calendar.add("version", "2.0")
        event = ICalendarEvent()
        event.add("uid", uid)
        event.add("dtstamp", datetime.now(UTC))
        event.add(
            "dtstart",
            draft.start.astimezone(UTC)
            if isinstance(draft.start, datetime)
            else draft.start,
        )
        event.add(
            "dtend",
            draft.end.astimezone(UTC)
            if isinstance(draft.end, datetime)
            else draft.end,
        )
        event.add("summary", draft.summary)
        if draft.description is not None:
            event.add("description", draft.description)
        if draft.location is not None:
            event.add("location", draft.location)
        for minutes in draft.reminders_minutes_before:
            alarm = ICalendarAlarm()
            alarm.add("action", "DISPLAY")
            alarm.add("trigger", -timedelta(minutes=minutes))
            alarm.add("description", draft.summary)
            event.add_component(alarm)
        calendar.add_component(event)
        return calendar.to_ical()

    def _task_calendar_data(self, uid: str, draft: CalDavTaskDraft) -> bytes:
        due = draft.due
        if isinstance(due, datetime):
            if due.tzinfo is not None and due.utcoffset() is not None:
                due = due.astimezone(UTC)
        completed_at = draft.completed_at
        if completed_at is not None:
            if (
                not draft.completed
                or completed_at.tzinfo is None
                or completed_at.utcoffset() is None
            ):
                raise _error("invalidEvent")
            completed_at = completed_at.astimezone(UTC)
        created = draft.created
        if created is not None:
            if created.tzinfo is None or created.utcoffset() is None:
                raise _error("invalidEvent")
            created = created.astimezone(UTC)
        if draft.sequence < 0:
            raise _error("invalidEvent")
        calendar = ICalendar()
        calendar.add("prodid", "-//MatoLoop//CalDAV Client//EN")
        calendar.add("version", "2.0")
        task = ICalendarTodo()
        task.add("uid", uid)
        now = datetime.now(UTC)
        task.add("dtstamp", now)
        task.add("created", created or now)
        task.add("last-modified", now)
        task.add("sequence", draft.sequence)
        task.add("summary", draft.title)
        if draft.description is not None:
            task.add("description", draft.description)
        if due is not None:
            task.add("due", due)
        task.add("status", "COMPLETED" if draft.completed else "NEEDS-ACTION")
        if completed_at is not None:
            task.add("completed", completed_at)
        calendar.add_component(task)
        return calendar.to_ical()

    async def create_event(
        self,
        calendar: CalDavCalendar,
        draft: CalDavEventDraft,
        idempotency_key: UUID,
    ) -> CalDavEvent:
        uid = f"{idempotency_key}@mato-loop"
        collection_url = self._collection_url(calendar.href)
        event_url = self._url(f"{idempotency_key}.ics", relative_to=collection_url)
        _body, headers = await self._request(
            "PUT",
            event_url,
            headers={"Content-Type": "text/calendar; charset=utf-8", "If-None-Match": "*"},
            content=self._calendar_data(uid, draft),
            expected={201, 204},
        )
        return self._event_from_draft(uid, str(event_url), headers.get("etag"), draft)

    async def update_event(
        self,
        existing: CalDavEvent,
        draft: CalDavEventDraft,
    ) -> CalDavEvent:
        if not existing.etag:
            raise _error("etagRequired")
        if existing.recurring or existing.scheduled or not existing.mutation_safe:
            raise _error("unsupportedMutation")
        event_url = self._url(existing.href)
        _body, headers = await self._request(
            "PUT",
            event_url,
            headers={
                "Content-Type": "text/calendar; charset=utf-8",
                "If-Match": existing.etag,
            },
            content=self._calendar_data(existing.uid, draft),
            expected={200, 201, 204},
        )
        return self._event_from_draft(
            existing.uid, str(event_url), headers.get("etag", existing.etag), draft
        )

    async def delete_event(self, existing: CalDavEvent) -> None:
        if not existing.etag:
            raise _error("etagRequired")
        if existing.recurring or existing.scheduled:
            raise _error("unsupportedMutation")
        await self._request(
            "DELETE",
            self._url(existing.href),
            headers={"If-Match": existing.etag},
            expected={200, 204, 404},
        )

    async def create_task(
        self,
        calendar: CalDavCalendar,
        draft: CalDavTaskDraft,
        idempotency_key: UUID,
    ) -> CalDavTask:
        draft = replace(draft, created=draft.created or datetime.now(UTC))
        uid = f"{idempotency_key}@mato-loop"
        collection_url = self._collection_url(calendar.href)
        task_url = self._url(f"{idempotency_key}.ics", relative_to=collection_url)
        _body, headers = await self._request(
            "PUT",
            task_url,
            headers={"Content-Type": "text/calendar; charset=utf-8", "If-None-Match": "*"},
            content=self._task_calendar_data(uid, draft),
            expected={201, 204},
        )
        return self._task_from_draft(uid, str(task_url), headers.get("etag"), draft)

    async def update_task(
        self,
        existing: CalDavTask,
        draft: CalDavTaskDraft,
    ) -> CalDavTask:
        if not existing.etag:
            raise _error("etagRequired")
        if existing.recurring or not existing.mutation_safe:
            raise _error("unsupportedMutation")
        task_url = self._url(existing.href)
        _body, headers = await self._request(
            "PUT",
            task_url,
            headers={
                "Content-Type": "text/calendar; charset=utf-8",
                "If-Match": existing.etag,
            },
            content=self._task_calendar_data(existing.uid, draft),
            expected={200, 201, 204},
        )
        return self._task_from_draft(
            existing.uid, str(task_url), headers.get("etag"), draft
        )

    async def delete_task(self, existing: CalDavTask) -> None:
        if not existing.etag:
            raise _error("etagRequired")
        if existing.recurring or not existing.mutation_safe:
            raise _error("unsupportedMutation")
        await self._request(
            "DELETE",
            self._url(existing.href),
            headers={"If-Match": existing.etag},
            expected={200, 204, 404},
        )

    @staticmethod
    def _event_from_draft(
        uid: str,
        href: str,
        etag: str | None,
        draft: CalDavEventDraft,
    ) -> CalDavEvent:
        start = (
            draft.start.astimezone(UTC)
            if isinstance(draft.start, datetime)
            else draft.start
        )
        end = (
            draft.end.astimezone(UTC)
            if isinstance(draft.end, datetime)
            else draft.end
        )
        return CalDavEvent(
            uid=uid,
            href=href,
            etag=etag,
            summary=draft.summary,
            start=start,
            end=end,
            all_day=isinstance(draft.start, date) and not isinstance(draft.start, datetime),
            description=draft.description,
            location=draft.location,
            reminders_minutes_before=draft.reminders_minutes_before,
        )

    @staticmethod
    def _task_from_draft(
        uid: str,
        href: str,
        etag: str | None,
        draft: CalDavTaskDraft,
    ) -> CalDavTask:
        due = draft.due
        if (
            isinstance(due, datetime)
            and due.tzinfo is not None
            and due.utcoffset() is not None
        ):
            due = due.astimezone(UTC)
        completed_at = (
            draft.completed_at.astimezone(UTC) if draft.completed_at is not None else None
        )
        return CalDavTask(
            uid=uid,
            href=href,
            etag=etag,
            title=draft.title,
            description=draft.description,
            due=due,
            completed=draft.completed,
            completed_at=completed_at,
            created=draft.created,
            sequence=draft.sequence,
        )
