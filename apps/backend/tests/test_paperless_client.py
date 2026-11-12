import json
from email.parser import BytesParser
from email.policy import default

import httpx
import pytest

from mato_loop_backend.integrations.paperless.client import (
    MAX_FILE_BYTES,
    MAX_JSON_BYTES,
    PaperlessClient,
    PaperlessError,
)


def _client(handler, base_url="https://paperless.example.test/archive/"):
    return PaperlessClient(base_url, "test-token", transport=httpx.MockTransport(handler))


async def test_document_requests_preserve_api_root_and_negotiate_v9() -> None:
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"id": 7, "title": "Receipt"})

    async with _client(handler) as client:
        assert (await client.get_document(7, version=9))["id"] == 7
        await client.update_document(7, {"title": "Receipt"})
        await client.request("POST", "/api/documents/bulk_edit/", json={"method": "rotate"})

    assert str(requests[0].url) == "https://paperless.example.test/archive/api/documents/7/?version=9"
    assert requests[0].headers["Authorization"] == "Token test-token"
    assert requests[0].headers["Accept"] == "application/json; version=9"
    assert requests[1].method == "PATCH"
    assert json.loads(requests[1].content) == {"title": "Receipt"}
    assert requests[2].url.path == "/archive/api/documents/bulk_edit/"


@pytest.mark.parametrize("url", [
    "ftp://paperless.example.test", "https://user@paperless.example.test",
    "https://paperless.example.test?", "https://paperless.example.test#fragment",
    "https://paperless.example.test/\\outside", "https://paperless.example.test/\n",
])
def test_invalid_base_urls_are_rejected(url) -> None:
    with pytest.raises(PaperlessError, match="paperless.invalidUrl"):
        PaperlessClient(url, "test-token")


async def test_paths_cannot_escape_origin_or_api_root() -> None:
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={})

    async with _client(handler) as client:
        for path in (
            "https://evil.example/api/documents/", "//evil.example/documents/",
            "../token/", "documents/../../token/", "documents/%2e%2e/token/",
            "documents/%252f/", "documents/\\outside", "documents/?page=2",
            "documents/#fragment",
        ):
            with pytest.raises(PaperlessError, match="paperless.invalidRequest"):
                await client.request("GET", path)
        with pytest.raises(PaperlessError, match="paperless.invalidRequest"):
            await client.list("../users")
    assert requests == []


async def test_redirect_is_not_followed_or_token_forwarded() -> None:
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(302, headers={"Location": "https://evil.example/"})

    async with _client(handler) as client:
        with pytest.raises(PaperlessError, match="paperless.redirectRejected"):
            await client.get_document(7)
    assert [request.url.host for request in requests] == ["paperless.example.test"]


@pytest.mark.parametrize("method, transport_failure, code, status", [
    ("PATCH", False, "paperless.outcomeUnknown", 503),
    ("POST", False, "paperless.authenticationFailed", 401),
    ("GET", True, "paperless.requestFailed", None),
    ("POST", True, "paperless.outcomeUnknown", None),
    ("PATCH", True, "paperless.outcomeUnknown", None),
    ("PUT", True, "paperless.outcomeUnknown", None),
    ("DELETE", True, "paperless.outcomeUnknown", None),
])
async def test_error_payloads_are_not_exposed_and_mutations_are_not_retried(
    method, transport_failure, code, status,
) -> None:
    requests = []

    def handler(request):
        requests.append(request)
        if transport_failure:
            raise httpx.ReadTimeout("private-server-diagnostics", request=request)
        return httpx.Response(status, text="private-server-diagnostics")

    async with _client(handler) as client:
        with pytest.raises(PaperlessError) as error:
            await client.request(method, "documents/7/", json={"title": "New"})
    assert (str(error.value), error.value.status_code, len(requests)) == (
        code, status, 1,
    )


async def test_bulk_edit_400_has_unknown_outcome_without_retry() -> None:
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(400, text="private-post-effect-error")

    async with _client(handler) as client:
        with pytest.raises(PaperlessError) as error:
            await client.request("POST", "documents/bulk_edit/", json={"method": "rotate"})
    assert (error.value.code, error.value.status_code, len(requests)) == (
        "paperless.outcomeUnknown", 400, 1,
    )


class _OversizedStream(httpx.AsyncByteStream):
    def __init__(self, limit):
        self.limit = limit
        self.produced = 0

    async def __aiter__(self):
        while self.produced <= self.limit + 1024 * 1024:
            self.produced += 64 * 1024
            yield b"x" * (64 * 1024)


@pytest.mark.parametrize("file_response, method, code", [
    (False, "GET", "paperless.responseTooLarge"),
    (True, "GET", "paperless.responseTooLarge"),
    (False, "POST", "paperless.outcomeUnknown"),
])
async def test_unknown_length_streams_stop_at_size_limit(file_response, method, code) -> None:
    limit = MAX_FILE_BYTES if file_response else MAX_JSON_BYTES
    stream = _OversizedStream(limit)

    def handler(_request):
        return httpx.Response(200, stream=stream)

    async with _client(handler) as client:
        with pytest.raises(PaperlessError, match=code):
            if file_response:
                await client.download(7)
            else:
                await client.request(method, "documents/7/")
    assert stream.produced <= limit + 64 * 1024


@pytest.mark.parametrize("mutation", [False, True])
async def test_declared_oversized_and_invalid_json_responses_are_rejected(mutation) -> None:
    responses = [
        httpx.Response(200, headers={"Content-Length": str(MAX_JSON_BYTES + 1)}),
        httpx.Response(200, content=b"<html>login</html>"),
        httpx.Response(200, json=[]),
    ]
    async with _client(lambda _request: responses.pop(0)) as client:
        for read_code in ("responseTooLarge", "malformedResponse", "malformedResponse"):
            code = "outcomeUnknown" if mutation else read_code
            with pytest.raises(PaperlessError, match=f"paperless.{code}"):
                if mutation:
                    await client.update_document(7, {"title": "New"})
                else:
                    await client.get_document(7)


async def test_pagination_is_returned_without_following_server_links() -> None:
    requests = []
    page = {"count": 2, "results": [{"id": 7}], "next": "https://evil.example/", "previous": None}

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=page)

    async with _client(handler) as client:
        assert await client.list("documents", params={"page": 1}) == page
    assert len(requests) == 1


async def test_tasks_use_v10_and_return_the_requested_page() -> None:
    requests = []
    page = {"count": 3, "results": [{"task_id": "task-uuid"}], "next": None, "previous": "?page=1"}

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=page)

    async with _client(handler) as client:
        assert await client.list_tasks(page=2, limit=2) == page
    assert [(request.headers["Accept"], request.url.path, dict(request.url.params)) for request in requests] == [
        ("application/json; version=10", "/archive/api/tasks/", {"page": "2", "page_size": "2"}),
    ]


async def test_tasks_fall_back_to_v9_only_with_a_task_filter() -> None:
    requests = []

    def handler(request):
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(406)
        return httpx.Response(200, json=[{"task_id": "task-uuid"}])

    async with _client(handler) as client:
        assert await client.list_tasks(task_id="task-uuid") == {
            "count": 1, "results": [{"task_id": "task-uuid"}], "next": None, "previous": None,
        }
    assert [(request.method, request.headers["Accept"], dict(request.url.params)) for request in requests] == [
        ("GET", "application/json; version=10", {"page": "1", "page_size": "20", "task_id": "task-uuid"}),
        ("GET", "application/json; version=9", {"task_id": "task-uuid"}),
    ]


async def test_unfiltered_tasks_do_not_fetch_v9_history() -> None:
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(406)

    async with _client(handler) as client:
        with pytest.raises(PaperlessError, match="paperless.taskFilterRequired"):
            await client.list_tasks()
    assert len(requests) == 1


async def test_upload_uses_multipart_and_preserves_queued_task_receipt() -> None:
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json="queued-task-uuid")

    async with _client(handler) as client:
        receipt = await client.upload(
            "receipt.pdf", b"%PDF-test", "application/pdf",
            fields={"title": "Receipt", "tags": [2, 3], "custom_fields": {"4": "value"}},
        )
        await client.upload(
            "new.pdf", b"%PDF-new", "application/pdf",
            document_id=7, fields={"version_label": "Corrected"},
        )
    request = requests[0]
    message = BytesParser(policy=default).parsebytes(
        b"Content-Type: " + request.headers["Content-Type"].encode() + b"\r\n\r\n" + request.content,
    )
    fields = [(part.get_param("name", header="content-disposition"), part) for part in message.iter_parts()]
    assert receipt == "queued-task-uuid"
    assert request.url.path == "/archive/api/documents/post_document/"
    assert [(name, part.get_content().strip()) for name, part in fields if name == "tags"] == [
        ("tags", "2"), ("tags", "3"),
    ]
    document = next(part for name, part in fields if name == "document")
    assert (document.get_filename(), document.get_content_type(), document.get_payload(decode=True)) == (
        "receipt.pdf", "application/pdf", b"%PDF-test",
    )
    assert json.loads(next(part.get_content() for name, part in fields if name == "custom_fields")) == {"4": "value"}
    assert requests[1].url.path == "/archive/api/documents/7/update_version/"


async def test_oversized_upload_is_rejected_before_sending() -> None:
    def handler(_request):
        pytest.fail("Oversized upload reached the server")

    async with _client(handler) as client:
        with pytest.raises(PaperlessError, match="paperless.fileTooLarge"):
            await client.upload("large.pdf", b"x" * (MAX_FILE_BYTES + 1), "application/pdf")


async def test_version_download_returns_bytes_and_mime_type() -> None:
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, content=b"%PDF-test", headers={"Content-Type": "application/pdf; charset=binary"})

    async with _client(handler, "http://paperless.example.test/api/") as client:
        assert await client.download(7, original=True, version=9, kind="preview") == (b"%PDF-test", "application/pdf")
    assert str(requests[0].url) == "http://paperless.example.test/api/documents/7/preview/?original=true&version=9"


@pytest.mark.parametrize("headers, compatible", [
    ({"X-Api-Version": "10", "X-Version": "2.20.0"}, True),
    ({"X-Api-Version": "8", "X-Version": "2.0.0"}, False),
    ({}, False),
])
async def test_connection_checks_headers_without_downgrade_probes(headers, compatible) -> None:
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"count": 0, "results": []}, headers=headers)

    async with _client(handler) as client:
        if compatible:
            assert await client.test_connection() == {
                "version": "2.20.0", "api_version": 10, "requested_api_version": 9,
            }
        else:
            with pytest.raises(PaperlessError, match="paperless.incompatibleVersion"):
                await client.test_connection()
    assert len(requests) == 1
