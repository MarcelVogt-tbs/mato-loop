from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.settings import (
    ChannelSettings,
    DefaultTaskListSettings,
    InstanceLimits,
    IntegrationSettings,
    MemorySettings,
    SettingsScope,
    SettingsStore,
    ToolExecutionPolicy,
)
from mato_loop_backend.settings.models import (
    AgentRuntimeSettings,
    AiSettings,
    LlmProviderConfig,
    ModelCapability,
    ModelConfig,
    ModelInputModality,
    ToolSettings,
    UserPreferences,
)


def test_missing_settings_fields_use_defaults() -> None:
    removed = {"max_steps": 7, "max_model_requests": 30, "max_tool_calls": 50, "max_research_tool_calls": 8}
    settings = AgentRuntimeSettings.model_validate(removed)

    assert not removed.keys() & settings.model_dump().keys()
    assert settings.request_timeout_seconds == 300
    assert settings.compaction_trigger_ratio == 0.8
    assert settings.compaction_target_ratio == 0.6
    assert settings.safety_reserve_tokens == 4_096
    assert settings.protected_recent_turns == 2
    assert settings.max_compaction_requests == 3
    assert settings.historical_tool_output_tokens == 2_000
    assert ToolSettings().web_fetch.public_addresses_only is True
    assert ToolSettings().default_calendar is None
    assert ToolSettings().default_task_list is None
    assert ToolSettings().resource_labels == {}
    assert InstanceLimits.settings_key == "instance-limits"
    assert InstanceLimits().max_upload_mib == 64
    assert InstanceLimits().session_quota_mib == 5_120
    assert InstanceLimits().max_extracted_characters == 5_000_000
    assert ChannelSettings.settings_key == "channels"
    assert ChannelSettings().allowed_providers == {"telegram"}


def test_instance_limits_validate_related_limits_and_aliases() -> None:
    settings = InstanceLimits.model_validate(
        {
            "maxUploadMib": 128,
            "maxMessageUploadMib": 256,
            "sessionQuotaMib": 1_024,
            "maxArchiveEntryMib": 128,
            "maxArchiveUncompressedMib": 256,
        }
    )

    assert settings.max_upload_bytes == 128 * 1024 * 1024
    assert settings.model_dump(by_alias=True)["maxUploadMib"] == 128
    with pytest.raises(ValidationError):
        InstanceLimits(max_upload_mib=65, session_quota_mib=64)


def test_default_calendar_requires_a_stable_calendar_target() -> None:
    integration_id = uuid4()
    settings = ToolSettings.model_validate(
        {
            "defaultCalendar": {
                "integrationId": str(integration_id),
                "calendarKey": f"calendar:{'a' * 32}",
            }
        }
    )

    assert settings.default_calendar.integration_id == integration_id
    with pytest.raises(ValidationError):
        ToolSettings.model_validate(
            {
                "defaultCalendar": {
                    "integrationId": str(integration_id),
                    "calendarKey": "calendar:unstable",
                }
            }
        )


def test_default_task_list_accepts_aliases_and_requires_a_stable_target() -> None:
    integration_id = uuid4()
    key = f"tasklist:{'a' * 32}"

    camel = ToolSettings.model_validate(
        {
            "defaultTaskList": {
                "integrationId": str(integration_id),
                "taskListKey": key,
            }
        }
    )
    snake = ToolSettings.model_validate(
        {
            "default_task_list": {
                "integration_id": integration_id,
                "task_list_key": key,
            }
        }
    )

    expected = DefaultTaskListSettings(
        integration_id=integration_id, task_list_key=key
    )
    assert camel.default_task_list == expected
    assert snake.default_task_list == expected
    with pytest.raises(ValidationError):
        DefaultTaskListSettings(
            integration_id=integration_id,
            task_list_key=f"calendar:{'a' * 32}",
        )


def test_ai_settings_collections_are_independent() -> None:
    first = AiSettings()
    second = AiSettings()

    first.providers.append(LlmProviderConfig(name="Local", adapter_type="openai-compatible"))

    assert len(first.providers) == 1
    assert second.providers == []


def test_integration_settings_defaults() -> None:
    first = IntegrationSettings()

    assert first.settings_key == "integrations"
    assert first.settings_scope == SettingsScope.GLOBAL
    assert first.instances == []


def test_legacy_model_defaults_to_chat_capability() -> None:
    provider = LlmProviderConfig(name="Legacy", adapter_type="openai-compatible")

    model = ModelConfig.model_validate(
        {"provider_id": str(provider.id), "model_id": "legacy"}
    )

    assert model.capabilities == {ModelCapability.CHAT}


def test_user_settings_are_user_scoped() -> None:
    assert UserPreferences.settings_scope == SettingsScope.USER
    assert MemorySettings.settings_scope == SettingsScope.USER
    settings = MemorySettings()
    assert settings.automatic_dreaming_enabled is False
    assert settings.dreaming_update_policy == ToolExecutionPolicy.AUTO
    assert settings.dreaming_remove_policy == ToolExecutionPolicy.ASK
    assert settings.automatic_memory_snippet_limit == 2
    assert settings.automatic_session_snippet_limit == 3
    assert settings.recent_session_snippet_limit == 2
    assert settings.automatic_context_max_tokens == 1_024


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
    assert default_model.max_output_tokens is None
    assert default_model.discovered_max_output_tokens is None
    assert default_model.characters_per_token == 4.0
    assert default_model.origin == "manual"
    assert default_model.availability == "available"
    assert default_model.last_seen_at is None
    assert default_model.discovery_metadata == {}
    assert default_model.supports_image_input is False
    assert legacy_model.context_window_tokens == 32_000
    assert legacy_model.max_output_tokens == 2_048
    assert legacy_model.supports_tools is False


def test_discovered_context_window_can_drive_effective_runtime_limit() -> None:
    model = ModelConfig(
        provider_id=uuid4(),
        model_id="detected",
        context_window_tokens=128_000,
        discovered_context_window_tokens=32_000,
        use_discovered_context_window=True,
        max_output_tokens=16_384,
    )

    assert model.context_window_tokens == 32_000
    assert model.max_output_tokens == 16_384
    assert model.context_window_source == "provider"


def test_existing_context_window_remains_a_manual_override() -> None:
    model = ModelConfig.model_validate(
        {
            "provider_id": uuid4(),
            "model_id": "existing",
            "contextWindowTokens": 64_000,
            "discoveredContextWindowTokens": 32_000,
        }
    )

    assert model.context_window_tokens == 64_000
    assert model.use_discovered_context_window is False
    assert model.context_window_source == "override"


def test_model_config_discards_legacy_compression_model() -> None:
    model = ModelConfig.model_validate(
        {
            "provider_id": uuid4(),
            "model_id": "legacy",
            "compression_model_id": uuid4(),
        }
    )

    assert "compression_model_id" not in model.model_dump()


def test_model_input_modalities_are_canonical_with_legacy_image_compatibility() -> None:
    provider_id = uuid4()

    legacy = ModelConfig(
        provider_id=provider_id,
        model_id="legacy",
        supports_image_input=True,
    )
    canonical = ModelConfig.model_validate(
        {
            "provider_id": provider_id,
            "model_id": "canonical",
            "inputModalities": ["text", "audio", "video"],
            "supportsImageInput": True,
        }
    )

    assert legacy.input_modalities == {
        ModelInputModality.TEXT,
        ModelInputModality.IMAGE,
    }
    assert legacy.model_dump()["supports_image_input"] is True
    assert canonical.input_modalities == {
        ModelInputModality.TEXT,
        ModelInputModality.AUDIO,
        ModelInputModality.VIDEO,
    }
    assert canonical.supports_image_input is False


def test_small_context_defaults_to_auto_output() -> None:
    model = ModelConfig.model_validate(
        {
            "provider_id": uuid4(),
            "model_id": "legacy-8k",
            "contextWindowTokens": 8_192,
        }
    )

    assert model.context_window_tokens == 8_192
    assert model.max_output_tokens is None


@pytest.mark.parametrize("key", ["max_output_tokens", "maxOutputTokens"])
def test_explicit_auto_output_overrides_legacy_config_without_mutating_it(key) -> None:
    config = {"max_output_tokens": 512, "maxOutputTokens": 1_024, "temperature": 0.6}
    model = ModelConfig.model_validate({
        "provider_id": uuid4(), "model_id": "opaque", key: None, "config": config,
    })
    assert model.max_output_tokens is None
    assert model.config == config
    assert ModelConfig.model_validate(model.model_dump()).max_output_tokens is None


@pytest.mark.parametrize("field", ["max_output_tokens", "discovered_max_output_tokens"])
@pytest.mark.parametrize("value", [0, -1, True, False, 1.5, 2.0, "512", "", [], {}])
def test_output_limits_require_strict_positive_integers_or_null(field, value) -> None:
    with pytest.raises(ValidationError):
        ModelConfig(provider_id=uuid4(), model_id="opaque", **{field: value})


@pytest.mark.parametrize("context", [2, 4_096, 128_000])
def test_manual_output_ceiling_is_independent_of_context(context) -> None:
    model = ModelConfig(
        provider_id=uuid4(), model_id="opaque", context_window_tokens=context,
        max_output_tokens=2_000_000, discovered_max_output_tokens=8_192,
    )
    assert model.max_output_tokens == 2_000_000
    assert model.discovered_max_output_tokens == 8_192


@pytest.mark.parametrize("key", ["context_window_tokens", "contextWindowTokens"])
@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("output", [None, 1])
def test_model_context_requires_two_tokens_with_auto_or_minimum_cap(key, legacy, output) -> None:
    values = {"provider_id": uuid4(), "model_id": "opaque", "max_output_tokens": output}
    context = {key: 1}
    with pytest.raises(ValidationError):
        ModelConfig.model_validate({**values, **({"config": context} if legacy else context)})
    context[key] = 2
    model = ModelConfig.model_validate({**values, **({"config": context} if legacy else context)})
    assert model.context_window_tokens == 2
    assert model.max_output_tokens == output


def test_compaction_target_must_be_below_trigger() -> None:
    with pytest.raises(ValidationError):
        AgentRuntimeSettings(compaction_trigger_ratio=0.7, compaction_target_ratio=0.7)


async def test_settings_can_be_loaded_with_row_lock() -> None:
    db = AsyncMock(spec=AsyncSession)
    db.scalar.return_value = None

    await SettingsStore.load(db, AiSettings, for_update=True)

    statement = db.scalar.await_args.args[0]
    assert "FOR UPDATE" in str(statement)


async def test_loading_ai_settings_does_not_persist_output_defaults() -> None:
    from types import SimpleNamespace

    data = {"models": [{"provider_id": str(uuid4()), "model_id": "opaque"}]}
    db = AsyncMock(spec=AsyncSession)
    db.scalar.return_value = SimpleNamespace(data=data)
    settings = await SettingsStore.load(db, AiSettings)
    assert settings.models[0].max_output_tokens is None
    assert "max_output_tokens" not in data["models"][0]
    db.add.assert_not_called()
    db.flush.assert_not_awaited()
    db.commit.assert_not_awaited()


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
