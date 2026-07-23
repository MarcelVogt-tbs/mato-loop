from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.agent.tools import available_tool_schemas
from mato_loop_backend.api.errors import ApiError
from mato_loop_backend.api.routes.integrations import (
    SearchIntegrationRequest,
    create_search_integration,
    delete_search_integration,
    update_search_integration,
)
from mato_loop_backend.api.routes.sessions import SessionUpdateRequest, update_session
from mato_loop_backend.integrations import search_available
from mato_loop_backend.models import ChatSession, UserAccount, WebSearchPolicy
from mato_loop_backend.settings import AiSettings, SearchSettings, SettingsStore
from mato_loop_backend.settings.models import ModelConfig, SearchProviderConfig


def _request(*, name: str = "SearXNG", enabled: bool = True) -> SearchIntegrationRequest:
    return SearchIntegrationRequest(
        integration_type="search",
        provider_type="searxng",
        name=name,
        base_url="https://search.example.com",
        enabled=enabled,
    )


async def test_first_search_integration_becomes_default(monkeypatch) -> None:
    settings = SearchSettings()
    db = AsyncMock(spec=AsyncSession)
    monkeypatch.setattr(SettingsStore, "load", AsyncMock(return_value=settings))
    monkeypatch.setattr(SettingsStore, "save", AsyncMock())

    result = await create_search_integration(_request(), db)

    provider = settings.providers[0]
    assert settings.default_provider_id == provider.id
    assert settings.enabled is True
    assert result["effectiveEnabled"] is True
    assert result["defaultIntegrationId"] == str(provider.id)
    db.commit.assert_awaited_once()


async def test_disabling_default_selects_next_enabled_integration(monkeypatch) -> None:
    first = SearchProviderConfig(
        name="First",
        adapter_type="searxng",
        config={"base_url": "https://first.example.com"},
    )
    second = SearchProviderConfig(
        name="Second",
        adapter_type="searxng",
        config={"base_url": "https://second.example.com"},
    )
    settings = SearchSettings(
        providers=[first, second],
        default_provider_id=first.id,
    )
    db = AsyncMock(spec=AsyncSession)
    monkeypatch.setattr(SettingsStore, "load", AsyncMock(return_value=settings))
    monkeypatch.setattr(SettingsStore, "save", AsyncMock())

    result = await update_search_integration(first.id, _request(name="First", enabled=False), db)

    assert settings.default_provider_id == second.id
    assert result["defaultIntegrationId"] == str(second.id)
    assert result["effectiveEnabled"] is True


async def test_deleting_last_search_integration_disables_forced_search(monkeypatch) -> None:
    provider = SearchProviderConfig(
        name="Only",
        adapter_type="searxng",
        config={"base_url": "https://search.example.com"},
    )
    settings = SearchSettings(providers=[provider], default_provider_id=provider.id)
    db = AsyncMock(spec=AsyncSession)
    monkeypatch.setattr(SettingsStore, "load", AsyncMock(return_value=settings))
    monkeypatch.setattr(SettingsStore, "save", AsyncMock())

    await delete_search_integration(provider.id, db)

    assert settings.providers == []
    assert settings.default_provider_id is None
    assert settings.enabled is False
    db.execute.assert_awaited_once()


def test_web_search_tool_requires_effective_integration() -> None:
    unavailable = SearchSettings(enabled=False)
    provider = SearchProviderConfig(
        name="Search",
        adapter_type="searxng",
        config={"base_url": "https://search.example.com"},
    )
    available = SearchSettings(providers=[provider], default_provider_id=provider.id)

    assert search_available(unavailable) is False
    assert all(
        tool["name"] != "web_search"
        for tool in available_tool_schemas(unavailable, "read-only")
    )
    assert any(
        tool["name"] == "web_search"
        for tool in available_tool_schemas(available, "read-only")
    )
    assert all(
        tool["name"] != "web_search"
        for tool in available_tool_schemas(
            available, "read-only", web_search_enabled=False
        )
    )


def test_agent_mode_controls_write_tools() -> None:
    settings = SearchSettings(enabled=False)
    read_tools = {
        tool["name"] for tool in available_tool_schemas(settings, "read-only")
    }
    write_tools = {
        tool["name"] for tool in available_tool_schemas(settings, "read-write")
    }

    assert {"list_directory", "read_file"}.issubset(read_tools)
    assert {"write_file", "create_directory", "delete_path"}.isdisjoint(read_tools)
    assert {"write_file", "create_directory", "delete_path"}.issubset(write_tools)


def test_webfetch_tool_has_explicit_availability_independent_of_search_and_workspace() -> None:
    settings = SearchSettings(enabled=False)

    default_tools = {
        tool["name"] for tool in available_tool_schemas(settings, "read-only")
    }
    read_tools = {
        tool["name"]
        for tool in available_tool_schemas(settings, "read-only", webfetch_enabled=True)
    }
    write_tools = {
        tool["name"]
        for tool in available_tool_schemas(settings, "read-write", webfetch_enabled=True)
    }

    assert "webfetch" not in default_tools
    assert "webfetch" in read_tools
    assert "webfetch" in write_tools
    assert "web_search" not in read_tools


async def test_session_cannot_force_unavailable_search(monkeypatch) -> None:
    user = UserAccount(id=uuid4(), username="test", password_hash="hash")
    session_id = uuid4()
    model = ModelConfig(provider_id=uuid4(), model_id="test-model")
    chat_session = ChatSession(
        id=session_id,
        user_id=user.id,
        title="Test",
        model_config_id=model.id,
        web_search_policy=WebSearchPolicy.AVAILABLE.value,
        memory_mode="disabled",
        workspace_access="read-only",
    )
    db = AsyncMock(spec=AsyncSession)
    db.get.return_value = chat_session
    db.scalar.return_value = None
    async def load_settings(_db, settings_type, **_kwargs):
        if settings_type is AiSettings:
            return AiSettings(models=[model])
        return SearchSettings()

    monkeypatch.setattr(SettingsStore, "load", load_settings)

    with pytest.raises(ApiError) as exc_info:
        await update_session(
            session_id,
            SessionUpdateRequest(web_search_policy=WebSearchPolicy.REQUIRED),
            user,
            db,
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.code == "integration.searchUnavailable"
    assert chat_session.web_search_policy == WebSearchPolicy.AVAILABLE
