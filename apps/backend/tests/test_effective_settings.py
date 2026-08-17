from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.api.errors import ApiError
from mato_loop_backend.api.routes.integrations import _load_scoped_integrations
from mato_loop_backend.api.routes.tool_settings import _load_scoped_tools
from mato_loop_backend.settings import (
    AiSettings,
    IntegrationInstance,
    IntegrationSettings,
    SettingsScope,
    SettingsStore,
    ToolSettings,
    UserAiSettings,
    UserIntegrationSettings,
    UserToolSettings,
    WebFetchSettings,
    load_effective_ai,
    load_effective_integrations,
    load_effective_tools,
)
from mato_loop_backend.settings.models import LlmProviderConfig, ModelConfig


def test_personal_settings_models_and_instance_flags_have_safe_defaults() -> None:
    assert AiSettings().personal_providers_enabled is True
    assert IntegrationSettings().personal_integrations_enabled is True
    assert IntegrationSettings().allowed_personal_adapter_types == {
        "searxng",
        "caldav",
        "bookstack",
        "git",
        "openscad",
    }
    assert ToolSettings().personal_settings_enabled is True
    assert UserAiSettings.settings_scope == SettingsScope.USER
    assert UserIntegrationSettings.settings_scope == SettingsScope.USER
    assert UserToolSettings.settings_scope == SettingsScope.USER


async def test_effective_ai_combines_providers_and_uses_only_effective_personal_defaults(
    monkeypatch,
) -> None:
    user_id = uuid4()
    instance_provider = LlmProviderConfig(name="Instance", adapter_type="openai-compatible")
    instance_model = ModelConfig(provider_id=instance_provider.id, model_id="instance")
    personal_provider = LlmProviderConfig(name="Personal", adapter_type="openai-compatible")
    personal_model = ModelConfig(provider_id=personal_provider.id, model_id="personal")
    unavailable_model = ModelConfig(
        provider_id=personal_provider.id, model_id="disabled", enabled=False
    )
    instance = AiSettings(
        providers=[instance_provider],
        models=[instance_model],
        default_model_id=instance_model.id,
        compaction_model_id=instance_model.id,
        title_generation_model_id=instance_model.id,
    )
    personal = UserAiSettings(
        providers=[personal_provider],
        models=[personal_model, unavailable_model],
        default_model_id=personal_model.id,
        compaction_model_id=unavailable_model.id,
    )
    monkeypatch.setattr(SettingsStore, "load", AsyncMock(return_value=instance))
    monkeypatch.setattr(SettingsStore, "load_optional", AsyncMock(return_value=personal))

    effective = await load_effective_ai(AsyncMock(spec=AsyncSession), user_id)

    assert [provider.owner_id for provider in effective.providers] == [None, user_id]
    assert effective.default_model_id == personal_model.id
    assert effective.compaction_model_id == instance_model.id
    assert effective.title_generation_model_id == instance_model.id
    assert instance_provider.owner_id is None
    assert personal_provider.owner_id is None
    assert "owner_id" not in effective.providers[1].model_dump()


async def test_effective_integrations_adds_only_allowed_personal_instances(monkeypatch) -> None:
    user_id = uuid4()
    agent_id = uuid4()
    global_item = IntegrationInstance(
        name="Global Git",
        integration_type="repository",
        adapter_type="git",
        execution_agent_id=agent_id,
    )
    allowed = IntegrationInstance(
        name="Personal Calendar",
        integration_type="calendar",
        adapter_type="caldav",
        execution_agent_id=agent_id,
    )
    blocked = IntegrationInstance(
        name="Personal Other",
        integration_type="other",
        adapter_type="other",
    )
    instance = IntegrationSettings(instances=[global_item])
    personal = UserIntegrationSettings(instances=[allowed, blocked])
    monkeypatch.setattr(SettingsStore, "load", AsyncMock(return_value=instance))
    monkeypatch.setattr(SettingsStore, "load_optional", AsyncMock(return_value=personal))
    db = AsyncMock(spec=AsyncSession)
    agents = type("Rows", (), {"all": lambda _self: [agent_id]})()
    db.scalars.return_value = agents

    effective = await load_effective_integrations(db, user_id)

    assert [item.id for item in effective.instances] == [global_item.id, allowed.id]
    assert [item.owner_id for item in effective.instances] == [None, user_id]
    assert "execution_agents.user_id" in str(db.scalars.await_args.args[0])


async def test_effective_integrations_excludes_unowned_agent_bindings(monkeypatch) -> None:
    user_id = uuid4()
    global_agent = IntegrationInstance(
        name="Legacy global Git",
        integration_type="git",
        adapter_type="git",
        execution_agent_id=uuid4(),
    )
    personal_agent = IntegrationInstance(
        name="Personal Git",
        integration_type="git",
        adapter_type="git",
        execution_agent_id=uuid4(),
    )
    instance = IntegrationSettings(instances=[global_agent])
    personal = UserIntegrationSettings(instances=[personal_agent])
    monkeypatch.setattr(SettingsStore, "load", AsyncMock(return_value=instance))
    monkeypatch.setattr(SettingsStore, "load_optional", AsyncMock(return_value=personal))
    db = AsyncMock(spec=AsyncSession)
    db.scalars.return_value = type("Rows", (), {"all": lambda _self: []})()

    effective = await load_effective_integrations(db, user_id)

    assert effective.instances == []


async def test_effective_ai_personal_default_can_select_instance_model(monkeypatch) -> None:
    user_id = uuid4()
    provider = LlmProviderConfig(name="Instance", adapter_type="openai-compatible")
    model = ModelConfig(provider_id=provider.id, model_id="instance")
    instance = AiSettings(providers=[provider], models=[model])
    personal = UserAiSettings(default_model_id=model.id)
    monkeypatch.setattr(SettingsStore, "load", AsyncMock(return_value=instance))
    monkeypatch.setattr(SettingsStore, "load_optional", AsyncMock(return_value=personal))

    effective = await load_effective_ai(AsyncMock(spec=AsyncSession), user_id)

    assert effective.default_model_id == model.id


async def test_effective_tools_uses_personal_snapshot_with_instance_web_fetch_limits(
    monkeypatch,
) -> None:
    instance = ToolSettings(
        policies={"instance": "ask"},
        web_fetch=WebFetchSettings(
            enabled=False,
            user_agent="Instance Agent",
            max_response_mib=7,
            default_policy="ask",
            allowed_private_origins=["https://internal.example"],
        ),
    )
    personal = UserToolSettings(
        policies={"personal": "auto"},
        web_fetch=WebFetchSettings(
            enabled=True,
            user_agent="Personal Agent",
            max_response_mib=99,
            default_policy="auto_allowed",
            allowed_private_origins=["https://personal.example"],
        ),
    )
    monkeypatch.setattr(SettingsStore, "load", AsyncMock(return_value=instance))
    monkeypatch.setattr(SettingsStore, "load_optional", AsyncMock(return_value=personal))

    effective = await load_effective_tools(AsyncMock(spec=AsyncSession), uuid4())

    assert effective.policies == {"personal": "auto"}
    assert effective.web_fetch.enabled is True
    assert effective.web_fetch.default_policy == "auto_allowed"
    assert effective.web_fetch.user_agent == "Instance Agent"
    assert effective.web_fetch.max_response_mib == 7
    assert effective.web_fetch.allowed_private_origins == ["https://internal.example"]


async def test_scoped_management_enforces_admin_and_personal_gates(monkeypatch) -> None:
    user = type("User", (), {"id": uuid4(), "is_admin": False})()
    integrations = IntegrationSettings(
        personal_integrations_enabled=True,
        allowed_personal_adapter_types={"caldav"},
    )
    tools = ToolSettings(personal_settings_enabled=False)

    async def load(_db, settings_type, **_kwargs):
        return integrations if settings_type is IntegrationSettings else tools

    monkeypatch.setattr(SettingsStore, "load", AsyncMock(side_effect=load))
    db = AsyncMock(spec=AsyncSession)

    with pytest.raises(ApiError, match="auth.adminRequired"):
        await _load_scoped_integrations(
            db, "instance", user, mutation=True
        )
    with pytest.raises(ApiError, match="auth.adminRequired"):
        await _load_scoped_integrations(db, "instance", user)
    with pytest.raises(ApiError, match="auth.adminRequired"):
        await _load_scoped_tools(db, "instance", user)
    with pytest.raises(ApiError, match="integration.personalAdapterDisabled"):
        await _load_scoped_integrations(
            db, "personal", user, adapter_type="git"
        )
    with pytest.raises(ApiError, match="tool.personalSettingsDisabled"):
        await _load_scoped_tools(db, "personal", user, mutation=True)


async def test_personal_tool_scope_starts_from_instance_snapshot(monkeypatch) -> None:
    user = type("User", (), {"id": uuid4(), "is_admin": False})()
    instance = ToolSettings(
        policies={"filesystem.delete": "disabled"},
        personal_settings_enabled=True,
    )
    monkeypatch.setattr(SettingsStore, "load", AsyncMock(return_value=instance))

    personal, owner_id = await _load_scoped_tools(
        AsyncMock(spec=AsyncSession), "personal", user, for_update=True
    )

    assert isinstance(personal, UserToolSettings)
    assert personal.policies == instance.policies
    assert owner_id == user.id


async def test_settings_store_can_load_absent_optional_row_and_delete_scope() -> None:
    user_id = uuid4()
    db = AsyncMock(spec=AsyncSession)
    db.scalar.return_value = None

    assert (
        await SettingsStore.load_optional(db, UserToolSettings, user_id=user_id) is None
    )
    await SettingsStore.delete(db, UserToolSettings, user_id=user_id)

    delete_statement = db.execute.await_args_list[-1].args[0]
    assert "DELETE FROM settings" in str(delete_statement)
    assert user_id in delete_statement.compile().params.values()
