import pytest

from mato_loop_backend.agent import video_analysis
from mato_loop_backend.agent.video_analysis import (
    NativeVideoResult,
    VideoAnalysisError,
    VisualSegment,
    analyze_video,
    resolve_native_video_target,
    resolve_video_vision_targets,
)
from mato_loop_backend.settings import AiSettings, TrustLevel
from mato_loop_backend.settings.models import (
    LlmProviderConfig,
    ModelConfig,
    ModelInputModality,
)
from mato_loop_backend.video_processing import (
    AudioSegment,
    MediaInfo,
    VideoFrame,
    VideoProcessingError,
    VideoProcessingTimeout,
)


def media_info(*, audio: bool = True) -> MediaInfo:
    return MediaInfo("mp4", "video/mp4", 2_000, 640, 360, 0, 1 if audio else None)


@pytest.mark.parametrize(
    ("active_modalities", "selected_modalities", "expected"),
    [
        ({"text", "image"}, {"text", "image"}, "active"),
        ({"text", "video"}, {"text", "image"}, "active"),
        ({"text", "image"}, {"text", "video"}, "selected"),
        ({"text"}, {"text", "image"}, "selected"),
    ],
)
def test_video_model_priority_uses_active_unless_configured_model_adds_video(
    active_modalities, selected_modalities, expected
) -> None:
    provider = LlmProviderConfig(name="Provider", adapter_type="openai-compatible")
    active = ModelConfig(
        provider_id=provider.id,
        model_id="active",
        input_modalities={ModelInputModality(item) for item in active_modalities},
        trust_level=TrustLevel.PRIVATE,
    )
    selected = ModelConfig(
        provider_id=provider.id,
        model_id="selected",
        input_modalities={ModelInputModality(item) for item in selected_modalities},
        trust_level=TrustLevel.PUBLIC,
    )
    vision = ModelConfig(provider_id=provider.id, model_id="vision", supports_image_input=True)
    settings = AiSettings(
        providers=[provider],
        models=[active, selected, vision],
        video_model_id=selected.id,
        vision_model_id=vision.id,
    )

    native = resolve_native_video_target(settings, active_model_id=active.id)
    frames = resolve_video_vision_targets(settings, active_model_id=active.id)

    target = native or frames[0]
    assert target.model.model_id == expected


def test_video_model_priority_uses_general_vision_as_last_fallback() -> None:
    provider = LlmProviderConfig(name="Provider", adapter_type="openai-compatible")
    active = ModelConfig(provider_id=provider.id, model_id="text")
    vision = ModelConfig(provider_id=provider.id, model_id="vision", supports_image_input=True)
    settings = AiSettings(
        providers=[provider],
        models=[active, vision],
        vision_model_id=vision.id,
    )

    targets = resolve_video_vision_targets(settings, active_model_id=active.id)

    assert [target.model.id for target in targets] == [vision.id]


async def test_native_video_analysis_includes_audio(monkeypatch) -> None:
    monkeypatch.setattr(video_analysis, "probe_video", lambda _data: media_info())
    monkeypatch.setattr(video_analysis, "extract_video_audio", lambda _data: b"wav")

    async def native(video, mime_type, audio, trust, detail, focus):
        assert (video, mime_type, audio, trust, detail, focus) == (
            b"video",
            "video/mp4",
            b"wav",
            TrustLevel.PRIVATE,
            "standard",
            None,
        )
        return NativeVideoResult("Chronological analysis", True)

    result = await analyze_video(
        b"video",
        source_trust=TrustLevel.PRIVATE,
        language="de",
        native_handler=native,
        frame_handler=None,
        transcription_handler=None,
    )

    assert result["mode"] == "native_audio_video"
    assert result["audioStatus"] == "analyzed"
    assert result["visualSegments"] == [
        {"startMs": 0, "endMs": 2_000, "text": "Chronological analysis"}
    ]


async def test_audio_extraction_failure_returns_specific_error(monkeypatch) -> None:
    monkeypatch.setattr(video_analysis, "probe_video", lambda _data: media_info())

    def fail_audio(_data):
        raise VideoProcessingError

    monkeypatch.setattr(video_analysis, "extract_video_audio", fail_audio)

    with pytest.raises(VideoAnalysisError, match="video.audioExtractionFailed"):
        await analyze_video(
            b"video",
            source_trust=TrustLevel.PRIVATE,
            language=None,
            native_handler=None,
            frame_handler=None,
            transcription_handler=None,
        )


async def test_frame_extraction_timeout_returns_timeout_error(monkeypatch) -> None:
    monkeypatch.setattr(video_analysis, "probe_video", lambda _data: media_info(audio=False))

    def timeout(_data, **_kwargs):
        raise VideoProcessingTimeout

    monkeypatch.setattr(video_analysis, "extract_video_frames", timeout)

    with pytest.raises(VideoAnalysisError, match="video.analysisTimeout"):
        await analyze_video(
            b"video",
            source_trust=TrustLevel.PRIVATE,
            language=None,
            native_handler=None,
            frame_handler=lambda *_args: None,
            transcription_handler=None,
        )


async def test_frame_fallback_combines_timestamps_and_transcript(monkeypatch) -> None:
    frames = [VideoFrame(0, b"first"), VideoFrame(1_000, b"second")]
    monkeypatch.setattr(video_analysis, "probe_video", lambda _data: media_info())
    monkeypatch.setattr(video_analysis, "extract_video_audio", lambda _data: b"wav")
    monkeypatch.setattr(video_analysis, "extract_video_frames", lambda _data, **_kwargs: frames)
    monkeypatch.setattr(
        video_analysis,
        "split_wav_audio",
        lambda _data: [AudioSegment(0, 2_000, b"wav")],
    )

    async def describe(received, transcripts, duration_ms, trust, focus):
        assert received == frames
        assert [segment.text for segment in transcripts] == ["Gesprochener Text"]
        assert duration_ms == 2_000
        assert trust == TrustLevel.PUBLIC
        assert focus == "setup steps"
        return [
            VisualSegment(0, 1_000, "At 0 ms the sequence starts. At 1000 ms it ends.")
        ]

    async def transcribe(audio, language, trust):
        assert (audio, language, trust) == (b"wav", "de", TrustLevel.PUBLIC)
        return "Gesprochener Text\nGesprochener Text"

    result = await analyze_video(
        b"video",
        source_trust=TrustLevel.PUBLIC,
        language="de",
        native_handler=None,
        frame_handler=describe,
        transcription_handler=transcribe,
        detail="detailed",
        focus="setup steps",
    )

    assert result["mode"] == "sampled_frames"
    assert result["audioStatus"] == "transcribed"
    assert result["visualSegments"] == [
        {
            "startMs": 0,
            "endMs": 1_000,
            "text": "At 0 ms the sequence starts. At 1000 ms it ends.",
        }
    ]
    assert result["transcriptSegments"] == [
        {"startMs": 0, "endMs": 2_000, "text": "Gesprochener Text"}
    ]
    assert result["sampledFrameTimestampsMs"] == [0, 1_000]
    assert result["detail"] == "detailed"
