from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.agent.tools import resolve_tool_execution_policy, tool_registry
from mato_loop_backend.api.errors import ApiError
from mato_loop_backend.api.routes.tool_settings import (
    PersonalSettingsEnabledRequest,
    ToolSettingsRequest,
    get_personal_tool_settings,
    get_tool_availability,
    get_tool_settings,
    reset_personal_tool_settings,
    set_personal_tool_settings_enabled,
    update_tool_settings,
)
from mato_loop_backend.settings import (
    AgentProfilePermissions,
    IntegrationSettings,
    SettingsStore,
    ToolExecutionPolicy,
    ToolSettings,
    UserToolSettings,
)


def _db() -> AsyncMock:
    return AsyncMock(spec=AsyncSession)


async def test_nonadmin_cannot_read_instance_tool_settings(monkeypatch) -> None:
    load = AsyncMock(return_value=ToolSettings())
    monkeypatch.setattr(SettingsStore, "load", load)

    with pytest.raises(ApiError, match="auth.adminRequired"):
        await get_tool_settings(
            _db(), scope="instance", user=SimpleNamespace(id=uuid4(), is_admin=False)
        )

    load.assert_awaited_once()


def test_legacy_duckduckgo_provider_is_migrated_to_no_key_search() -> None:
    settings = ToolSettings.model_validate(
        {"webSearch": {"primaryProvider": "duckduckgo"}}
    )

    assert settings.web_search.primary_provider == "no_key"


@pytest.mark.parametrize(
    ("session_policy", "workspace_policy", "available"),
    [
        (ToolExecutionPolicy.DISABLED, ToolExecutionPolicy.ASK, True),
        (ToolExecutionPolicy.AUTO, ToolExecutionPolicy.DISABLED, True),
        (ToolExecutionPolicy.DISABLED, ToolExecutionPolicy.DISABLED, False),
    ],
)
def test_root_specific_write_policies_keep_shared_tools_available(
    session_policy: ToolExecutionPolicy,
    workspace_policy: ToolExecutionPolicy,
    available: bool,
) -> None:
    settings = ToolSettings(
        policies={
            "session.files.write": session_policy,
            "filesystem.write": workspace_policy,
        }
    )

    tools = tool_registry.effective(
        IntegrationSettings(), tool_settings=settings
    ).tools

    assert ("write_file" in tools) is available
    assert ("create_directory" in tools) is available
    assert ("copy_path" in tools) is available
    assert ("move_path" in tools) is available


@pytest.mark.parametrize(
    ("baseline", "profile", "expected"),
    [
        (ToolExecutionPolicy.ASK, ToolExecutionPolicy.AUTO, ToolExecutionPolicy.AUTO),
        (ToolExecutionPolicy.AUTO, ToolExecutionPolicy.ASK, ToolExecutionPolicy.ASK),
        (ToolExecutionPolicy.ASK, ToolExecutionPolicy.DISABLED, ToolExecutionPolicy.DISABLED),
        (ToolExecutionPolicy.DISABLED, ToolExecutionPolicy.AUTO, ToolExecutionPolicy.DISABLED),
    ],
)
def test_agent_profile_policy_controls_available_global_capability(
    baseline: ToolExecutionPolicy,
    profile: ToolExecutionPolicy,
    expected: ToolExecutionPolicy,
) -> None:
    settings = ToolSettings(policies={"filesystem.read": baseline})
    settings._agent_permissions = AgentProfilePermissions(
        policies={"filesystem.read": profile}
    )

    result = resolve_tool_execution_policy(
        settings, "filesystem.read", ToolExecutionPolicy.AUTO
    )

    assert result.policy == expected


def test_agent_profile_missing_capability_inherits_live_default() -> None:
    settings = ToolSettings(policies={"filesystem.read": ToolExecutionPolicy.AUTO})
    settings._agent_permissions = AgentProfilePermissions()

    assert (
        resolve_tool_execution_policy(settings, "filesystem.read").policy
        == ToolExecutionPolicy.AUTO
    )


def test_agent_profile_overrides_scoped_defaults_but_not_hard_limits() -> None:
    integration_id = uuid4()
    settings = ToolSettings(
        policies={"calendar.write": ToolExecutionPolicy.ASK},
        integration_policies={
            str(integration_id): {"calendar.write": ToolExecutionPolicy.DISABLED}
        },
    )
    settings._agent_permissions = AgentProfilePermissions(
        integration_policies={
            str(integration_id): {"calendar.write": ToolExecutionPolicy.AUTO}
        }
    )

    assert (
        resolve_tool_execution_policy(
            settings, "calendar.write", integration_id=integration_id
        ).policy
        == ToolExecutionPolicy.AUTO
    )

    settings.policies["calendar.write"] = ToolExecutionPolicy.DISABLED
    assert (
        resolve_tool_execution_policy(
            settings, "calendar.write", integration_id=integration_id
        ).policy
        == ToolExecutionPolicy.DISABLED
    )


def test_agent_profile_inherits_live_resource_default() -> None:
    integration_id = uuid4()
    resource_key = "calendar:work"
    settings = ToolSettings(
        policies={"calendar.read": ToolExecutionPolicy.ASK},
        resource_policies={
            str(integration_id): {
                resource_key: {"calendar.read": ToolExecutionPolicy.DISABLED}
            }
        },
    )
    settings._agent_permissions = AgentProfilePermissions()

    assert (
        resolve_tool_execution_policy(
            settings,
            "calendar.read",
            integration_id=integration_id,
            resource_key=resource_key,
        ).policy
        == ToolExecutionPolicy.DISABLED
    )

    settings.resource_policies[str(integration_id)][resource_key][
        "calendar.read"
    ] = ToolExecutionPolicy.AUTO
    assert (
        resolve_tool_execution_policy(
            settings,
            "calendar.read",
            integration_id=integration_id,
            resource_key=resource_key,
        ).policy
        == ToolExecutionPolicy.AUTO
    )


def test_unavailable_agent_profile_fails_closed() -> None:
    settings = ToolSettings(policies={"filesystem.read": ToolExecutionPolicy.AUTO})
    settings._agent_profile_unavailable = True

    assert (
        resolve_tool_execution_policy(settings, "filesystem.read").policy
        == ToolExecutionPolicy.DISABLED
    )


def test_agent_profile_scoped_capability_is_offered_and_resolved_for_its_target() -> None:
    integration_id = uuid4()
    settings = ToolSettings(policies={"mail.read": ToolExecutionPolicy.AUTO})
    settings._agent_permissions = AgentProfilePermissions(
        policies={"mail.read": ToolExecutionPolicy.DISABLED},
        integration_policies={str(integration_id): {"mail.read": ToolExecutionPolicy.ASK}},
    )

    assert (
        resolve_tool_execution_policy(settings, "mail.read").policy
        == ToolExecutionPolicy.DISABLED
    )
    assert (
        resolve_tool_execution_policy(
            settings, "mail.read", include_scoped=True
        ).policy
        == ToolExecutionPolicy.ASK
    )
    assert (
        resolve_tool_execution_policy(
            settings, "mail.read", integration_id=integration_id
        ).policy
        == ToolExecutionPolicy.ASK
    )
    assert (
        resolve_tool_execution_policy(
            settings, "mail.read", integration_id=uuid4()
        ).policy
        == ToolExecutionPolicy.DISABLED
    )


def test_agent_profile_leaves_non_configurable_capability_at_baseline() -> None:
    settings = ToolSettings(policies={"network.fetch": ToolExecutionPolicy.ASK})
    settings._agent_permissions = AgentProfilePermissions()

    assert (
        resolve_tool_execution_policy(settings, "network.fetch").policy
        == ToolExecutionPolicy.ASK
    )


async def test_get_tool_settings_returns_defaults(monkeypatch) -> None:
    settings = ToolSettings()
    monkeypatch.setattr(SettingsStore, "load", AsyncMock(return_value=settings))

    result = await get_tool_settings(_db())

    assert result["personalSettingsEnabled"] is True

    assert {
        key: result[key]
        for key in (
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
        "session.files.write": "auto",
        "filesystem.write": "ask",
        "filesystem.delete": "ask",
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
        "settings.user_profile.update": "ask",
        "system.info": "auto",
        "process.execute": "ask",
        "shell.read": "auto",
        "shell.write": "ask",
        "automation.read": "ask",
        "automation.create": "ask",
        "automation.update": "ask",
        "automation.run": "ask",
        "automation.delete": "disabled",
        "coding.analyze": "ask",
        "coding.plan.create": "ask",
        "coding.plan.read": "auto",
        "coding.execute.enqueue": "ask",
    }
    assert result["shellPrefixes"] == {"read": [], "write": []}
    assert [group["id"] for group in result["groups"]] == [
        "filesystem",
        "process",
        "web",
        "media",
        "sessions",
        "automations",
        "memory",
        "agent",
        "coding",
    ]
    assert {
        tool["source"]["type"]
        for group in result["groups"]
        for tool in group["tools"]
    } == {"builtin"}
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


async def test_tool_availability_exposes_only_transcription_policy(monkeypatch) -> None:
    settings = ToolSettings(transcribe_audio={"policy": "ask"})
    monkeypatch.setattr(
        "mato_loop_backend.api.routes.tool_settings.load_effective_tools",
        AsyncMock(return_value=settings),
    )

    result = await get_tool_availability(
        SimpleNamespace(id=uuid4(), is_admin=False), _db()
    )

    assert result == {
        "webFetch": {"enabled": True, "defaultPolicy": "auto_public"},
        "transcribeAudio": {"policy": "ask"},
    }


async def test_personal_tool_settings_gate_and_redact_private_origins(monkeypatch) -> None:
    user = SimpleNamespace(id=uuid4(), is_admin=False)
    instance = ToolSettings(
        personal_settings_enabled=False,
        web_fetch={"allowed_private_origins": ["https://private.example"]},
    )
    monkeypatch.setattr(SettingsStore, "load", AsyncMock(return_value=instance))

    assert await get_personal_tool_settings(user, _db()) == {"enabled": False}
    with pytest.raises(ApiError, match="tool.personalSettingsDisabled"):
        await get_tool_settings(_db(), scope="personal", user=user)

    instance.personal_settings_enabled = True
    monkeypatch.setattr(
        "mato_loop_backend.api.routes.tool_settings.load_effective_tools",
        AsyncMock(return_value=instance),
    )
    result = await get_tool_settings(_db(), scope="personal", user=user)
    assert result["webFetch"]["allowedPrivateOrigins"] == []


async def test_personal_tool_settings_can_be_enabled_and_reset(monkeypatch) -> None:
    user = SimpleNamespace(id=uuid4(), is_admin=True)
    integration_id = uuid4()
    instance = ToolSettings(personal_settings_enabled=False)
    personal = UserToolSettings(
        policies={"filesystem.delete": "auto"},
        integration_policies={str(integration_id): {"mail.read": "disabled"}},
    )
    load = AsyncMock(return_value=instance)
    save = AsyncMock()
    monkeypatch.setattr(SettingsStore, "load", load)
    monkeypatch.setattr(SettingsStore, "load_optional", AsyncMock(return_value=personal))
    monkeypatch.setattr(SettingsStore, "save", save)
    monkeypatch.setattr(
        "mato_loop_backend.api.routes.tool_settings.load_effective_tools",
        AsyncMock(return_value=instance),
    )
    db = _db()

    assert await set_personal_tool_settings_enabled(
        PersonalSettingsEnabledRequest(enabled=True), db, user
    ) == {"enabled": True}
    result = await reset_personal_tool_settings(db, user=user)

    assert result["personalSettingsEnabled"] is True
    assert personal.personal_tool_defaults_enabled is False
    assert personal.integration_policies[str(integration_id)] == {
        "mail.read": "disabled"
    }
    save.assert_any_await(db, personal, user_id=user.id)
    assert db.commit.await_count == 2


async def test_put_tool_settings_canonicalizes_and_persists_origins(monkeypatch) -> None:
    save = AsyncMock()
    existing = ToolSettings()
    load = AsyncMock(return_value=existing)
    monkeypatch.setattr(SettingsStore, "load", load)
    monkeypatch.setattr(SettingsStore, "save", save)
    db = _db()
    payload = ToolSettingsRequest.model_validate(
        {
            "policies": {"filesystem.delete": "ask"},
            "shellPrefixes": {
                "read": [["cat"]],
                "write": [["git", "status"]],
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
    assert saved is existing
    assert saved.settings_key == "tools"
    assert saved.web_fetch.allowed_private_origins == result["webFetch"][
        "allowedPrivateOrigins"
    ]
    assert saved.web_fetch.user_agent == "ConfiguredBrowser/1.0"
    assert saved.shell_prefixes.read == [["cat"]]
    assert saved.shell_prefixes.write == [["git", "status"]]
    assert result["shellPrefixes"] == {
        "read": [["cat"]],
        "write": [["git", "status"]],
    }
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
