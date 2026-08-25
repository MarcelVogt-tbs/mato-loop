from __future__ import annotations

import base64
import binascii
import json
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any, Literal
from urllib.parse import quote

import httpx

MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_EVENT_BYTES = 256 * 1024
MAX_FILE_CONTENT_CHARS = 1_500_000
MAX_MESSAGE_CHARS = 500_000
MAX_STRING_CHARS = 500_000
MAX_ITEMS = 500
MAX_PATH_CHARS = 4_096
MAX_IDENTIFIER_CHARS = 300

_STATUS_CODES = {
    400: "invalidRequest",
    401: "authenticationFailed",
    403: "permissionDenied",
    404: "notFound",
    409: "conflict",
    413: "responseTooLarge",
    422: "invalidRequest",
    429: "rateLimited",
}
_MESSAGE_ERRORS = {
    "APIError",
    "MessageAbortedError",
    "MessageOutputLengthError",
    "ProviderAuthError",
    "UnknownError",
}


class OpenCodeError(RuntimeError):
    def __init__(
        self,
        code: str,
        status_code: int | None = None,
        outcome_unknown: bool = False,
    ) -> None:
        super().__init__(code)
        self.code = code
        self.status_code = status_code
        self.outcome_unknown = outcome_unknown


@dataclass(frozen=True, slots=True)
class OpenCodeProbe:
    version: str
    root: str


@dataclass(frozen=True, slots=True)
class OpenCodeProject:
    id: str
    relative_path: str
    label: str


@dataclass(frozen=True, slots=True)
class OpenCodeModel:
    key: str
    provider_id: str
    model_id: str
    provider_label: str
    label: str


@dataclass(frozen=True, slots=True)
class OpenCodeFileEntry:
    path: str
    name: str
    type: Literal["file", "directory"]
    ignored: bool


@dataclass(frozen=True, slots=True)
class OpenCodeFileContent:
    type: Literal["text", "binary"]
    content: str
    encoding: Literal["base64"] | None
    mime_type: str | None


@dataclass(frozen=True, slots=True)
class OpenCodeFileStatus:
    path: str
    added: int
    removed: int
    status: Literal["added", "deleted", "modified"]


@dataclass(frozen=True, slots=True)
class OpenCodeSession:
    id: str
    title: str
    directory: str


@dataclass(frozen=True, slots=True)
class OpenCodeSessionStatus:
    id: str
    status: Literal["idle", "retry", "busy"]


@dataclass(frozen=True, slots=True)
class OpenCodePermission:
    id: str
    session_id: str
    title: str
    type: str
    patterns: tuple[str, ...]
    metadata: dict[str, Any]


@dataclass(frozen=True, slots=True)
class OpenCodeMessageResult:
    text: str
    error: str | None


def _error(
    code: str,
    *,
    status_code: int | None = None,
    outcome_unknown: bool = False,
) -> OpenCodeError:
    return OpenCodeError(
        f"opencode.{code}",
        status_code=status_code,
        outcome_unknown=outcome_unknown,
    )


def _origin(url: httpx.URL) -> tuple[str, str, int]:
    return url.scheme, url.host.lower(), url.port or (443 if url.scheme == "https" else 80)


def _parse_url(value: str) -> httpx.URL:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > MAX_PATH_CHARS
        or "\\" in value
        or any(character.isspace() or ord(character) < 32 for character in value)
    ):
        raise _error("invalidUrl")
    try:
        url = httpx.URL(value)
    except (httpx.InvalidURL, ValueError) as exc:
        raise _error("invalidUrl") from exc
    if (
        url.scheme not in {"http", "https"}
        or not url.host
        or url.userinfo
        or url.query
        or url.fragment
    ):
        raise _error("invalidUrl")
    return url.copy_with(raw_path=url.raw_path.rstrip(b"/"))


def canonicalize_base_url(value: str) -> str:
    return str(_parse_url(value))


def _string(
    value: Any,
    *,
    limit: int,
    response: bool = True,
    empty: bool = False,
) -> str:
    if (
        not isinstance(value, str)
        or len(value) > limit
        or (not empty and not value)
        or any(ord(character) < 32 and character not in "\n\r\t" for character in value)
    ):
        raise _error("malformedResponse" if response else "invalidRequest")
    return value


def _identifier(value: Any, *, response: bool = True) -> str:
    result = _string(value, limit=MAX_IDENTIFIER_CHARS, response=response)
    if any(character.isspace() or character in "/\\?#" for character in result):
        raise _error("malformedResponse" if response else "invalidRequest")
    return result


def _absolute_path(value: Any, *, response: bool) -> str:
    path = _string(value, limit=MAX_PATH_CHARS, response=response)
    windows = PureWindowsPath(path)
    posix = PurePosixPath(path)
    if not windows.is_absolute() and not posix.is_absolute():
        raise _error("malformedResponse" if response else "invalidRequest")
    if any(
        part in {".", ".."} for part in path.replace("\\", "/").split("/")
    ) or any(ord(character) < 32 or 127 <= ord(character) <= 159 for character in path):
        raise _error("malformedResponse" if response else "invalidRequest")
    return path


def _relative_to(root: str, candidate: str) -> str:
    path_type = PureWindowsPath if PureWindowsPath(root).is_absolute() else PurePosixPath
    try:
        relative = path_type(candidate).relative_to(path_type(root))
    except (TypeError, ValueError) as exc:
        raise _error("malformedResponse") from exc
    return relative.as_posix() or "."


def _relative_path(value: Any, *, response: bool, allow_root: bool = True) -> str:
    path = _string(value, limit=MAX_PATH_CHARS, response=response)
    normalized = path.replace("\\", "/")
    if normalized in {"", "."}:
        if allow_root:
            return "."
        raise _error("malformedResponse" if response else "invalidRequest")
    pure = PurePosixPath(normalized)
    if pure.is_absolute() or normalized != pure.as_posix() or any(
        part in {"", ".", ".."} for part in normalized.split("/")
    ):
        raise _error("malformedResponse" if response else "invalidRequest")
    return normalized


def parse_model_key(value: str) -> tuple[str, str]:
    key = _string(value, limit=MAX_IDENTIFIER_CHARS * 2 + 1, response=False)
    provider_id, separator, model_id = key.partition("/")
    if not separator:
        raise _error("invalidRequest")
    provider_id = _identifier(provider_id, response=False)
    model_id = _string(model_id, limit=MAX_IDENTIFIER_CHARS, response=False)
    if (
        model_id.startswith("/")
        or model_id.endswith("/")
        or any(part in {"", ".", ".."} for part in model_id.split("/"))
        or any(
            character.isspace()
            or ord(character) < 32
            or character in "\\?#"
            for character in model_id
        )
    ):
        raise _error("invalidRequest")
    return provider_id, model_id


def _bounded_json(value: Any, *, depth: int = 0) -> Any:
    if depth > 20:
        raise _error("malformedResponse")
    if isinstance(value, str):
        return _string(value, limit=MAX_STRING_CHARS)
    if value is None or isinstance(value, bool | int | float):
        return value
    if isinstance(value, list):
        if len(value) > MAX_ITEMS:
            raise _error("responseTooLarge")
        return [_bounded_json(item, depth=depth + 1) for item in value]
    if isinstance(value, dict):
        if len(value) > MAX_ITEMS:
            raise _error("responseTooLarge")
        return {
            _string(key, limit=MAX_IDENTIFIER_CHARS): _bounded_json(item, depth=depth + 1)
            for key, item in value.items()
        }
    raise _error("malformedResponse")


def _loads(body: bytes) -> Any:
    def invalid_constant(_value: str) -> None:
        raise ValueError

    try:
        return json.loads(body, parse_constant=invalid_constant)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise _error("malformedResponse") from exc


def _items(payload: Any) -> list[Any]:
    if not isinstance(payload, list):
        raise _error("malformedResponse")
    if len(payload) > MAX_ITEMS:
        raise _error("responseTooLarge")
    return payload


def _permission(payload: Any) -> OpenCodePermission:
    if not isinstance(payload, dict):
        raise _error("malformedResponse")
    pattern = payload.get("pattern")
    if pattern is None:
        patterns: tuple[str, ...] = ()
    elif isinstance(pattern, str):
        patterns = (_string(pattern, limit=MAX_PATH_CHARS),)
    elif isinstance(pattern, list) and len(pattern) <= MAX_ITEMS:
        patterns = tuple(_string(item, limit=MAX_PATH_CHARS) for item in pattern)
    else:
        raise _error("malformedResponse")
    metadata = _bounded_json(payload.get("metadata"))
    if not isinstance(metadata, dict):
        raise _error("malformedResponse")
    return OpenCodePermission(
        id=_identifier(payload.get("id")),
        session_id=_identifier(payload.get("sessionID")),
        title=_string(payload.get("title"), limit=1_000),
        type=_string(payload.get("type"), limit=200),
        patterns=patterns,
        metadata=metadata,
    )


class OpenCodeClient:
    def __init__(
        self,
        base_url: str,
        username: str,
        password: str | None = None,
        *,
        client: httpx.AsyncClient | None = None,
        max_response_bytes: int = MAX_RESPONSE_BYTES,
        max_event_bytes: int = MAX_EVENT_BYTES,
    ) -> None:
        self._base_url = _parse_url(base_url)
        self._origin = _origin(self._base_url)
        username = _string(username, limit=255, response=False)
        if ":" in username or any(character in "\r\n" for character in username):
            raise _error("invalidRequest")
        if password is not None:
            password = _string(password, limit=4_096, response=False, empty=True)
            if any(character in "\r\n" for character in password):
                raise _error("invalidRequest")
        credentials = f"{username}:{password or ''}".encode()
        self._authorization = "Basic " + base64.b64encode(credentials).decode("ascii")
        if not 1 <= max_response_bytes <= MAX_RESPONSE_BYTES:
            raise _error("invalidRequest")
        if not 1 <= max_event_bytes <= MAX_EVENT_BYTES:
            raise _error("invalidRequest")
        timeout = httpx.Timeout(connect=5, read=120, write=30, pool=5)
        self._client = client or httpx.AsyncClient(timeout=timeout, trust_env=False)
        self._owns_client = client is None
        self._max_response_bytes = max_response_bytes
        self._max_event_bytes = max_event_bytes
        self._root: str | None = None

    async def __aenter__(self) -> OpenCodeClient:
        return self

    async def __aexit__(self, *_args: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    @property
    def base_url(self) -> str:
        return str(self._base_url)

    @staticmethod
    def parse_model_key(value: str) -> tuple[str, str]:
        return parse_model_key(value)

    @staticmethod
    def parse_permission(payload: Any) -> OpenCodePermission:
        return _permission(payload)

    def _url(self, path: str, params: Mapping[str, str] | None = None) -> httpx.URL:
        if (
            not path.startswith("/")
            or "//" in path
            or any(character.isspace() or ord(character) < 32 or character in "?#\\" for character in path)
        ):
            raise _error("invalidUrl")
        base_path = self._base_url.raw_path.rstrip(b"/")
        url = self._base_url.copy_with(raw_path=base_path + path.encode("ascii"))
        if params:
            url = url.copy_with(params=dict(params))
        if _origin(url) != self._origin:
            raise _error("crossOrigin")
        return url

    def _directory(self, value: str) -> str:
        directory = _absolute_path(value, response=False)
        if self._root is not None:
            _relative_to(self._root, directory)
        return directory

    def _request_object(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, str] | None = None,
        payload: Mapping[str, Any] | None = None,
        accept: str = "application/json",
    ) -> httpx.Request:
        headers = {"Authorization": self._authorization, "Accept": accept}
        content = None
        if payload is not None:
            headers["Content-Type"] = "application/json"
            content = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode()
        return self._client.build_request(
            method,
            self._url(path, params),
            headers=headers,
            content=content,
        )

    @staticmethod
    def _transport_outcome_unknown(exc: httpx.HTTPError, mutation: bool) -> bool:
        return mutation and not isinstance(
            exc,
            (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout),
        )

    async def _send(self, request: httpx.Request, *, mutation: bool) -> httpx.Response:
        try:
            return await self._client.send(request, stream=True, follow_redirects=False)
        except httpx.HTTPError as exc:
            raise _error(
                "unavailable",
                outcome_unknown=self._transport_outcome_unknown(exc, mutation),
            ) from exc

    @staticmethod
    def _check_response(response: httpx.Response, *, expected: set[int]) -> None:
        status = response.status_code
        if 300 <= status < 400:
            raise _error("redirectRejected", status_code=status)
        if status not in expected:
            raise _error(
                _STATUS_CODES.get(status, "unexpectedStatus"),
                status_code=status,
            )

    async def _json_request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, str] | None = None,
        payload: Mapping[str, Any] | None = None,
        expected: set[int] = frozenset({200}),
        mutation: bool = False,
    ) -> Any:
        request = self._request_object(method, path, params=params, payload=payload)
        response = await self._send(request, mutation=mutation)
        try:
            self._check_response(response, expected=expected)
            media_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
            if media_type != "application/json":
                raise _error("malformedResponse", outcome_unknown=mutation)
            length = response.headers.get("content-length")
            if length is not None:
                try:
                    parsed_length = int(length)
                except ValueError as exc:
                    raise _error("malformedResponse", outcome_unknown=mutation) from exc
                if parsed_length < 0:
                    raise _error("malformedResponse", outcome_unknown=mutation)
                if parsed_length > self._max_response_bytes:
                    raise _error("responseTooLarge", outcome_unknown=mutation)
            chunks: list[bytes] = []
            size = 0
            try:
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > self._max_response_bytes:
                        raise _error("responseTooLarge", outcome_unknown=mutation)
                    chunks.append(chunk)
            except httpx.HTTPError as exc:
                raise _error("unavailable", outcome_unknown=mutation) from exc
            body = b"".join(chunks)
        finally:
            await response.aclose()
        try:
            return _loads(body)
        except OpenCodeError as exc:
            if mutation and not exc.outcome_unknown:
                raise OpenCodeError(exc.code, exc.status_code, True) from exc
            raise

    async def probe(self) -> OpenCodeProbe:
        health = await self._json_request("GET", "/global/health")
        if not isinstance(health, dict) or health.get("healthy") is not True:
            raise _error("malformedResponse")
        version = _string(health.get("version"), limit=100)
        paths = await self._json_request("GET", "/path")
        if not isinstance(paths, dict):
            raise _error("malformedResponse")
        root = _absolute_path(paths.get("directory"), response=True)
        self._root = root
        return OpenCodeProbe(version=version, root=root)

    async def list_projects(self) -> tuple[OpenCodeProject, ...]:
        if self._root is None:
            raise _error("notProbed")
        payload = await self._json_request("GET", "/project")
        projects: list[OpenCodeProject] = []
        for item in _items(payload):
            if not isinstance(item, dict):
                raise _error("malformedResponse")
            project_id = _identifier(item.get("id"))
            worktree = _absolute_path(item.get("worktree"), response=True)
            try:
                relative = _relative_to(self._root, worktree)
            except OpenCodeError:
                continue
            name = item.get("name")
            label = (
                _string(name, limit=240)
                if name is not None
                else (PurePosixPath(relative).name if relative != "." else project_id)
            )
            projects.append(OpenCodeProject(project_id, relative, label))
        return tuple(projects)

    async def list_models(self) -> tuple[OpenCodeModel, ...]:
        payload = await self._json_request("GET", "/provider")
        if not isinstance(payload, dict):
            raise _error("malformedResponse")
        providers = payload.get("all")
        connected = payload.get("connected")
        if not isinstance(providers, list) or not isinstance(connected, list):
            raise _error("malformedResponse")
        if len(providers) > MAX_ITEMS or len(connected) > MAX_ITEMS:
            raise _error("responseTooLarge")
        connected_ids = {_identifier(item) for item in connected}
        models: list[OpenCodeModel] = []
        for provider in providers:
            if not isinstance(provider, dict):
                raise _error("malformedResponse")
            provider_id = _identifier(provider.get("id"))
            if provider_id not in connected_ids:
                continue
            provider_label = _string(provider.get("name"), limit=240)
            provider_models = provider.get("models")
            if not isinstance(provider_models, dict):
                raise _error("malformedResponse")
            if len(provider_models) > MAX_ITEMS or len(models) + len(provider_models) > MAX_ITEMS:
                raise _error("responseTooLarge")
            for key, model in provider_models.items():
                if not isinstance(model, dict):
                    raise _error("malformedResponse")
                model_id = _string(model.get("id"), limit=MAX_IDENTIFIER_CHARS)
                if model_id != key or model.get("providerID", provider_id) != provider_id:
                    raise _error("malformedResponse")
                opaque_key = f"{provider_id}/{model_id}"
                parse_model_key(opaque_key)
                models.append(
                    OpenCodeModel(
                        key=opaque_key,
                        provider_id=provider_id,
                        model_id=model_id,
                        provider_label=provider_label,
                        label=_string(model.get("name"), limit=240),
                    )
                )
        return tuple(models)

    async def list_files(self, directory: str, path: str) -> tuple[OpenCodeFileEntry, ...]:
        directory = self._directory(directory)
        path = _relative_path(path, response=False)
        payload = await self._json_request(
            "GET", "/file", params={"directory": directory, "path": path}
        )
        entries: list[OpenCodeFileEntry] = []
        prefix = "" if path == "." else path + "/"
        for item in _items(payload):
            if not isinstance(item, dict):
                raise _error("malformedResponse")
            entry_path = _relative_path(item.get("path"), response=True, allow_root=False)
            name = _string(item.get("name"), limit=255)
            entry_type = item.get("type")
            ignored = item.get("ignored")
            if (
                not entry_path.startswith(prefix)
                or entry_path == path
                or PurePosixPath(entry_path).name != name
                or entry_type not in {"file", "directory"}
                or not isinstance(ignored, bool)
            ):
                raise _error("malformedResponse")
            entries.append(OpenCodeFileEntry(entry_path, name, entry_type, ignored))
        return tuple(entries)

    async def read_file(self, directory: str, path: str) -> OpenCodeFileContent:
        directory = self._directory(directory)
        path = _relative_path(path, response=False, allow_root=False)
        payload = await self._json_request(
            "GET", "/file/content", params={"directory": directory, "path": path}
        )
        if not isinstance(payload, dict) or payload.get("type") not in {"text", "binary"}:
            raise _error("malformedResponse")
        content = _string(payload.get("content"), limit=MAX_FILE_CONTENT_CHARS, empty=True)
        encoding = payload.get("encoding")
        if encoding not in {None, "base64"}:
            raise _error("malformedResponse")
        if payload["type"] == "binary" and encoding != "base64":
            raise _error("malformedResponse")
        if encoding == "base64":
            try:
                decoded = base64.b64decode(content, validate=True)
            except (ValueError, binascii.Error) as exc:
                raise _error("malformedResponse") from exc
            if len(decoded) > MAX_RESPONSE_BYTES:
                raise _error("responseTooLarge")
        mime_type = payload.get("mimeType")
        if mime_type is not None:
            mime_type = _string(mime_type, limit=255)
        return OpenCodeFileContent(payload["type"], content, encoding, mime_type)

    async def file_status(self, directory: str) -> tuple[OpenCodeFileStatus, ...]:
        directory = self._directory(directory)
        payload = await self._json_request(
            "GET", "/file/status", params={"directory": directory}
        )
        result: list[OpenCodeFileStatus] = []
        for item in _items(payload):
            if not isinstance(item, dict):
                raise _error("malformedResponse")
            added = item.get("added")
            removed = item.get("removed")
            status = item.get("status")
            if (
                isinstance(added, bool)
                or not isinstance(added, int)
                or added < 0
                or isinstance(removed, bool)
                or not isinstance(removed, int)
                or removed < 0
                or status not in {"added", "deleted", "modified"}
            ):
                raise _error("malformedResponse")
            result.append(
                OpenCodeFileStatus(
                    _relative_path(item.get("path"), response=True, allow_root=False),
                    added,
                    removed,
                    status,
                )
            )
        return tuple(result)

    async def list_session_statuses(
        self, directory: str
    ) -> tuple[OpenCodeSessionStatus, ...]:
        directory = self._directory(directory)
        payload = await self._json_request(
            "GET", "/session/status", params={"directory": directory}
        )
        if not isinstance(payload, dict):
            raise _error("malformedResponse")
        if len(payload) > MAX_ITEMS:
            raise _error("responseTooLarge")
        statuses: list[OpenCodeSessionStatus] = []
        for session_id, item in payload.items():
            if not isinstance(item, dict) or item.get("type") not in {"idle", "retry", "busy"}:
                raise _error("malformedResponse")
            statuses.append(OpenCodeSessionStatus(_identifier(session_id), item["type"]))
        return tuple(statuses)

    def _session(self, payload: Any, directory: str) -> OpenCodeSession:
        if not isinstance(payload, dict):
            raise _error("malformedResponse")
        returned_directory = _absolute_path(payload.get("directory"), response=True)
        if _relative_to(directory, returned_directory) != ".":
            raise _error("malformedResponse")
        return OpenCodeSession(
            id=_identifier(payload.get("id")),
            title=_string(payload.get("title"), limit=1_000),
            directory=directory,
        )

    async def create_session(self, directory: str, title: str) -> OpenCodeSession:
        directory = self._directory(directory)
        title = _string(title, limit=1_000, response=False)
        payload = await self._json_request(
            "POST",
            "/session",
            params={"directory": directory},
            payload={"title": title},
            mutation=True,
        )
        return self._session(payload, directory)

    async def send_message(
        self,
        directory: str,
        session_id: str,
        agent: str,
        model_key: str,
        text: str,
        tools: Mapping[str, bool] | None = None,
    ) -> OpenCodeMessageResult:
        directory = self._directory(directory)
        session_id = _identifier(session_id, response=False)
        agent = _identifier(agent, response=False)
        provider_id, model_id = parse_model_key(model_key)
        text = _string(text, limit=MAX_MESSAGE_CHARS, response=False)
        body: dict[str, Any] = {
            "agent": agent,
            "model": {"providerID": provider_id, "modelID": model_id},
            "parts": [{"type": "text", "text": text}],
        }
        if tools is not None:
            if not isinstance(tools, Mapping) or len(tools) > MAX_ITEMS:
                raise _error("invalidRequest")
            body["tools"] = {
                _identifier(key, response=False): value
                for key, value in tools.items()
                if isinstance(value, bool)
            }
            if len(body["tools"]) != len(tools):
                raise _error("invalidRequest")
        payload = await self._json_request(
            "POST",
            f"/session/{quote(session_id, safe='')}/message",
            params={"directory": directory},
            payload=body,
            mutation=True,
        )
        if not isinstance(payload, dict) or not isinstance(payload.get("info"), dict):
            raise _error("malformedResponse", outcome_unknown=True)
        parts = _items(payload.get("parts"))
        texts: list[str] = []
        length = 0
        for part in parts:
            if not isinstance(part, dict):
                raise _error("malformedResponse", outcome_unknown=True)
            if part.get("type") == "text":
                value = _string(part.get("text"), limit=MAX_MESSAGE_CHARS, empty=True)
                length += len(value)
                if length > MAX_MESSAGE_CHARS:
                    raise _error("responseTooLarge", outcome_unknown=True)
                texts.append(value)
        remote_error = payload["info"].get("error")
        if remote_error is None:
            error = None
        elif isinstance(remote_error, dict) and remote_error.get("name") in _MESSAGE_ERRORS:
            error = remote_error["name"]
        else:
            error = "UnknownError"
        return OpenCodeMessageResult("".join(texts), error)

    async def abort_session(self, directory: str, session_id: str) -> bool:
        return await self._boolean_mutation(
            directory, f"/session/{quote(_identifier(session_id, response=False), safe='')}/abort", {}
        )

    async def reply_permission(
        self,
        directory: str,
        session_id: str,
        permission_id: str,
        response: Literal["once", "always", "reject"],
    ) -> bool:
        if response not in {"once", "always", "reject"}:
            raise _error("invalidRequest")
        session_id = quote(_identifier(session_id, response=False), safe="")
        permission_id = quote(_identifier(permission_id, response=False), safe="")
        return await self._boolean_mutation(
            directory,
            f"/session/{session_id}/permissions/{permission_id}",
            {"response": response},
        )

    async def _boolean_mutation(
        self, directory: str, path: str, payload: Mapping[str, Any]
    ) -> bool:
        directory = self._directory(directory)
        result = await self._json_request(
            "POST",
            path,
            params={"directory": directory},
            payload=payload,
            mutation=True,
        )
        if not isinstance(result, bool):
            raise _error("malformedResponse", outcome_unknown=True)
        return result

    async def events(self, directory: str) -> AsyncIterator[dict[str, Any]]:
        directory = self._directory(directory)
        request = self._request_object(
            "GET",
            "/event",
            params={"directory": directory},
            accept="text/event-stream",
        )
        response = await self._send(request, mutation=False)
        try:
            self._check_response(response, expected={200})
            media_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
            if media_type != "text/event-stream":
                raise _error("malformedResponse")
            buffer = b""
            data: list[bytes] = []
            data_size = 0
            event_size = 0

            def dispatch() -> dict[str, Any] | None:
                nonlocal data, data_size
                if not data:
                    return None
                payload = _loads(b"\n".join(data))
                data = []
                data_size = 0
                bounded = _bounded_json(payload)
                if not isinstance(bounded, dict):
                    raise _error("malformedResponse")
                return bounded

            try:
                async for chunk in response.aiter_bytes():
                    buffer += chunk
                    while b"\n" in buffer:
                        line, buffer = buffer.split(b"\n", 1)
                        line = line.removesuffix(b"\r")
                        event_size += len(line) + 1
                        if event_size > self._max_event_bytes:
                            raise _error("eventTooLarge")
                        if not line:
                            if event := dispatch():
                                yield event
                            event_size = 0
                            continue
                        if line.startswith(b":") or not line.startswith(b"data:"):
                            continue
                        value = line[5:]
                        if value.startswith(b" "):
                            value = value[1:]
                        data_size += len(value) + (1 if data else 0)
                        if data_size > self._max_event_bytes:
                            raise _error("eventTooLarge")
                        data.append(value)
                    if len(buffer) + event_size > self._max_event_bytes:
                        raise _error("eventTooLarge")
                if buffer:
                    line = buffer.removesuffix(b"\r")
                    event_size += len(line)
                    if event_size > self._max_event_bytes:
                        raise _error("eventTooLarge")
                    if line.startswith(b"data:"):
                        value = line[5:].removeprefix(b" ")
                        data_size += len(value) + (1 if data else 0)
                        if data_size > self._max_event_bytes:
                            raise _error("eventTooLarge")
                        data.append(value)
                if event := dispatch():
                    yield event
            except httpx.HTTPError as exc:
                raise _error("unavailable") from exc
        finally:
            await response.aclose()
