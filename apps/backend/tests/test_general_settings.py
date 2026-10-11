from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from starlette.requests import Request

from mato_loop_backend.api.routes import general_settings, mcp
from mato_loop_backend.public_url import resolve_public_base_url
from mato_loop_backend.settings import GeneralSettings, SettingsStore


@pytest.fixture
def http_request():
    return Request(
        {
            "type": "http",
            "scheme": "https",
            "path": "/",
            "headers": [
                (b"host", b"mato.example.test"),
                (b"x-forwarded-host", b"untrusted.example.test"),
            ],
        }
    )


@pytest.mark.parametrize(
    "stored, environment, expected, source",
    [
        (
            "https://stored.example.test",
            "https://env.example.test",
            "https://stored.example.test",
            "settings",
        ),
        (None, "https://env.example.test/", "https://env.example.test", "environment"),
        (None, None, "https://mato.example.test", "automatic"),
    ],
)
def test_public_url_priority_and_server_detection(
    monkeypatch, http_request, stored, environment, expected, source
):
    monkeypatch.setattr(
        "mato_loop_backend.public_url.get_runtime_config",
        lambda: SimpleNamespace(public_base_url=environment),
    )
    assert resolve_public_base_url(stored, http_request) == (expected, source)


@pytest.mark.parametrize("base", ["https://public.example.test", "http://192.168.1.30:10020"])
async def test_admin_public_url_is_saved_and_used_by_next_oauth_login(monkeypatch, http_request, base):
    settings = GeneralSettings(default_timezone="UTC")
    monkeypatch.setattr(SettingsStore, "load", AsyncMock(return_value=settings))
    monkeypatch.setattr(SettingsStore, "save", AsyncMock())
    db = AsyncMock()
    await general_settings.update_general_settings(
        GeneralSettings(publicBaseUrl=f" {base}/ "), http_request, db
    )
    assert (
        await mcp._callback_uri(db, http_request)
        == base + "/api/mcp/oauth/callback"
    )
    assert settings.default_timezone == "UTC"
    db.commit.assert_awaited_once()


async def test_model_trust_switch_preserves_stored_general_settings(monkeypatch, http_request):
    settings = GeneralSettings(default_timezone="UTC")
    monkeypatch.setattr(SettingsStore, "load", AsyncMock(return_value=settings))
    monkeypatch.setattr(SettingsStore, "save", AsyncMock())
    result = await general_settings.update_general_settings(
        GeneralSettings(modelTrustEnabled=False), http_request, AsyncMock()
    )
    assert (result["modelTrustEnabled"], settings.model_trust_enabled, settings.default_timezone) == (False, False, "UTC")


def test_general_settings_are_admin_only():
    assert all(
        any(
            dependency.call.__name__ == "require_admin"
            for dependency in route.dependant.dependencies
        )
        for route in general_settings.router.routes
    )
