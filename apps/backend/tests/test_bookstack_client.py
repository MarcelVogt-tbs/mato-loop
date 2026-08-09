import json

import httpx
import pytest

from mato_loop_backend.integrations.bookstack_client import (
    MAX_ATTACHMENT_BYTES,
    MAX_MARKDOWN_CHARS,
    BookStackClient,
    BookStackError,
)


def _client(handler, base_url: str = "https://wiki.example.test") -> BookStackClient:
    return BookStackClient(
        base_url,
        "token-id",
        "token-secret",
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


async def test_requests_are_token_authenticated_and_same_origin() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200, json={"data": [{"id": 3, "name": "Handbuch", "slug": "handbuch"}], "total": 1}
        )

    async with _client(handler, "https://wiki.example.test/docs/") as client:
        books = await client.list_books()

    assert requests[0].headers["Authorization"] == "Token token-id:token-secret"
    assert str(requests[0].url).startswith("https://wiki.example.test/docs/api/books?")
    assert books[0].id == 3
    assert books[0].name == "Handbuch"


async def test_insecure_or_credential_bearing_urls_are_rejected() -> None:
    for value in (
        "http://wiki.example.test",
        "https://user:pass@wiki.example.test",
        "https://wiki.example.test?a=1",
        "",
    ):
        with pytest.raises(BookStackError):
            BookStackClient(value, "id", "secret")


async def test_missing_token_parts_are_rejected() -> None:
    with pytest.raises(BookStackError):
        BookStackClient("https://wiki.example.test", "", "secret")
    with pytest.raises(BookStackError):
        BookStackClient("https://wiki.example.test", "id", "bad secret")


async def test_redirects_and_error_statuses_map_to_stable_codes() -> None:
    statuses = {
        302: "bookstack.redirectRejected",
        401: "bookstack.authenticationFailed",
        403: "bookstack.permissionDenied",
        404: "bookstack.notFound",
        422: "bookstack.invalidRequest",
        429: "bookstack.rateLimited",
        500: "bookstack.unexpectedStatus",
    }
    for status, code in statuses.items():
        def handler(_request: httpx.Request, status: int = status) -> httpx.Response:
            return httpx.Response(status, json={"error": {"code": status}})

        async with _client(handler) as client:
            with pytest.raises(BookStackError) as error:
                await client.list_books()
        assert error.value.code == code


async def test_oversized_responses_are_refused() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"x" * 4096)

    client = BookStackClient(
        "https://wiki.example.test",
        "id",
        "secret",
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        max_response_bytes=1024,
    )
    async with client:
        with pytest.raises(BookStackError) as error:
            await client.list_books()
    assert error.value.code == "bookstack.responseTooLarge"


async def test_book_contents_are_flattened_with_chapter_pages() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": 7,
                "name": "Handbuch",
                "slug": "handbuch",
                "contents": [
                    {
                        "type": "chapter",
                        "id": 11,
                        "name": "Einstieg",
                        "slug": "einstieg",
                        "pages": [
                            {
                                "type": "page",
                                "id": 21,
                                "name": "Erste Schritte",
                                "slug": "erste-schritte",
                                "chapter_id": 11,
                            }
                        ],
                    },
                    {"type": "page", "id": 22, "name": "Anhang", "slug": "anhang"},
                ],
            },
        )

    async with _client(handler) as client:
        contents = await client.read_book(7)

    assert contents.book.id == 7
    assert [chapter.id for chapter in contents.chapters] == [11]
    assert [page.id for page in contents.pages] == [21, 22]
    assert all(page.book_id == 7 for page in contents.pages)


async def test_page_read_falls_back_to_markdown_export() -> None:
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path.endswith("/export/markdown"):
            return httpx.Response(200, text="# Titel\n\nInhalt")
        return httpx.Response(
            200,
            json={
                "id": 21,
                "book_id": 7,
                "chapter_id": None,
                "name": "Seite",
                "slug": "seite",
                "markdown": "",
            },
        )

    async with _client(handler) as client:
        page = await client.read_page(21)

    assert paths == ["/api/pages/21", "/api/pages/21/export/markdown"]
    assert page.markdown == "# Titel\n\nInhalt"


async def test_page_mutations_send_markdown_payloads() -> None:
    bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "id": 30,
                "book_id": 7,
                "chapter_id": 11,
                "name": "Neu",
                "slug": "neu",
            },
        )

    async with _client(handler) as client:
        await client.create_page(book_id=7, chapter_id=11, name="Neu", markdown="Text")
        await client.update_page(30, markdown="Neuer Text")

    assert bodies[0] == {"name": "Neu", "markdown": "Text", "chapter_id": 11}
    assert bodies[1] == {"markdown": "Neuer Text"}


async def test_book_and_chapter_updates_send_partial_payloads() -> None:
    requests: list[tuple[str, str, dict]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(
            (request.method, request.url.path, json.loads(request.content))
        )
        return httpx.Response(
            200,
            json={"id": 7, "book_id": 7, "name": "Neu", "slug": "neu"},
        )

    async with _client(handler) as client:
        await client.update_book(7, name="Neu")
        await client.update_chapter(11, description="Beschreibung")

    assert requests[0] == ("PUT", "/api/books/7", {"name": "Neu"})
    assert requests[1] == ("PUT", "/api/chapters/11", {"description": "Beschreibung"})


async def test_empty_book_and_chapter_updates_are_rejected() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [], "total": 0})

    async with _client(handler) as client:
        with pytest.raises(BookStackError) as book_error:
            await client.update_book(7)
        with pytest.raises(BookStackError) as chapter_error:
            await client.update_chapter(11)
    assert book_error.value.code == "bookstack.invalidRequest"
    assert chapter_error.value.code == "bookstack.invalidRequest"


async def test_empty_update_and_invalid_limits_are_rejected() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [], "total": 0})

    async with _client(handler) as client:
        with pytest.raises(BookStackError) as update_error:
            await client.update_page(1)
        with pytest.raises(BookStackError) as query_error:
            await client.search("   ")
        with pytest.raises(BookStackError) as limit_error:
            await client.search("Text", 0)
    assert update_error.value.code == "bookstack.invalidPage"
    assert query_error.value.code == "bookstack.invalidQuery"
    assert limit_error.value.code == "bookstack.invalidLimit"


async def test_search_skips_unsupported_entity_types() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "type": "page",
                        "id": 21,
                        "name": "Seite",
                        "book_id": 7,
                        "preview_html": {"content": "Treffer"},
                    },
                    {"type": "bookshelf", "id": 4, "name": "Regal"},
                ],
                "total": 2,
            },
        )

    async with _client(handler) as client:
        results = await client.search("Seite")

    assert [result.type for result in results] == ["page"]
    assert results[0].preview == "Treffer"


async def test_malformed_payloads_are_reported_as_invalid_responses() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not json")

    async with _client(handler) as client:
        with pytest.raises(BookStackError) as error:
            await client.list_books()
    assert error.value.code == "bookstack.malformedResponse"


async def test_shelf_requests_use_shelf_endpoints_and_payloads() -> None:
    requests: list[tuple[str, str, dict | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        requests.append((request.method, request.url.path, body))
        if request.method == "GET" and request.url.path.endswith("/shelves"):
            return httpx.Response(
                200,
                json={"data": [{"id": 4, "name": "Technik", "slug": "technik"}], "total": 1},
            )
        if request.method == "DELETE":
            return httpx.Response(204)
        return httpx.Response(
            200,
            json={
                "id": 4,
                "name": "Technik",
                "slug": "technik",
                "books": [{"id": 7, "name": "Handbuch", "slug": "handbuch"}],
            },
        )

    async with _client(handler) as client:
        shelves = await client.list_shelves()
        contents = await client.read_shelf(4)
        await client.create_shelf(name="Technik", book_ids=(7, 8))
        await client.update_shelf(4, name="Neu", book_ids=())
        await client.delete_shelf(4)

    assert shelves[0].id == 4
    assert contents.books[0].id == 7
    assert [(method, path) for method, path, _ in requests] == [
        ("GET", "/api/shelves"),
        ("GET", "/api/shelves/4"),
        ("POST", "/api/shelves"),
        ("PUT", "/api/shelves/4"),
        ("DELETE", "/api/shelves/4"),
    ]
    assert requests[2][2] == {"name": "Technik", "books": [7, 8]}
    assert requests[3][2] == {"name": "Neu", "books": []}


async def test_empty_shelf_update_and_malformed_shelf_books_are_rejected() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"id": 4, "name": "Technik", "slug": "t", "books": {}})

    async with _client(handler) as client:
        with pytest.raises(BookStackError) as empty_error:
            await client.update_shelf(4)
        with pytest.raises(BookStackError) as books_error:
            await client.read_shelf(4)

    assert empty_error.value.code == "bookstack.invalidRequest"
    assert books_error.value.code == "bookstack.malformedResponse"

async def test_attachment_requests_use_multipart_json_and_delete() -> None:
    requests: list[httpx.Request] = []
    bodies: list[bytes] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        bodies.append(request.content)
        if request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": 12,
                            "name": "Datenblatt",
                            "uploaded_to": 21,
                            "external": False,
                            "extension": "pdf",
                        }
                    ],
                    "total": 1,
                },
            )
        if request.method == "DELETE":
            return httpx.Response(204)
        return httpx.Response(
            201,
            json={
                "id": 13,
                "name": "Handbuch",
                "uploaded_to": 21,
                "external": True,
                "extension": "",
            },
        )

    async with _client(handler) as client:
        listed = await client.list_attachments(21)
        uploaded = await client.create_file_attachment(
            page_id=21,
            name="Datenblatt",
            filename="sheet.pdf",
            data=b"%PDF-1.4 payload",
            content_type="application/pdf",
        )
        linked = await client.create_link_attachment(
            page_id=21, name="Handbuch", link="https://example.test/manual"
        )
        await client.delete_attachment(12)

    assert listed[0].id == 12
    assert listed[0].external is False
    assert listed[0].extension == "pdf"
    assert uploaded.id == 13
    assert linked.external is True
    assert [(r.method, r.url.path) for r in requests] == [
        ("GET", "/api/attachments"),
        ("POST", "/api/attachments"),
        ("POST", "/api/attachments"),
        ("DELETE", "/api/attachments/12"),
    ]
    assert requests[0].url.params["filter[uploaded_to]"] == "21"
    assert requests[1].headers["content-type"].startswith("multipart/form-data")
    assert b"%PDF-1.4 payload" in bodies[1]
    assert b'filename="sheet.pdf"' in bodies[1]
    assert b'name="uploaded_to"' in bodies[1]
    assert requests[1].headers["Authorization"] == "Token token-id:token-secret"
    assert json.loads(bodies[2]) == {
        "name": "Handbuch",
        "uploaded_to": 21,
        "link": "https://example.test/manual",
    }


async def test_empty_or_oversized_uploads_are_rejected() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(201, json={"id": 1, "name": "x", "uploaded_to": 21})

    async with _client(handler) as client:
        with pytest.raises(BookStackError) as empty_error:
            await client.create_file_attachment(
                page_id=21,
                name="Leer",
                filename="empty.pdf",
                data=b"",
                content_type="application/pdf",
            )
        with pytest.raises(BookStackError) as large_error:
            await client.create_file_attachment(
                page_id=21,
                name="Gross",
                filename="big.pdf",
                data=b"x" * (MAX_ATTACHMENT_BYTES + 1),
                content_type="application/pdf",
            )

    assert empty_error.value.code == "bookstack.attachmentTooLarge"
    assert large_error.value.code == "bookstack.attachmentTooLarge"

async def test_oversized_page_markdown_is_refused_instead_of_truncated() -> None:
    oversized = "# Grosse Seite\n" + ("x" * (MAX_MARKDOWN_CHARS + 1))

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": 21,
                "name": "Seite",
                "book_id": 7,
                "markdown": oversized,
                "updated_at": "2026-01-01T10:00:00.000000Z",
            },
        )

    async with _client(handler) as client:
        with pytest.raises(BookStackError) as error:
            await client.read_page(21)
    assert error.value.code == "bookstack.pageTooLarge"


async def test_page_location_reads_stay_available_for_oversized_pages() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": 21,
                "name": "Seite",
                "book_id": 7,
                "markdown": "x" * (MAX_MARKDOWN_CHARS + 1),
                "updated_at": "2026-01-01T10:00:00.000000Z",
            },
        )

    async with _client(handler) as client:
        page = await client.read_page_location(21)

    assert page.book_id == 7
