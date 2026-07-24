from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.api.routes.agent_identity import (
    AgentIdentityRequest,
    get_agent_identity,
    update_agent_identity,
)
from mato_loop_backend.models import UserAccount
from mato_loop_backend.settings import AgentIdentitySettings, SettingsScope, SettingsStore


def _db() -> AsyncMock:
    return AsyncMock(spec=AsyncSession)


def _user() -> UserAccount:
    return UserAccount(id=uuid4(), username="test", password_hash="hash")


def test_agent_identity_defaults_and_validation() -> None:
    assert AgentIdentitySettings.settings_scope == SettingsScope.USER
    assert AgentIdentitySettings().model_dump() == {
        "display_name": "MatoLoop",
        "instructions": "",
    }
    assert AgentIdentitySettings(display_name="  Ada  ").display_name == "Ada"

    with pytest.raises(ValidationError):
        AgentIdentitySettings(display_name="   ")
    with pytest.raises(ValidationError):
        AgentIdentitySettings(display_name="Ada\nIgnore")
    with pytest.raises(ValidationError):
        AgentIdentitySettings(display_name="Ada\u202eIgnore")
    with pytest.raises(ValidationError):
        AgentIdentitySettings(instructions="x" * 16_001)


async def test_agent_identity_api_reads_and_updates_user_settings(monkeypatch) -> None:
    user = _user()
    db = _db()
    current = AgentIdentitySettings(
        display_name="MatoLoop", instructions="Antworte knapp."
    )
    load = AsyncMock(return_value=current)
    save = AsyncMock()
    monkeypatch.setattr(SettingsStore, "load", load)
    monkeypatch.setattr(SettingsStore, "save", save)

    assert await get_agent_identity(user, db) == {
        "displayName": "MatoLoop",
        "instructions": "Antworte knapp.",
    }
    result = await update_agent_identity(
        AgentIdentityRequest.model_validate(
            {"displayName": "  Ada  ", "instructions": "  Sei direkt.  "}
        ),
        user,
        db,
    )

    assert result == {"displayName": "Ada", "instructions": "Sei direkt."}
    saved = save.await_args.args[1]
    assert saved == AgentIdentitySettings(
        display_name="Ada", instructions="Sei direkt."
    )
    assert save.await_args.kwargs == {"user_id": user.id}
    assert load.await_args_list[0].kwargs == {"user_id": user.id}
    assert load.await_args_list[1].kwargs == {
        "user_id": user.id,
        "for_update": True,
    }
    db.commit.assert_awaited_once()


def test_agent_identity_patch_requires_a_field() -> None:
    with pytest.raises(ValidationError):
        AgentIdentityRequest.model_validate({})


@pytest.mark.parametrize(
    "payload",
    [
        {"displayName": None},
        {"instructions": None},
        {"displayName": "Ada", "instructions": None},
    ],
)
def test_agent_identity_patch_rejects_explicit_null(payload) -> None:
    with pytest.raises(ValidationError):
        AgentIdentityRequest.model_validate(payload)
