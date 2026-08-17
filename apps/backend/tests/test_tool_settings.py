from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.api.errors import ApiError
from mato_loop_backend.api.routes.tool_settings import (
    ToolSettingsRequest,
    get_personal_tool_settings,
    get_tool_settings,
    update_tool_settings,
)
from mato_loop_backend.settings import (
    IntegrationInstance,
    IntegrationSettings,
    SettingsStore,
    ToolSettings,
)


def _db() -> AsyncMock:
    return AsyncMock(spec=AsyncSession)


def test_legacy_duckduckgo_provider_is_migrated_to_no_key_search() -> None:
    settings = ToolSettings.model_validate(
        {"webSearch": {"primaryProvider": "duckduckgo"}}
    )

    assert settings.web_search.primary_provider == "no_key"


async def test_get_tool_settings_returns_defaults(monkeypatch) -> None:
    settings = ToolSettings()
    integrations = IntegrationSettings()

    async def load_settings(_db, settings_type, **_kwargs):
        return integrations if settings_type is IntegrationSettings else settings

    monkeypatch.setattr(SettingsStore, "load", AsyncMock(side_effect=load_settings))

    result = await get_tool_settings(_db())

    assert {
        key: result[key]
        for key in (
            "webSearch",
            "webFetch",
            "generateImage",
            "editImage",
            "generateSpeech",
            "generateAudio",
            "transcribeAudio",
            "analyzeVideo",
            "updateAgentIdentity",
        )
    } == {
        "webSearch": {
            "primaryProvider": "no_key",
            "fallbackProvider": None,
            "providerOptions": [
                {
                    "value": "no_key",
                    "name": "Websuche ohne API-Key",
                    "adapterType": "ddgs",
                    "integrationId": None,
                }
            ],
        },
        "webFetch": {
            "enabled": True,
            "userAgent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36"
            ),
            "maxResponseMiB": 64,
            "defaultPolicy": "auto_public",
            "allowedPrivateOrigins": [],
        },
        "generateImage": {"policy": "disabled", "timeoutSeconds": 300},
        "editImage": {"policy": "ask", "timeoutSeconds": 300},
        "generateSpeech": {"policy": "disabled", "timeoutSeconds": 300},
        "generateAudio": {"policy": "disabled", "timeoutSeconds": 300},
        "transcribeAudio": {"policy": "disabled", "timeoutSeconds": 300},
        "analyzeVideo": {"policy": "ask", "timeoutSeconds": 1800},
        "updateAgentIdentity": {"policy": "ask"},
    }
    assert result["policies"] == {
        "filesystem.read": "auto",
        "filesystem.write": "auto",
        "filesystem.delete": "auto",
        "network.search": "auto",
        "media.generate.image": "disabled",
        "media.edit.image": "ask",
        "media.generate.speech": "disabled",
        "media.generate.audio": "disabled",
        "media.transcribe.audio": "disabled",
        "media.analyze.video": "ask",
        "session.read": "ask",
        "session.search": "ask",
        "memory.write": "ask",
        "memory.read": "auto",
        "skills.load": "auto",
        "skills.write": "ask",
        "settings.agent_identity.update": "ask",
        "system.info": "auto",
        "execution.directory": "auto",
        "process.execute": "ask",
    }
    assert [group["id"] for group in result["groups"]] == [
        "filesystem",
        "process",
        "web",
        "media",
        "sessions",
        "memory",
        "agent",
    ]
    session_tools = next(group for group in result["groups"] if group["id"] == "sessions")
    discovery = {
        tool["name"]: tool["actions"][0]["capability"]
        for tool in session_tools["tools"]
        if tool["name"] in {"session_browse", "session_search"}
    }
    assert discovery == {
        "session_browse": "session.search",
        "session_search": "session.search",
    }


async def test_nonadmin_cannot_read_instance_private_tool_origins(monkeypatch) -> None:
    settings = ToolSettings(
        web_fetch={"allowed_private_origins": ["https://private.example"]}
    )
    monkeypatch.setattr(SettingsStore, "load", AsyncMock(return_value=settings))
    user = SimpleNamespace(id=uuid4(), is_admin=False)

    with pytest.raises(ApiError, match="auth.adminRequired"):
        await get_tool_settings(_db(), scope="instance", user=user)


async def test_personal_tool_policy_read_exposes_only_gate(monkeypatch) -> None:
    settings = ToolSettings(
        personal_settings_enabled=False,
        web_fetch={"allowed_private_origins": ["https://private.example"]},
    )
    monkeypatch.setattr(SettingsStore, "load", AsyncMock(return_value=settings))

    result = await get_personal_tool_settings(
        SimpleNamespace(id=uuid4(), is_admin=False), _db()
    )

    assert result == {"enabled": False}


async def test_personal_effective_tools_redact_instance_private_origins(
    monkeypatch,
) -> None:
    settings = ToolSettings(
        web_fetch={"allowed_private_origins": ["https://private.example"]}
    )
    monkeypatch.setattr(
        "mato_loop_backend.api.routes.tool_settings.load_effective_tools",
        AsyncMock(return_value=settings),
    )
    monkeypatch.setattr(
        "mato_loop_backend.api.routes.tool_settings.load_effective_integrations",
        AsyncMock(return_value=IntegrationSettings()),
    )

    result = await get_tool_settings(
        _db(), scope="personal", user=SimpleNamespace(id=uuid4(), is_admin=False)
    )

    assert result["webFetch"]["allowedPrivateOrigins"] == []


async def test_put_tool_settings_canonicalizes_and_persists_origins(monkeypatch) -> None:
    save = AsyncMock()
    existing = ToolSettings()
    integration = IntegrationInstance(
        name="Intern",
        integration_type="search",
        adapter_type="searxng",
        config={"base_url": "https://search.example.com"},
    )
    integrations = IntegrationSettings(instances=[integration])

    async def load_settings(_db, settings_type, **_kwargs):
        return integrations if settings_type is IntegrationSettings else existing

    load = AsyncMock(side_effect=load_settings)
    monkeypatch.setattr(SettingsStore, "load", load)
    monkeypatch.setattr(SettingsStore, "save", save)
    db = _db()
    payload = ToolSettingsRequest.model_validate(
        {
            "policies": {"filesystem.delete": "ask"},
            "webSearch": {
                "primaryProvider": f"searxng:{integration.id}",
                "fallbackProvider": "no_key",
            },
            "webFetch": {
                "enabled": True,
                "userAgent": "ConfiguredBrowser/1.0",
                "maxResponseMiB": 96,
                "defaultPolicy": "auto_allowed",
                "allowedPrivateOrigins": [
                    "HTTPS://Example.COM:443/",
                    "http://127.0.0.1:8080",
                    "https://example.com",
                ],
            },
            "generateImage": {"policy": "ask", "timeoutSeconds": 600},
            "editImage": {"policy": "ask", "timeoutSeconds": 450},
            "generateSpeech": {"policy": "auto", "timeoutSeconds": 120},
            "generateAudio": {"policy": "disabled", "timeoutSeconds": 900},
            "transcribeAudio": {"policy": "ask", "timeoutSeconds": 180},
            "updateAgentIdentity": {"policy": "auto"},
        }
    )

    result = await update_tool_settings(payload, db)

    assert result["webFetch"] == {
        "enabled": True,
        "userAgent": "ConfiguredBrowser/1.0",
        "maxResponseMiB": 96,
        "defaultPolicy": "auto_allowed",
        "allowedPrivateOrigins": [
            "https://example.com",
            "http://127.0.0.1:8080",
        ],
    }
    saved = save.await_args.args[1]
    assert load.await_args_list[0].args[1] is ToolSettings
    assert load.await_args_list[0].kwargs == {}
    assert load.await_args_list[1].args[1] is ToolSettings
    assert load.await_args_list[1].kwargs == {"for_update": True}
    assert load.await_args_list[2].args[1] is IntegrationSettings
    assert load.await_args_list[2].kwargs == {"for_update": True}
    assert saved is existing
    assert saved.settings_key == "tools"
    assert saved.web_fetch.allowed_private_origins == result["webFetch"][
        "allowedPrivateOrigins"
    ]
    assert saved.web_fetch.user_agent == "ConfiguredBrowser/1.0"
    assert saved.web_search.primary_provider == f"searxng:{integration.id}"
    assert saved.web_search.fallback_provider == "no_key"
    assert saved.generate_image.policy == "ask"
    assert saved.generate_image.timeout_seconds == 600
    assert saved.edit_image.policy == "ask"
    assert saved.edit_image.timeout_seconds == 450
    assert saved.generate_speech.policy == "auto"
    assert saved.generate_speech.timeout_seconds == 120
    assert saved.generate_audio.policy == "disabled"
    assert saved.generate_audio.timeout_seconds == 900
    assert saved.transcribe_audio.policy == "ask"
    assert saved.transcribe_audio.timeout_seconds == 180
    assert saved.update_agent_identity.policy == "auto"
    assert saved.policies["filesystem.delete"] == "ask"
    assert saved.policies["media.generate.image"] == "ask"
    assert saved.policies["media.edit.image"] == "ask"
    assert result["policies"]["filesystem.delete"] == "ask"
    db.commit.assert_awaited_once()


@pytest.mark.parametrize(
    "origin",
    [
        "ftp://example.com",
        "https://user:secret@example.com",
        "https://example.com/private",
        "https://example.com?token=secret",
        "https://example.com#fragment",
    ],
)
def test_tool_settings_reject_non_origin_urls(origin: str) -> None:
    with pytest.raises(ValidationError):
        ToolSettingsRequest.model_validate(
            {
                "webFetch": {
                    "enabled": True,
                    "defaultPolicy": "ask",
                    "allowedPrivateOrigins": [origin],
                }
            }
        )


async def test_tool_settings_reject_an_unavailable_search_provider(monkeypatch) -> None:
    integrations = IntegrationSettings()
    settings = ToolSettings()

    async def load_settings(_db, settings_type, **_kwargs):
        return integrations if settings_type is IntegrationSettings else settings

    monkeypatch.setattr(SettingsStore, "load", AsyncMock(side_effect=load_settings))
    payload = ToolSettingsRequest.model_validate(
        {
            "webSearch": {
                "primaryProvider": "searxng:00000000-0000-0000-0000-000000000000"
            },
            "webFetch": {
                "enabled": True,
                "defaultPolicy": "ask",
                "allowedPrivateOrigins": [],
            },
        }
    )

    with pytest.raises(ApiError) as exc_info:
        await update_tool_settings(payload, _db())

    assert exc_info.value.status_code == 409
    assert exc_info.value.code == "integration.providerUnavailable"
    assert exc_info.value.field == "primary_provider"


@pytest.mark.parametrize("timeout", [9, 3_601, 30.5])
def test_tool_settings_reject_invalid_media_timeout(timeout: float) -> None:
    with pytest.raises(ValidationError):
        ToolSettingsRequest.model_validate(
            {
                "webFetch": {
                    "enabled": True,
                    "defaultPolicy": "ask",
                    "allowedPrivateOrigins": [],
                },
                "generateImage": {"policy": "ask", "timeoutSeconds": timeout},
            }
        )


@pytest.mark.parametrize("limit", [0, 257, 1.5])
def test_tool_settings_reject_invalid_webfetch_limit(limit: float) -> None:
    with pytest.raises(ValidationError):
        ToolSettingsRequest.model_validate(
            {
                "webFetch": {
                    "enabled": True,
                    "maxResponseMiB": limit,
                    "defaultPolicy": "ask",
                    "allowedPrivateOrigins": [],
                }
            }
        )


@pytest.mark.parametrize(
    "user_agent",
    ["", "   ", "Mozilla/5.0\r\nX-Injected: true", "Mözilla/5.0", "x" * 513],
)
def test_tool_settings_reject_invalid_webfetch_user_agent(user_agent: str) -> None:
    with pytest.raises(ValidationError):
        ToolSettingsRequest.model_validate(
            {
                "webFetch": {
                    "enabled": True,
                    "userAgent": user_agent,
                    "defaultPolicy": "ask",
                    "allowedPrivateOrigins": [],
                }
            }
        )
