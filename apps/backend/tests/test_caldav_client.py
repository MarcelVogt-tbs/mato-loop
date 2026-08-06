from datetime import UTC, date, datetime
from uuid import UUID

import httpx
import pytest

from mato_loop_backend.integrations.caldav_client import (
    CalDavCalendar,
    CalDavClient,
    CalDavError,
    CalDavEvent,
    CalDavEventDraft,
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
        calendar_href = (
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
                    calendar_href,
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
    assert first.calendars[0].key == second.calendars[0].key
    assert first.calendars[0].privileges == frozenset({"read", "write", "delete"})
    assert all(request.method == "PROPFIND" for request in requests)
    assert [request.headers["depth"] for request in requests[:3]] == ["0", "0", "1"]
    assert requests[0].headers["authorization"].startswith("Basic ")


async def test_report_query_parses_timed_and_all_day_events_with_etags() -> None:
    captured: httpx.Request | None = None
    timed = """BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
UID:timed-1
DTSTART:20260806T090000Z
DTEND:20260806T100000Z
SUMMARY:Standup
LOCATION:Room 1
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
    assert events[0].start == datetime(2026, 8, 6, 9, tzinfo=UTC)
    assert events[0].etag == '"timed-etag"'
    assert events[0].all_day is False
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
        start=datetime(2026, 8, 6, 9, tzinfo=UTC),
        end=datetime(2026, 8, 6, 10, tzinfo=UTC),
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
    assert requests[1].headers["if-match"] == '"created"'
    assert requests[2].headers["if-match"] == '"updated"'
    assert [request.method for request in requests] == ["PUT", "PUT", "DELETE"]


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
