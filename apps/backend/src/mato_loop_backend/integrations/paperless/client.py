from __future__ import annotations

import json as json_module
import re
from typing import Any

import httpx

API_VERSION = 9
MAX_JSON_BYTES = 4 * 1024 * 1024
MAX_FILE_BYTES = 32 * 1024 * 1024
_MUTATION_METHODS = {"POST", "PATCH", "PUT", "DELETE"}
_RESOURCE_PATHS = {
    name: f"{name}/"
    for name in (
        "documents", "tags", "correspondents", "document_types", "storage_paths",
        "custom_fields", "trash",
    )
}
_STATUS_CODES = {
    400: "invalidRequest",
    401: "authenticationFailed",
    403: "permissionDenied",
    404: "notFound",
    406: "incompatibleVersion",
    413: "fileTooLarge",
    429: "rateLimited",
}


class PaperlessError(RuntimeError):
    def __init__(self, code: str, *, status_code: int | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.status_code = status_code


def _error(code: str, *, status_code: int | None = None) -> PaperlessError:
    return PaperlessError(f"paperless.{code}", status_code=status_code)


def _base_url(value: str) -> httpx.URL:
    if (
        not isinstance(value, str) or not value
        or any(char in value for char in ("?", "#", "\\"))
        or any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in value)
    ):
        raise _error("invalidUrl")
    try:
        url = httpx.URL(value)
    except (httpx.InvalidURL, ValueError):
        raise _error("invalidUrl") from None
    if url.scheme not in {"http", "https"} or not url.host or url.userinfo or "@" in value:
        raise _error("invalidUrl")
    # Preserve reverse-proxy subpaths, accepting an optional API suffix.
    path = url.raw_path.rstrip(b"/")
    if path.endswith(b"/api"):
        path = path[:-4]
    if b"%" in path or any(part in {b".", b".."} for part in path.split(b"/")):
        raise _error("invalidUrl")
    return url.copy_with(raw_path=path + b"/api/")


def _identifier(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise _error("invalidRequest")
    return value


def _object(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise _error("malformedResponse")
    return payload


def _page(payload: Any) -> dict[str, Any]:
    page = _object(payload)
    if (
        not isinstance(page.get("results"), list)
        or isinstance(page.get("count"), bool)
        or not isinstance(page.get("count"), int)
        or page["count"] < 0
    ):
        raise _error("malformedResponse")
    return page


def _json(content: bytes) -> Any:
    if not content:
        return None
    try:
        return json_module.loads(content)
    except (ValueError, UnicodeError, RecursionError):
        raise _error("malformedResponse") from None


class PaperlessClient:
    """Bounded API v9 transport with v10 task pagination and queued task receipts."""

    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = _base_url(base_url)
        if (
            not isinstance(token, str) or not token
            or any(ord(char) < 33 or ord(char) > 126 for char in token)
        ):
            raise _error("authenticationFailed")
        self._client = httpx.AsyncClient(
            headers={
                "Authorization": f"Token {token}",
                "Accept": f"application/json; version={API_VERSION}",
            },
            transport=transport,
            timeout=httpx.Timeout(60.0, connect=10.0),
            follow_redirects=False,
            trust_env=False,
        )

    async def __aenter__(self) -> PaperlessClient:
        await self._client.__aenter__()
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self._client.__aexit__(*args)

    def _url(self, path: str) -> httpx.URL:
        # No URL joining: leading slashes cannot escape the configured API root.
        if not isinstance(path, str) or not re.fullmatch(
            r"/?[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)*/?", path,
        ):
            raise _error("invalidRequest")
        relative = path.lstrip("/")
        if relative.startswith("api/"):
            relative = relative[4:]
        return self._base_url.copy_with(
            raw_path=self._base_url.raw_path + relative.encode("ascii"),
        )

    async def _send(
        self,
        method: str,
        path: str,
        *,
        limit: int = MAX_JSON_BYTES,
        params: Any = None,
        json: Any = None,
        files: Any = None,
        data: Any = None,
        api_version: int = API_VERSION,
    ) -> tuple[bytes, httpx.Headers]:
        url = self._url(path)
        if method.upper() not in {"GET", "POST", "PATCH", "PUT", "DELETE", "HEAD", "OPTIONS"}:
            raise _error("invalidRequest")
        try:
            async with self._client.stream(
                method, url, params=params, json=json, files=files, data=data,
                headers={"Accept": f"application/json; version={api_version}"},
                follow_redirects=False,
            ) as response:
                status = response.status_code
                if 300 <= status < 400:
                    raise _error("redirectRejected", status_code=status)
                if not 200 <= status < 300:
                    code = _STATUS_CODES.get(status, "requestFailed")
                    if method.upper() in _MUTATION_METHODS and (
                        500 <= status < 600
                        or status == 400 and url.path.rstrip("/").rsplit("/", 1)[-1] in {
                            "bulk_edit", "merge_as_versions", "update_version",
                        }
                    ):
                        # These actions can fail after dispatch or partial execution.
                        code = "outcomeUnknown"
                    raise _error(code, status_code=status)
                length = response.headers.get("Content-Length", "")
                if length.isdecimal() and int(length) > limit:
                    raise _error("responseTooLarge")
                content = bytearray()
                async for chunk in response.aiter_bytes(chunk_size=64 * 1024):
                    if len(content) + len(chunk) > limit:
                        raise _error("responseTooLarge")
                    content.extend(chunk)
                return bytes(content), response.headers
        except httpx.HTTPError:
            code = "outcomeUnknown" if method.upper() in _MUTATION_METHODS else "requestFailed"
            raise _error(code) from None

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: Any = None,
        json: Any = None,
        files: Any = None,
        data: Any = None,
    ) -> Any:
        """Request one relative API path; never follow pagination or redirect URLs."""
        try:
            content, _headers = await self._send(
                method, path, params=params, json=json, files=files, data=data,
            )
            return _json(content)
        except PaperlessError as exc:
            # An unreadable success response does not make a mutation safe to retry.
            if method.upper() in _MUTATION_METHODS and exc.code in {
                "paperless.malformedResponse", "paperless.responseTooLarge",
            }:
                raise _error("outcomeUnknown") from None
            raise

    async def list(self, resource: str, *, params: Any = None) -> dict[str, Any]:
        if resource not in _RESOURCE_PATHS:
            raise _error("invalidRequest")
        payload = await self.request("GET", _RESOURCE_PATHS[resource], params=params)
        return _page(payload)

    async def list_tasks(
        self, *, task_id: str | None = None, page: int = 1, limit: int = 20,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"page": _identifier(page), "page_size": _identifier(limit)}
        if task_id is not None:
            if not isinstance(task_id, str) or not task_id.strip():
                raise _error("invalidRequest")
            params["task_id"] = task_id
        try:
            content, _headers = await self._send("GET", "tasks/", params=params, api_version=10)
        except PaperlessError as exc:
            if exc.status_code != 406:
                raise
            if task_id is None:
                raise _error("taskFilterRequired") from None
            # API v9 ignores pagination, so only an exact task lookup is safe.
            payload = await self.request("GET", "tasks/", params={"task_id": task_id})
            if not isinstance(payload, list):
                raise _error("malformedResponse") from None
            return _page({
                "count": len(payload), "next": None, "previous": None,
                "results": payload[(page - 1) * limit:page * limit],
            })
        return _page(_json(content))

    async def get_document(self, id: int, *, version: int | None = None) -> dict[str, Any]:
        params = {"version": _identifier(version)} if version is not None else None
        return _object(await self.request("GET", f"documents/{_identifier(id)}/", params=params))

    async def update_document(self, id: int, changes: dict) -> dict[str, Any]:
        payload = await self.request("PATCH", f"documents/{_identifier(id)}/", json=changes)
        if not isinstance(payload, dict):
            raise _error("outcomeUnknown")
        return payload

    async def download(
        self,
        id: int,
        *,
        original: bool = False,
        version: int | None = None,
        kind: str = "download",
    ) -> tuple[bytes, str]:
        if kind not in {"download", "preview", "thumb"}:
            raise _error("invalidRequest")
        params: dict[str, Any] = {"original": "true"} if original else {}
        if version is not None:
            params["version"] = _identifier(version)
        content, headers = await self._send(
            "GET", f"documents/{_identifier(id)}/{kind}/", params=params, limit=MAX_FILE_BYTES,
        )
        mime_type = headers.get("Content-Type", "application/octet-stream").split(";", 1)[0].strip()
        return content, mime_type or "application/octet-stream"

    async def upload(
        self,
        filename: str,
        content: bytes,
        mime_type: str,
        *,
        fields: dict | None = None,
        document_id: int | None = None,
    ) -> Any:
        if len(content) > MAX_FILE_BYTES:
            raise _error("fileTooLarge")
        if (
            not filename or filename in {".", ".."}
            or any(char in filename for char in ("/", "\\"))
            or any(ord(char) < 32 or ord(char) == 127 for char in filename)
            or not re.fullmatch(r"[\w!#$&^.+-]+/[\w!#$&^.+-]+", mime_type, flags=re.ASCII)
        ):
            raise _error("invalidRequest")
        form = dict(fields or {})
        if "document" in form:
            raise _error("invalidRequest")
        if isinstance(form.get("custom_fields"), (dict, list)):
            form["custom_fields"] = json_module.dumps(form["custom_fields"])
        path = (
            "documents/post_document/" if document_id is None
            else f"documents/{_identifier(document_id)}/update_version/"
        )
        return await self.request(
            "POST", path, data=form, files={"document": (filename, content, mime_type)},
        )

    async def test_connection(self) -> dict[str, Any]:
        content, headers = await self._send(
            "GET", "documents/", params={"page_size": 1},
        )
        _object(_json(content))
        api_version = headers.get("X-Api-Version", "")
        version = headers.get("X-Version", "").strip()
        if not api_version.isdecimal() or int(api_version) < API_VERSION or not version:
            raise _error("incompatibleVersion")
        return {"version": version, "api_version": int(api_version), "requested_api_version": API_VERSION}
