from __future__ import annotations

import base64
import binascii
import logging
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from mato_loop_backend.providers.base import ProviderError

logger = logging.getLogger(__name__)

MAX_IMAGE_BYTES = 10 * 1024 * 1024
_MAX_JSON_BYTES = 15 * 1024 * 1024


class _ImageData(BaseModel):
    model_config = ConfigDict(extra="ignore")

    b64_json: str


class _ImageResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    data: list[_ImageData] = Field(min_length=1, max_length=1)
    created: int | None = None


async def _request_image(
    *,
    base_url: str,
    api_key: str | None,
    payload: dict[str, Any],
    images: tuple[tuple[bytes, str], ...] = (),
    timeout: float,
) -> tuple[bytes, dict[str, Any]]:
    headers = {"Accept": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    operation = "image_editing" if images else "image_generation"
    endpoint = "edits" if images else "generations"
    request: dict[str, Any]
    if images:
        files = []
        for index, (data, mime_type) in enumerate(images):
            extension = {"image/png": "png", "image/jpeg": "jpg"}.get(mime_type)
            if extension is None:
                raise ProviderError(
                    "Unsupported image input type", code="provider.configurationInvalid"
                )
            files.append(("image[]", (f"image-{index + 1}.{extension}", data, mime_type)))
        request = {"data": payload, "files": files}
    else:
        request = {"json": payload}
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream(
                "POST",
                f"{base_url.rstrip('/')}/images/{endpoint}",
                headers=headers,
                **request,
            ) as response:
                if not response.is_success:
                    logger.warning("Images request failed with HTTP status %s", response.status_code)
                    raise ProviderError("Images request failed", status_code=response.status_code)
                content_length = response.headers.get("content-length")
                if content_length is not None:
                    try:
                        response_size = int(content_length)
                    except ValueError:
                        response_size = 0
                    if response_size > _MAX_JSON_BYTES:
                        raise ProviderError(
                            "Images response is too large", code="provider.responseTooLarge"
                        )
                content = bytearray()
                async for chunk in response.aiter_bytes():
                    if len(content) + len(chunk) > _MAX_JSON_BYTES:
                        raise ProviderError(
                            "Images response is too large", code="provider.responseTooLarge"
                        )
                    content.extend(chunk)
    except httpx.RequestError as exc:
        if isinstance(exc, httpx.TimeoutException):
            logger.warning(
                "Images request timed out: operation=%s timeout_seconds=%s exception=%s",
                operation,
                timeout,
                type(exc).__name__,
            )
            raise ProviderError(
                "Images request timed out",
                code="provider.timeout",
                params={"timeoutSeconds": timeout, "retryable": True},
            ) from exc
        logger.warning(
            "Images network request failed: operation=%s exception=%s",
            operation,
            type(exc).__name__,
        )
        raise ProviderError("Images provider is unavailable", code="provider.unavailable") from exc
    try:
        parsed = _ImageResponse.model_validate_json(content)
        data = base64.b64decode(parsed.data[0].b64_json, validate=True)
    except (ValueError, ValidationError, binascii.Error) as exc:
        raise ProviderError("Provider returned an invalid image", code="provider.invalidResponse") from exc
    if not data:
        raise ProviderError("Provider returned an empty image", code="provider.invalidResponse")
    if len(data) > MAX_IMAGE_BYTES:
        raise ProviderError("Provider image is too large", code="provider.responseTooLarge")
    metadata = {"created": parsed.created} if parsed.created is not None else {}
    return data, metadata


async def generate_image(
    *,
    base_url: str,
    api_key: str | None = None,
    model: str,
    prompt: str,
    size: str | None = None,
    steps: int | None = None,
    cfg_scale: float | None = None,
    seed: int | None = None,
    timeout: float = 300,
) -> tuple[bytes, dict[str, Any]]:
    payload: dict[str, Any] = {
        "model": model,
        "prompt": prompt,
        "n": 1,
        "response_format": "b64_json",
    }
    for key, value in (("size", size), ("steps", steps), ("cfg_scale", cfg_scale), ("seed", seed)):
        if value is not None:
            payload[key] = value
    return await _request_image(
        base_url=base_url,
        api_key=api_key,
        payload=payload,
        timeout=timeout,
    )


async def edit_image(
    *,
    base_url: str,
    api_key: str | None = None,
    model: str,
    prompt: str,
    images: tuple[tuple[bytes, str], ...],
    timeout: float = 300,
) -> tuple[bytes, dict[str, Any]]:
    if not images:
        raise ProviderError("Image editing requires an image", code="provider.configurationInvalid")
    return await _request_image(
        base_url=base_url,
        api_key=api_key,
        payload={"model": model, "prompt": prompt, "n": 1, "response_format": "b64_json"},
        images=images,
        timeout=timeout,
    )
