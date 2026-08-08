from __future__ import annotations

import json
import subprocess
import wave
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image

import mato_loop_backend.video_processing as video


def probe_payload(
    *,
    duration: str = "2.5",
    width: int = 640,
    height: int = 360,
    audio: bool = True,
) -> bytes:
    streams = [
        {
            "index": 0,
            "codec_type": "video",
            "width": width,
            "height": height,
            "disposition": {"default": 1, "attached_pic": 0},
        }
    ]
    if audio:
        streams.append(
            {"index": 1, "codec_type": "audio", "disposition": {"default": 1}}
        )
    return json.dumps(
        {"format": {"format_name": "mov,mp4,m4a,3gp,3g2,mj2", "duration": duration}, "streams": streams}
    ).encode()


def test_probe_video_validates_streams_duration_and_dimensions(monkeypatch) -> None:
    monkeypatch.setattr(video, "_run_media", lambda *_args: probe_payload())

    info = video.probe_video(b"mp4")

    assert (info.format, info.mime_type, info.duration_ms) == ("mp4", "video/mp4", 2_500)
    assert (info.width, info.height, info.has_audio) == (640, 360, True)

    monkeypatch.setattr(
        video,
        "_run_media",
        lambda *_args: probe_payload(duration=str(video.MAX_VIDEO_DURATION_SECONDS + 0.1)),
    )
    with pytest.raises(video.VideoProcessingError, match="attachment.invalidVideo"):
        video.probe_video(b"too-long")

    monkeypatch.setattr(
        video,
        "_run_media",
        lambda *_args: probe_payload(width=video.MAX_VIDEO_WIDTH + 1),
    )
    with pytest.raises(video.VideoProcessingError, match="attachment.invalidVideo"):
        video.probe_video(b"too-wide")


def test_audio_only_container_is_not_a_video(monkeypatch) -> None:
    payload = json.dumps(
        {
            "format": {"format_name": "matroska,webm", "duration": "1"},
            "streams": [{"index": 0, "codec_type": "audio"}],
        }
    ).encode()
    monkeypatch.setattr(video, "_run_media", lambda *_args: payload)

    assert video.probe_media(b"webm").has_video is False
    with pytest.raises(video.VideoProcessingError, match="attachment.invalidVideo"):
        video.probe_video(b"webm")


def test_media_commands_disable_shell_and_bound_stdout(monkeypatch) -> None:
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        assert Path(command[-1]).read_bytes() == b"data"
        kwargs["stdout"].write(b"{}")
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(video.subprocess, "run", run)

    command = ["ffprobe", "-protocol_whitelist", "pipe", "-i", "pipe:0"]
    assert video._run_media(command, b"data", 3, 10) == b"{}"
    assert calls[0][0][-3:] == ["file,pipe", "-i", calls[0][0][-1]]
    assert calls[0][1]["stdin"] is subprocess.DEVNULL
    assert "input" not in calls[0][1]
    assert calls[0][1]["shell"] is False
    assert calls[0][1]["timeout"] == 3
    assert not Path(calls[0][0][-1]).exists()

    def oversized(command, **kwargs):
        kwargs["stdout"].write(b"x" * 11)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(video.subprocess, "run", oversized)
    with pytest.raises(video.VideoProcessingError, match="attachment.invalidVideo"):
        video._run_media(["ffprobe"], b"data", 3, 10)

    def timeout(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(["ffprobe"], 3)

    monkeypatch.setattr(video.subprocess, "run", timeout)
    with pytest.raises(video.VideoProcessingTimeout):
        video._run_media(["ffprobe"], b"data", 3, 10)


def test_frame_and_audio_extraction_are_bounded_and_verified(monkeypatch) -> None:
    info = video.MediaInfo("mp4", "video/mp4", 2_000, 640, 360, 0, 1)
    monkeypatch.setattr(video, "probe_video", lambda _data: info)
    image_buffer = BytesIO()
    Image.new("RGB", (16, 9)).save(image_buffer, format="JPEG")
    jpeg = image_buffer.getvalue()

    frame_commands = []

    def run_frames(command, *_args):
        assert command[0] == "ffmpeg"
        frame_commands.append(command)
        return jpeg

    monkeypatch.setattr(video, "_run_media", run_frames)
    frames = video.extract_video_frames(b"video", [250, 1_250])
    assert [frame.timestamp_ms for frame in frames] == [250, 1_250]
    assert all(frame.data == jpeg for frame in frames)
    assert all(command.index("-ss") < command.index("-i") for command in frame_commands)

    monkeypatch.setattr(video, "_run_media", lambda *_args: b"\x00\x00" * 160)
    wav_data = video.extract_video_audio(b"video")
    assert wav_data is not None
    with wave.open(BytesIO(wav_data), "rb") as wav_file:
        assert (wav_file.getnchannels(), wav_file.getframerate(), wav_file.getsampwidth()) == (
            1,
            16_000,
            2,
        )

    with pytest.raises(video.VideoProcessingError, match="attachment.invalidVideo"):
        video.extract_video_frames(b"video", list(range(video.MAX_EXTRACTED_FRAMES + 1)))


def test_video_analysis_mode_profiles_and_audio_segments() -> None:
    assert len(video.video_frame_timestamps(802_434, "overview")) == 32
    assert len(video.video_frame_timestamps(802_434, "fine_grained")) == 64
    assert video.video_frame_timestamps(2_000, "overview") == [1_000]
    with pytest.raises(video.VideoProcessingError):
        video.video_frame_timestamps(2_000, "speech")

    buffer = BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(16_000)
        wav_file.writeframes(b"\x00\x00" * 40_000)

    segments = video.split_wav_audio(buffer.getvalue(), segment_seconds=1)

    assert [(item.start_ms, item.end_ms) for item in segments] == [
        (0, 1_000),
        (1_000, 2_000),
        (2_000, 2_500),
    ]
