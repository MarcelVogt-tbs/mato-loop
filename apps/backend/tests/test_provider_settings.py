from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import httpx
import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.api.errors import ApiError
from mato_loop_backend.api.routes import providers as provider_routes
from mato_loop_backend.api.routes.providers import (
    AzureSpeechProviderRequest,
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
    save_azure_speech_provider,
    save_openai_provider,
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
from mato_loop_backend.providers import azure_speech, codex_oauth
from mato_loop_backend.providers.openai_compatible import DiscoveredModel
from mato_loop_backend.providers.service import ProviderService
from mato_loop_backend.providers.settings_service import ProviderSettingsService
from mato_loop_backend.settings import (
    GENERAL_PROFILE_ID,
    PLAN_PROFILE_ID,
    AgentProfilesSettings,
    AiSettings,
    CustomAgentProfile,
    SettingsStore,
    ToolSettings,
    UserAgentProfilesSettings,
    UserAiSettings,
)
from mato_loop_backend.settings.models import (
    AgentProfileAccessOverrides,
    LlmProviderConfig,
    MemoryPolicy,
    ModelAvailability,
    ModelCapability,
    ModelConfig,
    ModelInputModality,
    ModelOrigin,
    ReasoningEffort,
)


def _db() -> AsyncMock:
    db = AsyncMock(spec=AsyncSession)
    db.scalar.return_value = None
    return db


def _mock_settings(monkeypatch, settings: AiSettings) -> None:
    async def load(_db, settings_type, **_kwargs):
        if settings_type is AiSettings:
            return settings
        if settings_type is AgentProfilesSettings:
            return AgentProfilesSettings()
        return ToolSettings()

    monkeypatch.setattr(SettingsStore, "load", AsyncMock(side_effect=load))
    monkeypatch.setattr(SettingsStore, "load_optional", AsyncMock(return_value=None))
    monkeypatch.setattr(SettingsStore, "list_user_scopes", AsyncMock(return_value=[]))
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
    "endpoint",
    [
        "http://speech.cognitiveservices.azure.com",
        "https://speech.cognitiveservices.azure.com/speechtotext",
        "https://speech.cognitiveservices.azure.com?key=secret",
        "https://speech.cognitiveservices.azure.com#fragment",
        "https://user:password@speech.cognitiveservices.azure.com",
    ],
)
def test_azure_endpoint_requires_https_origin(endpoint):
    with pytest.raises(ValidationError):
        AzureSpeechProviderRequest(name="Azure", base_url=endpoint)


@pytest.mark.parametrize("scope", ["instance", "personal"])
async def test_azure_provider_stores_scoped_key_outside_settings(monkeypatch, scope):
    settings = AiSettings() if scope == "instance" else UserAiSettings()
    owner_id = None if scope == "instance" else uuid4()
    db = _db()
    _mock_settings(monkeypatch, settings)
    monkeypatch.setattr(
        provider_routes, "_load_scoped_ai", AsyncMock(return_value=(settings, owner_id))
    )
    credential = SimpleNamespace(id=uuid4())
    store = AsyncMock(return_value=credential)
    monkeypatch.setattr(provider_routes, "store_credential", store)
    result = await save_azure_speech_provider(
        AzureSpeechProviderRequest.model_validate({
            "name": "Azure", "baseUrl": "https://speech.cognitiveservices.azure.com/",
            "apiKey": "test-key",
        }),
        db, scope=scope,
    )
    provider = settings.providers[0]
    assert provider.adapter_type == "azure-speech"
    assert provider.owner_id == owner_id
    assert provider.credential_id == credential.id
    assert provider.trust_level == "public"
    assert provider.pseudonymization_enabled is True
    assert provider.config == {"base_url": "https://speech.cognitiveservices.azure.com"}
    assert "test-key" not in settings.model_dump_json() + str(result)
    assert result["providers"][0]["hasCredential"] is True
    store.assert_awaited_once_with(
        db, owner_id=owner_id, kind="azure-speech-api-key", label="Azure",
        payload={"api_key": "test-key"}, credential=None,
    )


async def test_azure_provider_update_preserves_key_and_removal_clears_default(monkeypatch):
    provider = LlmProviderConfig(
        name="Azure", adapter_type="azure-speech", credential_id=uuid4(),
        config={"base_url": "https://speech.cognitiveservices.azure.com"},
    )
    model = ModelConfig(
        provider_id=provider.id, model_id=azure_speech.MODEL_ID,
        capabilities={ModelCapability.AUDIO_TRANSCRIPTION},
    )
    settings = AiSettings(
        providers=[provider], models=[model], audio_transcription_model_id=model.id
    )
    _mock_settings(monkeypatch, settings)
    db = _db()
    key_id = provider.credential_id
    payload = OpenAIProviderRequest.model_validate({
        "name": "Azure renamed", "baseUrl": "https://speech.cognitiveservices.azure.com"
    })
    await update_openai_provider(provider.id, payload, db)
    assert provider.name == "Azure renamed"
    assert provider.credential_id == key_id
    assert settings.audio_transcription_model_id == model.id
    delete = AsyncMock()
    monkeypatch.setattr(provider_routes, "delete_credential", delete)
    await update_openai_provider(
        provider.id,
        AzureSpeechProviderRequest(
            name="Azure", base_url="https://speech.cognitiveservices.azure.com",
            remove_api_key=True,
        ),
        db,
    )
    delete.assert_awaited_once_with(db, key_id, owner_id=None)
    assert provider.credential_id is None
    assert settings.audio_transcription_model_id is None


async def test_azure_update_revalidates_union_payload_for_actual_provider(monkeypatch):
    provider = LlmProviderConfig(
        name="Azure", adapter_type="azure-speech", credential_id=uuid4()
    )
    _mock_settings(monkeypatch, AiSettings(providers=[provider]))
    with pytest.raises(ApiError) as error:
        await update_openai_provider(
            provider.id,
            OpenAIProviderRequest(name="Azure", base_url="http://unsafe.test/v1"),
            _db(),
        )
    assert error.value.code == "provider.configurationInvalid"


async def test_azure_sync_creates_disabled_transcription_only_model(monkeypatch):
    provider = LlmProviderConfig(
        name="Azure", adapter_type="azure-speech", credential_id=uuid4()
    )
    settings = AiSettings(providers=[provider])
    _mock_settings(monkeypatch, settings)
    discovered = DiscoveredModel(
        id=azure_speech.MODEL_ID, name="Azure Speech Fast Transcription",
        capabilities={ModelCapability.AUDIO_TRANSCRIPTION},
        input_modalities={ModelInputModality.AUDIO},
        supports_tools=False, supports_parallel_tool_calls=False,
        capabilities_authoritative=True,
    )
    monkeypatch.setattr(
        ProviderService, "discover_models", AsyncMock(return_value=[discovered])
    )
    result = await sync_provider_models(provider.id, _db())
    model = settings.models[0]
    assert model.enabled is False
    assert model.capabilities == {ModelCapability.AUDIO_TRANSCRIPTION}
    assert model.supports_tools is False
    assert model.supports_streaming is False
    assert result["models"][0]["agentAvailable"] is False


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
    assert added.max_output_tokens is None
    assert added.discovered_max_output_tokens is None
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


@pytest.mark.parametrize(
    "limits, expected_context",
    [
        ({"context_window": 272_000, "max_context_window": 872_000}, 872_000),
        ({"context_window": 272_000}, 272_000),
        ({"context_window": 272_000, "max_context_window": None}, 272_000),
        ({"max_context_window": 872_000}, 872_000),
        ({}, None),
    ],
)
async def test_codex_sync_adds_discovered_models(monkeypatch, limits, expected_context) -> None:
    provider = LlmProviderConfig(name="Codex", adapter_type="openai-codex")
    settings = AiSettings(providers=[provider])
    _mock_settings(monkeypatch, settings)
    transport = httpx.MockTransport(lambda _request: httpx.Response(200, json={
        "models": [{
            "slug": "gpt-test",
            "display_name": "GPT Test",
            "visibility": "list",
            **limits,
            "input_modalities": ["text", "image"],
            "default_reasoning_level": "medium",
            "supported_reasoning_levels": [
                {"effort": effort} for effort in ("low", "medium", "high", "xhigh", "max", "ultra")
            ],
        }],
    }))
    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        codex_oauth.httpx, "AsyncClient", lambda **kwargs: real_client(transport=transport, **kwargs),
    )
    discovered = await codex_oauth.discover_models(access_token="access", account_id=None)
    monkeypatch.setattr(
        ProviderService,
        "discover_models",
        AsyncMock(return_value=discovered),
    )

    result = await sync_provider_models(provider.id, _db())

    model = settings.models[0]
    assert model.model_id == "openai-codex/gpt-test"
    assert model.origin == ModelOrigin.DISCOVERED
    assert model.enabled is False
    assert model.context_window_tokens == (expected_context or 128_000)
    assert model.discovered_context_window_tokens == expected_context
    assert model.discovery_metadata == discovered[0].metadata
    assert result["models"][0]["contextWindowSource"] == (
        "provider" if expected_context is not None else "fallback"
    )
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


@pytest.mark.parametrize("follow_provider", [True, False])
async def test_codex_rediscovery_updates_context_without_replacing_override(
    monkeypatch, follow_provider,
) -> None:
    provider = LlmProviderConfig(name="Codex", adapter_type="openai-codex")
    model = ModelConfig(
        provider_id=provider.id,
        model_id="openai-codex/gpt-test",
        origin=ModelOrigin.DISCOVERED,
        context_window_tokens=272_000 if follow_provider else 64_000,
        discovered_context_window_tokens=272_000,
        use_discovered_context_window=follow_provider,
    )
    settings = AiSettings(providers=[provider], models=[model])
    _mock_settings(monkeypatch, settings)
    discovered = DiscoveredModel(
        id=model.model_id, max_model_len=272_000, max_context_window=872_000,
    )
    monkeypatch.setattr(ProviderService, "discover_models", AsyncMock(return_value=[discovered]))

    result = await sync_provider_models(provider.id, _db())

    assert model.context_window_tokens == (872_000 if follow_provider else 64_000)
    assert model.discovered_context_window_tokens == 872_000
    assert model.use_discovered_context_window is follow_provider
    assert model.discovery_metadata == discovered.metadata
    assert result["models"][0]["contextWindowSource"] == (
        "provider" if follow_provider else "override"
    )


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


@pytest.mark.parametrize("context", [8_000, 32_000, 128_000])
async def test_sync_updates_models_following_provider_context(monkeypatch, context) -> None:
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
                    context_length=context,
                    max_context_window=128_000,
                )
            ]
        ),
    )

    result = await sync_provider_models(provider.id, _db())

    assert model.context_window_tokens == context
    assert model.discovered_context_window_tokens == context
    assert model.max_output_tokens == 16_000
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


async def test_manual_model_crud_and_toggles(monkeypatch) -> None:
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
                "reasoningEffort": "low",
            }
        ),
        db,
    )
    model = settings.models[0]
    assert model.origin == ModelOrigin.MANUAL
    assert model.supports_image_input is True
    assert model.reasoning_effort == "low"
    assert settings.default_model_id is None

    settings.default_model_id = model.id
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

    await set_provider_enabled(provider.id, EnabledRequest(enabled=False), db)
    assert settings.default_model_id is None

    provider.enabled = True
    await delete_model(updated.id, db)
    assert settings.models == []


async def test_instance_model_disable_removes_sparse_profile_references_for_all_users(
    monkeypatch,
) -> None:
    provider = LlmProviderConfig(name="Local", adapter_type="openai-compatible")
    model = ModelConfig(provider_id=provider.id, model_id="agent")
    ai = AiSettings(providers=[provider], models=[model])
    instance = AgentProfilesSettings(
        builtin_access_overrides={
            GENERAL_PROFILE_ID: AgentProfileAccessOverrides(
                defaultModelId=model.id,
                compactionModelId=model.id,
            )
        },
        profiles=[
            CustomAgentProfile(
                name="Instance",
                description="",
                baseProfileId=GENERAL_PROFILE_ID,
                overrides={"skillCreationModelId": model.id},
            )
        ],
    )
    user_documents = [
        (
            uuid4(),
            UserAgentProfilesSettings(
                profiles=[
                    CustomAgentProfile(
                        name=name,
                        description="",
                        baseProfileId=PLAN_PROFILE_ID,
                        overrides={"titleGenerationModelId": model.id},
                    )
                ]
            ),
        )
        for name in ("First", "Second")
    ]
    monkeypatch.setattr(SettingsStore, "load", AsyncMock(return_value=instance))
    monkeypatch.setattr(
        SettingsStore,
        "list_user_scopes",
        AsyncMock(return_value=[user_id for user_id, _ in user_documents]),
    )
    monkeypatch.setattr(
        SettingsStore,
        "load_optional",
        AsyncMock(
            side_effect=lambda _db, _kind, *, user_id, **_kwargs: dict(
                user_documents
            )[user_id]
        ),
    )
    save = AsyncMock()
    monkeypatch.setattr(SettingsStore, "save", save)
    db = _db()

    await ProviderSettingsService.set_model_enabled(
        db, ai, None, model.id, False
    )

    overrides = [
        instance.builtin_access_overrides[GENERAL_PROFILE_ID],
        instance.profiles[0].overrides,
        *(document.profiles[0].overrides for _, document in user_documents),
    ]
    assert all(
        not set(override.model_dump(exclude_unset=True)) & {
            "default_model_id",
            "compaction_model_id",
            "skill_creation_model_id",
            "title_generation_model_id",
        }
        for override in overrides
    )
    assert save.await_count == 4
    db.commit.assert_awaited_once()


async def test_personal_model_delete_cleans_only_owner_profile(
    monkeypatch,
) -> None:
    owner_id = uuid4()
    provider = LlmProviderConfig(name="Personal", adapter_type="openai-compatible")
    model = ModelConfig(provider_id=provider.id, model_id="personal")
    ai = UserAiSettings(providers=[provider], models=[model])
    instance = AgentProfilesSettings(
        profiles=[
            CustomAgentProfile(
                name="Instance",
                description="",
                baseProfileId=GENERAL_PROFILE_ID,
                overrides={"defaultModelId": model.id},
            )
        ]
    )
    personal = UserAgentProfilesSettings(
        profiles=[
            CustomAgentProfile(
                name="Personal",
                description="",
                baseProfileId=GENERAL_PROFILE_ID,
                overrides={"defaultModelId": model.id},
            )
        ]
    )
    monkeypatch.setattr(SettingsStore, "load", AsyncMock(return_value=instance))
    load_optional = AsyncMock(return_value=personal)
    monkeypatch.setattr(SettingsStore, "load_optional", load_optional)
    list_scopes = AsyncMock()
    monkeypatch.setattr(SettingsStore, "list_user_scopes", list_scopes)
    monkeypatch.setattr(SettingsStore, "save", AsyncMock())
    db = _db()

    await ProviderSettingsService.delete_model(db, ai, owner_id, model.id)

    assert "default_model_id" in instance.profiles[0].overrides.model_fields_set
    assert "default_model_id" not in personal.profiles[0].overrides.model_fields_set
    assert "default_model_id" not in personal.model_dump(mode="json")["profiles"][0]["overrides"]
    load_optional.assert_awaited_once_with(
        db, UserAgentProfilesSettings, user_id=owner_id, for_update=True
    )
    list_scopes.assert_not_awaited()
    db.commit.assert_awaited_once()


async def test_model_capability_change_removes_incompatible_profile_reference(
    monkeypatch,
) -> None:
    provider = LlmProviderConfig(name="Media", adapter_type="lemonade")
    model = ModelConfig(
        provider_id=provider.id,
        model_id="media",
        capabilities={ModelCapability.IMAGE_GENERATION},
    )
    ai = AiSettings(providers=[provider], models=[model])
    instance = AgentProfilesSettings(
        profiles=[
            CustomAgentProfile(
                name="Media",
                description="",
                baseProfileId=GENERAL_PROFILE_ID,
                overrides={"imageGenerationModelId": model.id},
            )
        ]
    )
    monkeypatch.setattr(SettingsStore, "load", AsyncMock(return_value=instance))
    monkeypatch.setattr(SettingsStore, "list_user_scopes", AsyncMock(return_value=[]))
    monkeypatch.setattr(SettingsStore, "save", AsyncMock())

    await ProviderSettingsService.update_model(
        _db(),
        ai,
        None,
        model.id,
        {
            "model_id": model.model_id,
            "capabilities": {ModelCapability.AUDIO_GENERATION},
        },
        None,
    )

    assert (
        "image_generation_model_id"
        not in instance.profiles[0].overrides.model_fields_set
    )


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
        if loaded_type is AgentProfilesSettings:
            return AgentProfilesSettings()
        return settings if loaded_type is settings_type else instance

    monkeypatch.setattr(SettingsStore, "load", AsyncMock(side_effect=load))
    monkeypatch.setattr(SettingsStore, "load_optional", AsyncMock(return_value=None))
    monkeypatch.setattr(SettingsStore, "list_user_scopes", AsyncMock(return_value=[]))
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
    assert complete.await_args.kwargs["max_output_tokens"] == 1_024


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
    provider = LlmProviderConfig(name="Local", adapter_type="openai-compatible")
    monkeypatch.setattr(
        SettingsStore, "load", AsyncMock(return_value=AiSettings(providers=[provider]))
    )
    user = SimpleNamespace(id=uuid4(), is_admin=False)

    with pytest.raises(ApiError) as exc_info:
        await set_provider_enabled(
            provider.id, EnabledRequest(enabled=False), _db(), user=user
        )

    assert exc_info.value.code == "auth.adminRequired"


async def test_personal_provider_policy_read_exposes_only_personal_gate(
    monkeypatch,
) -> None:
    settings = AiSettings(personal_providers_enabled=False)
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

    assert result == {"personalProvidersEnabled": False}


@pytest.mark.parametrize("is_admin", [False, True])
async def test_normal_provider_read_is_effective_and_hides_configuration(
    monkeypatch,
    is_admin: bool,
) -> None:
    user = SimpleNamespace(id=uuid4(), is_admin=is_admin)
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

    result = await list_providers(_db(), user=user)

    assert [item["name"] for item in result["providers"]] == [
        "Instance",
        "Personal",
    ]
    assert "defaultModelId" not in result
    assert all(item["config"] == {} for item in result["providers"])


async def test_owner_can_list_stored_providers_when_personal_creation_is_disabled(monkeypatch) -> None:
    user = SimpleNamespace(id=uuid4(), is_admin=False)
    instance_provider = LlmProviderConfig(
        name="Shared", adapter_type="openai-compatible",
        config={"base_url": "https://shared.example/v1"},
    )
    personal_provider = LlmProviderConfig(
        name="Mine", adapter_type="openai-compatible",
        config={"base_url": "https://mine.example/v1"},
    )
    instance = AiSettings(providers=[instance_provider], personal_providers_enabled=False)
    personal = UserAiSettings(providers=[personal_provider])

    async def load(_db, settings_type, **kwargs):
        if settings_type is UserAiSettings:
            assert kwargs["user_id"] == user.id
            return personal
        return instance

    monkeypatch.setattr(SettingsStore, "load", AsyncMock(side_effect=load))
    monkeypatch.setattr(provider_routes, "load_effective_ai", AsyncMock(return_value=instance))

    result = await list_providers(_db(), scope="personal", user=user)

    assert [item["name"] for item in result["providers"]] == ["Mine"]
    assert result["providers"][0]["config"]["baseUrl"] == "https://mine.example/v1"
    assert result["availableProviders"][0]["config"] == {}


async def test_personal_provider_read_includes_effective_selection_catalog(
    monkeypatch,
) -> None:
    db = _db()
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
    instance_model = ModelConfig(provider_id=instance_provider.id, model_id="instance")
    personal_model = ModelConfig(provider_id=personal_provider.id, model_id="personal")
    personal = UserAiSettings(
        providers=[personal_provider],
        models=[personal_model],
        default_model_id=instance_model.id,
    )
    effective = AiSettings(
        providers=[instance_provider, personal_provider],
        models=[instance_model, personal_model],
        default_model_id=instance_model.id,
        title_generation_model_id=instance_model.id,
    )
    monkeypatch.setattr(
        provider_routes, "_load_scoped_ai", AsyncMock(return_value=(personal, user.id))
    )
    load_effective = AsyncMock(return_value=effective)
    monkeypatch.setattr(provider_routes, "load_effective_ai", load_effective)

    result = await list_providers(db, scope="personal", user=user)

    assert [item["name"] for item in result["providers"]] == ["Personal"]
    assert [item["modelId"] for item in result["models"]] == ["personal"]
    assert "defaultModelId" not in result
    assert "effectiveTitleGenerationModelId" not in result
    assert [item["name"] for item in result["availableProviders"]] == [
        "Instance",
        "Personal",
    ]
    assert [item["modelId"] for item in result["availableModels"]] == [
        "instance",
        "personal",
    ]
    assert all(item["config"] == {} for item in result["availableProviders"])
    load_effective.assert_awaited_once_with(db, user.id)


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
        if settings_type is AgentProfilesSettings:
            return AgentProfilesSettings()
        return personal if settings_type is UserAiSettings else instance

    delete_credential = AsyncMock(return_value=True)
    monkeypatch.setattr(SettingsStore, "load", AsyncMock(side_effect=load))
    monkeypatch.setattr(SettingsStore, "load_optional", AsyncMock(return_value=None))
    monkeypatch.setattr(SettingsStore, "list_user_scopes", AsyncMock(return_value=[]))
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
        }
    )

    payload = _settings_payload(AiSettings(providers=[provider], models=[model]))

    assert payload["providers"][0]["modelsLastSyncedAt"] is None
    assert payload["providers"][0]["trustLevel"] == "private"
    assert payload["providers"][0]["pseudonymizationEnabled"] is True
    assert payload["models"][0]["origin"] == "manual"
    assert payload["models"][0]["availability"] == "available"
    assert payload["models"][0]["lastSeenAt"] is None
    assert payload["models"][0]["discoveryMetadata"] == {}
    assert payload["models"][0]["supportsImageInput"] is True
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
    assert "compactionModelId" not in payload
    assert "titleGenerationModelId" not in payload
    assert "titleVisionFallbackEnabled" not in payload


@pytest.mark.parametrize("value", [0, -1, True, 1.5, 2.0, "512"])
def test_model_request_rejects_invalid_output_limits(value) -> None:
    with pytest.raises(ValidationError):
        ModelRequest(modelId="opaque", maxOutputTokens=value)


@pytest.mark.parametrize("key", ["context_window_tokens", "contextWindowTokens"])
@pytest.mark.parametrize("output", [None, 1])
def test_model_request_requires_two_context_tokens_with_auto_or_minimum_cap(key, output) -> None:
    with pytest.raises(ValidationError):
        ModelRequest(modelId="opaque", maxOutputTokens=output, **{key: 1})
    request = ModelRequest(modelId="opaque", maxOutputTokens=output, **{key: 2})
    assert request.context_window_tokens == 2
    assert request.max_output_tokens == output


@pytest.mark.parametrize("field", ["discoveredMaxOutputTokens", "discovered_max_output_tokens"])
def test_discovered_output_limit_is_read_only(field) -> None:
    with pytest.raises(ValidationError):
        ModelRequest(modelId="opaque", **{field: 512})


async def test_model_output_update_preserves_omitted_fields_and_explicit_auto(monkeypatch) -> None:
    provider = LlmProviderConfig(name="Local", adapter_type="openai-compatible")
    settings = AiSettings(providers=[provider])
    _mock_settings(monkeypatch, settings)
    db = _db()
    result = await create_manual_model(provider.id, ModelRequest(modelId="opaque"), db)
    assert result["models"][0]["maxOutputTokens"] is None
    assert result["models"][0]["discoveredMaxOutputTokens"] is None
    model = settings.models[0]
    model.max_output_tokens = 64_000
    model.discovered_max_output_tokens = 8_192
    model.reasoning_effort = ReasoningEffort.HIGH
    model.reasoning_summary = True
    model.config = {"maxOutputTokens": 512, "temperature": 0.7, "unknown": {"x": [1]}}
    before = model.model_dump()
    for context in (4_096, 128_000):
        result = await update_model(
            model.id, ModelRequest(modelId="opaque", contextWindowTokens=context), db,
        )
        assert settings.models[0].model_dump() == {**before, "context_window_tokens": context}
        assert result["models"][0]["maxOutputTokens"] == 64_000
        assert result["models"][0]["discoveredMaxOutputTokens"] == 8_192
    result = await update_model(model.id, ModelRequest(modelId="opaque", maxOutputTokens=None), db)
    assert result["models"][0]["maxOutputTokens"] is None
    assert settings.models[0].config == before["config"]
    assert ModelConfig.model_validate(settings.models[0].model_dump()).max_output_tokens is None


@pytest.mark.parametrize("cap", [None, 32_768, 0, -1, True, 2.0, "32768"])
async def test_discovery_trusts_only_documented_output_cap_metadata(monkeypatch, cap) -> None:
    provider = LlmProviderConfig(name="Local", adapter_type="openai-compatible")
    settings = AiSettings(providers=[provider])
    _mock_settings(monkeypatch, settings)
    discovered = DiscoveredModel(
        id="opaque", context_length=128_000, max_tokens=512, max_output_tokens=1_024,
        default_parameters={"max_tokens": 256},
        top_provider={"max_completion_tokens": cap, "unknown": "not exposed"},
    )
    monkeypatch.setattr(ProviderService, "discover_models", AsyncMock(return_value=[discovered]))
    result = await sync_provider_models(provider.id, _db())
    expected = cap if type(cap) is int and cap > 0 else None
    assert result["models"][0]["maxOutputTokens"] is None
    assert result["models"][0]["discoveredMaxOutputTokens"] == expected
    assert discovered.metadata.get("top_provider") == (
        {"max_completion_tokens": expected} if expected else None
    )
    settings.models[0].max_output_tokens = 100_000
    monkeypatch.setattr(ProviderService, "discover_models", AsyncMock(return_value=[DiscoveredModel(id="opaque")]))
    result = await sync_provider_models(provider.id, _db())
    assert result["models"][0]["discoveredMaxOutputTokens"] is None
    assert result["models"][0]["maxOutputTokens"] == 100_000


@pytest.mark.parametrize("manual_cap", [None, 64_000])
async def test_sync_refreshes_manual_model_output_metadata_without_overwriting_settings(
    monkeypatch, manual_cap,
) -> None:
    provider = LlmProviderConfig(name="OpenRouter", adapter_type="openai-compatible")
    model = ModelConfig(
        provider_id=provider.id, model_id="exact/id", name="Manual", enabled=False,
        context_window_tokens=8_192, max_output_tokens=manual_cap,
        reasoning_effort=ReasoningEffort.HIGH,
        config={"temperature": 0.7, "unknown": [1, None]},
    )
    settings = AiSettings(providers=[provider], models=[model])
    _mock_settings(monkeypatch, settings)
    before = model.model_dump()
    for cap in (32_768, None):
        discovered = DiscoveredModel(
            id=model.model_id, name="Discovered", context_length=128_000,
            capabilities={ModelCapability.IMAGE_GENERATION},
            capabilities_authoritative=True, supports_tools=False,
            **({"top_provider": {"max_completion_tokens": cap}} if cap else {}),
        )
        monkeypatch.setattr(ProviderService, "discover_models", AsyncMock(return_value=[discovered]))
        result = await sync_provider_models(provider.id, _db())
        assert model.model_dump() == {**before, "discovered_max_output_tokens": cap}
        assert result["models"][0]["discoveredMaxOutputTokens"] == cap
        assert result["models"][0]["maxOutputTokens"] == manual_cap


async def test_model_rename_clears_only_discovered_output_cap(monkeypatch) -> None:
    provider = LlmProviderConfig(name="Local", adapter_type="openai-compatible")
    model = ModelConfig(
        provider_id=provider.id, model_id="A", discovered_max_output_tokens=512,
        max_output_tokens=64_000, context_window_tokens=8_192,
        config={"temperature": 0.7}, reasoning_effort=ReasoningEffort.HIGH,
    )
    settings = AiSettings(providers=[provider], models=[model])
    _mock_settings(monkeypatch, settings)
    before = model.model_dump()
    await update_model(model.id, ModelRequest(modelId="B"), _db())
    assert settings.models[0].model_dump() == {
        **before, "model_id": "B", "discovered_max_output_tokens": None,
    }


@pytest.mark.parametrize("origin", list(ModelOrigin))
@pytest.mark.parametrize("switch", [False, True])
async def test_model_provider_binding_changes_clear_only_discovered_cap(monkeypatch, origin, switch) -> None:
    providers = [LlmProviderConfig(name=name, adapter_type="openai-compatible") for name in ("A", "B")]
    model = ModelConfig(
        provider_id=providers[0].id, model_id="same-id", origin=origin,
        discovered_max_output_tokens=512, max_output_tokens=64_000,
        context_window_tokens=8_192, config={"temperature": 0.7},
    )
    settings = AiSettings(providers=providers, models=[model])
    _mock_settings(monkeypatch, settings)
    before = model.model_dump()
    target = providers[int(switch)].id
    await ProviderSettingsService.update_model(
        _db(), settings, None, model.id,
        {"model_id": model.model_id, "provider_id": str(target)}, None,
    )
    assert settings.models[0].model_dump() == {
        **before, "provider_id": target,
        "discovered_max_output_tokens": None if switch else 512,
    }


@pytest.mark.parametrize("origin", list(ModelOrigin))
@pytest.mark.parametrize("change", ["endpoint", "api_type", "api_key", "remove_api_key", "none"])
async def test_provider_identity_updates_invalidate_caps_in_both_origins(monkeypatch, origin, change) -> None:
    provider = LlmProviderConfig(
        name="Local", adapter_type="openai-compatible", credential_id=uuid4(),
        config={"base_url": "https://provider.test/v1", "api_type": "chat_completions"},
    )
    other = LlmProviderConfig(name="Other", adapter_type="openai-compatible")
    model = ModelConfig(
        provider_id=provider.id, model_id="not-listed", origin=origin,
        discovered_max_output_tokens=512, max_output_tokens=64_000,
        context_window_tokens=8_192, config={"temperature": 0.7},
    )
    untouched = ModelConfig(provider_id=other.id, model_id=model.model_id, discovered_max_output_tokens=256)
    settings = AiSettings(providers=[provider, other], models=[model, untouched])
    _mock_settings(monkeypatch, settings)
    monkeypatch.setattr(provider_routes, "load_credential", AsyncMock(return_value=None))
    monkeypatch.setattr(provider_routes, "store_credential", AsyncMock(return_value=SimpleNamespace(id=uuid4())))
    monkeypatch.setattr(provider_routes, "delete_credential", AsyncMock(return_value=True))
    before = model.model_dump()
    await update_openai_provider(provider.id, OpenAIProviderRequest(
        name="Renamed",
        base_url="https://other.test/v1" if change == "endpoint" else provider.config["base_url"],
        api_type="responses" if change == "api_type" else "chat_completions",
        api_key="test-key" if change == "api_key" else None,
        remove_api_key=change == "remove_api_key",
    ), _db())
    assert model.model_dump() == {
        **before, "discovered_max_output_tokens": 512 if change == "none" else None,
        "availability": ModelAvailability.MISSING
        if origin == ModelOrigin.DISCOVERED and change != "none" else before["availability"],
    }
    assert untouched.discovered_max_output_tokens == 256
    monkeypatch.setattr(ProviderService, "discover_models", AsyncMock(return_value=[]))
    await sync_provider_models(provider.id, _db())
    assert model.discovered_max_output_tokens is None
    assert model.enabled is True
    assert model.availability == (ModelAvailability.MISSING if origin == ModelOrigin.DISCOVERED else ModelAvailability.AVAILABLE)
    assert model.max_output_tokens == 64_000
    assert untouched.discovered_max_output_tokens == 256


@pytest.mark.parametrize("change", ["account", "credential", "none"])
async def test_codex_identity_change_clears_caps_but_token_rotation_preserves_them(monkeypatch, change) -> None:
    credential = SimpleNamespace(id=uuid4(), metadata_json={"accountId": "old"})
    provider = LlmProviderConfig(name="Codex", adapter_type="openai-codex", credential_id=credential.id)
    model = ModelConfig(provider_id=provider.id, model_id="opaque", discovered_max_output_tokens=512)
    settings = AiSettings(providers=[provider], models=[model])
    _mock_settings(monkeypatch, settings)
    db = _db()
    db.scalar.return_value = credential
    before = model.model_dump()
    await ProviderSettingsService.connect_codex(
        db, {"account_id": "new" if change == "account" else "old"}, None,
        load_settings=AsyncMock(return_value=(settings, None)),
        store_credential=AsyncMock(return_value=SimpleNamespace(id=uuid4()) if change == "credential" else credential),
    )
    assert model.model_dump() == {**before, "discovered_max_output_tokens": 512 if change == "none" else None}
    model.discovered_max_output_tokens = 512
    await ProviderSettingsService.disconnect_codex(
        db, settings, None, [SimpleNamespace(id=provider.credential_id)],
    )
    assert model.model_dump() == {**before, "discovered_max_output_tokens": None}
