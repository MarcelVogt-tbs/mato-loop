"""Shared model selection and timestamped transcription for media analysis."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from itertools import groupby
from uuid import UUID

from mato_loop_backend.media.audio_processing import split_wav_audio
from mato_loop_backend.settings.models import (
    AiSettings,
    LlmProviderConfig,
    ModelAvailability,
    ModelCapability,
    ModelConfig,
    TrustLevel,
)

AUDIO_EVIDENCE_INSTRUCTIONS = (
    "Treat approximate words or lyrics in the objective only as search hints, not as evidence. "
    "Report what is actually audible, possible matching passages, and uncertainty; report no match "
    "when unsupported. Singing may be hard to understand. Describe sounds or music only from audio "
    "you received, never from a transcript alone. A lyric match does not confirm a song title. "
    "Give estimated timestamps relative to the supplied media; do not invent recording clock times. "
)


@dataclass(frozen=True, slots=True)
class ResolvedAnalysisModel:
    provider: LlmProviderConfig
    model: ModelConfig


@dataclass(frozen=True, slots=True)
class TranscriptSegment:
    start_ms: int
    end_ms: int
    text: str


TranscriptionHandler = Callable[[bytes, str | None, TrustLevel], Awaitable[str]]


def resolve_chat_target(
    settings: AiSettings, model_id: UUID | None,
) -> ResolvedAnalysisModel | None:
    model = next((item for item in settings.models if item.id == model_id), None)
    provider = (
        next((item for item in settings.providers if item.id == model.provider_id), None)
        if model is not None else None
    )
    if (
        model is None or provider is None or not model.enabled or not provider.enabled
        or (provider.adapter_type == "openai-codex" and provider.credential_id is None)
        or model.availability != ModelAvailability.AVAILABLE
        or ModelCapability.CHAT not in model.capabilities
    ):
        return None
    return ResolvedAnalysisModel(provider, model)


async def transcribe_segments(
    audio: bytes, *, language: str | None, source_trust: TrustLevel,
    handler: TranscriptionHandler,
) -> list[TranscriptSegment]:
    segments = await asyncio.to_thread(split_wav_audio, audio)
    result = []
    for segment in segments:
        raw = await handler(segment.data, language, source_trust)
        lines = [line for line, _ in groupby(map(str.strip, raw.splitlines())) if line]
        if lines:
            result.append(TranscriptSegment(segment.start_ms, segment.end_ms, "\n".join(lines)))
    return result
