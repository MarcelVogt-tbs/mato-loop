from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from mato_loop_backend.agent.media_analysis import (
    ResolvedAnalysisModel,
    TranscriptionHandler,
    TranscriptSegment,
    resolve_chat_target,
    transcribe_segments,
)
from mato_loop_backend.media.video_processing import (
    MediaInfo,
    VideoAnalysisMode,
    VideoFrame,
    VideoProcessingError,
    VideoProcessingTimeout,
    extract_video_audio,
    extract_video_frames,
    probe_video,
)
from mato_loop_backend.providers.media import resolve_media_target
from mato_loop_backend.settings.models import (
    AiSettings,
    ModelCapability,
    ModelInputModality,
    TrustLevel,
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
class VisualSegment:
    start_ms: int
    end_ms: int
    text: str


NativeVideoHandler = Callable[
    [bytes, str, bytes | None, TrustLevel, VideoAnalysisMode, str | None],
    Awaitable[NativeVideoResult],
]
FrameVisionHandler = Callable[
    [list[VideoFrame], list[TranscriptSegment], int, TrustLevel, str | None],
    Awaitable[list[VisualSegment]],
]


def _segment_dict(segment: TranscriptSegment | VisualSegment) -> dict[str, Any]:
    return {
        "startMs": segment.start_ms,
        "endMs": segment.end_ms,
        "text": segment.text,
    }


def resolve_native_video_target(
    settings: AiSettings, *, active_model_id: UUID | None
) -> ResolvedAnalysisModel | None:
    for model_id in dict.fromkeys((active_model_id, settings.video_model_id)):
        target = resolve_chat_target(settings, model_id)
        if target is None:
            continue
        provider, model = target.provider, target.model
        if (
            ModelInputModality.VIDEO in model.input_modalities
            and provider.adapter_type == "openai-compatible"
            and provider.config.get("api_type", "chat_completions") == "chat_completions"
        ):
            return target
    return None


def resolve_video_vision_targets(
    settings: AiSettings, *, active_model_id: UUID | None
) -> tuple[ResolvedAnalysisModel, ...]:
    targets = []
    for model_id in dict.fromkeys(
        (active_model_id, settings.video_model_id, settings.vision_model_id)
    ):
        target = resolve_chat_target(settings, model_id)
        if target is not None and target.model.supports_image_input:
            targets.append(target)
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
    analysis_mode: VideoAnalysisMode = "overview",
    focus: str | None = None,
) -> dict[str, Any]:
    started_at = time.monotonic()
    try:
        info: MediaInfo = await asyncio.to_thread(probe_video, data)
    except VideoProcessingTimeout as exc:
        logger.warning("Video probing timed out", exc_info=True)
        raise VideoAnalysisError("video.analysisTimeout") from exc
    except VideoProcessingError as exc:
        logger.warning("Video probing failed", exc_info=True)
        raise VideoAnalysisError("attachment.invalidVideo") from exc
    logger.info("Video probing completed in %d ms", round((time.monotonic() - started_at) * 1_000))
    audio_started_at = time.monotonic()
    try:
        audio = await asyncio.to_thread(extract_video_audio, data) if info.has_audio else None
    except VideoProcessingTimeout as exc:
        logger.warning("Video audio extraction timed out", exc_info=True)
        raise VideoAnalysisError("video.analysisTimeout") from exc
    except VideoProcessingError as exc:
        logger.warning("Video audio extraction failed", exc_info=True)
        raise VideoAnalysisError("video.audioExtractionFailed") from exc
    logger.info(
        "Video audio extraction completed in %d ms",
        round((time.monotonic() - audio_started_at) * 1_000),
    )
    transcript_segments: list[TranscriptSegment] = []

    if analysis_mode == "speech":
        if audio is not None:
            if transcription_handler is None:
                raise VideoAnalysisError("video.audioAnalysisUnavailable")
            transcription_started_at = time.monotonic()
            transcript_segments = await transcribe_segments(
                audio,
                language=language,
                source_trust=source_trust,
                handler=transcription_handler,
            )
            logger.info(
                "Video transcription completed in %d ms",
                round((time.monotonic() - transcription_started_at) * 1_000),
            )
        return {
            "mode": "transcript_only",
            "durationMs": info.duration_ms,
            "width": info.width,
            "height": info.height,
            "audioStatus": "transcribed" if audio is not None else "absent",
            "audioQuality": "not_assessed",
            "visualSegments": [],
            "transcriptSegments": [
                _segment_dict(segment) for segment in transcript_segments
            ],
            "sampledFrameTimestampsMs": [],
            "analysisMode": analysis_mode,
        }

    if native_handler is not None:
        native = await native_handler(
            data, info.mime_type, audio, source_trust, analysis_mode, focus
        )
        if not native.analysis.strip():
            logger.warning("Native video analysis returned empty content")
            raise VideoAnalysisError("video.emptyAnalysis")
        if audio is not None and not native.audio_analyzed:
            if transcription_handler is None:
                raise VideoAnalysisError("video.audioAnalysisUnavailable")
            transcript_segments = await transcribe_segments(
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
            "analysisMode": analysis_mode,
        }

    if frame_handler is None:
        raise VideoAnalysisError("video.analysisUnavailable")
    frame_started_at = time.monotonic()
    try:
        frames = await asyncio.to_thread(
            extract_video_frames, data, analysis_mode=analysis_mode
        )
    except VideoProcessingTimeout as exc:
        logger.warning("Video frame extraction timed out", exc_info=True)
        raise VideoAnalysisError("video.analysisTimeout") from exc
    except VideoProcessingError as exc:
        logger.warning("Video frame extraction failed", exc_info=True)
        raise VideoAnalysisError("video.frameExtractionFailed") from exc
    logger.info(
        "Video frame extraction completed in %d ms",
        round((time.monotonic() - frame_started_at) * 1_000),
    )
    if audio is not None:
        if transcription_handler is None:
            raise VideoAnalysisError("video.audioAnalysisUnavailable")
        transcription_started_at = time.monotonic()
        transcript_segments = await transcribe_segments(
            audio,
            language=language,
            source_trust=source_trust,
            handler=transcription_handler,
        )
        logger.info(
            "Video transcription completed in %d ms",
            round((time.monotonic() - transcription_started_at) * 1_000),
        )
    vision_started_at = time.monotonic()
    visual_segments = await frame_handler(
        frames, transcript_segments, info.duration_ms, source_trust, focus
    )
    logger.info(
        "Video frame analysis completed in %d ms",
        round((time.monotonic() - vision_started_at) * 1_000),
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
        "analysisMode": analysis_mode,
    }
