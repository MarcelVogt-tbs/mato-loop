from __future__ import annotations

import asyncio
import json
import logging
from typing import Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from mato_loop_backend.integrations.webiq.adapter import WEBIQ_PASSAGE_LENGTH, WebIqConfig
from mato_loop_backend.web.fetch import WebFetchError, WebFetchResult, canonicalize_url

logger = logging.getLogger(__name__)
MAX_RESPONSE_BYTES = 1024 * 1024


class Page(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)

    url: str = Field(min_length=1, max_length=4_000)
    title: str | None = Field(default=None, max_length=10_000)
    content: str = Field(max_length=WEBIQ_PASSAGE_LENGTH)


class SearchResponse(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)

    webResults: list[Page] = Field(max_length=8)


class Image(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)

    url: str = Field(min_length=1, max_length=4_000)
    hostPageUrl: str = Field(min_length=1, max_length=4_000)
    title: str | None = Field(default=None, max_length=10_000)
    caption: str | None = Field(default=None, max_length=10_000)


class ImageResponse(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)

    imageResults: list[Image] = Field(max_length=8)


async def _request(
    config: WebIqConfig, path: Literal["search/web", "search/images", "browse"],
    payload: dict[str, object], *, api_key: str, timeout: int | None = None,
    max_response_bytes: int = MAX_RESPONSE_BYTES,
) -> dict:
    try:
        async with asyncio.timeout(timeout or config.timeout_seconds):
            async with httpx.AsyncClient(
                timeout=timeout or config.timeout_seconds, trust_env=False,
                follow_redirects=False,
            ) as client:
                async with client.stream(
                    "POST", f"https://api.microsoft.ai/v3/{path}",
                    headers={"x-apikey": api_key, "Accept": "application/json", "Accept-Encoding": "identity"},
                    json=payload,
                ) as response:
                    if response.status_code != 200:
                        code = {
                            202: "webfetch.crawlPending",
                            401: "integration.authenticationFailed",
                            429: "integration.rateLimited",
                        }.get(response.status_code, "integration.requestFailed")
                        logger.warning("Web IQ %s returned HTTP %s", path, response.status_code)
                        raise WebFetchError(code, params={"statusCode": response.status_code})
                    if response.headers.get("Content-Encoding", "identity").lower() not in {"", "identity"}:
                        raise WebFetchError("integration.invalidResponse")
                    limit = min(max_response_bytes, MAX_RESPONSE_BYTES)
                    if int(response.headers.get("Content-Length", "0")) > limit:
                        raise WebFetchError("webfetch.responseTooLarge")
                    body = bytearray()
                    async for chunk in response.aiter_raw():
                        if len(body) + len(chunk) > limit:
                            raise WebFetchError("webfetch.responseTooLarge")
                        body.extend(chunk)
                    data = json.loads(body)
                    if not isinstance(data, dict):
                        raise WebFetchError("integration.invalidResponse")
                    return data
    except (TimeoutError, httpx.TimeoutException) as exc:
        logger.warning("Web IQ %s timed out", path)
        raise WebFetchError("webfetch.timeout") from exc
    except httpx.RequestError as exc:
        logger.warning("Web IQ %s failed: %s", path, type(exc).__name__)
        raise WebFetchError("integration.unavailable") from exc
    except (ValueError, UnicodeError) as exc:
        raise WebFetchError("integration.invalidResponse") from exc


def _query(value: str) -> str:
    if not value.strip() or len(value) > 1_000:
        raise WebFetchError("validation.invalid", params={"field": "query", "maxLength": 1_000})
    return value


async def search(
    config: WebIqConfig, query: str, *, api_key: str,
    search_type: Literal["web", "images"] = "web",
) -> list[dict]:
    payload: dict[str, object] = {"query": _query(query), "maxResults": 8}
    if search_type == "web":
        payload.update(contentFormat="passage", maxLength=WEBIQ_PASSAGE_LENGTH)
    data = await _request(config, f"search/{search_type}", payload, api_key=api_key)
    try:
        if search_type == "images":
            return [
                {"url": item.hostPageUrl, "image": item.url, "title": item.title, "content": item.caption}
                for item in ImageResponse.model_validate(data).imageResults
            ]
        return [page.model_dump() for page in SearchResponse.model_validate(data).webResults]
    except ValidationError as exc:
        raise WebFetchError("integration.invalidResponse") from exc


async def fetch(
    config: WebIqConfig, url: str, *, query: str, api_key: str,
    timeout: int | None = None, max_response_bytes: int = MAX_RESPONSE_BYTES,
) -> WebFetchResult:
    data = await _request(
        config, "browse",
        {"url": url, "query": _query(query), "contentFormat": "passage",
         "maxLength": WEBIQ_PASSAGE_LENGTH, "liveCrawl": "fallback"},
        api_key=api_key, timeout=timeout, max_response_bytes=max_response_bytes,
    )
    try:
        page = Page.model_validate(data)
    except ValidationError as exc:
        raise WebFetchError("integration.invalidResponse") from exc
    returned_url = canonicalize_url(page.url, preserve_fragment=True)
    return WebFetchResult(
        final_url=returned_url, content_type="text/plain", content=page.content,
        status_code=200, output_format="text", title=page.title,
    )
