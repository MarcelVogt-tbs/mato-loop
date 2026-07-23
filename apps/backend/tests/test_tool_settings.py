from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.api.routes.tool_settings import (
    ToolSettingsRequest,
    get_tool_settings,
    update_tool_settings,
)
from mato_loop_backend.settings import SettingsStore, ToolSettings


def _db() -> AsyncMock:
    return AsyncMock(spec=AsyncSession)


async def test_get_tool_settings_returns_defaults(monkeypatch) -> None:
    settings = ToolSettings()
    monkeypatch.setattr(SettingsStore, "load", AsyncMock(return_value=settings))

    result = await get_tool_settings(_db())

    assert result == {
        "webFetch": {
            "enabled": True,
            "defaultPolicy": "ask",
            "allowedPrivateOrigins": [],
        },
        "generateImage": {"policy": "disabled", "timeoutSeconds": 300},
        "generateSpeech": {"policy": "disabled", "timeoutSeconds": 300},
        "generateAudio": {"policy": "disabled", "timeoutSeconds": 300},
        "transcribeAudio": {"policy": "disabled", "timeoutSeconds": 300},
    }


async def test_put_tool_settings_canonicalizes_and_persists_origins(monkeypatch) -> None:
    save = AsyncMock()
    existing = ToolSettings()
    load = AsyncMock(return_value=existing)
    monkeypatch.setattr(SettingsStore, "load", load)
    monkeypatch.setattr(SettingsStore, "save", save)
    db = _db()
    payload = ToolSettingsRequest.model_validate(
        {
            "webFetch": {
                "enabled": True,
                "defaultPolicy": "auto_allowed",
                "allowedPrivateOrigins": [
                    "HTTPS://Example.COM:443/",
                    "http://127.0.0.1:8080",
                    "https://example.com",
                ],
            },
            "generateImage": {"policy": "ask", "timeoutSeconds": 600},
            "generateSpeech": {"policy": "auto", "timeoutSeconds": 120},
            "generateAudio": {"policy": "disabled", "timeoutSeconds": 900},
            "transcribeAudio": {"policy": "ask", "timeoutSeconds": 180},
        }
    )

    result = await update_tool_settings(payload, db)

    assert result["webFetch"] == {
        "enabled": True,
        "defaultPolicy": "auto_allowed",
        "allowedPrivateOrigins": [
            "https://example.com",
            "http://127.0.0.1:8080",
        ],
    }
    saved = save.await_args.args[1]
    load.assert_awaited_once_with(db, ToolSettings, for_update=True)
    assert saved is existing
    assert saved.settings_key == "tools"
    assert saved.web_fetch.allowed_private_origins == result["webFetch"][
        "allowedPrivateOrigins"
    ]
    assert saved.generate_image.policy == "ask"
    assert saved.generate_image.timeout_seconds == 600
    assert saved.generate_speech.policy == "auto"
    assert saved.generate_speech.timeout_seconds == 120
    assert saved.generate_audio.policy == "disabled"
    assert saved.generate_audio.timeout_seconds == 900
    assert saved.transcribe_audio.policy == "ask"
    assert saved.transcribe_audio.timeout_seconds == 180
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
