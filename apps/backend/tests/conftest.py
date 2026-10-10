from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from mato_loop_backend.settings import AgentRuntimeSettings, SettingsStore
from mato_loop_backend.settings.models import ModelDefaultsSettings, UserModelDefaultsSettings

_load = SettingsStore.load
_load_optional = SettingsStore.load_optional


@pytest.fixture
def skill_library(monkeypatch, tmp_path):
    import mato_loop_backend.skill_storage as storage
    from mato_loop_backend.settings import DEFAULT_SKILLS
    from mato_loop_backend.skills import default_package

    monkeypatch.setattr(storage, "get_runtime_config", lambda: SimpleNamespace(skills_dir=tmp_path / "skills"))
    for skill_id in DEFAULT_SKILLS:
        storage.SkillStorage().publish(None, skill_id, default_package(skill_id))
    return storage.SkillStorage()


def pytest_addoption(parser):
    parser.addoption("--subagent-db", action="store_true", help="Run native session PostgreSQL integration tests")
    parser.addoption("--memory-db", action="store_true", help="Run memory approval PostgreSQL integration tests")
    parser.addoption(
        "--profile-migration-db",
        action="store_true",
        help="Run the agent profile migration against a temporary PostgreSQL database",
    )
    parser.addoption("--debug-db", action="store_true", help="Run debug MCP PostgreSQL integration tests")


@pytest.fixture
def mock_db_settings(monkeypatch):
    """Opt in to default settings when a test uses an incomplete database mock."""
    async def load_optional(session, settings_type, **kwargs):
        if isinstance(session, AsyncMock):
            record = session.scalar.return_value
            if settings_type in (ModelDefaultsSettings, UserModelDefaultsSettings) and getattr(record, "key", None) != settings_type.settings_key:
                return None
            if isinstance(record, AsyncMock) or (
                record is not None and not hasattr(record, "data")
            ):
                return None
        return await _load_optional(session, settings_type, **kwargs)

    async def load(session, settings_type, **kwargs):
        if (
            settings_type is AgentRuntimeSettings
            and isinstance(session, AsyncMock)
            and isinstance(session.scalar.return_value, AsyncMock)
        ):
            return AgentRuntimeSettings()
        return await _load(session, settings_type, **kwargs)

    monkeypatch.setattr(SettingsStore, "load_optional", load_optional)
    monkeypatch.setattr(SettingsStore, "load", load)
