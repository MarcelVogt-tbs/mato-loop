"""Timestamped, bounded PCM segments shared by audio and video analysis."""

from __future__ import annotations

import wave
from dataclasses import dataclass
from io import BytesIO

from mato_loop_backend.media.processing import AttachmentProcessingError, validate_pcm_wav
from mato_loop_backend.providers.base import MAX_PROVIDER_AUDIO_BYTES

AUDIO_SEGMENT_SECONDS = 120


@dataclass(frozen=True, slots=True)
class AudioSegment:
    start_ms: int
    end_ms: int
    data: bytes


def split_wav_audio(
    data: bytes, *, segment_seconds: int = AUDIO_SEGMENT_SECONDS,
) -> list[AudioSegment]:
    if isinstance(segment_seconds, bool) or not 1 <= segment_seconds <= 300:
        raise AttachmentProcessingError("attachment.invalidAudio")
    info = validate_pcm_wav(data)
    frame_bytes = info.channels * info.sample_width
    frames_per_segment = min(
        segment_seconds * info.sample_rate,
        (MAX_PROVIDER_AUDIO_BYTES - 44) // frame_bytes,
    )
    segments = []
    with wave.open(BytesIO(data), "rb") as source:
        for start in range(0, info.frames, frames_per_segment):
            frames = source.readframes(frames_per_segment)
            end = start + len(frames) // frame_bytes
            output = BytesIO()
            with wave.open(output, "wb") as target:
                target.setnchannels(info.channels)
                target.setsampwidth(info.sample_width)
                target.setframerate(info.sample_rate)
                target.writeframes(frames)
            segments.append(AudioSegment(
                start * 1_000 // info.sample_rate,
                end * 1_000 // info.sample_rate,
                output.getvalue(),
            ))
    return segments
