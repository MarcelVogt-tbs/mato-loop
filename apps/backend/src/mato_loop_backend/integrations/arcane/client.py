from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import quote

import httpx
from pydantic import BaseModel, ConfigDict, HttpUrl, model_validator


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

    async def request(
        self, method: str, path: str, *, params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None, streamed: bool = False, text: bool = False,
    ) -> Any:
        # Paths are assembled only from host-known fragments and encoded identifiers.
        if not path.startswith("/") or ".." in path or "?" in path or "#" in path:
            raise ValueError("invalid Arcane API path")
        url = self._base.copy_with(raw_path=self._base.raw_path + b"/api" + path.encode())
        if params:
            url = url.copy_with(params=params)
        request = self._client.build_request(
            method, url, headers={"X-Api-Key": self._key, "Accept": "text/plain" if text else "application/json"},
            json=body,
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
            if status == 429:
                raise ArcaneError("integration.rateLimited")
            if status < 200 or status >= 300:
                raise ArcaneError("integration.outcomeUnknown" if mutation else "integration.unavailable")
            limit = 2 * 1024 * 1024 if streamed else 32 * 1024 if text else 256 * 1024
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
            if text:
                return content.decode("utf-8", errors="replace")
            try:
                payload = json.loads(content) if content else None
            except (ValueError, UnicodeDecodeError) as exc:
                raise ArcaneError("integration.outcomeUnknown" if mutation else "integration.invalidResponse") from exc
            if not isinstance(payload, dict) or payload.get("success") is not True:
                raise ArcaneError("integration.outcomeUnknown" if mutation else "integration.invalidResponse")
            return payload.get("data"), payload.get("pagination")
        except httpx.HTTPError as exc:
            raise ArcaneError("integration.outcomeUnknown" if mutation else "integration.unavailable") from exc
        finally:
            await response.aclose()
