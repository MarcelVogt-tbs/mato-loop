from datetime import UTC, date, datetime, timedelta, timezone
from uuid import UUID

import httpx
import pytest

from mato_loop_backend.integrations.caldav.client import (
    CalDavCalendar,
    CalDavClient,
    CalDavError,
    CalDavEvent,
    CalDavEventDraft,
    CalDavTask,
    CalDavTaskDraft,
)


def _response(properties: str, href: str) -> str:
    return (
        f"<d:response><d:href>{href}</d:href><d:propstat><d:prop>{properties}</d:prop>"
        "<d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response>"
    )


def _multistatus(*responses: str) -> str:
    return (
        '<d:multistatus xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">'
        + "".join(responses)
        + "</d:multistatus>"
    )


def _calendar() -> CalDavCalendar:
    return CalDavCalendar(
        key="opaque",
        href="https://calendar.example.test/home/work/",
        name="Work",
        privileges=frozenset({"read", "write", "delete"}),
    )


async def test_discovery_is_same_origin_stable_and_maps_known_privileges() -> None:
    requests: list[httpx.Request] = []
    discovery_number = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal discovery_number
        requests.append(request)
        step = len(requests) % 3
        if step == 1:
            discovery_number += 1
            return httpx.Response(
                207,
                text=_multistatus(
                    _response(
                        "<d:current-user-principal><d:href>/principals/alice/</d:href>"
                        "</d:current-user-principal>",
                        "/dav/",
                    )
                ),
            )
        if step == 2:
            return httpx.Response(
                207,
                text=_multistatus(
                    _response(
                        "<c:calendar-home-set><d:href>/home/alice/</d:href>"
                        "</c:calendar-home-set>",
                        "/principals/alice/",
                    )
                ),
            )
        event_href = (
            "/home/alice/work/"
            if discovery_number == 1
            else "https://calendar.example.test/home/alice/work/"
        )
        return httpx.Response(
            207,
            text=_multistatus(
                _response("<d:resourcetype><d:collection/></d:resourcetype>", "/home/alice/"),
                _response(
                    "<d:resourcetype><d:collection/><c:calendar/></d:resourcetype>"
                    "<d:displayname>Work</d:displayname>"
                    "<c:supported-calendar-component-set><c:comp name=\"VEVENT\"/>"
                    "</c:supported-calendar-component-set>"
                    "<d:current-user-privilege-set>"
                    "<d:privilege><d:read/></d:privilege>"
                    "<d:privilege><d:write-content/></d:privilege>"
                    "<d:privilege><d:unbind/></d:privilege>"
                    "<d:privilege><c:schedule-send/></d:privilege>"
                    "</d:current-user-privilege-set>",
                    event_href,
                ),
                _response(
                    "<d:resourcetype><d:collection/><c:calendar/></d:resourcetype>"
                    "<d:displayname>Tasks</d:displayname>"
                    "<c:supported-calendar-component-set><c:comp name=\"VTODO\"/>"
                    "</c:supported-calendar-component-set>",
                    "/home/alice/tasks/",
                ),
                _response(
                    "<d:resourcetype><d:collection/><c:calendar/></d:resourcetype>"
                    "<d:displayname>Mixed</d:displayname>"
                    "<c:supported-calendar-component-set><c:comp name=\"VEVENT\"/>"
                    "<c:comp name=\"VTODO\"/></c:supported-calendar-component-set>",
                    "/home/alice/mixed/",
                ),
                _response(
                    "<d:resourcetype><d:collection/><c:calendar/></d:resourcetype>"
                    "<d:displayname>Universal</d:displayname>",
                    "/home/alice/universal/",
                ),
                _response(
                    "<d:resourcetype><d:collection/><c:calendar/></d:resourcetype>"
                    "<c:supported-calendar-component-set><c:comp name=\"VJOURNAL\"/>"
                    "</c:supported-calendar-component-set>",
                    "/home/alice/journal/",
                ),
            ),
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = CalDavClient(
            "https://calendar.example.test/dav/", "alice", "secret", client=http
        )
        first = await client.discover()
        second = await client.discover()

    assert first.principal_href == "https://calendar.example.test/principals/alice/"
    assert first.calendar_home_href == "https://calendar.example.test/home/alice/"
    first_by_name = {calendar.name: calendar for calendar in first.calendars}
    second_by_name = {calendar.name: calendar for calendar in second.calendars}
    assert first_by_name.keys() == {"Work", "Tasks", "Mixed", "Universal"}
    assert first_by_name["Work"].key == second_by_name["Work"].key
    assert first_by_name["Work"].privileges == frozenset({"read", "write", "delete"})
    assert first_by_name["Work"].supported_components == {"VEVENT"}
    assert first_by_name["Tasks"].supported_components == {"VTODO"}
    assert first_by_name["Mixed"].supported_components == {"VEVENT", "VTODO"}
    assert first_by_name["Universal"].supported_components == {"VEVENT", "VTODO"}
    assert all(request.method == "PROPFIND" for request in requests)
    assert [request.headers["depth"] for request in requests[:3]] == ["0", "0", "1"]
    assert requests[0].headers["authorization"].startswith("Basic ")


async def test_report_query_parses_timed_and_all_day_events_with_etags() -> None:
    captured: httpx.Request | None = None
    timed = """BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
UID:timed-1
DTSTART;TZID=Europe/Berlin:20260806T090000
DTEND;TZID=Europe/Berlin:20260806T100000
SUMMARY:Standup
LOCATION:Room 1
BEGIN:VALARM
ACTION:DISPLAY
TRIGGER:-P1D
DESCRIPTION:Standup
END:VALARM
BEGIN:VALARM
ACTION:DISPLAY
TRIGGER:-PT1H
DESCRIPTION:Standup
END:VALARM
END:VEVENT
END:VCALENDAR
"""
    all_day = """BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
UID:day-1
DTSTART;VALUE=DATE:20260807
DTEND;VALUE=DATE:20260808
SUMMARY:Holiday
END:VEVENT
END:VCALENDAR
"""

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal captured
        captured = request
        return httpx.Response(
            207,
            text=_multistatus(
                _response(
                    f"<d:getetag>&quot;timed-etag&quot;</d:getetag>"
                    f"<c:calendar-data><![CDATA[{timed}]]></c:calendar-data>",
                    "/home/work/timed.ics",
                ),
                _response(
                    f"<d:getetag>&quot;day-etag&quot;</d:getetag>"
                    f"<c:calendar-data><![CDATA[{all_day}]]></c:calendar-data>",
                    "/home/work/day.ics",
                ),
            ),
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        events = await CalDavClient(
            "https://calendar.example.test/", "alice", "secret", client=http
        ).query_events(
            _calendar(),
            datetime(2026, 8, 1, tzinfo=UTC),
            datetime(2026, 9, 1, tzinfo=UTC),
        )

    assert captured is not None
    assert captured.method == "REPORT"
    assert captured.headers["depth"] == "1"
    assert b'start="20260801T000000Z"' in captured.content
    assert b'end="20260901T000000Z"' in captured.content
    assert b"expand" in captured.content
    assert events[0].start == datetime(2026, 8, 6, 7, tzinfo=UTC)
    assert events[0].etag == '"timed-etag"'
    assert events[0].all_day is False
    assert events[0].reminders_minutes_before == (1440, 60)
    assert events[0].mutation_safe is True
    assert events[1].start == date(2026, 8, 7)
    assert events[1].end == date(2026, 8, 8)
    assert events[1].all_day is True


async def test_conditional_writes_use_deterministic_uid_and_href() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        etag = '"created"' if len(requests) == 1 else '"updated"'
        return httpx.Response(201 if len(requests) == 1 else 204, headers={"ETag": etag})

    key = UUID("12345678-1234-5678-1234-567812345678")
    draft = CalDavEventDraft(
        summary="Planning",
        start=datetime(2026, 8, 6, 9, tzinfo=timezone(timedelta(hours=2))),
        end=datetime(2026, 8, 6, 10, tzinfo=timezone(timedelta(hours=2))),
        reminders_minutes_before=(1440, 60),
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = CalDavClient(
            "https://calendar.example.test/", "alice", "secret", client=http
        )
        created = await client.create_event(_calendar(), draft, key)
        updated = await client.update_event(created, draft)
        await client.delete_event(updated)

    assert requests[0].url.path.endswith("/12345678-1234-5678-1234-567812345678.ics")
    assert requests[0].headers["if-none-match"] == "*"
    assert b"UID:12345678-1234-5678-1234-567812345678@mato-loop" in requests[0].content
    assert b"DTSTART:20260806T070000Z" in requests[0].content
    assert b"DTEND:20260806T080000Z" in requests[0].content
    assert b"TZID" not in requests[0].content
    assert requests[0].content.count(b"BEGIN:VALARM") == 2
    assert b"TRIGGER:-P1D" in requests[0].content
    assert b"TRIGGER:-PT1H" in requests[0].content
    assert b"DTSTART:20260806T070000Z" in requests[1].content
    assert created.start == datetime(2026, 8, 6, 7, tzinfo=UTC)
    assert created.reminders_minutes_before == (1440, 60)
    assert updated.end == datetime(2026, 8, 6, 8, tzinfo=UTC)
    assert requests[1].headers["if-match"] == '"created"'
    assert requests[2].headers["if-match"] == '"updated"'
    assert [request.method for request in requests] == ["PUT", "PUT", "DELETE"]


async def test_task_query_parses_due_and_filters_completed_tasks() -> None:
    requests: list[httpx.Request] = []
    active = """BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VTODO
UID:active-1
CREATED:20260801T080000Z
LAST-MODIFIED:20260802T080000Z
SEQUENCE:4
DUE;VALUE=DATE:20260807
SUMMARY:Buy milk
DESCRIPTION:Two bottles
END:VTODO
END:VCALENDAR
"""
    completed = """BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VTODO
UID:completed-1
DUE;TZID=Europe/Berlin:20260808T090000
SUMMARY:File report
STATUS:COMPLETED
COMPLETED:20260808T080000Z
RRULE:FREQ=DAILY
RELATED-TO:parent-1
BEGIN:VALARM
ACTION:DISPLAY
TRIGGER:-PT15M
DESCRIPTION:Reminder
END:VALARM
END:VTODO
BEGIN:VEVENT
UID:mixed-event
DTSTART;VALUE=DATE:20260808
SUMMARY:Mixed resource
END:VEVENT
END:VCALENDAR
"""

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            207,
            text=_multistatus(
                _response(
                    "<d:getetag>&quot;active&quot;</d:getetag>"
                    f"<c:calendar-data><![CDATA[{active}]]></c:calendar-data>",
                    "/home/work/active.ics",
                ),
                _response(
                    "<d:getetag>&quot;completed&quot;</d:getetag>"
                    f"<c:calendar-data><![CDATA[{completed}]]></c:calendar-data>",
                    "/home/work/completed.ics",
                ),
            ),
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = CalDavClient(
            "https://calendar.example.test/", "alice", "secret", client=http
        )
        pending = await client.query_tasks(_calendar())
        all_tasks = await client.query_tasks(_calendar(), include_completed=True)

    assert b'comp-filter name="VTODO"' in requests[0].content
    assert b"expand" not in requests[0].content
    assert len(pending) == 1
    assert pending[0].title == "Buy milk"
    assert pending[0].description == "Two bottles"
    assert pending[0].due == date(2026, 8, 7)
    assert pending[0].created == datetime(2026, 8, 1, 8, tzinfo=UTC)
    assert pending[0].sequence == 4
    assert pending[0].mutation_safe is True
    assert all_tasks[1].due == datetime(2026, 8, 8, 7, tzinfo=UTC)
    assert all_tasks[1].completed_at == datetime(2026, 8, 8, 8, tzinfo=UTC)
    assert all_tasks[1].completed is True
    assert all_tasks[1].recurring is True
    assert all_tasks[1].mutation_safe is False


async def test_task_query_preserves_floating_due() -> None:
    task_data = """BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VTODO
UID:floating-1
DUE:20260808T090000
SUMMARY:Local appointment
END:VTODO
END:VCALENDAR
"""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            207,
            text=_multistatus(
                _response(
                    "<d:getetag>&quot;floating&quot;</d:getetag>"
                    f"<c:calendar-data><![CDATA[{task_data}]]></c:calendar-data>",
                    "/home/work/floating.ics",
                )
            ),
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = CalDavClient(
            "https://calendar.example.test/", "alice", "secret", client=http
        )
        tasks = await client.query_tasks(_calendar())

    assert tasks[0].due == datetime(2026, 8, 8, 9)


async def test_task_query_marks_unrepresented_parameters_as_mutation_unsafe() -> None:
    task_data = """BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VTODO
UID:localized-1
SUMMARY;LANGUAGE=de:Einkaufen
END:VTODO
END:VCALENDAR
"""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            207,
            text=_multistatus(
                _response(
                    "<d:getetag>&quot;localized&quot;</d:getetag>"
                    f"<c:calendar-data><![CDATA[{task_data}]]></c:calendar-data>",
                    "/home/work/localized.ics",
                )
            ),
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = CalDavClient(
            "https://calendar.example.test/", "alice", "secret", client=http
        )
        tasks = await client.query_tasks(_calendar())

    assert tasks[0].title == "Einkaufen"
    assert tasks[0].mutation_safe is False


async def test_task_conditional_create_update_and_delete() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        etag = '"created"' if len(requests) == 1 else '"updated"'
        return httpx.Response(201 if len(requests) == 1 else 204, headers={"ETag": etag})

    key = UUID("87654321-4321-8765-4321-876543218765")
    draft = CalDavTaskDraft(
        title="File report",
        description="Before lunch",
        due=date(2026, 8, 8),
    )
    completed_at = datetime(2026, 8, 8, 10, tzinfo=timezone(timedelta(hours=2)))
    completed_draft = CalDavTaskDraft(
        title="File report",
        due=datetime(2026, 8, 8, 9, tzinfo=timezone(timedelta(hours=2))),
        completed=True,
        completed_at=completed_at,
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = CalDavClient(
            "https://calendar.example.test/", "alice", "secret", client=http
        )
        created = await client.create_task(_calendar(), draft, key)
        updated = await client.update_task(created, completed_draft)
        await client.delete_task(updated)

    assert requests[0].headers["if-none-match"] == "*"
    assert requests[0].url.path.endswith("/87654321-4321-8765-4321-876543218765.ics")
    assert b"BEGIN:VTODO" in requests[0].content
    assert b"UID:87654321-4321-8765-4321-876543218765@mato-loop" in requests[0].content
    assert b"DUE;VALUE=DATE:20260808" in requests[0].content
    assert b"STATUS:NEEDS-ACTION" in requests[0].content
    assert requests[1].headers["if-match"] == '"created"'
    assert b"DUE:20260808T070000Z" in requests[1].content
    assert b"STATUS:COMPLETED" in requests[1].content
    assert b"COMPLETED:20260808T080000Z" in requests[1].content
    assert updated.completed_at == datetime(2026, 8, 8, 8, tzinfo=UTC)
    assert requests[2].headers["if-match"] == '"updated"'
    assert [request.method for request in requests] == ["PUT", "PUT", "DELETE"]


async def test_task_delete_rejects_multi_component_resource() -> None:
    task = CalDavTask(
        uid="task-1",
        href="https://calendar.example.test/home/work/task-1.ics",
        etag='"etag"',
        title="Task",
        mutation_safe=False,
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _request: None)) as http:
        client = CalDavClient(
            "https://calendar.example.test/", "alice", "secret", client=http
        )
        with pytest.raises(CalDavError, match="caldav.unsupportedMutation"):
            await client.delete_task(task)


async def test_task_update_does_not_reuse_stale_etag_when_server_omits_one() -> None:
    existing = CalDavTask(
        uid="task-1",
        href="https://calendar.example.test/home/work/task-1.ics",
        etag='"old"',
        title="Task",
    )

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(204)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = CalDavClient(
            "https://calendar.example.test/", "alice", "secret", client=http
        )
        updated = await client.update_task(existing, CalDavTaskDraft(title="Updated"))

    assert updated.etag is None


def test_all_day_writes_remain_date_values() -> None:
    draft = CalDavEventDraft(
        summary="Holiday",
        start=date(2026, 8, 6),
        end=date(2026, 8, 7),
    )

    payload = CalDavClient._calendar_data(object(), "uid", draft)

    assert b"DTSTART;VALUE=DATE:20260806" in payload
    assert b"DTEND;VALUE=DATE:20260807" in payload


@pytest.mark.parametrize(
    "start",
    [
        "DTSTART:20260806T090000",
        'DTSTART;TZID="Unknown/Zone":20260806T090000',
    ],
)
async def test_query_rejects_timed_events_without_a_resolvable_timezone(start) -> None:
    calendar_data = f"""BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
UID:ambiguous
{start}
DTEND:20260806T100000
SUMMARY:Ambiguous
END:VEVENT
END:VCALENDAR
"""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            207,
            text=_multistatus(
                _response(
                    f"<c:calendar-data><![CDATA[{calendar_data}]]></c:calendar-data>",
                    "/home/work/ambiguous.ics",
                )
            ),
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(CalDavError, match="caldav.malformedCalendarData"):
            await CalDavClient(
                "https://calendar.example.test/", "alice", "secret", client=http
            ).query_events(
                _calendar(),
                datetime(2026, 8, 1, tzinfo=UTC),
                datetime(2026, 9, 1, tzinfo=UTC),
            )
async def test_cross_origin_discovery_href_is_rejected_without_following_it() -> None:
    requests = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        return httpx.Response(
            207,
            text=_multistatus(
                _response(
                    "<d:current-user-principal>"
                    "<d:href>https://evil.example/principal/</d:href>"
                    "</d:current-user-principal>",
                    "/dav/",
                )
            ),
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = CalDavClient(
            "https://calendar.example.test/dav/", "alice", "secret", client=http
        )
        with pytest.raises(CalDavError, match="caldav.crossOrigin"):
            await client.discover()

    assert requests == 1


@pytest.mark.parametrize(
    "url",
    [
        "http://calendar.example.test/",
        "https://alice@calendar.example.test/",
        "https://calendar.example.test/?view=calendar",
        "https://calendar.example.test/#calendar",
    ],
)
def test_base_url_rejects_userinfo_query_and_fragment(url: str) -> None:
    with pytest.raises(CalDavError, match="caldav.invalidUrl"):
        CalDavClient(url, "alice", "secret")


async def test_malformed_xml_has_controlled_error() -> None:
    transport = httpx.MockTransport(lambda _request: httpx.Response(207, content=b"<broken"))
    async with httpx.AsyncClient(transport=transport) as http:
        client = CalDavClient(
            "https://calendar.example.test/", "alice", "secret", client=http
        )
        with pytest.raises(CalDavError) as error:
            await client.discover()

    assert error.value.code == "caldav.malformedXml"
    assert "broken" not in str(error.value)


async def test_response_body_limit_is_enforced() -> None:
    transport = httpx.MockTransport(lambda _request: httpx.Response(207, content=b"x" * 65))
    async with httpx.AsyncClient(transport=transport) as http:
        client = CalDavClient(
            "https://calendar.example.test/",
            "alice",
            "secret",
            client=http,
            max_response_bytes=64,
        )
        with pytest.raises(CalDavError) as error:
            await client.discover()

    assert error.value.code == "caldav.responseTooLarge"


@pytest.mark.parametrize("status", [302, 500])
async def test_redirects_and_http_failures_return_safe_errors(status: int) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status,
            headers={"Location": "https://evil.example/"},
            text="server-secret-detail",
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = CalDavClient(
            "https://calendar.example.test/", "alice", "secret", client=http
        )
        with pytest.raises(CalDavError) as error:
            await client.discover()

    expected = "caldav.redirectRejected" if status == 302 else "caldav.unexpectedStatus"
    assert error.value.code == expected
    assert "server-secret-detail" not in str(error.value)


async def test_recurrence_mutations_remain_unsupported() -> None:
    existing = CalDavEvent(
        uid="recurring",
        href="https://calendar.example.test/home/work/recurring.ics",
        etag='"etag"',
        summary="Series",
        start=datetime(2026, 8, 6, 9, tzinfo=UTC),
        end=datetime(2026, 8, 6, 10, tzinfo=UTC),
        all_day=False,
        recurring=True,
    )
    draft = CalDavEventDraft(
        summary="Changed",
        start=existing.start,
        end=existing.end,
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _request: None)) as http:
        client = CalDavClient(
            "https://calendar.example.test/", "alice", "secret", client=http
        )
        with pytest.raises(CalDavError, match="caldav.unsupportedMutation"):
            await client.update_event(existing, draft)


async def test_update_rejects_unrepresented_calendar_data() -> None:
    existing = CalDavEvent(
        uid="alarm",
        href="https://calendar.example.test/home/work/alarm.ics",
        etag='"etag"',
        summary="Reminder",
        start=datetime(2026, 8, 6, 9, tzinfo=UTC),
        end=datetime(2026, 8, 6, 10, tzinfo=UTC),
        all_day=False,
        mutation_safe=False,
    )
    draft = CalDavEventDraft(
        summary="Changed",
        start=existing.start,
        end=existing.end,
    )
    transport = httpx.MockTransport(
        lambda _request: pytest.fail("unsafe update must not perform HTTP")
    )
    async with httpx.AsyncClient(transport=transport) as http:
        client = CalDavClient(
            "https://calendar.example.test/", "alice", "secret", client=http
        )
        with pytest.raises(CalDavError, match="caldav.unsupportedMutation"):
            await client.update_event(existing, draft)


async def test_write_transport_failure_reports_unknown_outcome() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("synthetic disconnect", request=request)

    draft = CalDavEventDraft(
        summary="Planning",
        start=datetime(2026, 8, 6, 9, tzinfo=UTC),
        end=datetime(2026, 8, 6, 10, tzinfo=UTC),
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = CalDavClient(
            "https://calendar.example.test/", "alice", "secret", client=http
        )
        with pytest.raises(CalDavError, match="caldav.outcomeUnknown"):
            await client.create_event(
                _calendar(), draft, UUID("12345678-1234-5678-1234-567812345678")
            )
