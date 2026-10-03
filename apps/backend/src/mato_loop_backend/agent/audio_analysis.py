from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, ValidationError

from mato_loop_backend.agent.media_analysis import (
    ResolvedAnalysisModel,
    TranscriptionHandler,
    resolve_chat_target,
    transcribe_segments,
)
from mato_loop_backend.media.audio_processing import AudioSegment, split_wav_audio
from mato_loop_backend.media.processing import (
    AttachmentProcessingLimits,
    process_attachment,
)
from mato_loop_backend.media.video_processing import VideoAnalysisMode
from mato_loop_backend.providers.base import MAX_PROVIDER_AUDIO_BYTES
from mato_loop_backend.providers.media import resolve_media_target
from mato_loop_backend.settings.models import (
    AiSettings,
    ModelCapability,
    ModelInputModality,
    TrustLevel,
)

logger = logging.getLogger(__name__)
MAX_AUDIO_ANALYSIS_BYTES = 64 * 1024 * 1024


class AudioAnalysisError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class AudioEvent(BaseModel):
    start_ms: int = Field(alias="startMs", ge=0, strict=True)
    end_ms: int = Field(alias="endMs", ge=0, strict=True)
    text: str = Field(min_length=1, max_length=20_000)


class NativeAudioResult(BaseModel):
    segments: list[AudioEvent] = Field(min_length=1, max_length=100)


NativeAudioHandler = Callable[
    [bytes, str, int, TrustLevel, VideoAnalysisMode, str | None, str | None], Awaitable[str]
]


def resolve_audio_target(
    settings: AiSettings, *, active_model_id: UUID | None, speech: bool = False,
) -> ResolvedAnalysisModel | None:
    if speech:
        target = resolve_media_target(
            settings, ModelCapability.AUDIO_TRANSCRIPTION, active_model_id=active_model_id,
        )
        return ResolvedAnalysisModel(*target) if target else None
    for model_id in dict.fromkeys((active_model_id, settings.audio_analysis_model_id)):
        target = resolve_chat_target(settings, model_id)
        if target is not None and (
            ModelInputModality.AUDIO in target.model.input_modalities
            and target.provider.adapter_type == "openai-compatible"
            and target.provider.config.get("api_type", "chat_completions") == "chat_completions"
        ):
            return target
    return None


def audio_analysis_available(settings: AiSettings, *, active_model_id: UUID | None) -> bool:
    return any(
        resolve_audio_target(settings, active_model_id=active_model_id, speech=speech)
        is not None for speech in (False, True)
    )


async def analyze_audio(
    data: bytes, *, mime_type: str, filename: str, source_trust: TrustLevel,
    language: str | None, analysis_mode: VideoAnalysisMode, focus: str | None,
    native_handler: NativeAudioHandler | None,
    transcription_handler: TranscriptionHandler | None,
) -> dict[str, Any]:
    speech = analysis_mode == "speech"
    if (speech and transcription_handler is None) or (not speech and native_handler is None):
        raise AudioAnalysisError("audio.transcriptionUnavailable" if speech else "audio.analysisUnavailable")
    processed = await asyncio.to_thread(
        process_attachment, data, kind="audio", mime_type=mime_type, filename=filename,
        limits=AttachmentProcessingLimits(max_media_bytes=MAX_AUDIO_ANALYSIS_BYTES),
    )
    assert processed is not None and processed.duration_ms is not None
    wav = processed.derived_files.get("audio.wav", data)
    result: dict[str, Any] = {
        "mode": "transcript_only" if speech else "native_audio",
        "analysisMode": analysis_mode,
        "durationMs": processed.duration_ms,
        "audioStatus": "transcribed" if speech else "analyzed",
        "audioQuality": "not_assessed",
        "timestampPrecision": "segment_bounds" if speech else "estimated",
        "transcriptSegments": [],
        "audioSegments": [],
    }
    if speech:
        assert transcription_handler is not None
        segments = await transcribe_segments(
            wav, language=language, source_trust=source_trust, handler=transcription_handler,
        )
        result["transcriptSegments"] = [
            {"startMs": item.start_ms, "endMs": item.end_ms, "text": item.text}
            for item in segments
        ]
        return result

    assert native_handler is not None
    if processed.format == "mp3" and len(data) <= MAX_PROVIDER_AUDIO_BYTES:
        chunks = [AudioSegment(0, processed.duration_ms, data)]
        native_mime = "audio/mpeg"
    else:
        chunks = await asyncio.to_thread(split_wav_audio, wav)
        native_mime = "audio/wav"
    for chunk in chunks:
        duration = chunk.end_ms - chunk.start_ms
        content = (await native_handler(
            chunk.data, native_mime, duration, source_trust, analysis_mode, focus, language,
        )).strip()
        if content.startswith("```") and content.endswith("```"):
            content = content.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        try:
            native = NativeAudioResult.model_validate_json(content)
            for event in native.segments:
                if not event.text.strip() or not event.start_ms <= event.end_ms <= duration:
                    raise ValueError("Audio event outside segment")
                result["audioSegments"].append({
                    "startMs": chunk.start_ms + event.start_ms,
                    "endMs": chunk.start_ms + event.end_ms,
                    "text": event.text.strip(),
                })
        except (ValidationError, ValueError) as exc:
            logger.warning("Native audio analysis returned invalid segments")
            raise AudioAnalysisError("audio.invalidAnalysis") from exc
    return result
