"""Bounded probing and transient extraction for video attachments."""

from __future__ import annotations

import json
import math
import subprocess
import tempfile
import wave
from collections.abc import Sequence
from dataclasses import dataclass
from io import BytesIO
from typing import Literal

from PIL import Image

MAX_VIDEO_BYTES = 64 * 1024 * 1024
MAX_VIDEO_DURATION_SECONDS = 15 * 60
MAX_VIDEO_WIDTH = 7_680
MAX_VIDEO_HEIGHT = 4_320
MAX_VIDEO_PIXELS = MAX_VIDEO_WIDTH * MAX_VIDEO_HEIGHT
MAX_MEDIA_STREAMS = 16
MAX_VIDEO_STREAMS = 4
MAX_AUDIO_STREAMS = 8
MAX_EXTRACTED_FRAMES = 64
MAX_FRAME_BYTES = 8 * 1024 * 1024
MAX_FRAME_WIDTH = 1_920
MAX_FRAME_HEIGHT = 1_080
MAX_PROBE_BYTES = 64 * 1024
FFPROBE_TIMEOUT_SECONDS = 30
FFMPEG_FRAME_TIMEOUT_SECONDS = 30
FFMPEG_AUDIO_TIMEOUT_SECONDS = 180
AUDIO_SEGMENT_SECONDS = 120

VideoDetail = Literal["standard", "detailed"]
_FRAME_PROFILES: dict[VideoDetail, tuple[int, int]] = {
    "standard": (5_000, 32),
    "detailed": (2_000, 64),
}

_VIDEO_MIMES = {"video/mp4": "mp4", "video/webm": "webm"}
_VIDEO_SUFFIXES = {".mp4": "mp4", ".webm": "webm"}


class VideoProcessingError(ValueError):
    def __init__(self) -> None:
        super().__init__("attachment.invalidVideo")
        self.code = "attachment.invalidVideo"


class VideoProcessingTimeout(VideoProcessingError):
    pass


@dataclass(frozen=True, slots=True)
class MediaInfo:
    format: str
    mime_type: str
    duration_ms: int
    width: int | None
    height: int | None
    video_stream_index: int | None
    audio_stream_index: int | None

    @property
    def has_video(self) -> bool:
        return self.video_stream_index is not None

    @property
    def has_audio(self) -> bool:
        return self.audio_stream_index is not None


@dataclass(frozen=True, slots=True)
class VideoFrame:
    timestamp_ms: int
    data: bytes


@dataclass(frozen=True, slots=True)
class AudioSegment:
    start_ms: int
    end_ms: int
    data: bytes


def claimed_video_format(mime_type: str, suffix: str) -> str | None:
    return _VIDEO_MIMES.get(mime_type) or _VIDEO_SUFFIXES.get(suffix)


def video_container_candidate(data: bytes, mime_type: str, suffix: str) -> bool:
    return (
        claimed_video_format(mime_type, suffix) is not None
        or data.startswith(b"\x1aE\xdf\xa3")
        or (len(data) >= 12 and data[4:8] == b"ftyp")
    )


def _run_media(
    command: list[str],
    data: bytes,
    timeout: int,
    max_stdout: int,
    source_path: str | None = None,
) -> bytes:
    if not data or len(data) > MAX_VIDEO_BYTES:
        raise VideoProcessingError
    try:
        if source_path is None:
            with tempfile.NamedTemporaryFile() as source:
                source.write(data)
                source.flush()
                return _run_media(command, data, timeout, max_stdout, source.name)
        with tempfile.TemporaryFile() as output:
            prepared = [source_path if item == "pipe:0" else item for item in command]
            for index, item in enumerate(prepared[:-1]):
                if item == "-protocol_whitelist":
                    prepared[index + 1] = "file,pipe"
            result = subprocess.run(
                prepared,
                stdin=subprocess.DEVNULL,
                stdout=output,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=timeout,
                shell=False,
            )
            size = output.tell()
            if result.returncode != 0 or size <= 0 or size > max_stdout:
                raise VideoProcessingError
            output.seek(0)
            return output.read(max_stdout + 1)
    except VideoProcessingError:
        raise
    except subprocess.TimeoutExpired as exc:
        raise VideoProcessingTimeout from exc
    except OSError as exc:
        raise VideoProcessingError from exc


def _container_format(value: object) -> str:
    names = set(value.split(",")) if isinstance(value, str) else set()
    if "webm" in names:
        return "webm"
    if "mp4" in names:
        return "mp4"
    raise VideoProcessingError


def _duration(payload: dict, streams: list[dict]) -> float:
    values = [payload.get("format", {}).get("duration")]
    values.extend(stream.get("duration") for stream in streams)
    try:
        durations = [float(value) for value in values if value not in {None, "N/A"}]
    except (TypeError, ValueError) as exc:
        raise VideoProcessingError from exc
    if any(not math.isfinite(value) or value < 0 for value in durations):
        raise VideoProcessingError
    positive = [value for value in durations if value > 0]
    if not positive:
        raise VideoProcessingError
    return max(positive)


def probe_media(data: bytes) -> MediaInfo:
    """Inspect a supported MP4/WebM container without trusting its name or signature."""
    raw = _run_media(
        [
            "ffprobe",
            "-v",
            "error",
            "-protocol_whitelist",
            "pipe",
            "-show_entries",
            "format=format_name,duration:stream=index,codec_type,width,height,duration:stream_disposition=attached_pic,default",
            "-of",
            "json",
            "pipe:0",
        ],
        data,
        FFPROBE_TIMEOUT_SECONDS,
        MAX_PROBE_BYTES,
    )
    try:
        payload = json.loads(raw)
        streams = payload["streams"]
        if not isinstance(streams, list) or not 1 <= len(streams) <= MAX_MEDIA_STREAMS:
            raise VideoProcessingError
        if not all(isinstance(stream, dict) for stream in streams):
            raise VideoProcessingError
        indices = [int(stream["index"]) for stream in streams]
        if any(index < 0 for index in indices) or len(indices) != len(set(indices)):
            raise VideoProcessingError
        media_format = _container_format(payload.get("format", {}).get("format_name"))
        duration = _duration(payload, streams)
        videos = [
            stream
            for stream in streams
            if stream.get("codec_type") == "video"
            and stream.get("disposition", {}).get("attached_pic") != 1
        ]
        audios = [stream for stream in streams if stream.get("codec_type") == "audio"]
        if len(videos) > MAX_VIDEO_STREAMS or len(audios) > MAX_AUDIO_STREAMS:
            raise VideoProcessingError
        if not videos and not audios:
            raise VideoProcessingError
        for stream in videos:
            width = int(stream["width"])
            height = int(stream["height"])
            if (
                not 1 <= width <= MAX_VIDEO_WIDTH
                or not 1 <= height <= MAX_VIDEO_HEIGHT
                or width * height > MAX_VIDEO_PIXELS
            ):
                raise VideoProcessingError
        if videos and duration > MAX_VIDEO_DURATION_SECONDS:
            raise VideoProcessingError
        videos.sort(key=lambda stream: stream.get("disposition", {}).get("default") != 1)
        audios.sort(key=lambda stream: stream.get("disposition", {}).get("default") != 1)
        video = videos[0] if videos else None
        audio = audios[0] if audios else None
        return MediaInfo(
            media_format,
            f"video/{media_format}",
            max(1, round(duration * 1_000)),
            int(video["width"]) if video else None,
            int(video["height"]) if video else None,
            int(video["index"]) if video else None,
            int(audio["index"]) if audio else None,
        )
    except VideoProcessingError:
        raise
    except (IndexError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise VideoProcessingError from exc


def probe_video(data: bytes) -> MediaInfo:
    info = probe_media(data)
    if not info.has_video:
        raise VideoProcessingError
    return info


def _frame_timestamps(duration_ms: int, count: int) -> list[int]:
    if isinstance(count, bool) or not 1 <= count <= MAX_EXTRACTED_FRAMES:
        raise VideoProcessingError
    return list(
        dict.fromkeys((2 * index + 1) * duration_ms // (2 * count) for index in range(count))
    )


def video_frame_timestamps(duration_ms: int, detail: VideoDetail) -> list[int]:
    try:
        spacing_ms, maximum = _FRAME_PROFILES[detail]
    except KeyError as exc:
        raise VideoProcessingError from exc
    count = min(maximum, max(1, math.ceil(duration_ms / spacing_ms)))
    return _frame_timestamps(duration_ms, count)


def extract_video_frames(
    data: bytes,
    timestamps_ms: Sequence[int] | None = None,
    *,
    max_frames: int | None = None,
    detail: VideoDetail = "standard",
) -> list[VideoFrame]:
    """Extract timestamped JPEGs in memory for an analyzer, never for persistence."""
    info = probe_video(data)
    timestamps = (
        list(timestamps_ms)
        if timestamps_ms is not None
        else (
            _frame_timestamps(info.duration_ms, max_frames)
            if max_frames is not None
            else video_frame_timestamps(info.duration_ms, detail)
        )
    )
    if (
        not timestamps
        or len(timestamps) > MAX_EXTRACTED_FRAMES
        or any(
            isinstance(timestamp, bool)
            or not isinstance(timestamp, int)
            or not 0 <= timestamp < info.duration_ms
            for timestamp in timestamps
        )
    ):
        raise VideoProcessingError

    frames = []
    with tempfile.NamedTemporaryFile() as source:
        source.write(data)
        source.flush()
        for timestamp in timestamps:
            frame = _run_media(
                [
                    "ffmpeg",
                    "-nostdin",
                    "-v",
                    "error",
                    "-protocol_whitelist",
                    "pipe",
                    "-ss",
                    f"{timestamp / 1_000:.3f}",
                    "-i",
                    "pipe:0",
                    "-map",
                    f"0:{info.video_stream_index}",
                    "-frames:v",
                    "1",
                    "-vf",
                    "scale=1920:1080:force_original_aspect_ratio=decrease",
                    "-threads",
                    "1",
                    "-f",
                    "image2pipe",
                    "-vcodec",
                    "mjpeg",
                    "pipe:1",
                ],
                data,
                FFMPEG_FRAME_TIMEOUT_SECONDS,
                MAX_FRAME_BYTES,
                source.name,
            )
            try:
                with Image.open(BytesIO(frame)) as image:
                    width, height = image.size
                    if (
                        image.format != "JPEG"
                        or width > MAX_FRAME_WIDTH
                        or height > MAX_FRAME_HEIGHT
                    ):
                        raise VideoProcessingError
                    image.verify()
                with Image.open(BytesIO(frame)) as image:
                    image.load()
            except VideoProcessingError:
                raise
            except (OSError, SyntaxError, ValueError) as exc:
                raise VideoProcessingError from exc
            frames.append(VideoFrame(timestamp, frame))
    return frames


def extract_video_audio(data: bytes) -> bytes | None:
    """Return a verified mono 16 kHz PCM WAV, or None when the video has no audio."""
    info = probe_video(data)
    if not info.has_audio:
        return None
    pcm = _run_media(
        [
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-protocol_whitelist",
            "pipe",
            "-i",
            "pipe:0",
            "-map",
            f"0:{info.audio_stream_index}",
            "-vn",
            "-t",
            str(MAX_VIDEO_DURATION_SECONDS),
            "-ac",
            "1",
            "-ar",
            "16000",
            "-acodec",
            "pcm_s16le",
            "-threads",
            "1",
            "-f",
            "s16le",
            "pipe:1",
        ],
        data,
        FFMPEG_AUDIO_TIMEOUT_SECONDS,
        MAX_VIDEO_DURATION_SECONDS * 16_000 * 2,
    )
    if len(pcm) % 2:
        raise VideoProcessingError
    output = BytesIO()
    with wave.open(output, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(16_000)
        wav_file.writeframes(pcm)
    wav_data = output.getvalue()
    try:
        with wave.open(BytesIO(wav_data), "rb") as wav_file:
            valid = (
                wav_file.getnchannels() == 1
                and wav_file.getsampwidth() == 2
                and wav_file.getframerate() == 16_000
                and 0 < wav_file.getnframes() <= MAX_VIDEO_DURATION_SECONDS * 16_000
                and len(wav_file.readframes(wav_file.getnframes())) == wav_file.getnframes() * 2
            )
    except (EOFError, OSError, wave.Error) as exc:
        raise VideoProcessingError from exc
    if not valid:
        raise VideoProcessingError
    return wav_data


def split_wav_audio(
    data: bytes, *, segment_seconds: int = AUDIO_SEGMENT_SECONDS
) -> list[AudioSegment]:
    if isinstance(segment_seconds, bool) or not 1 <= segment_seconds <= 300:
        raise VideoProcessingError
    try:
        with wave.open(BytesIO(data), "rb") as source:
            if (
                source.getnchannels() != 1
                or source.getsampwidth() != 2
                or source.getframerate() != 16_000
            ):
                raise VideoProcessingError
            rate = source.getframerate()
            frames_per_segment = segment_seconds * rate
            segments = []
            start_frame = 0
            while start_frame < source.getnframes():
                frames = source.readframes(frames_per_segment)
                if not frames:
                    break
                end_frame = start_frame + len(frames) // source.getsampwidth()
                output = BytesIO()
                with wave.open(output, "wb") as target:
                    target.setnchannels(1)
                    target.setsampwidth(2)
                    target.setframerate(rate)
                    target.writeframes(frames)
                segments.append(
                    AudioSegment(
                        start_ms=start_frame * 1_000 // rate,
                        end_ms=end_frame * 1_000 // rate,
                        data=output.getvalue(),
                    )
                )
                start_frame = end_frame
    except VideoProcessingError:
        raise
    except (EOFError, OSError, wave.Error) as exc:
        raise VideoProcessingError from exc
    if not segments:
        raise VideoProcessingError
    return segments
