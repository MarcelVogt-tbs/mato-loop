from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.api.routes.auth import (
    UserPreferencesRequest,
    update_user_preferences,
    user_preferences,
)
from mato_loop_backend.models import UserAccount
from mato_loop_backend.settings import (
    IntegrationSettings,
    MemorySettings,
    SettingsScope,
    SettingsStore,
)
from mato_loop_backend.settings.models import (
    AgentRuntimeSettings,
    AiSettings,
    LlmProviderConfig,
    ModelCapability,
    ModelConfig,
    ModelWebFetchPolicy,
    ToolSettings,
    UserPreferences,
    WebFetchPolicy,
    WebFetchSettings,
)
from mato_loop_backend.settings.web_fetch import resolve_web_fetch_policy


def test_missing_settings_fields_use_defaults() -> None:
    settings = AgentRuntimeSettings.model_validate({"max_steps": 7})

    assert settings.max_steps == 7
    assert settings.max_tool_calls == 20
    assert settings.request_timeout_seconds == 300
    assert settings.compaction_trigger_ratio == 0.8
    assert settings.compaction_target_ratio == 0.6
    assert settings.safety_reserve_tokens == 4_096
    assert settings.protected_recent_turns == 2
    assert settings.approval_timeout_seconds == 600
    assert settings.max_compaction_requests == 3


def test_ai_settings_collections_are_independent() -> None:
    first = AiSettings()
    second = AiSettings()

    first.providers.append(LlmProviderConfig(name="Local", adapter_type="openai-compatible"))

    assert len(first.providers) == 1
    assert second.providers == []


def test_integration_settings_use_an_independent_global_instance_list() -> None:
    first = IntegrationSettings()
    second = IntegrationSettings()

    assert first.settings_key == "integrations"
    assert first.settings_scope == SettingsScope.GLOBAL
    assert first.instances == []
    assert second.instances == []


def test_legacy_model_defaults_to_chat_capability() -> None:
    provider = LlmProviderConfig(name="Legacy", adapter_type="openai-compatible")

    model = ModelConfig.model_validate(
        {"provider_id": str(provider.id), "model_id": "legacy"}
    )

    assert model.capabilities == {ModelCapability.CHAT}


def test_user_preferences_default_to_collapsed_thinking() -> None:
    assert UserPreferences().thinking_expanded is False
    assert UserPreferences.settings_scope == SettingsScope.USER
    assert MemorySettings.settings_scope == SettingsScope.USER
    assert MemorySettings().automatic_dreaming_enabled is False


def test_model_runtime_uses_defaults_and_legacy_config_values() -> None:
    provider_id = uuid4()

    default_model = ModelConfig(provider_id=provider_id, model_id="default")
    legacy_model = ModelConfig.model_validate(
        {
            "provider_id": provider_id,
            "model_id": "legacy",
            "config": {
                "contextWindowTokens": 32_000,
                "max_output_tokens": 2_048,
                "supportsTools": False,
            },
        }
    )

    assert default_model.context_window_tokens == 128_000
    assert default_model.max_output_tokens == 16_384
    assert default_model.characters_per_token == 4.0
    assert default_model.origin == "manual"
    assert default_model.availability == "available"
    assert default_model.last_seen_at is None
    assert default_model.discovery_metadata == {}
    assert default_model.webfetch_policy == ModelWebFetchPolicy.INHERIT
    assert default_model.supports_image_input is False
    assert legacy_model.context_window_tokens == 32_000
    assert legacy_model.max_output_tokens == 2_048
    assert legacy_model.supports_tools is False


def test_model_config_discards_legacy_compression_model() -> None:
    model = ModelConfig.model_validate(
        {
            "provider_id": uuid4(),
            "model_id": "legacy",
            "compression_model_id": uuid4(),
        }
    )

    assert "compression_model_id" not in model.model_dump()


def test_model_webfetch_fields_accept_snake_and_camel_case() -> None:
    provider_id = uuid4()

    camel_model = ModelConfig.model_validate(
        {
            "provider_id": provider_id,
            "model_id": "camel",
            "webfetchPolicy": "auto_allowed",
            "supportsImageInput": True,
        }
    )
    snake_model = ModelConfig(
        provider_id=provider_id,
        model_id="snake",
        webfetch_policy="disabled",
        supports_image_input=True,
    )

    assert camel_model.webfetch_policy == ModelWebFetchPolicy.AUTO_ALLOWED
    assert camel_model.supports_image_input is True
    assert snake_model.webfetch_policy == ModelWebFetchPolicy.DISABLED
    assert snake_model.supports_image_input is True


def test_effective_webfetch_policy_resolves_inheritance_override_and_hard_gate() -> None:
    model = ModelConfig(provider_id=uuid4(), model_id="model")
    settings = ToolSettings(
        web_fetch=WebFetchSettings(default_policy=WebFetchPolicy.AUTO_PUBLIC)
    )

    assert resolve_web_fetch_policy(settings, model) == WebFetchPolicy.AUTO_PUBLIC

    model.webfetch_policy = ModelWebFetchPolicy.AUTO_ALLOWED
    assert resolve_web_fetch_policy(settings, model) == WebFetchPolicy.AUTO_ALLOWED

    settings.web_fetch.enabled = False
    assert resolve_web_fetch_policy(settings, model) == WebFetchPolicy.DISABLED


def test_legacy_small_context_derives_a_safe_output_limit() -> None:
    model = ModelConfig.model_validate(
        {
            "provider_id": uuid4(),
            "model_id": "legacy-8k",
            "contextWindowTokens": 8_192,
        }
    )

    assert model.context_window_tokens == 8_192
    assert model.max_output_tokens == 2_048


def test_compaction_target_must_be_below_trigger() -> None:
    with pytest.raises(ValidationError):
        AgentRuntimeSettings(compaction_trigger_ratio=0.7, compaction_target_ratio=0.7)


async def test_settings_can_be_loaded_with_row_lock() -> None:
    db = AsyncMock(spec=AsyncSession)
    db.scalar.return_value = None

    await SettingsStore.load(db, AiSettings, for_update=True)

    statement = db.scalar.await_args.args[0]
    assert "FOR UPDATE" in str(statement)


async def test_user_settings_require_an_explicit_user_scope() -> None:
    db = AsyncMock(spec=AsyncSession)

    with pytest.raises(ValueError, match="requires a user_id"):
        await SettingsStore.load(db, MemorySettings)
    with pytest.raises(ValueError, match="does not accept a user_id"):
        await SettingsStore.load(db, AiSettings, user_id=uuid4())

    db.scalar.assert_not_awaited()


async def test_user_settings_are_loaded_and_saved_for_the_same_user() -> None:
    user_id = uuid4()
    db = AsyncMock(spec=AsyncSession)
    db.scalar.return_value = None

    settings = await SettingsStore.load(db, MemorySettings, user_id=user_id)
    await SettingsStore.save(db, settings, user_id=user_id)

    load_statement = db.scalar.await_args_list[0].args[0]
    assert user_id in load_statement.compile().params.values()
    record = db.add.call_args.args[0]
    assert record.key == "memory"
    assert record.user_id == user_id


async def test_thinking_preference_is_read_and_persisted(monkeypatch) -> None:
    preferences = UserPreferences()
    load = AsyncMock(return_value=preferences)
    save = AsyncMock()
    monkeypatch.setattr(SettingsStore, "load", load)
    monkeypatch.setattr(SettingsStore, "save", save)
    db = AsyncMock(spec=AsyncSession)
    user = UserAccount(id=uuid4(), username="test", password_hash="hash")

    assert await user_preferences(user, db) == {"thinkingExpanded": False}
    result = await update_user_preferences(
        UserPreferencesRequest(thinking_expanded=True), user, db
    )

    assert result == {"thinkingExpanded": True}
    save.assert_awaited_once_with(db, preferences, user_id=user.id)
    assert load.await_args_list[0].kwargs == {"user_id": user.id}
    db.commit.assert_awaited_once()
