from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import Response
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.api.errors import ApiError
from mato_loop_backend.api.routes import auth as auth_routes
from mato_loop_backend.auth import local as local_auth
from mato_loop_backend.models import LoginSession, UserAccount
from mato_loop_backend.security import create_login_session, hash_token, require_admin, require_user
from mato_loop_backend.settings import AgentProfilesSettings, GeneralSettings, SettingsStore


def _user(*, active: bool = True, admin: bool = False) -> UserAccount:
    return UserAccount(
        id=uuid4(),
        username="test",
        password_hash="hash",
        is_admin=admin,
        is_active=active,
    )


async def test_setup_locks_before_check_and_creates_first_admin(monkeypatch) -> None:
    events = []
    db = AsyncMock(spec=AsyncSession)
    db.execute.side_effect = lambda _statement: events.append("lock")
    db.scalar.side_effect = lambda _statement: events.append("count") or 0
    save = AsyncMock(side_effect=lambda _db, settings: events.append(settings.settings_key))
    monkeypatch.setattr(SettingsStore, "save", save)
    monkeypatch.setattr(SettingsStore, "load", AsyncMock(return_value=GeneralSettings()))
    monkeypatch.setattr(SettingsStore, "load_optional", AsyncMock(return_value=None))
    db.commit.side_effect = lambda: events.append("commit")
    monkeypatch.setattr(auth_routes, "hash_password", lambda _password: "hashed")
    monkeypatch.setattr(
        auth_routes,
        "create_login_session",
        AsyncMock(return_value=("token", datetime(2030, 1, 1, tzinfo=UTC))),
    )
    response = Response()

    result = await auth_routes.setup(
        auth_routes.CredentialsRequest(username=" admin ", password="long-password"),
        response,
        db,
    )

    user = db.add.call_args.args[0]
    assert events == ["lock", "count", "agent-profiles", "tools", "commit"]
    save.assert_any_await(db, AgentProfilesSettings())
    tools = save.await_args_list[1].args[1]
    assert (tools.private_permission_preset, tools.public_permission_preset, tools.private_permissions.policies, tools.public_permissions.policies) == ("ask_changes", "ask_all", {}, {})
    assert user.username == "admin"
    assert user.is_admin and user.is_active
    assert result["isAdmin"] is True
    assert "token" in response.headers["set-cookie"]
    db.commit.assert_awaited_once()


async def test_setup_rejects_whitespace_before_locking() -> None:
    db = AsyncMock(spec=AsyncSession)

    with pytest.raises(ApiError) as exc_info:
        await auth_routes.setup(
            auth_routes.CredentialsRequest(username=" ", password="long-password"),
            Response(),
            db,
        )

    assert exc_info.value.code == "validation.required"
    db.execute.assert_not_awaited()


async def test_setup_lock_serializes_completed_setup(monkeypatch) -> None:
    db = AsyncMock(spec=AsyncSession)
    db.scalar.return_value = 1
    save = AsyncMock()
    monkeypatch.setattr(SettingsStore, "save", save)

    with pytest.raises(ApiError) as exc_info:
        await auth_routes.setup(
            auth_routes.CredentialsRequest(username="admin", password="long-password"),
            Response(),
            db,
        )

    assert exc_info.value.code == "auth.setupCompleted"
    assert "pg_advisory_xact_lock" in str(db.execute.await_args.args[0])
    save.assert_not_awaited()
    db.commit.assert_not_awaited()


async def test_login_accepts_short_password_as_invalid_credentials(monkeypatch) -> None:
    authenticate = AsyncMock(return_value=None)
    monkeypatch.setattr(auth_routes.LocalAuthProvider, "authenticate", authenticate)
    db = AsyncMock(spec=AsyncSession)

    with pytest.raises(ApiError) as exc_info:
        await auth_routes.login(
            auth_routes.LoginRequest(username="test", password="short"),
            Response(),
            db,
        )

    assert exc_info.value.code == "auth.invalidCredentials"
    authenticate.assert_awaited_once_with(db, "test", "short")


async def test_local_login_matches_username_case_insensitively(monkeypatch) -> None:
    user = _user()
    db = AsyncMock(spec=AsyncSession)
    db.scalar.return_value = user
    monkeypatch.setattr(local_auth, "verify_password", lambda _password, _hash: True)

    assert await local_auth.LocalAuthProvider.authenticate(db, " TEST ", "password") is user

    statement = db.scalar.await_args.args[0]
    compiled = statement.compile(dialect=postgresql.dialect())
    assert "lower(user_accounts.username) = lower(" in str(compiled)
    assert "TEST" in compiled.params.values()


async def test_session_issuance_rejects_inactive_user() -> None:
    db = AsyncMock(spec=AsyncSession)

    with pytest.raises(ApiError) as exc_info:
        await create_login_session(db, _user(active=False))

    assert exc_info.value.code == "auth.accountDisabled"
    db.add.assert_not_called()


async def test_session_issuance_is_provider_neutral() -> None:
    db = AsyncMock(spec=AsyncSession)
    user = _user()

    token, expires_at = await create_login_session(db, user)

    login = db.add.call_args.args[0]
    assert token
    assert expires_at > datetime.now(UTC)
    assert isinstance(login, LoginSession)
    assert login.user_id == user.id


async def test_require_user_rejects_inactive_account() -> None:
    db = AsyncMock(spec=AsyncSession)
    db.scalar.return_value = LoginSession(
        user_id=uuid4(), token_hash="hash", expires_at=datetime(2030, 1, 1, tzinfo=UTC)
    )
    db.get.return_value = _user(active=False)

    with pytest.raises(ApiError) as exc_info:
        await require_user(db=db, session_token="token")

    assert exc_info.value.code == "auth.accountDisabled"


async def test_require_admin_rejects_non_admin() -> None:
    with pytest.raises(ApiError) as exc_info:
        await require_admin(_user(admin=False))

    assert exc_info.value.code == "auth.adminRequired"


async def test_auth_options_exposes_only_local_provider() -> None:
    assert await auth_routes.auth_options() == {
        "providers": [{"id": "local", "type": "local", "name": "Lokales Konto"}]
    }


async def test_debug_token_activates_diagnostics_and_is_stored_hashed(monkeypatch) -> None:
    user = _user(admin=True)
    db = AsyncMock(spec=AsyncSession)
    monkeypatch.setattr(auth_routes.secrets, "token_urlsafe", lambda _size: "plain-token")
    response = Response()

    result = await auth_routes.create_debug_token(response, user, db)

    assert result["token"] == "plain-token"
    assert user.debug_token_hash == hash_token("plain-token")
    assert user.debug_token_hash != result["token"]
    assert result["createdAt"] == user.debug_token_created_at.isoformat()
    assert response.headers["cache-control"] == "no-store"
    db.commit.assert_awaited_once()


async def test_revoke_debug_token_deactivates_diagnostics() -> None:
    user = _user(admin=True)
    user.debug_token_hash = "hash"
    user.debug_token_created_at = datetime.now(UTC)
    db = AsyncMock(spec=AsyncSession)

    response = await auth_routes.revoke_debug_token(user, db)

    assert response.status_code == 204
    assert user.debug_token_hash is None
    assert user.debug_token_created_at is None
    db.commit.assert_awaited_once()
