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
    tool_configuration_payload,
    update_tool_settings,
)
from mato_loop_backend.settings import (
    AgentProfilePermissions,
    IntegrationSettings,
    SettingsStore,
    ToolExecutionPolicy,
    ToolSettings,
    TrustLevel,
    UserToolSettings,
    WebFetchSettings,
    build_agent_profile,
    default_agent_profiles,
    plan_agent_profile,
)


def _db() -> AsyncMock:
    return AsyncMock(spec=AsyncSession)


async def test_nonadmin_cannot_read_instance_tool_settings(monkeypatch) -> None:
    load = AsyncMock(return_value=ToolSettings())
    monkeypatch.setattr(SettingsStore, "load", load)

    with pytest.raises(ApiError, match="auth.adminRequired"):
        await get_tool_settings(
            _db(), TrustLevel.PUBLIC, scope="instance", user=SimpleNamespace(id=uuid4(), is_admin=False)
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
        public_permissions={
            "policies": {
                "session.files.write": session_policy,
                "filesystem.write": workspace_policy,
            }
        }
    ).select_trust(TrustLevel.PUBLIC)

    tools = tool_registry.effective(
        IntegrationSettings(), tool_settings=settings
    ).tools

    assert ("write_file" in tools) is available
    assert ("create_directory" in tools) is available
    assert ("copy_path" in tools) is available
    assert ("move_path" in tools) is available


@pytest.mark.parametrize(
    ("disabled", "read_available", "copy_available", "move_available"),
    [
        ((), True, True, True),
        (("filesystem.read", "filesystem.write", "filesystem.delete"), True, True, True),
        (("session.files.read", "session.files.write"), True, True, True),
        (("session.files.read", "filesystem.write"), True, True, True),
        (("filesystem.read", "session.files.write"), True, True, True),
        (("session.files.write", "filesystem.write"), True, False, False),
        (("session.files.read", "filesystem.read"), False, False, True),
        (("session.files.write", "filesystem.delete"), True, True, False),
    ],
)
def test_root_specific_read_and_transfer_availability(
    disabled: tuple[str, ...],
    read_available: bool,
    copy_available: bool,
    move_available: bool,
) -> None:
    settings = ToolSettings(
        public_permissions={
            "policies": {
                capability: (
                    ToolExecutionPolicy.DISABLED
                    if capability in disabled
                    else ToolExecutionPolicy.AUTO
                )
                for capability in (
                    "session.files.read",
                    "filesystem.read",
                    "session.files.write",
                    "filesystem.write",
                    "filesystem.delete",
                )
            }
        }
    ).select_trust(TrustLevel.PUBLIC)

    tools = tool_registry.effective(
        IntegrationSettings(), tool_settings=settings
    ).tools

    for name in ("read", "glob", "grep", "provide_file"):
        assert (name in tools) is read_available
    assert ("copy_path" in tools) is copy_available
    assert ("move_path" in tools) is move_available
    assert ("delete_path" in tools) is ("filesystem.delete" not in disabled)


@pytest.mark.parametrize("trust", [TrustLevel.PRIVATE, TrustLevel.PUBLIC])
@pytest.mark.parametrize(
    "capability",
    ["session.files.read", "session.files.write", "network.fetch", "network.search", "skills.load"],
)
def test_auto_defaults_and_saved_policies(
    trust: TrustLevel, capability: str
) -> None:
    settings = ToolSettings().select_trust(trust)
    default = tool_registry.action_default(capability)

    assert default == ToolExecutionPolicy.AUTO
    assert resolve_tool_execution_policy(settings, capability, default).policy == default
    for policy in ToolExecutionPolicy:
        settings.permissions.policies[capability] = policy
        assert resolve_tool_execution_policy(settings, capability, default).policy == policy


@pytest.mark.parametrize("trust", [TrustLevel.PRIVATE, TrustLevel.PUBLIC])
@pytest.mark.parametrize(
    ("global_policies", "profile_policies", "expected"),
    [
        ({"filesystem.read": "ask"}, {}, "ask"),
        ({"filesystem.read": "disabled"}, {}, "disabled"),
        ({"filesystem.read": "auto"}, {}, "auto"),
        ({"filesystem.read": "disabled", "session.files.read": "auto"}, {}, "auto"),
        ({"filesystem.read": "auto", "session.files.read": "disabled"}, {}, "disabled"),
        ({}, {"filesystem.read": "disabled"}, "disabled"),
        ({"session.files.read": "disabled"}, {"filesystem.read": "auto"}, "auto"),
        ({"session.files.read": "auto"}, {"filesystem.read": "ask"}, "ask"),
        ({}, {"filesystem.read": "disabled", "session.files.read": "auto"}, "auto"),
        ({}, {"filesystem.read": "auto", "session.files.read": "disabled"}, "disabled"),
    ],
)
def test_session_file_read_inherits_saved_filesystem_policy_per_level(
    trust: TrustLevel,
    global_policies: dict[str, str],
    profile_policies: dict[str, str],
    expected: str,
) -> None:
    settings = ToolSettings(
        private_permissions={"policies": global_policies},
        public_permissions={"policies": global_policies},
    ).select_trust(trust)
    settings._agent_permissions = AgentProfilePermissions(policies=profile_policies)

    assert (
        resolve_tool_execution_policy(
            settings, "session.files.read", ToolExecutionPolicy.AUTO
        ).policy
        == expected
    )


@pytest.mark.parametrize(
    ("baseline", "profile", "expected"),
    [
        (ToolExecutionPolicy.ASK, ToolExecutionPolicy.AUTO, ToolExecutionPolicy.AUTO),
        (ToolExecutionPolicy.AUTO, ToolExecutionPolicy.ASK, ToolExecutionPolicy.ASK),
        (ToolExecutionPolicy.ASK, ToolExecutionPolicy.DISABLED, ToolExecutionPolicy.DISABLED),
        (ToolExecutionPolicy.DISABLED, ToolExecutionPolicy.AUTO, ToolExecutionPolicy.AUTO),
    ],
)
def test_agent_profile_policy_controls_available_global_capability(
    baseline: ToolExecutionPolicy,
    profile: ToolExecutionPolicy,
    expected: ToolExecutionPolicy,
) -> None:
    settings = ToolSettings(
        public_permissions={"policies": {"filesystem.read": baseline}}
    ).select_trust(TrustLevel.PUBLIC)
    settings._agent_permissions = AgentProfilePermissions(
        policies={"filesystem.read": profile}
    )

    result = resolve_tool_execution_policy(
        settings, "filesystem.read", ToolExecutionPolicy.AUTO
    )

    assert result.policy == expected


def test_agent_profile_missing_capability_inherits_live_default() -> None:
    settings = ToolSettings(
        public_permissions={"policies": {"filesystem.read": ToolExecutionPolicy.AUTO}}
    ).select_trust(TrustLevel.PUBLIC)
    settings._agent_permissions = AgentProfilePermissions()

    assert (
        resolve_tool_execution_policy(settings, "filesystem.read").policy
        == ToolExecutionPolicy.AUTO
    )


@pytest.mark.parametrize("trust", [TrustLevel.PRIVATE, TrustLevel.PUBLIC])
def test_plan_profile_disables_computer_changes(trust: TrustLevel) -> None:
    profile = plan_agent_profile()
    settings = ToolSettings().select_trust(trust)
    settings._agent_permissions = (
        profile.private_permissions
        if trust == TrustLevel.PRIVATE
        else profile.public_permissions
    )

    for capability in (
        "filesystem.write",
        "filesystem.delete",
        "shell.write",
    ):
        assert (
            resolve_tool_execution_policy(settings, capability).policy
            == ToolExecutionPolicy.DISABLED
        )
    assert (
        resolve_tool_execution_policy(settings, "session.files.write").policy
        == ToolExecutionPolicy.AUTO
    )


@pytest.mark.parametrize("trust", [TrustLevel.PRIVATE, TrustLevel.PUBLIC])
def test_only_coding_profiles_default_to_automatic_public_computer_reads(trust) -> None:
    for profile in default_agent_profiles():
        settings = ToolSettings().select_trust(trust)
        settings._agent_permissions = (
            profile.private_permissions if trust == TrustLevel.PRIVATE else profile.public_permissions
        )
        for capability in ("filesystem.read", "shell.read"):
            expected = (
                ToolExecutionPolicy.ASK
                if profile.name == "Allgemein" and trust == TrustLevel.PUBLIC
                else ToolExecutionPolicy.AUTO
            )
            assert resolve_tool_execution_policy(
                settings, capability, tool_registry.action_default(capability)
            ).policy == expected
            settings._agent_permissions.policies[capability] = ToolExecutionPolicy.ASK
            assert resolve_tool_execution_policy(
                settings, capability, tool_registry.action_default(capability)
            ).policy == ToolExecutionPolicy.ASK


@pytest.mark.parametrize("trust", [TrustLevel.PRIVATE, TrustLevel.PUBLIC])
def test_build_profile_defaults_to_auto_writes_and_preserves_overrides(trust: TrustLevel) -> None:
    profile = build_agent_profile()
    settings = ToolSettings().select_trust(trust)
    settings._agent_permissions = (
        profile.private_permissions
        if trust == TrustLevel.PRIVATE
        else profile.public_permissions
    )

    for capability in ("filesystem.write", "filesystem.delete", "shell.write"):
        assert (
            resolve_tool_execution_policy(settings, capability).policy
            == ToolExecutionPolicy.AUTO
        )
        for policy in (ToolExecutionPolicy.ASK, ToolExecutionPolicy.DISABLED):
            settings._agent_permissions.policies[capability] = policy
            assert resolve_tool_execution_policy(settings, capability).policy == policy
    assert (
        resolve_tool_execution_policy(
            settings,
            "calendar.write",
            ToolExecutionPolicy.ASK,
            integration_id=uuid4(),
        ).policy
        == ToolExecutionPolicy.ASK
    )


def test_agent_profile_overrides_scoped_defaults_but_not_hard_limits() -> None:
    integration_id = uuid4()
    settings = ToolSettings(
        public_permissions={
            "policies": {"calendar.write": ToolExecutionPolicy.ASK},
            "integrationPolicies": {
                str(integration_id): {"calendar.write": ToolExecutionPolicy.DISABLED}
            },
        },
    ).select_trust(TrustLevel.PUBLIC)
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

    settings.permissions.policies["calendar.write"] = ToolExecutionPolicy.DISABLED
    assert (
        resolve_tool_execution_policy(
            settings, "calendar.write", integration_id=integration_id
        ).policy
        == ToolExecutionPolicy.AUTO
    )


def test_agent_profile_inherits_live_resource_default() -> None:
    integration_id = uuid4()
    resource_key = "calendar:work"
    settings = ToolSettings(
        public_permissions={
            "policies": {"calendar.read": ToolExecutionPolicy.ASK},
            "resourcePolicies": {
                str(integration_id): {
                    resource_key: {"calendar.read": ToolExecutionPolicy.DISABLED}
                }
            },
        },
    ).select_trust(TrustLevel.PUBLIC)
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

    settings.permissions.resource_policies[str(integration_id)][resource_key][
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
    settings = ToolSettings(
        public_permissions={"policies": {"filesystem.read": ToolExecutionPolicy.AUTO}}
    ).select_trust(TrustLevel.PUBLIC)
    settings._agent_profile_unavailable = True

    assert (
        resolve_tool_execution_policy(settings, "filesystem.read").policy
        == ToolExecutionPolicy.DISABLED
    )


def test_agent_profile_scoped_capability_is_offered_and_resolved_for_its_target() -> None:
    integration_id = uuid4()
    settings = ToolSettings(
        public_permissions={"policies": {"mail.read": ToolExecutionPolicy.AUTO}}
    ).select_trust(TrustLevel.PUBLIC)
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


def test_agent_profile_can_override_webfetch_capability() -> None:
    settings = ToolSettings(
        public_permissions={"policies": {"network.fetch": "disabled"}}
    ).select_trust(TrustLevel.PUBLIC)
    settings._agent_permissions = AgentProfilePermissions(
        policies={"network.fetch": "auto"}
    )

    assert (
        resolve_tool_execution_policy(settings, "network.fetch").policy
        == ToolExecutionPolicy.AUTO
    )


async def test_get_tool_settings_returns_defaults(monkeypatch) -> None:
    settings = ToolSettings()
    monkeypatch.setattr(SettingsStore, "load", AsyncMock(return_value=settings))

    result = await get_tool_settings(_db(), TrustLevel.PUBLIC)

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
            "userAgent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36"
            ),
            "maxResponseMiB": 64,
            "publicAddressesOnly": True,
        },
        "generateImage": {"policy": "ask", "timeoutSeconds": 300},
        "editImage": {"policy": "ask", "timeoutSeconds": 300},
        "generateSpeech": {"policy": "ask", "timeoutSeconds": 300},
        "generateAudio": {"policy": "ask", "timeoutSeconds": 300},
        "transcribeAudio": {"policy": "ask", "timeoutSeconds": 300},
        "analyzeVideo": {"policy": "ask", "timeoutSeconds": 1800},
        "updateAgentIdentity": {"policy": "ask"},
    }
    auto_capabilities = {
        "session.files.read",
        "session.files.write",
        "network.fetch",
        "network.search",
        "skills.load",
    }
    assert {
        capability
        for capability, policy in result["policies"].items()
        if policy == "auto"
    } == auto_capabilities
    assert all(
        policy == "ask"
        for capability, policy in result["policies"].items()
        if capability not in auto_capabilities
    )
    assert "network.fetch" in result["policies"]
    assert "session.tool_result.read" in result["policies"]
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
    webfetch = next(
        tool
        for group in result["groups"]
        for tool in group["tools"]
        if tool["name"] == "webfetch"
    )
    assert {
        action["capability"]: action["available"]
        for action in webfetch["actions"]
    } == {"network.fetch": True}
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


def test_tool_configuration_uses_frontend_webfetch_field_names() -> None:
    result = tool_configuration_payload(
        ToolSettings(
            web_fetch={
                "userAgent": "MatoLoop/1.0",
                "maxResponseMiB": 32,
                "publicAddressesOnly": False,
            }
        )
    )

    assert result["webFetch"] == {
        "userAgent": "MatoLoop/1.0",
        "maxResponseMiB": 32,
        "publicAddressesOnly": False,
    }


async def test_tool_availability_exposes_hard_caps(monkeypatch) -> None:
    settings = ToolSettings(transcribe_audio={"policy": "ask"})
    monkeypatch.setattr(
        "mato_loop_backend.api.routes.tool_settings.load_effective_tools",
        AsyncMock(return_value=settings),
    )

    result = await get_tool_availability(
        SimpleNamespace(id=uuid4(), is_admin=False), _db()
    )

    assert result == {"tools": {}}


async def test_personal_tool_settings_gate(monkeypatch) -> None:
    user = SimpleNamespace(id=uuid4(), is_admin=False)
    instance = ToolSettings(personal_settings_enabled=False)
    monkeypatch.setattr(SettingsStore, "load", AsyncMock(return_value=instance))

    assert await get_personal_tool_settings(user, _db()) == {"enabled": False}
    with pytest.raises(ApiError, match="tool.personalSettingsDisabled"):
        await get_tool_settings(_db(), TrustLevel.PUBLIC, scope="personal", user=user)

    instance.personal_settings_enabled = True
    monkeypatch.setattr(
        "mato_loop_backend.api.routes.tool_settings.load_effective_tools",
        AsyncMock(return_value=instance),
    )
    result = await get_tool_settings(
        _db(), TrustLevel.PUBLIC, scope="personal", user=user
    )
    assert result["webFetch"]["maxResponseMiB"] == 64


async def test_personal_tool_settings_can_be_enabled_and_reset(monkeypatch) -> None:
    user = SimpleNamespace(id=uuid4(), is_admin=True)
    integration_id = uuid4()
    instance = ToolSettings(personal_settings_enabled=False)
    personal = UserToolSettings(
        public_permissions={
            "policies": {"filesystem.delete": "auto"},
            "integrationPolicies": {
                str(integration_id): {"mail.read": "disabled"}
            },
        },
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
    assert personal.public_permissions.integration_policies[str(integration_id)] == {
        "mail.read": "disabled"
    }
    save.assert_any_await(db, personal, user_id=user.id)
    assert db.commit.await_count == 2


async def test_put_tool_settings_saves_only_selected_branch(monkeypatch) -> None:
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
            "generateImage": {"policy": "ask", "timeoutSeconds": 600},
            "editImage": {"policy": "ask", "timeoutSeconds": 450},
            "generateSpeech": {"policy": "auto", "timeoutSeconds": 120},
            "generateAudio": {"policy": "disabled", "timeoutSeconds": 900},
            "transcribeAudio": {"policy": "ask", "timeoutSeconds": 180},
            "updateAgentIdentity": {"policy": "auto"},
        }
    )

    result = await update_tool_settings(payload, db, TrustLevel.PUBLIC)

    assert result["webFetch"]["maxResponseMiB"] == 64
    saved = save.await_args.args[1]
    assert load.await_args_list[0].args[1] is ToolSettings
    assert load.await_args_list[0].kwargs == {}
    assert load.await_args_list[1].args[1] is ToolSettings
    assert load.await_args_list[1].kwargs == {"for_update": True}
    assert saved is existing
    assert saved.settings_key == "tools"
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
    assert saved.generate_speech.policy == "ask"
    assert saved.generate_speech.timeout_seconds == 120
    assert saved.generate_audio.policy == "ask"
    assert saved.generate_audio.timeout_seconds == 900
    assert saved.transcribe_audio.policy == "ask"
    assert saved.transcribe_audio.timeout_seconds == 180
    assert saved.update_agent_identity.policy == "ask"
    assert saved.public_permissions.policies["filesystem.delete"] == "ask"
    assert saved.private_permissions.policies == {}
    assert result["policies"]["filesystem.delete"] == "ask"
    db.commit.assert_awaited_once()


@pytest.mark.parametrize("timeout", [9, 3_601, 30.5])
def test_tool_settings_reject_invalid_media_timeout(timeout: float) -> None:
    with pytest.raises(ValidationError):
        ToolSettingsRequest.model_validate(
            {
                "generateImage": {"policy": "ask", "timeoutSeconds": timeout},
            }
        )


@pytest.mark.parametrize("limit", [0, 257, 1.5])
def test_tool_settings_reject_invalid_webfetch_limit(limit: float) -> None:
    with pytest.raises(ValidationError):
        WebFetchSettings.model_validate({"maxResponseMiB": limit})


@pytest.mark.parametrize(
    "user_agent",
    ["", "   ", "Mozilla/5.0\r\nX-Injected: true", "Mözilla/5.0", "x" * 513],
)
def test_tool_settings_reject_invalid_webfetch_user_agent(user_agent: str) -> None:
    with pytest.raises(ValidationError):
        WebFetchSettings.model_validate({"userAgent": user_agent})


def test_removed_webfetch_settings_are_rejected() -> None:
    with pytest.raises(ValidationError):
        WebFetchSettings.model_validate({"enabled": True})
