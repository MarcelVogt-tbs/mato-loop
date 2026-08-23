from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.api.errors import ApiError
from mato_loop_backend.api.routes import providers as provider_routes
from mato_loop_backend.api.routes.providers import (
    DefaultModelRequest,
    DefaultModelsRequest,
    EnabledRequest,
    MemoryPolicyRequest,
    ModelRequest,
    OpenAIProviderRequest,
    _settings_payload,
    codex_login_status,
    codex_status,
    create_manual_model,
    delete_model,
    delete_provider,
    get_personal_provider_policy,
    list_providers,
    save_openai_provider,
    set_ai_enabled,
    set_default_model,
    set_default_models,
    set_model_enabled,
    set_provider_enabled,
    set_provider_memory_policy,
    sync_provider_models,
    update_model,
    update_openai_provider,
)
from mato_loop_backend.api.routes.providers import (
    test_model as run_model_test,
)
from mato_loop_backend.providers.openai_compatible import DiscoveredModel
from mato_loop_backend.providers.service import ProviderService
from mato_loop_backend.settings import (
    AiSettings,
    SettingsStore,
    ToolSettings,
    UserAiSettings,
)
from mato_loop_backend.settings.models import (
    LlmProviderConfig,
    MemoryPolicy,
    ModelAvailability,
    ModelCapability,
    ModelConfig,
    ModelInputModality,
    ModelOrigin,
    WebFetchSettings,
)


def _db() -> AsyncMock:
    db = AsyncMock(spec=AsyncSession)
    db.scalar.return_value = None
    return db


def _mock_settings(monkeypatch, settings: AiSettings) -> None:
    async def load(_db, settings_type, **_kwargs):
        return settings if settings_type is AiSettings else ToolSettings()

    monkeypatch.setattr(SettingsStore, "load", AsyncMock(side_effect=load))
    monkeypatch.setattr(SettingsStore, "save", AsyncMock())


async def test_provider_create_needs_no_model_and_accepts_camel_case(monkeypatch) -> None:
    settings = AiSettings()
    db = _db()
    _mock_settings(monkeypatch, settings)

    payload = OpenAIProviderRequest.model_validate(
        {
            "name": "Local",
            "baseUrl": "http://localhost:11434/v1",
            "apiType": "responses",
            "apiKey": None,
        }
    )
    result = await save_openai_provider(payload, db)

    assert settings.models == []
    assert settings.default_model_id is None
    assert settings.providers[0].trust_level == "public"
    assert settings.providers[0].pseudonymization_enabled is True
    assert result["providers"][0]["config"] == {
        "baseUrl": "http://localhost:11434/v1",
        "apiType": "responses",
    }
    assert result["providerId"] == str(settings.providers[0].id)
    assert result["providers"][0]["memoryPolicy"] == "inherit"


@pytest.mark.parametrize(
    "base_url",
    [
        "https://user:secret@provider.test/v1",
        "https://provider.test/v1?token=secret",
        "https://provider.test/v1#fragment",
    ],
)
def test_provider_base_url_rejects_embedded_secrets(base_url: str) -> None:
    with pytest.raises(ValidationError):
        OpenAIProviderRequest(name="Unsafe", base_url=base_url)


async def test_provider_update_preserves_credential_and_models(monkeypatch) -> None:
    provider = LlmProviderConfig(
        name="Local",
        adapter_type="openai-compatible",
        credential_id="b943ac10-b63e-4a08-a0ee-8792cf198d1f",
        config={"base_url": "http://localhost:11434/v1", "api_type": "chat_completions"},
    )
    model = ModelConfig(provider_id=provider.id, model_id="old-model", name="Alt")
    settings = AiSettings(providers=[provider], models=[model], default_model_id=model.id)
    db = _db()
    _mock_settings(monkeypatch, settings)
    before = model.model_dump()

    await update_openai_provider(
        provider.id,
        OpenAIProviderRequest(
            name="Local updated",
            base_url="http://localhost:11434/v2",
            api_type="responses",
            api_key="",
        ),
        db,
    )

    assert str(provider.credential_id) == "b943ac10-b63e-4a08-a0ee-8792cf198d1f"
    assert provider.name == "Local updated"
    assert provider.config["api_type"] == "responses"
    assert model.model_dump() == before
    db.get.assert_not_awaited()


async def test_provider_memory_policy_can_be_updated(monkeypatch) -> None:
    provider = LlmProviderConfig(name="Local", adapter_type="openai-compatible")
    settings = AiSettings(providers=[provider])
    _mock_settings(monkeypatch, settings)

    result = await set_provider_memory_policy(
        provider.id, MemoryPolicyRequest(memory_policy=MemoryPolicy.DENY), _db()
    )

    assert provider.memory_policy == MemoryPolicy.DENY
    assert result["providers"][0]["memoryPolicy"] == "deny"


async def test_provider_transport_change_invalidates_discovered_models(monkeypatch) -> None:
    provider = LlmProviderConfig(
        name="Local",
        adapter_type="openai-compatible",
        config={"base_url": "http://localhost:11434/v1", "api_type": "chat_completions"},
        models_last_synced_at="2026-07-19T10:00:00Z",
    )
    model = ModelConfig(
        provider_id=provider.id,
        model_id="old-model",
        origin=ModelOrigin.DISCOVERED,
    )
    settings = AiSettings(providers=[provider], models=[model], default_model_id=model.id)
    _mock_settings(monkeypatch, settings)

    await update_openai_provider(
        provider.id,
        OpenAIProviderRequest(name="Local", base_url="http://localhost:11434/v2"),
        _db(),
    )

    assert provider.models_last_synced_at is None
    assert model.availability == ModelAvailability.MISSING
    assert settings.default_model_id is None


async def test_update_openai_provider_rejects_other_adapter(monkeypatch) -> None:
    provider = LlmProviderConfig(name="ChatGPT Codex", adapter_type="openai-codex")
    settings = AiSettings(providers=[provider])
    _mock_settings(monkeypatch, settings)

    with pytest.raises(ApiError) as exc_info:
        await update_openai_provider(
            provider.id,
            OpenAIProviderRequest(
                name="Falscher Adapter",
                base_url="https://api.example.com/v1",
            ),
            _db(),
        )

    assert exc_info.value.code == "provider.notFound"


async def test_sync_adds_updates_and_marks_discovered_models_missing(monkeypatch) -> None:
    provider = LlmProviderConfig(name="Local", adapter_type="openai-compatible")
    manual = ModelConfig(provider_id=provider.id, model_id="same", name="Manuell")
    present = ModelConfig(
        provider_id=provider.id,
        model_id="present",
        enabled=True,
        origin=ModelOrigin.DISCOVERED,
        availability=ModelAvailability.MISSING,
        context_window_tokens=32_000,
        max_output_tokens=2_000,
    )
    missing = ModelConfig(
        provider_id=provider.id,
        model_id="gone",
        enabled=True,
        origin=ModelOrigin.DISCOVERED,
    )
    settings = AiSettings(
        providers=[provider],
        models=[manual, present, missing],
        default_model_id=missing.id,
    )
    db = _db()
    _mock_settings(monkeypatch, settings)
    monkeypatch.setattr(
        ProviderService,
        "discover_models",
        AsyncMock(
            return_value=[
                DiscoveredModel(id="same", owned_by="vendor"),
                DiscoveredModel(id="present", owned_by="vendor"),
                DiscoveredModel(
                    id="opaque/vendor:model@1",
                    name="Opaque",
                    max_model_len=8_192,
                ),
            ]
        ),
    )
    present_id = present.id

    result = await sync_provider_models(provider.id, db)

    assert manual.origin == ModelOrigin.MANUAL
    assert manual.name == "Manuell"
    assert manual.last_seen_at is None
    assert present.id == present_id
    assert present.enabled is True
    assert present.context_window_tokens == 32_000
    assert present.availability == ModelAvailability.AVAILABLE
    assert present.last_seen_at is not None
    assert present.discovery_metadata == {"owned_by": "vendor"}
    assert missing.availability == ModelAvailability.MISSING
    added = next(item for item in settings.models if item.model_id == "opaque/vendor:model@1")
    assert added.origin == ModelOrigin.DISCOVERED
    assert added.enabled is False
    assert added.name == "Opaque"
    assert added.context_window_tokens == 8_192
    assert added.discovered_context_window_tokens == 8_192
    assert added.use_discovered_context_window is True
    assert added.max_output_tokens == 2_048
    assert settings.default_model_id is None
    assert provider.models_last_synced_at is not None
    assert result["models"][-1]["modelId"] == "opaque/vendor:model@1"


async def test_sync_retains_discovered_input_modalities_without_name_inference(
    monkeypatch,
) -> None:
    provider = LlmProviderConfig(name="Local", adapter_type="openai-compatible")
    settings = AiSettings(providers=[provider])
    _mock_settings(monkeypatch, settings)
    monkeypatch.setattr(
        ProviderService,
        "discover_models",
        AsyncMock(
            return_value=[
                DiscoveredModel(
                    id="plain-model",
                    input_modalities={"text", "audio", "video"},
                ),
                DiscoveredModel(id="video-in-name-only"),
            ]
        ),
    )

    result = await sync_provider_models(provider.id, _db())

    assert settings.models[0].input_modalities == {
        ModelInputModality.TEXT,
        ModelInputModality.AUDIO,
        ModelInputModality.VIDEO,
    }
    assert settings.models[1].input_modalities == {ModelInputModality.TEXT}
    assert result["models"][0]["inputModalities"] == ["audio", "text", "video"]
    assert result["models"][0]["supportsImageInput"] is False


async def test_codex_sync_adds_discovered_models(monkeypatch) -> None:
    provider = LlmProviderConfig(name="Codex", adapter_type="openai-codex")
    settings = AiSettings(providers=[provider])
    _mock_settings(monkeypatch, settings)
    monkeypatch.setattr(
        ProviderService,
        "discover_models",
        AsyncMock(
            return_value=[
                DiscoveredModel(
                    id="openai-codex/gpt-test",
                    name="GPT Test",
                    max_model_len=272_000,
                    supports_parallel_tool_calls=False,
                    supports_image_input=True,
                    default_reasoning_effort="medium",
                    reasoning_efforts=["low", "medium", "high", "xhigh", "max", "ultra"],
                ),
                DiscoveredModel(
                    id="openai-codex/gpt-image-2",
                    name="GPT Image 2",
                    capabilities={"image_generation", "image_editing"},
                    capabilities_authoritative=True,
                    native_for_chat_provider=True,
                    supports_tools=False,
                ),
            ]
        ),
    )

    result = await sync_provider_models(provider.id, _db())

    model = settings.models[0]
    assert model.model_id == "openai-codex/gpt-test"
    assert model.origin == ModelOrigin.DISCOVERED
    assert model.enabled is False
    assert model.context_window_tokens == 272_000
    assert model.discovered_context_window_tokens == 272_000
    assert model.use_discovered_context_window is True
    assert model.supports_parallel_tool_calls is False
    assert model.supports_image_input is True
    assert result["models"][0]["modelId"] == "openai-codex/gpt-test"
    assert result["models"][0]["reasoningEffort"] == "medium"
    assert result["models"][0]["reasoningEfforts"] == [
        "low",
        "medium",
        "high",
        "xhigh",
        "max",
        "ultra",
    ]
    assert settings.models[1].enabled is True
    assert settings.models[1].capabilities == {
        ModelCapability.IMAGE_GENERATION,
        ModelCapability.IMAGE_EDITING,
    }


async def test_lemonade_sync_updates_capabilities_without_replacing_context_override(
    monkeypatch,
) -> None:
    provider = LlmProviderConfig(name="Lemonade", adapter_type="lemonade")
    discovered = ModelConfig(
        provider_id=provider.id,
        model_id="media",
        origin=ModelOrigin.DISCOVERED,
        capabilities={ModelCapability.IMAGE_GENERATION},
        context_window_tokens=32_000,
        max_output_tokens=4_000,
    )
    manual = ModelConfig(
        provider_id=provider.id,
        model_id="manual",
        capabilities={ModelCapability.CHAT, ModelCapability.IMAGE_GENERATION},
    )
    settings = AiSettings(providers=[provider], models=[discovered, manual])
    _mock_settings(monkeypatch, settings)
    monkeypatch.setattr(
        ProviderService,
        "discover_models",
        AsyncMock(
            return_value=[
                DiscoveredModel(
                    id="media",
                    capabilities={ModelCapability.AUDIO_TRANSCRIPTION},
                    max_context_window=8_192,
                    supports_tools=True,
                    supports_parallel_tool_calls=False,
                ),
                DiscoveredModel(id="manual", capabilities={ModelCapability.AUDIO_GENERATION}),
            ]
        ),
    )

    await sync_provider_models(provider.id, _db())

    assert discovered.capabilities == {ModelCapability.AUDIO_TRANSCRIPTION}
    assert discovered.context_window_tokens == 32_000
    assert discovered.discovered_context_window_tokens == 8_192
    assert discovered.context_window_source == "override"
    assert discovered.supports_tools is True
    assert discovered.supports_parallel_tool_calls is False
    assert manual.capabilities == {
        ModelCapability.CHAT,
        ModelCapability.IMAGE_GENERATION,
    }


async def test_sync_updates_models_following_provider_context(monkeypatch) -> None:
    provider = LlmProviderConfig(name="Local", adapter_type="openai-compatible")
    model = ModelConfig(
        provider_id=provider.id,
        model_id="auto-context",
        origin=ModelOrigin.DISCOVERED,
        context_window_tokens=64_000,
        discovered_context_window_tokens=64_000,
        use_discovered_context_window=True,
        max_output_tokens=16_000,
    )
    settings = AiSettings(providers=[provider], models=[model])
    _mock_settings(monkeypatch, settings)
    monkeypatch.setattr(
        ProviderService,
        "discover_models",
        AsyncMock(
            return_value=[
                DiscoveredModel(
                    id="auto-context",
                    context_length=32_000,
                    max_context_window=128_000,
                )
            ]
        ),
    )

    result = await sync_provider_models(provider.id, _db())

    assert model.context_window_tokens == 32_000
    assert model.discovered_context_window_tokens == 32_000
    assert model.max_output_tokens == 8_000
    assert result["models"][0]["contextWindowSource"] == "provider"


async def test_codex_sync_ignores_own_oauth_token_rotation(monkeypatch) -> None:
    credential_id = uuid4()
    credential = SimpleNamespace(
        encrypted_payload=b"old-token",
        metadata_json={"accountId": "account"},
        user_id=None,
    )
    provider = LlmProviderConfig(
        name="Codex",
        adapter_type="openai-codex",
        credential_id=credential_id,
    )
    settings = AiSettings(providers=[provider])
    db = _db()
    db.get.return_value = credential
    _mock_settings(monkeypatch, settings)

    async def discover(_db, **_kwargs):
        credential.encrypted_payload = b"refreshed-token"
        return []

    monkeypatch.setattr(ProviderService, "discover_models", discover)

    await sync_provider_models(provider.id, db)

    assert provider.models_last_synced_at is not None


async def test_codex_login_creates_provider_without_static_model(monkeypatch) -> None:
    attempt = SimpleNamespace(id="login", created_at=0, owner_id=None)
    device_token = SimpleNamespace(authorization_code="code", code_verifier="verifier")
    credential = SimpleNamespace(id=uuid4())
    settings = AiSettings()
    db = _db()
    _mock_settings(monkeypatch, settings)
    monkeypatch.setattr(provider_routes.oauth_manager, "begin_poll", Mock(return_value=(attempt, True)))
    monkeypatch.setattr(provider_routes.oauth_manager, "expired", Mock(return_value=False))
    monkeypatch.setattr(provider_routes.oauth_manager, "end_poll", Mock())
    monkeypatch.setattr(provider_routes.oauth_manager, "finish", Mock())
    monkeypatch.setattr(provider_routes, "poll_device_code", AsyncMock(return_value=device_token))
    monkeypatch.setattr(
        provider_routes,
        "exchange_code",
        AsyncMock(
            return_value={
                "access": "access",
                "refresh": "refresh",
                "expires": 1,
                "account_id": "account",
            }
        ),
    )
    monkeypatch.setattr(provider_routes, "store_credential", AsyncMock(return_value=credential))

    result = await codex_login_status("login", db)

    assert result["status"] == "authenticated"
    assert result["providerId"] == str(settings.providers[0].id)
    assert settings.providers[0].adapter_type == "openai-codex"
    assert settings.providers[0].credential_id == credential.id
    assert settings.models == []


async def test_manual_model_crud_toggles_and_default(monkeypatch) -> None:
    provider = LlmProviderConfig(name="Local", adapter_type="openai-compatible")
    settings = AiSettings(providers=[provider])
    db = _db()
    _mock_settings(monkeypatch, settings)

    await create_manual_model(
        provider.id,
        ModelRequest.model_validate(
            {
                "modelId": "alpha",
                "name": "Alpha",
                "contextWindowTokens": 32_000,
                "maxOutputTokens": 4_000,
                "supportsImageInput": True,
                "webfetchPolicy": "auto_allowed",
                "reasoningEffort": "low",
            }
        ),
        db,
    )
    model = settings.models[0]
    assert model.origin == ModelOrigin.MANUAL
    assert model.supports_image_input is True
    assert model.webfetch_policy == "auto_allowed"
    assert model.reasoning_effort == "low"
    assert settings.default_model_id is None

    await set_default_model(DefaultModelRequest.model_validate(str(model.id)), db)
    assert settings.default_model_id == model.id
    settings.compaction_model_id = model.id
    settings.title_generation_model_id = model.id

    await set_model_enabled(model.id, EnabledRequest(enabled=False), db)
    assert model.enabled is False
    assert settings.default_model_id is None
    assert settings.compaction_model_id is None
    assert settings.title_generation_model_id is None

    await set_model_enabled(model.id, EnabledRequest(enabled=True), db)
    await update_model(
        model.id,
        ModelRequest(
            model_id="alpha-v2",
            name="Alpha 2",
            context_window_tokens=64_000,
            max_output_tokens=8_000,
            supports_tools=False,
        ),
        db,
    )
    updated = settings.models[0]
    assert updated.id == model.id
    assert updated.model_id == "alpha-v2"
    assert updated.context_window_tokens == 64_000
    assert updated.supports_parallel_tool_calls is False

    with pytest.raises(ApiError) as exc_info:
        await set_default_model(DefaultModelRequest(model_id=updated.id), db)
    assert exc_info.value.code == "session.modelToolsUnavailable"
    await set_provider_enabled(provider.id, EnabledRequest(enabled=False), db)
    assert settings.default_model_id is None

    provider.enabled = True
    await delete_model(updated.id, db)
    assert settings.models == []


async def test_default_models_are_saved_atomically(monkeypatch) -> None:
    provider = LlmProviderConfig(name="Local", adapter_type="openai-compatible")
    agent = ModelConfig(provider_id=provider.id, model_id="agent")
    compaction = ModelConfig(provider_id=provider.id, model_id="compaction")
    title = ModelConfig(provider_id=provider.id, model_id="title")
    skill_builder = ModelConfig(provider_id=provider.id, model_id="skill-builder")
    settings = AiSettings(
        providers=[provider], models=[agent, compaction, title, skill_builder]
    )
    _mock_settings(monkeypatch, settings)

    result = await set_default_models(
        DefaultModelsRequest(
            default_model_id=agent.id,
            compaction_model_id=compaction.id,
            title_generation_model_id=title.id,
            skill_creation_model_id=skill_builder.id,
        ),
        _db(),
    )

    assert settings.default_model_id == agent.id
    assert settings.compaction_model_id == compaction.id
    assert settings.title_generation_model_id == title.id
    assert settings.skill_creation_model_id == skill_builder.id
    assert result["defaultModelId"] == str(agent.id)
    assert result["compactionModelId"] == str(compaction.id)
    assert result["titleGenerationModelId"] == str(title.id)
    assert result["skillCreationModelId"] == str(skill_builder.id)

    skill_builder.supports_tools = False
    with pytest.raises(ApiError) as exc_info:
        await set_default_models(
            DefaultModelsRequest(
                default_model_id=agent.id,
                compaction_model_id=compaction.id,
                title_generation_model_id=title.id,
                skill_creation_model_id=skill_builder.id,
            ),
            _db(),
        )
    assert exc_info.value.code == "session.modelToolsUnavailable"
    assert exc_info.value.field == "skillCreationModelId"


async def test_capability_defaults_require_matching_models(monkeypatch) -> None:
    provider = LlmProviderConfig(name="Lemonade", adapter_type="lemonade")
    chat = ModelConfig(provider_id=provider.id, model_id="chat")
    image = ModelConfig(
        provider_id=provider.id,
        model_id="image",
        capabilities={ModelCapability.IMAGE_GENERATION},
    )
    settings = AiSettings(providers=[provider], models=[chat, image])
    _mock_settings(monkeypatch, settings)

    result = await set_default_models(
        DefaultModelsRequest(
            default_model_id=chat.id,
            compaction_model_id=None,
            title_generation_model_id=None,
            image_generation_model_id=image.id,
        ),
        _db(),
    )

    assert settings.image_generation_model_id == image.id
    assert result["imageGenerationModelId"] == str(image.id)

    with pytest.raises(ApiError) as exc_info:
        await set_default_models(
            DefaultModelsRequest(
                default_model_id=image.id,
                compaction_model_id=None,
                title_generation_model_id=None,
            ),
            _db(),
        )
    assert exc_info.value.code == "provider.modelCapabilityUnavailable"
    assert exc_info.value.field == "defaultModelId"


async def test_vision_default_requires_image_input(monkeypatch) -> None:
    provider = LlmProviderConfig(name="Local", adapter_type="openai-compatible")
    text = ModelConfig(provider_id=provider.id, model_id="text")
    vision = ModelConfig(
        provider_id=provider.id,
        model_id="vision",
        supports_image_input=True,
        supports_tools=False,
    )
    settings = AiSettings(providers=[provider], models=[text, vision])
    _mock_settings(monkeypatch, settings)

    result = await set_default_models(
        DefaultModelsRequest(
            default_model_id=None,
            compaction_model_id=None,
            title_generation_model_id=None,
            vision_model_id=vision.id,
        ),
        _db(),
    )

    assert settings.vision_model_id == vision.id
    assert result["visionModelId"] == str(vision.id)

    with pytest.raises(ApiError) as exc_info:
        await set_default_models(
            DefaultModelsRequest(
                default_model_id=None,
                compaction_model_id=None,
                title_generation_model_id=None,
                vision_model_id=text.id,
            ),
            _db(),
        )
    assert exc_info.value.code == "provider.modelCapabilityUnavailable"
    assert exc_info.value.field == "visionModelId"


async def test_video_default_supports_native_video_or_frame_analysis(monkeypatch) -> None:
    provider = LlmProviderConfig(name="vLLM", adapter_type="openai-compatible")
    video = ModelConfig(
        provider_id=provider.id,
        model_id="video",
        input_modalities={ModelInputModality.TEXT, ModelInputModality.VIDEO},
    )
    settings = AiSettings(providers=[provider], models=[video])
    _mock_settings(monkeypatch, settings)

    result = await set_default_models(
        DefaultModelsRequest(
            default_model_id=None,
            compaction_model_id=None,
            title_generation_model_id=None,
            video_model_id=video.id,
        ),
        _db(),
    )

    assert settings.video_model_id == video.id
    assert result["videoModelId"] == str(video.id)

    provider.config["api_type"] = "responses"
    with pytest.raises(ApiError) as exc_info:
        await set_default_models(
            DefaultModelsRequest(
                default_model_id=None,
                compaction_model_id=None,
                title_generation_model_id=None,
                video_model_id=video.id,
            ),
            _db(),
        )
    assert exc_info.value.code == "provider.modelCapabilityUnavailable"
    assert exc_info.value.field == "videoModelId"

    frame_model = ModelConfig(
        provider_id=provider.id,
        model_id="frames",
        supports_image_input=True,
    )
    settings.models.append(frame_model)
    result = await set_default_models(
        DefaultModelsRequest(
            default_model_id=None,
            compaction_model_id=None,
            title_generation_model_id=None,
            video_model_id=frame_model.id,
        ),
        _db(),
    )
    assert settings.video_model_id == frame_model.id
    assert result["videoModelId"] == str(frame_model.id)


async def test_media_default_rejects_unsupported_provider_adapter(monkeypatch) -> None:
    provider = LlmProviderConfig(name="OpenAI", adapter_type="openai-compatible")
    image = ModelConfig(
        provider_id=provider.id,
        model_id="image",
        capabilities={ModelCapability.IMAGE_GENERATION},
    )
    settings = AiSettings(providers=[provider], models=[image])
    _mock_settings(monkeypatch, settings)

    with pytest.raises(ApiError) as exc_info:
        await set_default_models(
            DefaultModelsRequest(
                default_model_id=None,
                compaction_model_id=None,
                title_generation_model_id=None,
                image_generation_model_id=image.id,
            ),
            _db(),
        )

    assert exc_info.value.code == "provider.modelCapabilityUnavailable"
    assert exc_info.value.field == "imageGenerationModelId"
    assert settings.image_generation_model_id is None


async def test_default_models_reject_unavailable_model_with_field(monkeypatch) -> None:
    provider = LlmProviderConfig(name="Local", adapter_type="openai-compatible")
    unavailable = ModelConfig(provider_id=provider.id, model_id="title", enabled=False)
    settings = AiSettings(providers=[provider], models=[unavailable])
    _mock_settings(monkeypatch, settings)

    with pytest.raises(ApiError) as exc_info:
        await set_default_models(
            DefaultModelsRequest(
                default_model_id=None,
                compaction_model_id=None,
                title_generation_model_id=unavailable.id,
            ),
            _db(),
        )

    assert exc_info.value.code == "provider.modelUnavailable"
    assert exc_info.value.field == "titleGenerationModelId"


async def test_global_runtime_can_be_reenabled(monkeypatch) -> None:
    settings = AiSettings(enabled=False)
    _mock_settings(monkeypatch, settings)

    result = await set_ai_enabled(EnabledRequest(enabled=True), _db())

    assert settings.enabled is True
    assert result["enabled"] is True


async def test_disconnected_codex_model_cannot_be_default(monkeypatch) -> None:
    provider = LlmProviderConfig(name="Codex", adapter_type="openai-codex")
    model = ModelConfig(provider_id=provider.id, model_id="openai-codex/test")
    settings = AiSettings(providers=[provider], models=[model])
    _mock_settings(monkeypatch, settings)

    with pytest.raises(ApiError) as exc_info:
        await set_default_model(DefaultModelRequest(model_id=model.id), _db())

    assert exc_info.value.code == "provider.modelUnavailable"


async def test_duplicate_model_id_is_rejected_per_provider(monkeypatch) -> None:
    provider = LlmProviderConfig(name="Local", adapter_type="openai-compatible")
    settings = AiSettings(
        providers=[provider],
        models=[ModelConfig(provider_id=provider.id, model_id="exact/id")],
    )
    _mock_settings(monkeypatch, settings)

    with pytest.raises(ApiError) as exc_info:
        await create_manual_model(provider.id, ModelRequest(model_id="exact/id"), _db())

    assert exc_info.value.status_code == 409
    assert exc_info.value.code == "provider.modelIdConflict"


@pytest.mark.parametrize("scope", ["instance", "personal"])
@pytest.mark.parametrize("origin", [ModelOrigin.MANUAL, ModelOrigin.DISCOVERED])
async def test_model_delete_clears_defaults_and_sessions_in_its_scope(
    monkeypatch, scope, origin
) -> None:
    user = SimpleNamespace(id=uuid4(), is_admin=True)
    provider = LlmProviderConfig(name="Local", adapter_type="openai-compatible")
    model = ModelConfig(provider_id=provider.id, model_id="model", origin=origin)
    default_fields = (
        "default_model_id",
        "compaction_model_id",
        "title_generation_model_id",
        "skill_creation_model_id",
        "dreaming_model_id",
        "vision_model_id",
        "video_model_id",
        "image_generation_model_id",
        "speech_generation_model_id",
        "audio_generation_model_id",
        "audio_transcription_model_id",
    )
    settings_type = AiSettings if scope == "instance" else UserAiSettings
    settings = settings_type(
        providers=[provider],
        models=[model],
        **{field: model.id for field in default_fields},
    )
    instance = settings if scope == "instance" else AiSettings(personal_providers_enabled=True)

    async def load(_db, loaded_type, **_kwargs):
        return settings if loaded_type is settings_type else instance

    monkeypatch.setattr(SettingsStore, "load", AsyncMock(side_effect=load))
    monkeypatch.setattr(SettingsStore, "save", AsyncMock())
    db = _db()

    await delete_model(model.id, db, scope=scope, user=user)

    assert settings.models == []
    assert all(getattr(settings, field) is None for field in default_fields)
    statement = db.execute.await_args.args[0]
    sql = str(statement)
    assert ("chat_sessions.user_id" in sql) is (scope == "personal")
    assert user.id in statement.compile().params.values() if scope == "personal" else True


async def test_model_specific_test_uses_selected_model(monkeypatch) -> None:
    provider = LlmProviderConfig(name="Local", adapter_type="openai-compatible")
    first = ModelConfig(provider_id=provider.id, model_id="first")
    second = ModelConfig(provider_id=provider.id, model_id="second", enabled=False)
    settings = AiSettings(providers=[provider], models=[first, second])
    db = _db()
    _mock_settings(monkeypatch, settings)
    complete = AsyncMock()
    monkeypatch.setattr(ProviderService, "complete", complete)

    assert await run_model_test(second.id, db) == {"status": "ok"}
    assert complete.await_args.kwargs["model"] is second


async def test_model_specific_test_rejects_non_chat_model(monkeypatch) -> None:
    provider = LlmProviderConfig(name="Lemonade", adapter_type="lemonade")
    image = ModelConfig(
        provider_id=provider.id,
        model_id="image",
        capabilities={ModelCapability.IMAGE_GENERATION},
    )
    settings = AiSettings(providers=[provider], models=[image])
    _mock_settings(monkeypatch, settings)

    with pytest.raises(ApiError) as exc_info:
        await run_model_test(image.id, _db())

    assert exc_info.value.status_code == 409
    assert exc_info.value.code == "provider.modelCapabilityUnavailable"


async def test_provider_delete_clears_default_without_selecting_another(monkeypatch) -> None:
    first_provider = LlmProviderConfig(name="First", adapter_type="openai-compatible")
    second_provider = LlmProviderConfig(name="Second", adapter_type="openai-compatible")
    first_model = ModelConfig(provider_id=first_provider.id, model_id="first")
    second_model = ModelConfig(provider_id=second_provider.id, model_id="second")
    settings = AiSettings(
        providers=[first_provider, second_provider],
        models=[first_model, second_model],
        default_model_id=first_model.id,
        compaction_model_id=first_model.id,
        title_generation_model_id=first_model.id,
    )
    _mock_settings(monkeypatch, settings)

    await delete_provider(first_provider.id, _db())

    assert settings.models == [second_model]
    assert settings.default_model_id is None
    assert settings.compaction_model_id is None
    assert settings.title_generation_model_id is None


async def test_instance_provider_mutation_requires_admin(monkeypatch) -> None:
    monkeypatch.setattr(SettingsStore, "load", AsyncMock(return_value=AiSettings()))
    user = SimpleNamespace(id=uuid4(), is_admin=False)

    with pytest.raises(ApiError) as exc_info:
        await set_ai_enabled(EnabledRequest(enabled=False), _db(), user=user)

    assert exc_info.value.code == "auth.adminRequired"


async def test_personal_provider_policy_read_exposes_only_runtime_gates(
    monkeypatch,
) -> None:
    settings = AiSettings(enabled=False, personal_providers_enabled=False)
    settings.providers.append(
        LlmProviderConfig(
            name="Secret",
            adapter_type="openai-compatible",
            config={"base_url": "https://private.example/v1"},
        )
    )
    monkeypatch.setattr(SettingsStore, "load", AsyncMock(return_value=settings))

    result = await get_personal_provider_policy(
        SimpleNamespace(id=uuid4(), is_admin=False), _db()
    )

    assert result == {"enabled": False, "personalProvidersEnabled": False}


async def test_normal_provider_read_is_effective_and_hides_configuration(
    monkeypatch,
) -> None:
    user = SimpleNamespace(id=uuid4(), is_admin=False)
    instance_provider = LlmProviderConfig(
        name="Instance",
        adapter_type="openai-compatible",
        config={"base_url": "https://internal.example/v1"},
    )
    personal_provider = LlmProviderConfig(
        name="Personal",
        adapter_type="openai-compatible",
        config={"base_url": "https://personal.example/v1"},
    )
    models = [
        ModelConfig(provider_id=instance_provider.id, model_id="instance"),
        ModelConfig(provider_id=personal_provider.id, model_id="personal"),
    ]
    effective = AiSettings(
        providers=[instance_provider, personal_provider],
        models=models,
        default_model_id=models[1].id,
    )
    monkeypatch.setattr(
        provider_routes, "load_effective_ai", AsyncMock(return_value=effective)
    )
    monkeypatch.setattr(
        provider_routes, "load_effective_tools", AsyncMock(return_value=ToolSettings())
    )

    result = await list_providers(_db(), user=user)

    assert [item["name"] for item in result["providers"]] == [
        "Instance",
        "Personal",
    ]
    assert result["defaultModelId"] == str(models[1].id)
    assert all(item["config"] == {} for item in result["providers"])


async def test_nonadmin_cannot_read_or_test_explicit_instance_provider(
    monkeypatch,
) -> None:
    user = SimpleNamespace(id=uuid4(), is_admin=False)
    load = AsyncMock(return_value=AiSettings())
    complete = AsyncMock()
    monkeypatch.setattr(SettingsStore, "load", load)
    monkeypatch.setattr(ProviderService, "complete", complete)

    with pytest.raises(ApiError, match="auth.adminRequired"):
        await list_providers(_db(), scope="instance", user=user)
    with pytest.raises(ApiError, match="auth.adminRequired"):
        await run_model_test(uuid4(), _db(), scope="instance", user=user)
    with pytest.raises(ApiError, match="auth.adminRequired"):
        await codex_status(_db(), scope="instance", user=user)

    load.assert_not_awaited()
    complete.assert_not_awaited()


@pytest.mark.parametrize("scope", ["instance", "personal"])
async def test_provider_deletion_clears_sessions_and_credentials_in_its_scope(
    monkeypatch, scope
) -> None:
    user = SimpleNamespace(id=uuid4(), is_admin=True)
    provider = LlmProviderConfig(
        name="Scoped",
        adapter_type="openai-compatible",
        credential_id=uuid4(),
    )
    model = ModelConfig(provider_id=provider.id, model_id="model")
    instance = AiSettings(providers=[provider], models=[model])
    personal = UserAiSettings(providers=[provider], models=[model])

    async def load(_db, settings_type, **_kwargs):
        return personal if settings_type is UserAiSettings else instance

    delete_credential = AsyncMock(return_value=True)
    monkeypatch.setattr(SettingsStore, "load", AsyncMock(side_effect=load))
    monkeypatch.setattr(SettingsStore, "save", AsyncMock())
    monkeypatch.setattr(provider_routes, "delete_credential", delete_credential)
    db = _db()

    await delete_provider(provider.id, db, scope=scope, user=user)

    statement = db.execute.await_args.args[0]
    sql = str(statement)
    assert ("chat_sessions.user_id" in sql) is (scope == "personal")
    assert user.id in statement.compile().params.values() if scope == "personal" else True
    delete_credential.assert_awaited_once_with(
        db, provider.credential_id, owner_id=user.id if scope == "personal" else None
    )


def test_provider_payload_contains_discovery_fields_in_camel_case() -> None:
    provider = LlmProviderConfig(
        name="Local",
        adapter_type="openai-compatible",
        trust_level="private",
    )
    model = ModelConfig.model_validate(
        {
            "provider_id": provider.id,
            "model_id": "Qwen/Qwen3.8-27B",
            "supportsImageInput": True,
            "webfetchPolicy": "auto_public",
        }
    )

    payload = _settings_payload(
        AiSettings(providers=[provider], models=[model]),
        ToolSettings(web_fetch=WebFetchSettings(enabled=False)),
    )

    assert payload["providers"][0]["modelsLastSyncedAt"] is None
    assert payload["providers"][0]["trustLevel"] == "private"
    assert payload["providers"][0]["pseudonymizationEnabled"] is True
    assert payload["models"][0]["origin"] == "manual"
    assert payload["models"][0]["availability"] == "available"
    assert payload["models"][0]["lastSeenAt"] is None
    assert payload["models"][0]["discoveryMetadata"] == {}
    assert payload["models"][0]["supportsImageInput"] is True
    assert payload["models"][0]["webfetchPolicy"] == "auto_public"
    assert payload["models"][0]["effectiveWebfetchPolicy"] == "disabled"
    assert payload["models"][0]["trustLevel"] is None
    assert payload["models"][0]["effectiveTrustLevel"] == "private"
    assert payload["models"][0]["pseudonymizationEnabled"] is None
    assert payload["models"][0]["effectivePseudonymizationEnabled"] is False
    assert payload["models"][0]["reasoningEffort"] == "medium"
    assert payload["models"][0]["reasoningEfforts"] == [
        "none",
        "low",
        "medium",
        "xhigh",
    ]
    assert payload["compactionModelId"] is None
    assert payload["titleGenerationModelId"] is None
    assert "compressionModelId" not in payload["models"][0]
