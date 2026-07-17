from mato_loop_backend.settings.models import AgentRuntimeSettings, AiSettings, LlmProviderConfig


def test_missing_settings_fields_use_defaults() -> None:
    settings = AgentRuntimeSettings.model_validate({"max_steps": 7})

    assert settings.max_steps == 7
    assert settings.max_tool_calls == 20
    assert settings.request_timeout_seconds == 300


def test_ai_settings_collections_are_independent() -> None:
    first = AiSettings()
    second = AiSettings()

    first.providers.append(LlmProviderConfig(name="Local", adapter_type="openai-compatible"))

    assert len(first.providers) == 1
    assert second.providers == []
