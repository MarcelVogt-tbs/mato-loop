from __future__ import annotations

import asyncio
import base64
import binascii
import logging
import random
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from mato_loop_backend.media.attachments import MAX_IMAGE_ATTACHMENT_BYTES
from mato_loop_backend.providers.base import ProviderError
from mato_loop_backend.providers.codex_auth import codex_headers
from mato_loop_backend.providers.codex_oauth import (
    CODEX_IMAGES_EDITS_URL,
    CODEX_IMAGES_GENERATIONS_URL,
)

logger = logging.getLogger(__name__)

_MAX_JSON_BYTES = MAX_IMAGE_ATTACHMENT_BYTES * 4 // 3 + 1024 * 1024


class _ImageData(BaseModel):
    model_config = ConfigDict(extra="ignore")

    b64_json: str


class _ImageResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    created: int
    data: list[_ImageData] = Field(min_length=1, max_length=1)
    background: str | None = None
    quality: str | None = None
    size: str | None = None


async def _request_image(
    *,
    credential: dict[str, Any],
    url: str,
    payload: dict[str, Any],
    turn_id: str,
    timeout: float,
) -> tuple[bytes, dict[str, Any]]:
    headers = codex_headers(credential, image_turn_id=turn_id)
    response_content: bytes | None = None
    for attempt in range(5):
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                async with client.stream(
                    "POST",
                    url,
                    headers=headers,
                    json=payload,
                ) as response:
                    if response.status_code >= 400:
                        if response.status_code < 500 or attempt == 4:
                            logger.warning(
                                "Codex image request failed with HTTP status %s",
                                response.status_code,
                            )
                            raise ProviderError(
                                "Codex image request failed",
                                status_code=response.status_code,
                            )
                    else:
                        content = bytearray()
                        async for chunk in response.aiter_bytes():
                            if len(content) + len(chunk) > _MAX_JSON_BYTES:
                                raise ProviderError(
                                    "Codex image response is too large",
                                    code="provider.responseTooLarge",
                                )
                            content.extend(chunk)
                        response_content = bytes(content)
                        break
        except ProviderError:
            raise
        except httpx.RequestError as exc:
            if attempt == 4:
                if isinstance(exc, httpx.TimeoutException):
                    raise ProviderError(
                        "Codex image request timed out",
                        code="provider.timeout",
                        params={"timeoutSeconds": timeout, "retryable": True},
                    ) from exc
                raise ProviderError(
                    "Codex image request failed", code="provider.unavailable"
                ) from exc
        await asyncio.sleep((0.2 * 2**attempt) * random.uniform(0.9, 1.1))
    if response_content is None:
        raise ProviderError("Codex image request failed", code="provider.requestFailed")
    try:
        parsed = _ImageResponse.model_validate_json(response_content)
        data = base64.b64decode(parsed.data[0].b64_json, validate=True)
    except (ValidationError, ValueError, binascii.Error) as exc:
        raise ProviderError(
            "Codex returned an invalid image", code="provider.invalidResponse"
        ) from exc
    if not data:
        raise ProviderError(
            "Codex returned an invalid image", code="provider.invalidResponse"
        )
    if len(data) > MAX_IMAGE_ATTACHMENT_BYTES:
        raise ProviderError(
            "Codex image is too large", code="provider.responseTooLarge"
        )
    metadata = {
        key: value
        for key, value in {
            "created": parsed.created,
            "background": parsed.background,
            "quality": parsed.quality,
            "size": parsed.size,
        }.items()
        if value is not None
    }
    return data, metadata


async def generate_image(
    *,
    credential: dict[str, Any],
    prompt: str,
    size: str | None,
    turn_id: str,
    timeout: float,
) -> tuple[bytes, dict[str, Any]]:
    return await _request_image(
        credential=credential,
        url=CODEX_IMAGES_GENERATIONS_URL,
        payload={
            "prompt": prompt,
            "background": "auto",
            "model": "gpt-image-2",
            "quality": "auto",
            "size": size or "auto",
        },
        turn_id=turn_id,
        timeout=timeout,
    )


async def edit_image(
    *,
    credential: dict[str, Any],
    images: tuple[tuple[bytes, str], ...],
    prompt: str,
    turn_id: str,
    timeout: float,
) -> tuple[bytes, dict[str, Any]]:
    return await _request_image(
        credential=credential,
        url=CODEX_IMAGES_EDITS_URL,
        payload={
            "images": [
                {
                    "image_url": (
                        f"data:{mime_type};base64,"
                        f"{base64.b64encode(data).decode('ascii')}"
                    )
                }
                for data, mime_type in images
            ],
            "prompt": prompt,
            "background": "auto",
            "model": "gpt-image-2",
            "quality": "auto",
            "size": "auto",
        },
        turn_id=turn_id,
        timeout=timeout,
    )
