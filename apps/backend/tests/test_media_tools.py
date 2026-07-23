import json
import wave
from io import BytesIO
from unittest.mock import AsyncMock
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.agent.permissions import PermissionDecision, media_permission
from mato_loop_backend.agent.tools import (
    ToolContext,
    effective_tool_capabilities,
    tool_registry,
)
from mato_loop_backend.models import ChatSession
from mato_loop_backend.providers import MediaResult, ToolCall
from mato_loop_backend.providers.base import ProviderError
from mato_loop_backend.providers.media import MediaService, available_media_capabilities
from mato_loop_backend.session_storage import SessionStorage
from mato_loop_backend.settings import (
    AiSettings,
    SearchSettings,
    SettingsStore,
    ToolSettings,
)
from mato_loop_backend.settings.models import (
    LlmProviderConfig,
    ModelCapability,
    ModelConfig,
)
from mato_loop_backend.workspace import RootFileService


def wav_bytes() -> bytes:
    buffer = BytesIO()
    with wave.open(buffer, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(8_000)
        audio.writeframes(b"\x00\x00" * 80)
    return buffer.getvalue()


def context(tmp_path, call: ToolCall, decision=PermissionDecision.ALLOW) -> ToolContext:
    session = ChatSession(
        id=uuid4(),
        user_id=uuid4(),
        title="Medien",
        workspace_access="read-only",
    )
    storage = SessionStorage(tmp_path / "sessions")
    storage.provision(session.id)
    return ToolContext(
        db=AsyncMock(spec=AsyncSession),
        run_id=uuid4(),
        session=session,
        tool_call=call,
        root_files=RootFileService(
            session.id, session_storage=storage, workspace_home=tmp_path
        ),
        search_settings=SearchSettings(),
        ask_handler=AsyncMock(return_value=decision),
    )


def media_settings(capability: ModelCapability) -> AiSettings:
    provider = LlmProviderConfig(name="Lemonade", adapter_type="lemonade")
    model = ModelConfig(
        provider_id=provider.id,
        model_id="media",
        capabilities={capability},
    )
    defaults = {
        ModelCapability.IMAGE_GENERATION: "image_generation_model_id",
        ModelCapability.SPEECH_GENERATION: "speech_generation_model_id",
        ModelCapability.AUDIO_GENERATION: "audio_generation_model_id",
        ModelCapability.AUDIO_TRANSCRIPTION: "audio_transcription_model_id",
    }
    return AiSettings(
        providers=[provider],
        models=[model],
        **{defaults[capability]: model.id},
    )


def mock_settings_load(monkeypatch, tools: ToolSettings, ai: AiSettings) -> AsyncMock:
    async def load(_db, settings_type, **_kwargs):
        return tools if settings_type is ToolSettings else ai

    mocked = AsyncMock(side_effect=load)
    monkeypatch.setattr(SettingsStore, "load", mocked)
    return mocked


def test_media_schemas_follow_effective_capabilities() -> None:
    settings = ToolSettings.model_validate(
        {
            "generateImage": {"policy": "ask"},
            "generateSpeech": {"policy": "auto"},
        }
    )
    ai = media_settings(ModelCapability.IMAGE_GENERATION)
    capabilities = effective_tool_capabilities(
        settings, available_media_capabilities(ai)
    )
    schemas = tool_registry.schemas(
        SearchSettings(), "read-only", tool_capabilities=capabilities
    )
    names = {item["name"] for item in schemas}

    assert capabilities == {"media.generate.image"}
    assert "generate_image" in names
    assert "generate_speech" not in names
    assert "generate_audio" not in names
    assert "transcribe_audio" not in names


async def test_disabled_media_tool_is_rejected_before_provider(
    tmp_path, monkeypatch
) -> None:
    call = ToolCall(id="image", name="generate_image", arguments={"prompt": "Test"})
    tool_context = context(tmp_path, call)
    mock_settings_load(
        monkeypatch, ToolSettings(), media_settings(ModelCapability.IMAGE_GENERATION)
    )
    generate = AsyncMock(side_effect=AssertionError("Provider darf nicht aufgerufen werden"))
    monkeypatch.setattr(MediaService, "generate_image", generate)

    result = await tool_registry.execute(call, tool_context)

    assert result.is_error is True
    assert json.loads(result.content) == {
        "error": {
            "code": "tool.unavailable",
            "message": "The tool operation could not be completed.",
        }
    }
    generate.assert_not_awaited()


async def test_ask_denial_stops_before_media_service(tmp_path, monkeypatch) -> None:
    settings = ToolSettings.model_validate({"generateImage": {"policy": "ask"}})
    call = ToolCall(id="image", name="generate_image", arguments={"prompt": "Test"})
    tool_context = context(tmp_path, call, PermissionDecision.DENY)
    mock_settings_load(
        monkeypatch, settings, media_settings(ModelCapability.IMAGE_GENERATION)
    )
    generate = AsyncMock(side_effect=AssertionError("Provider darf nicht aufgerufen werden"))
    monkeypatch.setattr(MediaService, "generate_image", generate)

    result = await tool_registry.execute(call, tool_context)

    assert result.metadata == {"errorCode": "permission.denied"}
    generate.assert_not_awaited()
    request = tool_context.ask_handler.await_args.args[0]
    assert request == media_permission(
        tool_context.session.id, "media.generate.image", "generate_image"
    )


async def test_policy_revocation_during_approval_stops_media_service(
    tmp_path, monkeypatch
) -> None:
    ask_settings = ToolSettings.model_validate({"generateImage": {"policy": "ask"}})
    call = ToolCall(id="image", name="generate_image", arguments={"prompt": "Test"})
    tool_context = context(tmp_path, call)
    ai = media_settings(ModelCapability.IMAGE_GENERATION)
    tool_loads = 0

    async def load(_db, settings_type, **_kwargs):
        nonlocal tool_loads
        if settings_type is AiSettings:
            return ai
        tool_loads += 1
        return ask_settings if tool_loads == 1 else ToolSettings()

    monkeypatch.setattr(SettingsStore, "load", AsyncMock(side_effect=load))
    generate = AsyncMock(side_effect=AssertionError("Provider darf nicht aufgerufen werden"))
    monkeypatch.setattr(MediaService, "generate_image", generate)

    result = await tool_registry.execute(call, tool_context)

    assert result.metadata == {"errorCode": "tool.unavailable"}
    generate.assert_not_awaited()
    tool_context.ask_handler.assert_awaited_once()


async def test_auto_media_tool_returns_typed_attachment(tmp_path, monkeypatch) -> None:
    settings = ToolSettings.model_validate(
        {"generateSpeech": {"policy": "auto", "timeoutSeconds": 125}}
    )
    call = ToolCall(id="speech", name="generate_speech", arguments={"text": "Hallo"})
    tool_context = context(tmp_path, call)
    mock_settings_load(
        monkeypatch, settings, media_settings(ModelCapability.SPEECH_GENERATION)
    )
    generate = AsyncMock(return_value=MediaResult(wav_bytes(), "audio/wav", "speech.wav"))
    monkeypatch.setattr(MediaService, "generate_speech", generate)

    result = await tool_registry.execute(call, tool_context)

    assert result.is_error is False
    assert result.attachments[0].kind == "audio"
    assert result.attachments[0].filename == "speech.wav"
    tool_context.ask_handler.assert_not_awaited()
    generate.assert_awaited_once()
    assert generate.await_args.kwargs["timeout"] == 125


async def test_media_timeout_is_returned_to_model_with_retry_context(
    tmp_path, monkeypatch
) -> None:
    settings = ToolSettings.model_validate(
        {"generateImage": {"policy": "auto", "timeoutSeconds": 640}}
    )
    call = ToolCall(id="image", name="generate_image", arguments={"prompt": "Test"})
    tool_context = context(tmp_path, call)
    mock_settings_load(
        monkeypatch, settings, media_settings(ModelCapability.IMAGE_GENERATION)
    )
    generate = AsyncMock(
        side_effect=ProviderError(
            "timed out",
            code="provider.timeout",
            params={"timeoutSeconds": 640, "retryable": True},
        )
    )
    monkeypatch.setattr(MediaService, "generate_image", generate)

    result = await tool_registry.execute(call, tool_context)

    assert result.is_error is True
    assert json.loads(result.content) == {
        "error": {
            "code": "provider.timeout",
            "message": "The model provider request could not be completed.",
            "params": {"timeoutSeconds": 640, "retryable": True},
        }
    }
    assert generate.await_args.kwargs["timeout"] == 640


async def test_transcription_cannot_load_attachment_from_other_session(
    tmp_path, monkeypatch
) -> None:
    settings = ToolSettings.model_validate({"transcribeAudio": {"policy": "auto"}})
    call = ToolCall(
        id="transcribe",
        name="transcribe_audio",
        arguments={"attachment_id": str(uuid4())},
    )
    tool_context = context(tmp_path, call)
    tool_context.db.scalar.return_value = None
    mock_settings_load(
        monkeypatch, settings, media_settings(ModelCapability.AUDIO_TRANSCRIPTION)
    )
    transcribe = AsyncMock(side_effect=AssertionError("Provider darf nicht aufgerufen werden"))
    monkeypatch.setattr(MediaService, "transcribe_audio", transcribe)

    result = await tool_registry.execute(call, tool_context)

    assert result.metadata == {"errorCode": "attachment.notFound"}
    transcribe.assert_not_awaited()
    statement = tool_context.db.scalar.await_args.args[0]
    assert tool_context.session.id in statement.compile().params.values()
