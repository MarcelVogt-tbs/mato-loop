from unittest.mock import AsyncMock

import pytest

from mato_loop_backend.settings import SettingsStore

_load_optional = SettingsStore.load_optional


@pytest.fixture(autouse=True)
def no_personal_settings_by_default(monkeypatch):
    async def load_optional(session, settings_type, **kwargs):
        if isinstance(session, AsyncMock):
            record = session.scalar.return_value
            if isinstance(record, AsyncMock) or (
                record is not None and not hasattr(record, "data")
            ):
                return None
        return await _load_optional(session, settings_type, **kwargs)

    monkeypatch.setattr(SettingsStore, "load_optional", load_optional)
