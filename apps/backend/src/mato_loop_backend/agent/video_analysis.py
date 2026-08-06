from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from mato_loop_backend.providers.media import resolve_media_target
from mato_loop_backend.settings.models import (
    AiSettings,
    LlmProviderConfig,
    ModelAvailability,
    ModelCapability,
    ModelConfig,
    ModelInputModality,
    TrustLevel,
)
from mato_loop_backend.video_processing import (
    AudioSegment,
    MediaInfo,
    VideoDetail,
    VideoFrame,
    VideoProcessingError,
    VideoProcessingTimeout,
    extract_video_audio,
    extract_video_frames,
    probe_video,
    split_wav_audio,
)

logger = logging.getLogger(__name__)


class VideoAnalysisError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class NativeVideoResult:
    analysis: str
    audio_analyzed: bool


@dataclass(frozen=True, slots=True)
class ResolvedVideoModel:
    provider: LlmProviderConfig
    model: ModelConfig


@dataclass(frozen=True, slots=True)
class TranscriptSegment:
    start_ms: int
    end_ms: int
    text: str


@dataclass(frozen=True, slots=True)
class VisualSegment:
    start_ms: int
    end_ms: int
    text: str


NativeVideoHandler = Callable[
    [bytes, str, bytes | None, TrustLevel, VideoDetail, str | None],
    Awaitable[NativeVideoResult],
]
FrameVisionHandler = Callable[
    [list[VideoFrame], list[TranscriptSegment], int, TrustLevel, str | None],
    Awaitable[list[VisualSegment]],
]
TranscriptionHandler = Callable[[bytes, str | None, TrustLevel], Awaitable[str]]


def _clean_transcript(value: str) -> str:
    lines = []
    previous = None
    for line in (line.strip() for line in value.splitlines()):
        if line and line != previous:
            lines.append(line)
        previous = line
    return "\n".join(lines)


async def _transcribe_segments(
    audio: bytes,
    *,
    language: str | None,
    source_trust: TrustLevel,
    handler: TranscriptionHandler,
) -> list[TranscriptSegment]:
    segments: list[AudioSegment] = await asyncio.to_thread(split_wav_audio, audio)
    result = []
    for segment in segments:
        text = _clean_transcript(await handler(segment.data, language, source_trust))
        if text:
            result.append(TranscriptSegment(segment.start_ms, segment.end_ms, text))
    return result


def _segment_dict(segment: TranscriptSegment | VisualSegment) -> dict[str, Any]:
    return {
        "startMs": segment.start_ms,
        "endMs": segment.end_ms,
        "text": segment.text,
    }


def _chat_target(
    settings: AiSettings, model_id: UUID | None
) -> tuple[LlmProviderConfig, ModelConfig] | None:
    model = next((item for item in settings.models if item.id == model_id), None)
    provider = (
        next((item for item in settings.providers if item.id == model.provider_id), None)
        if model is not None
        else None
    )
    if (
        not settings.enabled
        or model is None
        or provider is None
        or not model.enabled
        or not provider.enabled
        or (provider.adapter_type == "openai-codex" and provider.credential_id is None)
        or model.availability != ModelAvailability.AVAILABLE
        or ModelCapability.CHAT not in model.capabilities
    ):
        return None
    return provider, model


def resolve_native_video_target(
    settings: AiSettings, *, active_model_id: UUID | None
) -> ResolvedVideoModel | None:
    for model_id in dict.fromkeys((active_model_id, settings.video_model_id)):
        target = _chat_target(settings, model_id)
        if target is None:
            continue
        provider, model = target
        if (
            ModelInputModality.VIDEO in model.input_modalities
            and provider.adapter_type == "openai-compatible"
            and provider.config.get("api_type", "chat_completions") == "chat_completions"
        ):
            return ResolvedVideoModel(provider, model)
    return None


def resolve_video_vision_targets(
    settings: AiSettings, *, active_model_id: UUID | None
) -> tuple[ResolvedVideoModel, ...]:
    targets = []
    for model_id in dict.fromkeys(
        (active_model_id, settings.video_model_id, settings.vision_model_id)
    ):
        target = _chat_target(settings, model_id)
        if target is not None and target[1].supports_image_input:
            targets.append(ResolvedVideoModel(*target))
    return tuple(targets)


def video_analysis_available(
    settings: AiSettings, *, active_model_id: UUID | None
) -> bool:
    return (
        resolve_native_video_target(settings, active_model_id=active_model_id) is not None
        or bool(resolve_video_vision_targets(settings, active_model_id=active_model_id))
    )


def video_transcription_available(
    settings: AiSettings, *, active_model_id: UUID | None
) -> bool:
    return (
        resolve_media_target(
            settings,
            ModelCapability.AUDIO_TRANSCRIPTION,
            active_model_id=active_model_id,
        )
        is not None
    )


async def analyze_video(
    data: bytes,
    *,
    source_trust: TrustLevel,
    language: str | None,
    native_handler: NativeVideoHandler | None,
    frame_handler: FrameVisionHandler | None,
    transcription_handler: TranscriptionHandler | None,
    detail: VideoDetail = "standard",
    focus: str | None = None,
) -> dict[str, Any]:
    try:
        info: MediaInfo = await asyncio.to_thread(probe_video, data)
    except VideoProcessingTimeout as exc:
        logger.warning("Video probing timed out", exc_info=True)
        raise VideoAnalysisError("video.analysisTimeout") from exc
    except VideoProcessingError as exc:
        logger.warning("Video probing failed", exc_info=True)
        raise VideoAnalysisError("attachment.invalidVideo") from exc
    try:
        audio = await asyncio.to_thread(extract_video_audio, data) if info.has_audio else None
    except VideoProcessingTimeout as exc:
        logger.warning("Video audio extraction timed out", exc_info=True)
        raise VideoAnalysisError("video.analysisTimeout") from exc
    except VideoProcessingError as exc:
        logger.warning("Video audio extraction failed", exc_info=True)
        raise VideoAnalysisError("video.audioExtractionFailed") from exc
    transcript_segments: list[TranscriptSegment] = []

    if native_handler is not None:
        native = await native_handler(
            data, info.mime_type, audio, source_trust, detail, focus
        )
        if not native.analysis.strip():
            logger.warning("Native video analysis returned empty content")
            raise VideoAnalysisError("video.emptyAnalysis")
        if audio is not None and not native.audio_analyzed:
            if transcription_handler is None:
                raise VideoAnalysisError("video.audioAnalysisUnavailable")
            transcript_segments = await _transcribe_segments(
                audio,
                language=language,
                source_trust=source_trust,
                handler=transcription_handler,
            )
        return {
            "mode": (
                "native_audio_video" if native.audio_analyzed else "native_video"
            ),
            "durationMs": info.duration_ms,
            "width": info.width,
            "height": info.height,
            "audioStatus": (
                "analyzed"
                if native.audio_analyzed
                else "transcribed"
                if audio is not None
                else "absent"
            ),
            "audioQuality": "not_assessed",
            "visualSegments": [
                {
                    "startMs": 0,
                    "endMs": info.duration_ms,
                    "text": native.analysis.strip(),
                }
            ],
            "transcriptSegments": [
                _segment_dict(segment) for segment in transcript_segments
            ],
            "sampledFrameTimestampsMs": [],
            "detail": detail,
        }

    if frame_handler is None:
        raise VideoAnalysisError("video.analysisUnavailable")
    try:
        frames = await asyncio.to_thread(extract_video_frames, data, detail=detail)
    except VideoProcessingTimeout as exc:
        logger.warning("Video frame extraction timed out", exc_info=True)
        raise VideoAnalysisError("video.analysisTimeout") from exc
    except VideoProcessingError as exc:
        logger.warning("Video frame extraction failed", exc_info=True)
        raise VideoAnalysisError("video.frameExtractionFailed") from exc
    if audio is not None:
        if transcription_handler is None:
            raise VideoAnalysisError("video.audioAnalysisUnavailable")
        transcript_segments = await _transcribe_segments(
            audio,
            language=language,
            source_trust=source_trust,
            handler=transcription_handler,
        )
    visual_segments = await frame_handler(
        frames, transcript_segments, info.duration_ms, source_trust, focus
    )
    if not visual_segments or any(not segment.text.strip() for segment in visual_segments):
        logger.warning("Video frame analysis returned empty content")
        raise VideoAnalysisError("video.emptyAnalysis")
    return {
        "mode": "sampled_frames",
        "durationMs": info.duration_ms,
        "width": info.width,
        "height": info.height,
        "audioStatus": "transcribed" if audio is not None else "absent",
        "audioQuality": "not_assessed",
        "visualSegments": [_segment_dict(segment) for segment in visual_segments],
        "transcriptSegments": [
            _segment_dict(segment) for segment in transcript_segments
        ],
        "sampledFrameTimestampsMs": [frame.timestamp_ms for frame in frames],
        "detail": detail,
    }
