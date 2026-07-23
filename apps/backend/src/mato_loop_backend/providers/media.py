from __future__ import annotations

import asyncio
import math
import re
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.models import Credential
from mato_loop_backend.privacy import PseudonymizationError, RequestPseudonymizer
from mato_loop_backend.providers import lemonade
from mato_loop_backend.providers.base import ProviderError
from mato_loop_backend.security import read_credential
from mato_loop_backend.settings import AiSettings, SettingsStore
from mato_loop_backend.settings.models import (
    LlmProviderConfig,
    ModelAvailability,
    ModelCapability,
    ModelConfig,
    TrustLevel,
    effective_model_pseudonymization,
    effective_model_trust,
)

_SIZE_PATTERN = re.compile(r"^[1-9][0-9]{1,3}x[1-9][0-9]{1,3}$")
_LANGUAGE_PATTERN = re.compile(r"^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$")


@dataclass(frozen=True, slots=True)
class MediaResult:
    data: bytes
    mime_type: str
    filename: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class TranscriptionResult:
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ResolvedMediaModel:
    provider: LlmProviderConfig
    model: ModelConfig
    base_url: str
    api_key: str | None


_DEFAULT_FIELDS = {
    ModelCapability.IMAGE_GENERATION: "image_generation_model_id",
    ModelCapability.SPEECH_GENERATION: "speech_generation_model_id",
    ModelCapability.AUDIO_GENERATION: "audio_generation_model_id",
    ModelCapability.AUDIO_TRANSCRIPTION: "audio_transcription_model_id",
}


def media_model_available(
    settings: AiSettings,
    capability: ModelCapability,
    model_id: UUID | None = None,
) -> bool:
    field_name = _DEFAULT_FIELDS.get(capability)
    if field_name is None or not settings.enabled:
        return False
    candidate_id = model_id if model_id is not None else getattr(settings, field_name)
    model = next((item for item in settings.models if item.id == candidate_id), None)
    provider = (
        next((item for item in settings.providers if item.id == model.provider_id), None)
        if model is not None
        else None
    )
    return bool(
        model is not None
        and provider is not None
        and model.enabled
        and provider.enabled
        and model.availability == ModelAvailability.AVAILABLE
        and capability in model.capabilities
        and provider.adapter_type == "lemonade"
    )


def available_media_capabilities(settings: AiSettings) -> set[ModelCapability]:
    return {
        capability
        for capability in _DEFAULT_FIELDS
        if media_model_available(settings, capability)
    }


def _require_text(value: str, *, max_length: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > max_length:
        raise ProviderError("Invalid media request", code="provider.configurationInvalid")
    return value


def _optional_language(value: str | None) -> str | None:
    if value is not None and not _LANGUAGE_PATTERN.fullmatch(value):
        raise ProviderError("Invalid media request", code="provider.configurationInvalid")
    return value


def _optional_positive_number(
    value: float | int | None, *, maximum: float
) -> float | int | None:
    if value is None:
        return None
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value <= 0
        or value > maximum
    ):
        raise ProviderError("Invalid media request", code="provider.configurationInvalid")
    return value


async def _protected_text(
    provider: LlmProviderConfig, model: ModelConfig, value: str
) -> str:
    if (
        effective_model_trust(provider, model) != TrustLevel.PUBLIC
        or not effective_model_pseudonymization(provider, model)
    ):
        return value
    try:
        return await asyncio.to_thread(RequestPseudonymizer().protect_text, value)
    except PseudonymizationError as exc:
        raise ProviderError(
            "Media pseudonymization failed", code="privacy.pseudonymizationFailed"
        ) from exc


class MediaService:
    @staticmethod
    async def resolve_model(
        db: AsyncSession, capability: ModelCapability
    ) -> ResolvedMediaModel:
        field_name = _DEFAULT_FIELDS.get(capability)
        if field_name is None:
            raise ProviderError(
                "Unsupported media capability", code="provider.modelCapabilityUnavailable"
            )
        settings = await SettingsStore.load(db, AiSettings)
        model_id = getattr(settings, field_name)
        if not media_model_available(settings, capability, model_id):
            raise ProviderError(
                "No available default model for capability",
                code="provider.modelCapabilityUnavailable",
            )
        model = next((item for item in settings.models if item.id == model_id), None)
        provider = (
            next((item for item in settings.providers if item.id == model.provider_id), None)
            if model
            else None
        )
        assert model is not None and provider is not None
        base_url = str(provider.config.get("base_url") or "").rstrip("/")
        if not base_url:
            raise ProviderError(
                "Provider base URL is missing", code="provider.configurationInvalid"
            )
        api_key = None
        if provider.credential_id:
            credential = await db.get(Credential, provider.credential_id, populate_existing=True)
            if credential is None:
                raise ProviderError(
                    "Provider credential is missing", code="provider.configurationInvalid"
                )
            try:
                credential_payload = read_credential(credential)
            except Exception as exc:
                raise ProviderError(
                    "Provider credential is invalid", code="provider.configurationInvalid"
                ) from exc
            value = credential_payload.get("api_key")
            api_key = value if isinstance(value, str) and value else None
        return ResolvedMediaModel(provider, model, base_url, api_key)

    @classmethod
    async def generate_image(
        cls,
        db: AsyncSession,
        *,
        prompt: str,
        size: str = "512x512",
        steps: int | None = None,
        cfg_scale: float | None = None,
        seed: int | None = None,
        timeout: float = 300,
    ) -> MediaResult:
        prompt = _require_text(prompt, max_length=20_000)
        if not _SIZE_PATTERN.fullmatch(size):
            raise ProviderError("Invalid image size", code="provider.configurationInvalid")
        width, height = (int(item) for item in size.split("x", 1))
        if width > 4096 or height > 4096:
            raise ProviderError("Invalid image size", code="provider.configurationInvalid")
        _optional_positive_number(steps, maximum=1_000)
        _optional_positive_number(cfg_scale, maximum=100)
        if seed is not None and (isinstance(seed, bool) or not isinstance(seed, int) or seed < 0):
            raise ProviderError("Invalid image seed", code="provider.configurationInvalid")
        resolved = await cls.resolve_model(db, ModelCapability.IMAGE_GENERATION)
        protected = await _protected_text(resolved.provider, resolved.model, prompt)
        data, metadata = await lemonade.generate_image(
            base_url=resolved.base_url,
            api_key=resolved.api_key,
            model=resolved.model.model_id,
            prompt=protected,
            size=size,
            steps=steps,
            cfg_scale=cfg_scale,
            seed=seed,
            timeout=timeout,
        )
        return MediaResult(data, "image/png", "generated.png", metadata)

    @classmethod
    async def generate_speech(
        cls,
        db: AsyncSession,
        *,
        text: str,
        voice: str = "shimmer",
        speed: float = 1.0,
        timeout: float = 300,
    ) -> MediaResult:
        text = _require_text(text, max_length=100_000)
        voice = _require_text(voice, max_length=500)
        _optional_positive_number(speed, maximum=4)
        resolved = await cls.resolve_model(db, ModelCapability.SPEECH_GENERATION)
        protected = await _protected_text(resolved.provider, resolved.model, text)
        data = await lemonade.generate_speech(
            base_url=resolved.base_url,
            api_key=resolved.api_key,
            model=resolved.model.model_id,
            text=protected,
            voice=voice,
            speed=speed,
            timeout=timeout,
        )
        return MediaResult(data, "audio/wav", "speech.wav")

    @classmethod
    async def generate_audio(
        cls,
        db: AsyncSession,
        *,
        prompt: str,
        lyrics: str | None = None,
        vocal_language: str | None = None,
        duration: float | None = None,
        steps: int | None = None,
        cfg: float | None = None,
        seed: int | None = None,
        timeout: float = 300,
    ) -> MediaResult:
        prompt = _require_text(prompt, max_length=20_000)
        if lyrics is not None and len(lyrics) > 100_000:
            raise ProviderError("Invalid lyrics", code="provider.configurationInvalid")
        vocal_language = _optional_language(vocal_language)
        _optional_positive_number(duration, maximum=3_600)
        _optional_positive_number(steps, maximum=1_000)
        _optional_positive_number(cfg, maximum=100)
        if seed is not None and (isinstance(seed, bool) or not isinstance(seed, int) or seed < 0):
            raise ProviderError("Invalid audio seed", code="provider.configurationInvalid")
        resolved = await cls.resolve_model(db, ModelCapability.AUDIO_GENERATION)
        protected_prompt = await _protected_text(resolved.provider, resolved.model, prompt)
        protected_lyrics = (
            await _protected_text(resolved.provider, resolved.model, lyrics)
            if lyrics
            else lyrics
        )
        data = await lemonade.generate_audio(
            base_url=resolved.base_url,
            api_key=resolved.api_key,
            model=resolved.model.model_id,
            prompt=protected_prompt,
            lyrics=protected_lyrics,
            vocal_language=vocal_language,
            duration=duration,
            steps=steps,
            cfg=cfg,
            seed=seed,
            timeout=timeout,
        )
        return MediaResult(data, "audio/wav", "generated.wav")

    @classmethod
    async def transcribe_audio(
        cls,
        db: AsyncSession,
        *,
        audio: bytes,
        language: str | None = None,
        timeout: float = 300,
    ) -> TranscriptionResult:
        if not isinstance(audio, bytes) or not audio or len(audio) > lemonade.MAX_AUDIO_BYTES:
            raise ProviderError("Invalid audio input", code="provider.configurationInvalid")
        language = _optional_language(language)
        resolved = await cls.resolve_model(db, ModelCapability.AUDIO_TRANSCRIPTION)
        text = await lemonade.transcribe_audio(
            base_url=resolved.base_url,
            api_key=resolved.api_key,
            model=resolved.model.model_id,
            audio=audio,
            language=language,
            timeout=timeout,
        )
        return TranscriptionResult(text)
