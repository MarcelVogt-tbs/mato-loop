import json
import wave
from io import BytesIO
from unittest.mock import AsyncMock

import pytest

from mato_loop_backend.agent.audio_analysis import (
    AudioAnalysisError,
    analyze_audio,
    resolve_audio_target,
)
from mato_loop_backend.media.audio_processing import split_wav_audio
from mato_loop_backend.media.processing import AttachmentProcessingError
from mato_loop_backend.providers.settings_service import model_reference_available
from mato_loop_backend.settings import (
    AiSettings,
    TrustLevel,
    apply_agent_profile_ai,
    general_profile,
    resolve_agent_profile,
)
from mato_loop_backend.settings.models import (
    AgentProfileOverrides,
    CustomAgentProfile,
    LlmProviderConfig,
    ModelConfig,
)


def wav_bytes(seconds=1, channels=2, rate=8_000):
    buffer = BytesIO()
    with wave.open(buffer, "wb") as audio:
        audio.setnchannels(channels)
        audio.setsampwidth(2)
        audio.setframerate(rate)
        audio.writeframes(b"\x00\x00" * channels * rate * seconds)
    return buffer.getvalue()


async def analyze(data, **kwargs):
    return await analyze_audio(
        data, mime_type="audio/wav", filename="recording.wav", source_trust=TrustLevel.PRIVATE,
        language="en", focus="Find approximate sung lyrics: only you",
        **kwargs,
    )


@pytest.mark.parametrize("api_type", ["chat_completions", "responses"])
def test_audio_model_requires_native_transport_and_inherits_profile(api_type):
    provider = LlmProviderConfig(
        name="Audio", adapter_type="openai-compatible", config={"api_type": api_type},
    )
    active = ModelConfig(provider_id=provider.id, model_id="text")
    audio = ModelConfig(provider_id=provider.id, model_id="audio", input_modalities={"audio"})
    settings = AiSettings(providers=[provider], models=[active, audio])
    base = general_profile()
    base.audio_analysis_model_id = audio.id
    profile = CustomAgentProfile(
        name="Audio", description="", base_profile_id=base.id, overrides=AgentProfileOverrides(),
    )
    settings = apply_agent_profile_ai(settings, resolve_agent_profile(profile, base, None))
    target = resolve_audio_target(settings, active_model_id=active.id)
    assert (target.model.id if target else None) == (audio.id if api_type == "chat_completions" else None)
    assert model_reference_available(settings, "audio_analysis_model_id", audio.id) == (target is not None)


async def test_native_audio_keeps_pcm_and_adds_segment_offsets():
    data = wav_bytes(seconds=121)
    native = AsyncMock(return_value=json.dumps({
        "segments": [{"startMs": 100, "endMs": 500, "text": "Possible lyric match"}],
    }))
    result = await analyze(data, analysis_mode="fine_grained", native_handler=native, transcription_handler=None)
    assert [(event["startMs"], event["endMs"]) for event in result["audioSegments"]] == [
        (100, 500), (120_100, 120_500),
    ]
    with wave.open(BytesIO(native.await_args_list[0].args[0]), "rb") as audio:
        assert (audio.getnchannels(), audio.getframerate()) == (2, 8_000)


async def test_speech_is_transcript_only_and_never_calls_native_audio():
    native = AsyncMock(side_effect=AssertionError("Speech must not call native analysis"))
    result = await analyze(
        wav_bytes(), analysis_mode="speech", native_handler=native,
        transcription_handler=AsyncMock(return_value="only you\nonly you"),
    )
    assert (result["mode"], result["audioSegments"], result["transcriptSegments"]) == (
        "transcript_only", [], [{"startMs": 0, "endMs": 1_000, "text": "only you"}],
    )


async def test_transcription_cannot_substitute_native_audio():
    with pytest.raises(AudioAnalysisError, match="audio.analysisUnavailable"):
        await analyze(
            wav_bytes(), analysis_mode="overview", native_handler=None,
            transcription_handler=AsyncMock(side_effect=AssertionError("No silent fallback")),
        )


@pytest.mark.parametrize("data", [b"not audio", wav_bytes()[:-2]])
async def test_invalid_audio_never_reaches_model(data):
    with pytest.raises(AttachmentProcessingError, match="attachment.invalidAudio"):
        await analyze(
            data, analysis_mode="overview",
            native_handler=AsyncMock(side_effect=AssertionError("Invalid input sent")),
            transcription_handler=None,
        )


@pytest.mark.parametrize("content", ["", '{"segments":[]}', '{"segments":[{"startMs":0,"endMs":1001,"text":"match"}]}'])
async def test_invalid_native_timestamps_are_rejected(content):
    with pytest.raises(AudioAnalysisError, match="audio.invalidAnalysis"):
        await analyze(
            wav_bytes(), analysis_mode="overview", native_handler=AsyncMock(return_value=content),
            transcription_handler=None,
        )


def test_audio_chunks_respect_provider_byte_limit(monkeypatch):
    monkeypatch.setattr("mato_loop_backend.media.audio_processing.MAX_PROVIDER_AUDIO_BYTES", 16_044)
    assert [len(chunk.data) for chunk in split_wav_audio(wav_bytes())] == [16_044, 16_044]
