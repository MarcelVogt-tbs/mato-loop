from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Literal

import httpx

MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_ITEMS = 500
MAX_SEARCH_RESULTS = 100
MAX_MARKDOWN_CHARS = 100_000
MAX_ATTACHMENT_BYTES = 32 * 1024 * 1024

BookStackEntityType = Literal["book", "chapter", "page"]


class BookStackError(RuntimeError):
    def __init__(self, code: str, *, status_code: int | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.status_code = status_code


@dataclass(frozen=True, slots=True)
class BookStackBook:
    id: int
    name: str
    slug: str
    description: str | None = None


@dataclass(frozen=True, slots=True)
class BookStackChapter:
    id: int
    book_id: int
    name: str
    slug: str
    description: str | None = None


@dataclass(frozen=True, slots=True)
class BookStackPage:
    id: int
    book_id: int
    chapter_id: int | None
    name: str
    slug: str
    updated_at: str | None = None
    draft: bool = False
    markdown: str | None = None


@dataclass(frozen=True, slots=True)
class BookStackSearchResult:
    type: BookStackEntityType
    id: int
    name: str
    book_id: int | None
    chapter_id: int | None
    preview: str | None


@dataclass(frozen=True, slots=True)
class BookStackShelf:
    id: int
    name: str
    slug: str
    description: str | None = None


@dataclass(frozen=True, slots=True)
class BookStackShelfContents:
    shelf: BookStackShelf
    books: tuple[BookStackBook, ...]


@dataclass(frozen=True, slots=True)
class BookStackAttachment:
    id: int
    name: str
    page_id: int
    external: bool
    extension: str | None = None


@dataclass(frozen=True, slots=True)
class BookStackBookContents:
    book: BookStackBook
    chapters: tuple[BookStackChapter, ...]
    pages: tuple[BookStackPage, ...]


def _error(code: str, *, status_code: int | None = None) -> BookStackError:
    return BookStackError(f"bookstack.{code}", status_code=status_code)


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
    return url.copy_with(raw_path=url.raw_path.rstrip(b"/"))


def _text(value: Any, limit: int | None) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    if not cleaned:
        return None
    return cleaned if limit is None else cleaned[:limit]


def _identifier(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise _error("malformedResponse")
    return value


def _optional_identifier(value: Any) -> int | None:
    if value is None or isinstance(value, bool) or not isinstance(value, int) or value < 1:
        return None
    return value


def _name(value: Any) -> str:
    name = _text(value, 255)
    if name is None:
        raise _error("malformedResponse")
    return name


def _book(payload: Any) -> BookStackBook:
    if not isinstance(payload, dict):
        raise _error("malformedResponse")
    return BookStackBook(
        id=_identifier(payload.get("id")),
        name=_name(payload.get("name")),
        slug=_text(payload.get("slug"), 255) or "",
        description=_text(payload.get("description"), 2_000),
    )


def _shelf(payload: Any) -> BookStackShelf:
    if not isinstance(payload, dict):
        raise _error("malformedResponse")
    return BookStackShelf(
        id=_identifier(payload.get("id")),
        name=_name(payload.get("name")),
        slug=_text(payload.get("slug"), 255) or "",
        description=_text(payload.get("description"), 2_000),
    )


def _attachment(payload: Any, page_id: int | None = None) -> BookStackAttachment:
    if not isinstance(payload, dict):
        raise _error("malformedResponse")
    return BookStackAttachment(
        id=_identifier(payload.get("id")),
        name=_name(payload.get("name")),
        page_id=_optional_identifier(payload.get("uploaded_to"))
        or _identifier(page_id),
        external=bool(payload.get("external")),
        extension=_text(payload.get("extension"), 20),
    )


def _chapter(payload: Any, book_id: int | None = None) -> BookStackChapter:
    if not isinstance(payload, dict):
        raise _error("malformedResponse")
    return BookStackChapter(
        id=_identifier(payload.get("id")),
        book_id=_optional_identifier(payload.get("book_id")) or _identifier(book_id),
        name=_name(payload.get("name")),
        slug=_text(payload.get("slug"), 255) or "",
        description=_text(payload.get("description"), 2_000),
    )


def _page(payload: Any, book_id: int | None = None) -> BookStackPage:
    if not isinstance(payload, dict):
        raise _error("malformedResponse")
    return BookStackPage(
        id=_identifier(payload.get("id")),
        book_id=_optional_identifier(payload.get("book_id")) or _identifier(book_id),
        chapter_id=_optional_identifier(payload.get("chapter_id")),
        name=_name(payload.get("name")),
        slug=_text(payload.get("slug"), 255) or "",
        updated_at=_text(payload.get("updated_at"), 64),
        draft=bool(payload.get("draft")),
        markdown=_text(payload.get("markdown"), None),
    )


def _require_readable_markdown(page: BookStackPage) -> BookStackPage:
    """Refuse pages whose markdown exceeds the readable limit.

    Silently truncating would let the agent write the shortened text back as if
    it were the full page, destroying the remainder. Reporting an error keeps
    oversized pages readable only through BookStack itself.
    """
    if page.markdown is not None and len(page.markdown) > MAX_MARKDOWN_CHARS:
        raise _error("pageTooLarge")
    return page


def _search_result(payload: Any) -> BookStackSearchResult | None:
    if not isinstance(payload, dict):
        raise _error("malformedResponse")
    entity_type = payload.get("type")
    if entity_type not in {"book", "chapter", "page"}:
        return None
    preview = payload.get("preview_html")
    return BookStackSearchResult(
        type=entity_type,
        id=_identifier(payload.get("id")),
        name=_name(payload.get("name")),
        book_id=_optional_identifier(payload.get("book_id")),
        chapter_id=_optional_identifier(payload.get("chapter_id")),
        preview=(
            _text(preview.get("content"), 1_000) if isinstance(preview, dict) else None
        ),
    )


class BookStackClient:
    def __init__(
        self,
        base_url: str,
        token_id: str,
        token_secret: str,
        *,
        client: httpx.AsyncClient | None = None,
        max_response_bytes: int = MAX_RESPONSE_BYTES,
    ) -> None:
        self._base_url = _parse_url(base_url)
        self._origin = _origin(self._base_url)
        if not token_id or not token_secret or any(
            character.isspace() or ord(character) < 32
            for character in f"{token_id}{token_secret}"
        ):
            raise _error("authenticationFailed")
        self._authorization = f"Token {token_id}:{token_secret}"
        self._client = client or httpx.AsyncClient(timeout=30, trust_env=False)
        self._owns_client = client is None
        if max_response_bytes < 1 or max_response_bytes > MAX_RESPONSE_BYTES:
            raise _error("invalidLimit")
        self._max_response_bytes = max_response_bytes

    async def __aenter__(self) -> BookStackClient:
        return self

    async def __aexit__(self, *_args: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    def _url(self, path: str, params: dict[str, Any] | None = None) -> httpx.URL:
        if not path.startswith("/") or any(
            character.isspace() or ord(character) < 32 or character in "?#\\"
            for character in path
        ):
            raise _error("invalidUrl")
        base_path = self._base_url.raw_path.decode().rstrip("/")
        url = self._base_url.copy_with(raw_path=f"{base_path}/api{path}".encode())
        if params:
            url = url.copy_with(params=params)
        if _origin(url) != self._origin:
            raise _error("crossOrigin")
        return url

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
        form: dict[str, Any] | None = None,
        upload: tuple[str, bytes, str] | None = None,
        expected: set[int] = frozenset({200}),
        text_response: bool = False,
        discard_response: bool = False,
    ) -> Any:
        url = self._url(path, params)
        headers = {"Authorization": self._authorization, "Accept": "application/json"}
        content = None
        request_kwargs: dict[str, Any] = {}
        if upload is not None:
            filename, data, content_type = upload
            request_kwargs["data"] = form or {}
            request_kwargs["files"] = {"file": (filename, data, content_type)}
        elif form is not None:
            request_kwargs["data"] = form
        elif payload is not None:
            headers["Content-Type"] = "application/json"
            content = json.dumps(payload, ensure_ascii=False).encode()
        request = self._client.build_request(
            method, url, headers=headers, content=content, **request_kwargs
        )
        try:
            response = await self._client.send(
                request, stream=True, follow_redirects=False
            )
        except httpx.HTTPError as exc:
            raise _error(
                "outcomeUnknown"
                if method in {"POST", "PUT", "DELETE"}
                else "unavailable"
            ) from exc
        try:
            if 300 <= response.status_code < 400:
                raise _error("redirectRejected", status_code=response.status_code)
            if response.status_code not in expected:
                raise _error(
                    _STATUS_CODES.get(response.status_code, "unexpectedStatus"),
                    status_code=response.status_code,
                )
            if discard_response:
                return None
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
            body = b"".join(chunks)
        finally:
            await response.aclose()
        if response.status_code == 204 or not body:
            return None
        if text_response:
            try:
                return body.decode("utf-8", errors="replace")
            except ValueError as exc:
                raise _error("malformedResponse") from exc
        try:
            return json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise _error("malformedResponse") from exc

    @staticmethod
    def _listing(payload: Any) -> list[Any]:
        if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
            raise _error("malformedResponse")
        data = payload["data"]
        if len(data) > MAX_ITEMS:
            raise _error("responseTooLarge")
        return data

    @staticmethod
    def _is_truncated(payload: Any, received: int) -> bool:
        """Report whether BookStack holds more entries than it returned.

        Listings are capped, and a silently shortened list reads like a complete
        one. The agent would then believe a book does not exist and create a
        duplicate instead of using the existing one.
        """
        total = payload.get("total") if isinstance(payload, dict) else None
        return isinstance(total, int) and total > received

    async def list_books(
        self, limit: int = MAX_ITEMS
    ) -> tuple[tuple[BookStackBook, ...], bool]:
        if limit < 1 or limit > MAX_ITEMS:
            raise _error("invalidLimit")
        payload = await self._request(
            "GET", "/books", params={"count": limit, "sort": "+name"}
        )
        items = tuple(_book(item) for item in self._listing(payload))
        return items, self._is_truncated(payload, len(items))

    async def read_book(self, book_id: int) -> BookStackBookContents:
        payload = await self._request("GET", f"/books/{_identifier(book_id)}")
        if not isinstance(payload, dict):
            raise _error("malformedResponse")
        book = _book(payload)
        chapters: list[BookStackChapter] = []
        pages: list[BookStackPage] = []
        contents = payload.get("contents")
        for item in contents if isinstance(contents, list) else []:
            if not isinstance(item, dict):
                raise _error("malformedResponse")
            if item.get("type") == "chapter":
                chapters.append(_chapter(item, book.id))
                for child in item.get("pages") if isinstance(item.get("pages"), list) else []:
                    pages.append(_page(child, book.id))
            elif item.get("type") == "page":
                pages.append(_page(item, book.id))
            if len(pages) > MAX_ITEMS or len(chapters) > MAX_ITEMS:
                raise _error("responseTooLarge")
        return BookStackBookContents(
            book=book, chapters=tuple(chapters), pages=tuple(pages)
        )

    async def search(
        self, query: str, limit: int = 20
    ) -> tuple[BookStackSearchResult, ...]:
        cleaned = query.strip()
        if not cleaned:
            raise _error("invalidQuery")
        if limit < 1 or limit > MAX_SEARCH_RESULTS:
            raise _error("invalidLimit")
        payload = await self._request(
            "GET",
            "/search",
            params={"query": cleaned[:400], "count": limit, "page": 1},
        )
        results = (_search_result(item) for item in self._listing(payload))
        return tuple(result for result in results if result is not None)

    async def read_page(self, page_id: int) -> BookStackPage:
        payload = await self._request("GET", f"/pages/{_identifier(page_id)}")
        page = _page(payload)
        if page.markdown:
            return _require_readable_markdown(page)
        markdown = await self._request(
            "GET", f"/pages/{page.id}/export/markdown", text_response=True
        )
        return _require_readable_markdown(
            BookStackPage(
                id=page.id,
                book_id=page.book_id,
                chapter_id=page.chapter_id,
                name=page.name,
                slug=page.slug,
                updated_at=page.updated_at,
                draft=page.draft,
                markdown=_text(markdown, None),
            )
        )

    async def read_page_location(self, page_id: int) -> BookStackPage:
        """Fetch page metadata without its content.

        Used to verify that a page still belongs to an approved book. Skipping
        the markdown export keeps this cheap and, unlike read_page, keeps
        oversized pages usable for operations that never touch their content.
        """
        payload = await self._request("GET", f"/pages/{_identifier(page_id)}")
        if not isinstance(payload, dict):
            raise _error("malformedResponse")
        return _page({**payload, "markdown": None})

    async def read_chapter(self, chapter_id: int) -> BookStackChapter:
        payload = await self._request("GET", f"/chapters/{_identifier(chapter_id)}")
        if not isinstance(payload, dict):
            raise _error("malformedResponse")
        return _chapter(payload)

    async def read_attachment_location(self, attachment_id: int) -> BookStackAttachment:
        payload = await self._request(
            "GET", f"/attachments/{_identifier(attachment_id)}"
        )
        if not isinstance(payload, dict):
            raise _error("malformedResponse")
        return _attachment(payload)

    async def create_page(
        self,
        *,
        book_id: int,
        chapter_id: int | None,
        name: str,
        markdown: str,
    ) -> BookStackPage:
        payload: dict[str, Any] = {"name": name, "markdown": markdown}
        if chapter_id is not None:
            payload["chapter_id"] = _identifier(chapter_id)
        else:
            payload["book_id"] = _identifier(book_id)
        return _page(
            await self._request("POST", "/pages", payload=payload, expected={200, 201})
        )

    async def update_page(
        self,
        page_id: int,
        *,
        name: str | None = None,
        markdown: str | None = None,
    ) -> None:
        payload: dict[str, Any] = {}
        if name is not None:
            payload["name"] = name
        if markdown is not None:
            payload["markdown"] = markdown
        if not payload:
            raise _error("invalidPage")
        await self._request(
            "PUT",
            f"/pages/{_identifier(page_id)}",
            payload=payload,
            discard_response=True,
        )

    async def delete_page(self, page_id: int) -> None:
        await self._request(
            "DELETE", f"/pages/{_identifier(page_id)}", expected={200, 204}
        )

    async def create_book(
        self, *, name: str, description: str | None = None
    ) -> BookStackBook:
        payload: dict[str, Any] = {"name": name}
        if description is not None:
            payload["description"] = description
        return _book(
            await self._request("POST", "/books", payload=payload, expected={200, 201})
        )

    async def update_book(
        self,
        book_id: int,
        *,
        name: str | None = None,
        description: str | None = None,
    ) -> BookStackBook:
        payload: dict[str, Any] = {}
        if name is not None:
            payload["name"] = name
        if description is not None:
            payload["description"] = description
        if not payload:
            raise _error("invalidRequest")
        return _book(
            await self._request(
                "PUT", f"/books/{_identifier(book_id)}", payload=payload
            )
        )

    async def delete_book(self, book_id: int) -> None:
        await self._request(
            "DELETE", f"/books/{_identifier(book_id)}", expected={200, 204}
        )

    async def create_chapter(
        self, *, book_id: int, name: str, description: str | None = None
    ) -> BookStackChapter:
        payload: dict[str, Any] = {"book_id": _identifier(book_id), "name": name}
        if description is not None:
            payload["description"] = description
        return _chapter(
            await self._request(
                "POST", "/chapters", payload=payload, expected={200, 201}
            )
        )

    async def update_chapter(
        self,
        chapter_id: int,
        *,
        name: str | None = None,
        description: str | None = None,
    ) -> BookStackChapter:
        payload: dict[str, Any] = {}
        if name is not None:
            payload["name"] = name
        if description is not None:
            payload["description"] = description
        if not payload:
            raise _error("invalidRequest")
        return _chapter(
            await self._request(
                "PUT", f"/chapters/{_identifier(chapter_id)}", payload=payload
            )
        )

    async def delete_chapter(self, chapter_id: int) -> None:
        await self._request(
            "DELETE", f"/chapters/{_identifier(chapter_id)}", expected={200, 204}
        )

    async def list_shelves(
        self, limit: int = MAX_ITEMS
    ) -> tuple[tuple[BookStackShelf, ...], bool]:
        if limit < 1 or limit > MAX_ITEMS:
            raise _error("invalidLimit")
        payload = await self._request(
            "GET", "/shelves", params={"count": limit, "sort": "+name"}
        )
        items = tuple(_shelf(item) for item in self._listing(payload))
        return items, self._is_truncated(payload, len(items))

    async def read_shelf(self, shelf_id: int) -> BookStackShelfContents:
        payload = await self._request("GET", f"/shelves/{_identifier(shelf_id)}")
        if not isinstance(payload, dict):
            raise _error("malformedResponse")
        books = payload.get("books")
        if books is not None and not isinstance(books, list):
            raise _error("malformedResponse")
        entries = books or []
        if len(entries) > MAX_ITEMS:
            raise _error("responseTooLarge")
        return BookStackShelfContents(
            shelf=_shelf(payload), books=tuple(_book(item) for item in entries)
        )

    async def create_shelf(
        self,
        *,
        name: str,
        description: str | None = None,
        book_ids: tuple[int, ...] | None = None,
    ) -> BookStackShelf:
        payload = self._shelf_payload(
            name=name, description=description, book_ids=book_ids
        )
        return _shelf(
            await self._request("POST", "/shelves", payload=payload, expected={200, 201})
        )

    async def update_shelf(
        self,
        shelf_id: int,
        *,
        name: str | None = None,
        description: str | None = None,
        book_ids: tuple[int, ...] | None = None,
    ) -> BookStackShelf:
        payload = self._shelf_payload(
            name=name, description=description, book_ids=book_ids
        )
        if not payload:
            raise _error("invalidRequest")
        return _shelf(
            await self._request(
                "PUT", f"/shelves/{_identifier(shelf_id)}", payload=payload
            )
        )

    async def delete_shelf(self, shelf_id: int) -> None:
        await self._request(
            "DELETE", f"/shelves/{_identifier(shelf_id)}", expected={200, 204}
        )

    async def list_attachments(
        self, page_id: int, limit: int = MAX_ITEMS
    ) -> tuple[BookStackAttachment, ...]:
        if limit < 1 or limit > MAX_ITEMS:
            raise _error("invalidLimit")
        payload = await self._request(
            "GET",
            "/attachments",
            params={"count": limit, "filter[uploaded_to]": _identifier(page_id)},
        )
        return tuple(
            _attachment(item, page_id) for item in self._listing(payload)
        )

    async def create_file_attachment(
        self,
        *,
        page_id: int,
        name: str,
        filename: str,
        data: bytes,
        content_type: str,
    ) -> BookStackAttachment:
        if not data or len(data) > MAX_ATTACHMENT_BYTES:
            raise _error("attachmentTooLarge")
        return _attachment(
            await self._request(
                "POST",
                "/attachments",
                form={"name": name, "uploaded_to": _identifier(page_id)},
                upload=(filename, data, content_type),
                expected={200, 201},
            ),
            page_id,
        )

    async def create_link_attachment(
        self, *, page_id: int, name: str, link: str
    ) -> BookStackAttachment:
        return _attachment(
            await self._request(
                "POST",
                "/attachments",
                payload={
                    "name": name,
                    "uploaded_to": _identifier(page_id),
                    "link": link,
                },
                expected={200, 201},
            ),
            page_id,
        )

    async def delete_attachment(self, attachment_id: int) -> None:
        await self._request(
            "DELETE",
            f"/attachments/{_identifier(attachment_id)}",
            expected={200, 204},
        )

    @staticmethod
    def _shelf_payload(
        *,
        name: str | None,
        description: str | None,
        book_ids: tuple[int, ...] | None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        if name is not None:
            payload["name"] = name
        if description is not None:
            payload["description"] = description
        if book_ids is not None:
            if len(book_ids) > MAX_ITEMS:
                raise _error("invalidRequest")
            payload["books"] = [_identifier(book_id) for book_id in book_ids]
        return payload


_STATUS_CODES = {
    400: "invalidRequest",
    401: "authenticationFailed",
    403: "permissionDenied",
    404: "notFound",
    422: "invalidRequest",
    429: "rateLimited",
}
