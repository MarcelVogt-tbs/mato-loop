from __future__ import annotations

import asyncio
import json
import re
from collections import deque
from typing import Any
from urllib.parse import quote

import aiohttp
import httpx
from pydantic import BaseModel, ConfigDict, HttpUrl, model_validator

LOG_CAPTURE_SECONDS = 30
LOG_CAPTURE_BYTES = 256 * 1024


class ArcaneConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_url: HttpUrl

    @model_validator(mode="after")
    def validate_url(self) -> ArcaneConfig:
        if any((self.base_url.username, self.base_url.password, self.base_url.query, self.base_url.fragment)):
            raise ValueError("Arcane URL must not include credentials, query, or fragment")
        return self


class ArcaneError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def segment(value: str) -> str:
    if value in {".", ".."} or not value or len(value) > 256 or any(ord(char) < 32 for char in value):
        raise ValueError("invalid Arcane resource identifier")
    return quote(value, safe="")


def environment_id(value: str) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[A-Za-z0-9_-]{1,80}", value) is None:
        raise ValueError("invalid Arcane environment ID")
    return value


class ArcaneClient:
    def __init__(self, base_url: str, api_key: str, *, client: httpx.AsyncClient | None = None) -> None:
        config = ArcaneConfig(base_url=base_url)
        if not api_key or any(ord(char) < 33 or ord(char) > 126 for char in api_key):
            raise ArcaneError("integration.authenticationFailed")
        url = httpx.URL(str(config.base_url))
        self._base = url.copy_with(raw_path=url.raw_path.rstrip(b"/"))
        self._key = api_key
        self._client = client or httpx.AsyncClient(timeout=httpx.Timeout(30, read=300), trust_env=False)
        self._owned = client is None

    async def __aenter__(self) -> ArcaneClient:
        return self

    async def __aexit__(self, *_args: object) -> None:
        if self._owned:
            await self._client.aclose()

    def _url(self, path: str, params: dict[str, Any] | None = None) -> httpx.URL:
        # Paths are assembled only from host-known fragments and encoded identifiers.
        if not path.startswith("/") or ".." in path or "?" in path or "#" in path:
            raise ValueError("invalid Arcane API path")
        url = self._base.copy_with(raw_path=self._base.raw_path.rstrip(b"/") + b"/api" + path.encode())
        return url.copy_with(params=params) if params else url

    async def logs(self, environment: str, container: str, tail: int) -> dict[str, Any]:
        url = self._url(f"/environments/{environment_id(environment)}/ws/containers/{segment(container)}/logs",
                        {"tail": tail, "follow": "false", "format": "json", "timestamps": "true"})
        trace = aiohttp.TraceConfig()

        async def reject_redirect(*_args: Any) -> None:
            raise ArcaneError("integration.unavailable")

        trace.on_request_redirect.append(reject_redirect)
        lines: deque[bytes] = deque(maxlen=tail)
        size = 0
        received = 0
        truncated = False
        complete = False
        try:
            async with aiohttp.ClientSession(trust_env=False, timeout=aiohttp.ClientTimeout(total=30), trace_configs=[trace]) as session:
                async with session.ws_connect(str(url), headers={"X-Api-Key": self._key}, max_msg_size=LOG_CAPTURE_BYTES) as socket:
                    async with asyncio.timeout(LOG_CAPTURE_SECONDS):
                        while received < LOG_CAPTURE_BYTES:
                            try:
                                # Arcane does not close a finished log hub or emit an end marker.
                                message = await socket.receive(timeout=2 if lines else 10)
                            except TimeoutError:
                                break
                            if message.type == aiohttp.WSMsgType.CLOSE:
                                complete = message.data == 1000
                                truncated |= message.data == aiohttp.WSCloseCode.MESSAGE_TOO_BIG
                                break
                            if message.type == aiohttp.WSMsgType.ERROR and socket.close_code == aiohttp.WSCloseCode.MESSAGE_TOO_BIG:
                                truncated = True
                                break
                            if message.type != aiohttp.WSMsgType.TEXT:
                                raise ArcaneError("integration.invalidResponse")
                            received += len(message.data.encode("utf-8"))
                            event = json.loads(message.data)
                            if not isinstance(event, dict) or not isinstance(event.get("message"), str):
                                raise ArcaneError("integration.invalidResponse")
                            if event.get("service") == "arcane" and event.get("level") == "error":
                                raise ArcaneError("integration.unavailable")
                            for line in event["message"].splitlines() or [""]:
                                entry = (line + "\n").encode("utf-8")
                                truncated |= len(entry) > 32 * 1024 or len(lines) == tail
                                if len(lines) == tail:
                                    size -= len(lines[0])
                                entry = entry[-32 * 1024:]
                                lines.append(entry)
                                size += len(entry)
                                while size > 32 * 1024:
                                    size -= len(lines.popleft())
                                    truncated = True
                        else:
                            truncated = True
        except aiohttp.WSServerHandshakeError as exc:
            code = {401: "integration.authenticationFailed", 403: "integration.permissionDenied",
                    404: "integration.remoteNotFound", 429: "integration.rateLimited"}.get(exc.status, "integration.unavailable")
            raise ArcaneError(code) from exc
        except TimeoutError as exc:
            if not lines:
                raise ArcaneError("integration.unavailable") from exc
        except (aiohttp.ClientError, ValueError) as exc:
            raise ArcaneError("integration.unavailable") from exc
        return {"logs": b"".join(lines).decode("utf-8", errors="replace"), "truncated": truncated, "captureComplete": complete}

    async def request(
        self, method: str, path: str, *, params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None, streamed: bool = False,
        multipart: dict[str, Any] | None = None, max_bytes: int = 256 * 1024,
    ) -> Any:
        url = self._url(path, params)
        request = self._client.build_request(
            method, url, headers={"X-Api-Key": self._key, "Accept": "application/json"},
            **({"files": {key: (None, json.dumps(value), "application/json") for key, value in multipart.items()}}
               if multipart is not None else {"json": body}),
        )
        mutation = method != "GET"
        try:
            response = await self._client.send(request, stream=True, follow_redirects=False)
        except httpx.HTTPError as exc:
            raise ArcaneError("integration.outcomeUnknown" if mutation else "integration.unavailable") from exc
        try:
            status = response.status_code
            if status in {400, 422}:
                raise ArcaneError("tool.invalidArguments")
            if status == 401:
                raise ArcaneError("integration.authenticationFailed")
            if status == 403:
                raise ArcaneError("integration.permissionDenied")
            if status == 404:
                raise ArcaneError("integration.remoteNotFound")
            if status == 409:
                raise ArcaneError("integration.changed")
            if status == 429:
                raise ArcaneError("integration.rateLimited")
            if status < 200 or status >= 300:
                raise ArcaneError("integration.outcomeUnknown" if mutation else "integration.unavailable")
            limit = 2 * 1024 * 1024 if streamed else max_bytes
            if streamed:
                size = 0
                activity_id = None
                async for line in response.aiter_lines():
                    size += len(line.encode("utf-8")) + 1
                    if size > limit:
                        raise ArcaneError("integration.outcomeUnknown")
                    try:
                        event = json.loads(line)
                    except ValueError as exc:
                        raise ArcaneError("integration.outcomeUnknown") from exc
                    if not isinstance(event, dict):
                        raise ArcaneError("integration.outcomeUnknown")
                    if isinstance(event.get("activityId"), str):
                        activity_id = event["activityId"][:160]
                    if event.get("error") is not None:
                        raise ArcaneError("integration.outcomeUnknown")
                    if event.get("done") is True:
                        return {"completed": True, "activityId": activity_id}
                raise ArcaneError("integration.outcomeUnknown")
            size = 0
            chunks: list[bytes] = []
            async for chunk in response.aiter_bytes():
                size += len(chunk)
                if size > limit:
                    raise ArcaneError("integration.outcomeUnknown" if mutation else "integration.responseTooLarge")
                chunks.append(chunk)
            content = b"".join(chunks)
            try:
                payload = json.loads(content) if content else None
            except (ValueError, UnicodeDecodeError) as exc:
                raise ArcaneError("integration.outcomeUnknown" if mutation else "integration.invalidResponse") from exc
            if not isinstance(payload, dict) or payload.get("success") is not True:
                raise ArcaneError("integration.outcomeUnknown" if mutation else "integration.invalidResponse")
            if not mutation and "data" not in payload:
                raise ArcaneError("integration.invalidResponse")
            return payload.get("data"), payload.get("pagination")
        except httpx.HTTPError as exc:
            raise ArcaneError("integration.outcomeUnknown" if mutation else "integration.unavailable") from exc
        finally:
            await response.aclose()
