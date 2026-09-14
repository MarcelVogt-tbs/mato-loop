from __future__ import annotations

import asyncio
import json
import logging
from typing import Annotated

import httpx
from pydantic import BaseModel, Field, HttpUrl, StringConstraints, TypeAdapter, ValidationError

from mato_loop_backend.providers.base import ProviderError
from mato_loop_backend.providers.openai_compatible import DiscoveredModel
from mato_loop_backend.settings.models import ModelCapability, ModelInputModality

logger = logging.getLogger(__name__)

MODEL_ID = "azure-speech-fast-transcription"
API_VERSION = "2025-10-15"
MAX_AUDIO_BYTES = 64 * 1024 * 1024
_MAX_RESPONSE_BYTES = 1024 * 1024
_Locale = Annotated[
    str, StringConstraints(strict=True, pattern=r"^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})+$")
]


class _LocalesResponse(BaseModel):
    transcribe: list[_Locale] = Field(alias="Transcribe", min_length=1, max_length=1000)


class _CombinedPhrase(BaseModel):
    text: str = Field(strict=True, max_length=_MAX_RESPONSE_BYTES)


class _TranscriptionResponse(BaseModel):
    combined_phrases: list[_CombinedPhrase] = Field(alias="combinedPhrases", max_length=2)


def validate_endpoint(value: str | HttpUrl) -> str:
    endpoint = TypeAdapter(HttpUrl).validate_python(value)
    if (
        endpoint.scheme != "https"
        or endpoint.username
        or endpoint.password
        or endpoint.query
        or endpoint.fragment
        or endpoint.path not in {None, "", "/"}
    ):
        raise ValueError("Azure Speech requires an HTTPS origin without credentials")
    return str(endpoint).rstrip("/")


async def _request(
    *,
    base_url: str,
    api_key: str | None,
    timeout: float,
    audio: bytes | None = None,
    locales: list[str] | None = None,
) -> bytes:
    try:
        endpoint = validate_endpoint(base_url)
    except ValueError as exc:
        raise ProviderError(
            "Invalid Azure Speech endpoint", code="provider.configurationInvalid"
        ) from exc
    if not isinstance(api_key, str) or not api_key.strip():
        raise ProviderError(
            "Azure Speech API key is missing", code="provider.notConnected"
        )
    operation = "audio_transcription" if audio is not None else "discovery"
    path = "transcriptions:transcribe" if audio is not None else "transcriptions/locales"
    definition = {"locales": locales or [], "profanityFilterMode": "None"}
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream(
                "POST" if audio is not None else "GET",
                f"{endpoint}/speechtotext/{path}",
                params={"api-version": API_VERSION},
                headers={
                    "Ocp-Apim-Subscription-Key": api_key.strip(),
                    "Accept": "application/json",
                },
                data={"definition": json.dumps(definition)} if audio is not None else None,
                files={"audio": ("audio.wav", audio, "audio/wav")} if audio is not None else None,
            ) as response:
                if not response.is_success:
                    logger.warning(
                        "Azure Speech request failed: operation=%s status=%s",
                        operation,
                        response.status_code,
                    )
                    raise ProviderError(
                        "Azure Speech request failed", status_code=response.status_code
                    )
                content = bytearray()
                async for chunk in response.aiter_bytes():
                    if len(content) + len(chunk) > _MAX_RESPONSE_BYTES:
                        raise ProviderError(
                            "Azure Speech response is too large",
                            code="provider.responseTooLarge",
                        )
                    content.extend(chunk)
                return bytes(content)
    except httpx.RequestError as exc:
        logger.warning(
            "Azure Speech transport failed: operation=%s timeout_seconds=%s exception=%s",
            operation,
            timeout,
            type(exc).__name__,
        )
        if isinstance(exc, httpx.TimeoutException):
            raise ProviderError(
                "Azure Speech request timed out",
                code="provider.timeout",
                params={"timeoutSeconds": timeout, "retryable": True},
            ) from exc
        raise ProviderError(
            "Azure Speech is unavailable", code="provider.unavailable"
        ) from exc


async def _supported_locales(
    *, base_url: str, api_key: str | None, timeout: float
) -> list[str]:
    content = await _request(base_url=base_url, api_key=api_key, timeout=timeout)
    try:
        return _LocalesResponse.model_validate_json(content).transcribe
    except ValidationError as exc:
        raise ProviderError(
            "Azure Speech returned invalid locales", code="provider.invalidResponse"
        ) from exc


async def discover_models(
    *, base_url: str, api_key: str | None, timeout: float = 30
) -> list[DiscoveredModel]:
    await _supported_locales(base_url=base_url, api_key=api_key, timeout=timeout)
    return [
        DiscoveredModel(
            id=MODEL_ID,
            name="Azure Speech Fast Transcription",
            capabilities={ModelCapability.AUDIO_TRANSCRIPTION},
            input_modalities={ModelInputModality.AUDIO},
            supports_tools=False,
            supports_parallel_tool_calls=False,
            capabilities_authoritative=True,
        )
    ]


async def transcribe_audio(
    *,
    base_url: str,
    api_key: str | None,
    model: str,
    audio: bytes,
    language: str | None = None,
    timeout: float = 300,
) -> str:
    if model != MODEL_ID:
        raise ProviderError(
            "Unsupported Azure Speech model", code="provider.modelCapabilityUnavailable"
        )
    if not isinstance(audio, bytes) or not audio or len(audio) > MAX_AUDIO_BYTES:
        raise ProviderError("Invalid audio input", code="provider.configurationInvalid")
    try:
        async with asyncio.timeout(timeout):
            locales = None
            if language is not None:
                supported = await _supported_locales(
                    base_url=base_url, api_key=api_key, timeout=timeout
                )
                locales = [
                    locale
                    for locale in supported
                    if locale.casefold() == language.casefold()
                    or (
                        "-" not in language
                        and locale.partition("-")[0].casefold() == language.casefold()
                    )
                ]
                if not locales:
                    raise ProviderError(
                        "Unsupported Azure Speech language",
                        code="provider.configurationInvalid",
                        params={"field": "language"},
                    )
                # Azure language identification does not distinguish regional variants.
                locales = locales[:1]
            content = await _request(
                base_url=base_url,
                api_key=api_key,
                timeout=timeout,
                audio=audio,
                locales=locales,
            )
    except TimeoutError as exc:
        raise ProviderError(
            "Azure Speech request timed out",
            code="provider.timeout",
            params={"timeoutSeconds": timeout, "retryable": True},
        ) from exc
    try:
        result = _TranscriptionResponse.model_validate_json(content)
    except ValidationError as exc:
        raise ProviderError(
            "Azure Speech returned an invalid transcription",
            code="provider.invalidResponse",
        ) from exc
    return "\n".join(phrase.text for phrase in result.combined_phrases)
