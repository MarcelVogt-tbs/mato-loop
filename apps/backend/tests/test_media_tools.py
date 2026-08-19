import json
import wave
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.agent.media_prompt_profiles import (
    GPT_IMAGE_2_EDIT_INSTRUCTIONS,
    GPT_IMAGE_2_GENERATE_INSTRUCTIONS,
    Z_IMAGE_TURBO_GENERATE_INSTRUCTIONS,
    with_media_prompt_profiles,
)
from mato_loop_backend.agent.permissions import PermissionDecision, media_permission
from mato_loop_backend.agent.tools import (
    ToolContext,
    effective_tool_capabilities,
    tool_registry,
)
from mato_loop_backend.attachments import AttachmentService
from mato_loop_backend.models import ChatSession, Message, MessageAttachment
from mato_loop_backend.providers import MediaResult, ToolCall
from mato_loop_backend.providers.base import ProviderError
from mato_loop_backend.providers.media import (
    MediaService,
    available_media_capabilities,
    media_model_available,
    resolve_media_target,
)
from mato_loop_backend.session_storage import SessionStorage
from mato_loop_backend.settings import (
    AiSettings,
    IntegrationSettings,
    SettingsStore,
    ToolSettings,
)
from mato_loop_backend.settings.models import (
    LlmProviderConfig,
    ModelCapability,
    ModelConfig,
    TrustLevel,
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
        root_files=RootFileService(session.id, session_storage=storage),
        integration_settings=IntegrationSettings(),
        ask_handler=AsyncMock(return_value=decision),
    )


def media_settings(capability: ModelCapability) -> AiSettings:
    provider = LlmProviderConfig(
        name="Lemonade",
        adapter_type="lemonade",
        config={"base_url": "https://lemonade.test/v1"},
    )
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


def codex_edit_settings() -> tuple[AiSettings, ModelConfig]:
    provider = LlmProviderConfig(
        name="Codex",
        adapter_type="openai-codex",
        credential_id=uuid4(),
    )
    chat = ModelConfig(provider_id=provider.id, model_id="openai-codex/gpt-test")
    image = ModelConfig(
        provider_id=provider.id,
        model_id="openai-codex/gpt-image-2",
        capabilities={
            ModelCapability.IMAGE_GENERATION,
            ModelCapability.IMAGE_EDITING,
        },
        discovery_metadata={"native_for_chat_provider": True},
    )
    return AiSettings(providers=[provider], models=[chat, image]), chat


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
        IntegrationSettings(), "read-only", tool_capabilities=capabilities
    )
    names = {item["name"] for item in schemas}

    assert capabilities == {
        "media.generate.image",
        "settings.agent_identity.update",
    }
    assert "generate_image" in names
    assert "update_agent_identity" in names
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
    generate = AsyncMock(side_effect=AssertionError("Provider must not be called"))
    monkeypatch.setattr(MediaService, "generate_image", generate)

    result = await tool_registry.execute(call, tool_context)

    assert result.is_error is True
    assert json.loads(result.content) == {
        "error": {
            "code": "tool.unavailable",
                "message": "The tool is unavailable.",
        }
    }
    generate.assert_not_awaited()


async def test_video_tool_revalidation_keeps_frame_analysis_available(
    tmp_path, monkeypatch
) -> None:
    provider = LlmProviderConfig(name="Local", adapter_type="openai-compatible")
    model = ModelConfig(
        provider_id=provider.id,
        model_id="vision",
        supports_image_input=True,
    )
    ai = AiSettings(providers=[provider], models=[model], video_model_id=model.id)
    call = ToolCall(id="video", name="analyze_video", arguments={})
    tool_context = context(tmp_path, call)
    tool_context.model_id = model.id
    mock_settings_load(monkeypatch, ToolSettings(), ai)

    result = await tool_registry.execute(call, tool_context)

    assert result.metadata == {"errorCode": "tool.invalidArguments"}


async def test_ask_denial_stops_before_media_service(tmp_path, monkeypatch) -> None:
    settings = ToolSettings.model_validate({"generateImage": {"policy": "ask"}})
    call = ToolCall(id="image", name="generate_image", arguments={"prompt": "Test"})
    tool_context = context(tmp_path, call, PermissionDecision.DENY)
    ai = media_settings(ModelCapability.IMAGE_GENERATION)
    mock_settings_load(monkeypatch, settings, ai)
    generate = AsyncMock(side_effect=AssertionError("Provider must not be called"))
    monkeypatch.setattr(MediaService, "generate_image", generate)

    result = await tool_registry.execute(call, tool_context)

    assert result.metadata == {"errorCode": "permission.denied"}
    generate.assert_not_awaited()
    request = tool_context.ask_handler.await_args.args[0]
    provider, model = ai.providers[0], ai.models[0]
    assert request == media_permission(
        tool_context.session.id,
        "media.generate.image",
        "generate_image",
        provider_id=provider.id,
        model_id=model.id,
        model_name=model.model_id,
        trust_level="public",
    )


async def test_generated_image_is_also_published_as_a_draft(
    tmp_path, monkeypatch
) -> None:
    settings = ToolSettings.model_validate({"generateImage": {"policy": "auto"}})
    call = ToolCall(id="image", name="generate_image", arguments={"prompt": "Test"})
    tool_context = context(tmp_path, call)
    ai = media_settings(ModelCapability.IMAGE_GENERATION)
    tool_context.model_id = ai.models[0].id
    mock_settings_load(monkeypatch, settings, ai)
    monkeypatch.setattr(
        MediaService,
        "generate_image",
        AsyncMock(return_value=MediaResult(b"image", "image/png", "generated.png")),
    )

    result = await tool_registry.execute(call, tool_context)

    assert result.draft_action is not None
    assert result.draft_action.filename == "generated.png"
    assert result.draft_action.attachment_position == 0
    assert tool_context.root_files.read_bytes(
        "session", result.draft_action.path, limit=100
    ) == b"image"


def test_image_target_prefers_native_provider_model_over_global_default() -> None:
    codex = LlmProviderConfig(
        name="Codex",
        adapter_type="openai-codex",
        credential_id=uuid4(),
    )
    lemonade = LlmProviderConfig(name="Lemonade", adapter_type="lemonade")
    chat = ModelConfig(provider_id=codex.id, model_id="openai-codex/gpt-test")
    native = ModelConfig(
        provider_id=codex.id,
        model_id="openai-codex/gpt-image-2",
        capabilities={
            ModelCapability.IMAGE_GENERATION,
            ModelCapability.IMAGE_EDITING,
        },
        discovery_metadata={"native_for_chat_provider": True},
    )
    fallback = ModelConfig(
        provider_id=lemonade.id,
        model_id="Z-Image-Turbo",
        capabilities={ModelCapability.IMAGE_GENERATION},
    )
    settings = AiSettings(
        providers=[codex, lemonade],
        models=[chat, native, fallback],
        image_generation_model_id=fallback.id,
    )
    impostor = ModelConfig(
        provider_id=codex.id,
        model_id="openai-codex/not-an-image-model",
        capabilities={ModelCapability.IMAGE_GENERATION},
    )
    settings.models.append(impostor)

    assert not media_model_available(
        settings, ModelCapability.IMAGE_GENERATION, impostor.id
    )

    assert resolve_media_target(
        settings,
        ModelCapability.IMAGE_GENERATION,
        active_model_id=chat.id,
    ) == (codex, native)
    assert resolve_media_target(
        settings,
        ModelCapability.IMAGE_EDITING,
        active_model_id=chat.id,
    ) == (codex, native)

    native.enabled = False
    assert resolve_media_target(
        settings,
        ModelCapability.IMAGE_GENERATION,
        active_model_id=chat.id,
    ) == (lemonade, fallback)


def test_media_prompt_profiles_follow_offered_resolved_target() -> None:
    codex = LlmProviderConfig(
        name="Codex",
        adapter_type="openai-codex",
        credential_id=uuid4(),
    )
    lemonade = LlmProviderConfig(name="Lemonade", adapter_type="lemonade")
    chat = ModelConfig(provider_id=codex.id, model_id="openai-codex/gpt-test")
    native = ModelConfig(
        provider_id=codex.id,
        model_id="openai-codex/gpt-image-2",
        capabilities={
            ModelCapability.IMAGE_GENERATION,
            ModelCapability.IMAGE_EDITING,
        },
        discovery_metadata={"native_for_chat_provider": True},
    )
    z_image = ModelConfig(
        provider_id=lemonade.id,
        model_id="Z-Image-Turbo",
        capabilities={ModelCapability.IMAGE_GENERATION},
    )
    settings = AiSettings(
        providers=[codex, lemonade],
        models=[chat, native, z_image],
        image_generation_model_id=z_image.id,
    )
    offered = tool_registry.effective(
        IntegrationSettings(),
        "read-only",
        tool_capabilities={"media.generate.image", "media.edit.image"},
    ).only("generate_image", "edit_image")

    profiled = with_media_prompt_profiles(
        offered, settings, active_model_id=chat.id
    )

    assert profiled.prompt_fragments == (
        GPT_IMAGE_2_GENERATE_INSTRUCTIONS,
        GPT_IMAGE_2_EDIT_INSTRUCTIONS,
    )
    assert profiled.tools["generate_image"].selected_options["media_model_id"] == str(
        native.id
    )

    native.enabled = False
    generation_only = offered.only("generate_image")
    profiled = with_media_prompt_profiles(
        generation_only, settings, active_model_id=chat.id
    )

    assert profiled.prompt_fragments == (Z_IMAGE_TURBO_GENERATE_INSTRUCTIONS,)
    assert profiled.tools["generate_image"].selected_options == {
        "media_provider_id": str(lemonade.id),
        "media_model_id": str(z_image.id),
        "media_adapter_type": "lemonade",
        "media_provider_model_id": "Z-Image-Turbo",
    }


def test_lemonade_does_not_advertise_unimplemented_image_editing() -> None:
    provider = LlmProviderConfig(name="Lemonade", adapter_type="lemonade")
    model = ModelConfig(
        provider_id=provider.id,
        model_id="manual-edit-model",
        capabilities={ModelCapability.IMAGE_EDITING},
    )
    settings = AiSettings(
        providers=[provider],
        models=[model],
        image_generation_model_id=model.id,
    )

    assert ModelCapability.IMAGE_EDITING not in available_media_capabilities(settings)


async def test_media_tool_rejects_target_changed_after_prompt_binding(
    tmp_path, monkeypatch
) -> None:
    codex = LlmProviderConfig(
        name="Codex",
        adapter_type="openai-codex",
        credential_id=uuid4(),
    )
    lemonade = LlmProviderConfig(
        name="Lemonade",
        adapter_type="lemonade",
        config={"base_url": "https://lemonade.test/v1"},
    )
    chat = ModelConfig(provider_id=codex.id, model_id="openai-codex/gpt-test")
    native = ModelConfig(
        provider_id=codex.id,
        model_id="openai-codex/gpt-image-2",
        capabilities={ModelCapability.IMAGE_GENERATION},
        discovery_metadata={"native_for_chat_provider": True},
    )
    z_image = ModelConfig(
        provider_id=lemonade.id,
        model_id="Z-Image-Turbo",
        capabilities={ModelCapability.IMAGE_GENERATION},
    )
    initial = AiSettings(
        providers=[codex, lemonade],
        models=[chat, native, z_image],
        image_generation_model_id=z_image.id,
    )
    changed = initial.model_copy(deep=True)
    changed.models[1].enabled = False
    tools = ToolSettings.model_validate({"generateImage": {"policy": "auto"}})
    offered = tool_registry.effective(
        IntegrationSettings(),
        "read-only",
        tool_capabilities={"media.generate.image"},
        tool_settings=tools,
    ).only("generate_image")
    bound = with_media_prompt_profiles(offered, initial, active_model_id=chat.id)
    call = ToolCall(id="image", name="generate_image", arguments={"prompt": "Test"})
    tool_context = context(tmp_path, call)
    tool_context.model_id = chat.id
    mock_settings_load(monkeypatch, tools, changed)
    generate = AsyncMock(side_effect=AssertionError("Provider must not be called"))
    monkeypatch.setattr(MediaService, "generate_image", generate)

    result = await bound.execute(call, tool_context)

    assert result.metadata == {"errorCode": "tool.unavailable"}
    generate.assert_not_awaited()
    tool_context.ask_handler.assert_not_awaited()


async def test_edit_image_sends_ordered_session_images_and_scopes_approval(
    tmp_path, monkeypatch
) -> None:
    attachment_ids = [uuid4(), uuid4()]
    original_attachment_id = uuid4()
    call = ToolCall(
        id="edit",
        name="edit_image",
        arguments={
            "prompt": "Change only the background; keep both subjects unchanged.",
            "attachment_ids": [str(item) for item in attachment_ids],
        },
    )
    tool_context = context(tmp_path, call)
    ai, chat = codex_edit_settings()
    tool_context.model_id = chat.id
    tools = ToolSettings.model_validate({"editImage": {"policy": "ask"}})
    mock_settings_load(monkeypatch, tools, ai)
    tool_context.db.get.return_value = SimpleNamespace(user_id=None)
    message = Message(
        id=uuid4(),
        session_id=tool_context.session.id,
        role="user",
        content="Bearbeite diese Bilder",
        trust_level="public",
    )
    attachments = [
        MessageAttachment(
            id=attachment_id,
            message_id=message.id,
            message=message,
            position=position,
            kind="image",
            category="generated" if position == 0 else "user_upload",
            purpose="artifact" if position == 0 else "attachment",
            processing_status="ready",
            mime_type="image/png",
            size_bytes=5,
            sha256="0" * 64,
            storage_path=f"attachments/{attachment_id}/image.png",
            metadata_json=(
                {"authorizationSourceAttachmentIds": [str(original_attachment_id)]}
                if position == 0
                else {}
            ),
        )
        for position, attachment_id in enumerate(attachment_ids)
    ]
    scalars = MagicMock()
    scalars.all.return_value = list(reversed(attachments))
    tool_context.db.scalars.return_value = scalars
    read_image = AsyncMock(side_effect=[b"first", b"second"])
    monkeypatch.setattr(AttachmentService, "read_image", read_image)
    edit = AsyncMock(
        return_value=MediaResult(
            b"result",
            "image/png",
            "generated.png",
            trust_level=TrustLevel.PUBLIC,
        )
    )
    monkeypatch.setattr(MediaService, "edit_image", edit)

    result = await tool_registry.execute(call, tool_context)

    assert result.is_error is False
    assert result.content == "Image edited."
    assert edit.await_args.kwargs["images"] == (
        (b"first", "image/png"),
        (b"second", "image/png"),
    )
    request = tool_context.ask_handler.await_args.args[0]
    scope = json.loads(request.resource_scope)
    assert request.capability == "media.edit.image"
    assert scope["sourceAttachmentIds"] == sorted(
        [str(original_attachment_id), str(attachment_ids[1])]
    )
    assert edit.await_args.kwargs["source_attachment_ids"] == tuple(attachment_ids)
    assert result.attachments[0].metadata["authorizationSourceAttachmentIds"] == sorted(
        [str(original_attachment_id), str(attachment_ids[1])]
    )


async def test_edit_image_blocks_private_sources_for_public_target(
    tmp_path, monkeypatch
) -> None:
    attachment_id = uuid4()
    call = ToolCall(
        id="edit",
        name="edit_image",
        arguments={"prompt": "Change the background", "attachment_ids": [str(attachment_id)]},
    )
    tool_context = context(tmp_path, call)
    ai, chat = codex_edit_settings()
    tool_context.model_id = chat.id
    tools = ToolSettings.model_validate({"editImage": {"policy": "auto"}})
    mock_settings_load(monkeypatch, tools, ai)
    tool_context.db.get.return_value = SimpleNamespace(user_id=None)
    message = Message(
        id=uuid4(),
        session_id=tool_context.session.id,
        role="user",
        content="Privat",
        trust_level="private",
    )
    attachment = MessageAttachment(
        id=attachment_id,
        message_id=message.id,
        message=message,
        position=0,
        kind="image",
        category="user_upload",
        processing_status="ready",
        mime_type="image/png",
        size_bytes=5,
        sha256="0" * 64,
        storage_path=f"attachments/{attachment_id}/image.png",
    )
    scalars = MagicMock()
    scalars.all.return_value = [attachment]
    tool_context.db.scalars.return_value = scalars
    edit = AsyncMock()
    monkeypatch.setattr(MediaService, "edit_image", edit)

    result = await tool_registry.execute(call, tool_context)

    assert result.metadata == {"errorCode": "privacy.privateContentBlocked"}
    edit.assert_not_awaited()


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
    generate = AsyncMock(side_effect=AssertionError("Provider must not be called"))
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
    transcribe = AsyncMock(side_effect=AssertionError("Provider must not be called"))
    monkeypatch.setattr(MediaService, "transcribe_audio", transcribe)

    result = await tool_registry.execute(call, tool_context)

    assert result.metadata == {"errorCode": "attachment.notFound"}
    transcribe.assert_not_awaited()
    statement = tool_context.db.scalar.await_args.args[0]
    assert tool_context.session.id in statement.compile().params.values()
