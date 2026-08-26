from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.api.routes.user_profile import (
    UserProfileChanges,
    get_user_profile,
    update_user_profile,
)
from mato_loop_backend.models import UserAccount
from mato_loop_backend.settings import (
    SettingsScope,
    SettingsStore,
    TrustLevel,
    UserProfileFact,
    UserProfileSettings,
)
from mato_loop_backend.user_profile import (
    normalize_profile_content,
    user_profile_content_hashes,
    user_profile_runtime_payload,
)


def _user() -> UserAccount:
    return UserAccount(id=uuid4(), username="test", password_hash="hash")


def test_user_profile_defaults_overrides_serialization_and_hashes() -> None:
    assert UserProfileSettings.settings_scope == SettingsScope.USER
    profile = UserProfileSettings(
        preferred_name={
            "both": "  User's name is Ada.  ",
            "privateOnly": "User's full name is Ada Lovelace.",
        },
        primary_language={"publicOnly": "User's primary language is English."},
    )

    assert user_profile_runtime_payload(profile, TrustLevel.PUBLIC) == {
        "preferredName": "User's name is Ada.",
        "formOfAddress": None,
        "pronouns": None,
        "primaryLanguage": "User's primary language is English.",
        "primaryResidence": None,
    }
    assert user_profile_runtime_payload(profile, TrustLevel.PRIVATE)["preferredName"] == (
        "User's full name is Ada Lovelace."
    )
    assert user_profile_runtime_payload(profile, TrustLevel.PRIVATE)["primaryLanguage"] is None
    assert user_profile_content_hashes(profile) == {
        normalize_profile_content("user'S   NAME is ada.")[1],
        normalize_profile_content("User's full name is Ada Lovelace.")[1],
        normalize_profile_content(
            "User's primary language is English."
        )[1],
    }


def test_user_profile_rejects_blank_or_oversized_facts() -> None:
    with pytest.raises(ValidationError):
        UserProfileFact(both="   ")
    with pytest.raises(ValidationError):
        UserProfileFact(private_only="x" * 20_001)


async def test_user_profile_api_merges_explicit_clears(
    monkeypatch,
) -> None:
    user = _user()
    db = AsyncMock(spec=AsyncSession)
    current = UserProfileSettings(
        preferred_name={"both": "User's name is Ada.", "privateOnly": "Private name."},
        pronouns={"both": "User uses she/her pronouns."},
    )
    load = AsyncMock(return_value=current)
    save = AsyncMock()
    monkeypatch.setattr(SettingsStore, "load", load)
    monkeypatch.setattr(SettingsStore, "save", save)

    assert (await get_user_profile(user, db))["preferredName"]["privateOnly"] == "Private name."
    result = await update_user_profile(
        UserProfileChanges.model_validate(
            {
                "preferredName": {
                    "both": "User's name is Grace.",
                    "privateOnly": None,
                },
                "pronouns": None,
            }
        ),
        user,
        db,
    )

    assert result["preferredName"] == {
        "both": "User's name is Grace.",
        "privateOnly": None,
        "publicOnly": None,
    }
    assert result["pronouns"] == {
        "both": None,
        "privateOnly": None,
        "publicOnly": None,
    }
    load.assert_awaited_with(db, UserProfileSettings, user_id=user.id, for_update=True)
    save.assert_awaited_once_with(db, save.await_args.args[1], user_id=user.id)
    db.commit.assert_awaited_once()


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"preferredName": {}},
    ],
)
def test_user_profile_patch_requires_an_actual_change(payload) -> None:
    with pytest.raises(ValidationError):
        UserProfileChanges.model_validate(payload)
