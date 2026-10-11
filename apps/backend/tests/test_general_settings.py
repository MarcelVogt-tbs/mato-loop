from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError
from starlette.requests import Request

from mato_loop_backend.api.errors import ApiError
from mato_loop_backend.api.routes import general_settings, mcp
from mato_loop_backend.public_url import resolve_public_base_url
from mato_loop_backend.settings import GeneralSettings, SettingsStore, ToolSettings


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
    monkeypatch.setattr(SettingsStore, "load", AsyncMock(side_effect=lambda _db, kind, **_: settings if kind is GeneralSettings else kind()))
    monkeypatch.setattr(SettingsStore, "save", AsyncMock())
    db = AsyncMock()
    await general_settings.update_general_settings(
        general_settings.GeneralSettingsUpdate(publicBaseUrl=f" {base}/ "), http_request, db
    )
    assert (
        await mcp._callback_uri(db, http_request)
        == base + "/api/mcp/oauth/callback"
    )
    assert settings.default_timezone == "UTC"
    db.commit.assert_awaited_once()


async def test_model_trust_switch_preserves_stored_general_settings(monkeypatch, http_request):
    settings = GeneralSettings(default_timezone="UTC")
    monkeypatch.setattr(SettingsStore, "load", AsyncMock(side_effect=lambda _db, kind, **_: settings if kind is GeneralSettings else kind()))
    monkeypatch.setattr(SettingsStore, "save", AsyncMock())
    result = await general_settings.update_general_settings(
        general_settings.GeneralSettingsUpdate(modelTrustEnabled=False), http_request, AsyncMock()
    )
    assert (result["modelTrustEnabled"], settings.model_trust_enabled, settings.default_timezone) == (False, False, "UTC")


@pytest.mark.parametrize("trust", ["private", "public"])
@pytest.mark.parametrize("preset, read, write, delete, sensitive", [
    ("ask_all", "ask", "ask", "ask", "ask"),
    ("ask_changes", "auto", "ask", "ask", "ask"),
    ("ask_delete_sensitive", "auto", "auto", "ask", "ask"),
    ("ask_sensitive", "auto", "auto", "auto", "ask"),
    ("auto_all", "auto", "auto", "auto", "auto"),
])
async def test_tool_presets_change_only_selected_global_policies(monkeypatch, http_request, trust, preset, read, write, delete, sensitive):
    tools = ToolSettings(
        private_permissions={"policies": {"filesystem.delete": "disabled"}},
        public_permissions={"policies": {"filesystem.delete": "disabled"}},
        tool_availability={"shell_run": False}, shell_prefixes={"read": [["git", "status"]]},
    )
    before = tools.model_dump()
    documents = {GeneralSettings: GeneralSettings(), ToolSettings: tools}
    monkeypatch.setattr(SettingsStore, "load", AsyncMock(side_effect=lambda _db, kind, **_: documents[kind]))
    save = AsyncMock()
    monkeypatch.setattr(SettingsStore, "save", save)
    db = AsyncMock()
    result = await general_settings.update_general_settings(
        general_settings.GeneralSettingsUpdate(toolPermissionPresets={trust: preset}), http_request, db
    )
    policies = getattr(tools, f"{trust}_permissions").policies
    assert tuple(policies[key] for key in (
        "filesystem.read", "filesystem.write", "shell.write", "filesystem.delete", "automation.delete",
        "sensitive_data.access", "secrets.access", "secrets.write",
    )) == (read, write, write, delete, delete, sensitive, sensitive, sensitive)
    after = tools.model_dump()
    after[f"{trust}_permissions"]["policies"] = before[f"{trust}_permissions"]["policies"]
    assert after == before
    assert result["toolPermissionPresets"][trust] == preset
    assert {type(call.args[1]) for call in save.await_args_list} == {GeneralSettings, ToolSettings}
    db.commit.assert_awaited_once()

    policies["filesystem.read"] = "disabled"
    result = await general_settings.update_general_settings(general_settings.GeneralSettingsUpdate(), http_request, db)
    assert result["toolPermissionPresets"][trust] is None
    assert len(save.await_args_list) == 3


async def test_disabled_model_distinction_preserves_public_tool_permissions(monkeypatch, http_request):
    monkeypatch.setattr(SettingsStore, "load", AsyncMock(return_value=GeneralSettings(modelTrustEnabled=False)))
    with pytest.raises(ApiError, match="validation.invalidChoice"):
        await general_settings.update_general_settings(
            general_settings.GeneralSettingsUpdate(toolPermissionPresets={"public": "auto_all"}), http_request, AsyncMock()
        )


@pytest.mark.parametrize("presets", [{"other": "auto_all"}, {"private": "unknown"}, {"private": None}])
def test_tool_presets_validate_requested_branch_and_preset(presets):
    with pytest.raises(ValidationError):
        general_settings.GeneralSettingsUpdate(toolPermissionPresets=presets)


def test_general_settings_are_admin_only():
    assert all(
        any(
            dependency.call.__name__ == "require_admin"
            for dependency in route.dependant.dependencies
        )
        for route in general_settings.router.routes
    )
