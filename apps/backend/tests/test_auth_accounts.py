from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import UniqueConstraint
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.auth import (
    AuthenticatedIdentity,
    ExternalAccountUnavailableError,
    ExternalIdentityConflictError,
    FinalLoginMethodError,
    link_external_identity,
    resolve_external_identity,
    unlink_external_identity,
)
from mato_loop_backend.models import AuthenticationProvider, UserAccount, UserAuthIdentity


def _provider(*, enabled: bool = True, auto_create: bool = False) -> AuthenticationProvider:
    return AuthenticationProvider(
        id=uuid4(),
        provider_type="oidc",
        name="Test",
        enabled=enabled,
        auto_create_users=auto_create,
        configuration={},
    )


def _user(*, password_hash: str | None = "hash") -> UserAccount:
    return UserAccount(
        id=uuid4(),
        username=f"user-{uuid4().hex}",
        password_hash=password_hash,
        is_admin=False,
        is_active=True,
    )


async def test_resolve_external_identity_uses_only_provider_and_subject() -> None:
    provider = _provider()
    user = _user()
    linked = UserAuthIdentity(
        id=uuid4(), user_id=user.id, provider_id=provider.id, subject="subject"
    )
    db = AsyncMock(spec=AsyncSession)
    db.scalar.return_value = linked
    db.get.return_value = user

    resolved = await resolve_external_identity(
        db, provider, AuthenticatedIdentity(subject="subject", username="someone-else")
    )

    assert resolved is user
    assert db.scalar.await_count == 1
    db.add.assert_not_called()


async def test_resolve_external_identity_jit_requires_enabled_auto_create() -> None:
    identity = AuthenticatedIdentity(subject="subject", username="new-user")
    for provider in (_provider(enabled=False, auto_create=True), _provider(auto_create=False)):
        db = AsyncMock(spec=AsyncSession)
        db.scalar.return_value = None
        with pytest.raises(ExternalAccountUnavailableError):
            await resolve_external_identity(db, provider, identity)
        db.add.assert_not_called()


async def test_resolve_external_identity_jit_creates_without_matching_username() -> None:
    provider = _provider(auto_create=True)
    db = AsyncMock(spec=AsyncSession)
    db.scalar.side_effect = [None, None]
    added = []
    db.add.side_effect = added.append

    async def assign_id() -> None:
        if added and isinstance(added[0], UserAccount) and added[0].id is None:
            added[0].id = uuid4()

    db.flush.side_effect = assign_id

    user = await resolve_external_identity(
        db, provider, AuthenticatedIdentity(subject="subject", username="  new-user  ")
    )

    assert user.username == "new-user"
    assert user.password_hash is None
    assert added == [user, added[1]]
    assert added[1].user_id == user.id
    assert added[1].subject == "subject"


async def test_resolve_external_identity_never_merges_on_username() -> None:
    db = AsyncMock(spec=AsyncSession)
    db.scalar.side_effect = [None, uuid4()]

    with pytest.raises(ExternalAccountUnavailableError):
        await resolve_external_identity(
            db,
            _provider(auto_create=True),
            AuthenticatedIdentity(subject="new-subject", username="existing"),
        )

    db.add.assert_not_called()


async def test_link_external_identity_rejects_subject_linked_elsewhere() -> None:
    provider = _provider()
    user = _user()
    db = AsyncMock(spec=AsyncSession)
    db.scalar.return_value = UserAuthIdentity(
        id=uuid4(), user_id=uuid4(), provider_id=provider.id, subject="subject"
    )

    with pytest.raises(ExternalIdentityConflictError):
        await link_external_identity(db, user, provider, "subject")

    db.add.assert_not_called()


async def test_link_external_identity_requires_active_user_and_enabled_provider() -> None:
    db = AsyncMock(spec=AsyncSession)
    user = _user()
    user.is_active = False

    with pytest.raises(ExternalAccountUnavailableError):
        await link_external_identity(db, user, _provider(), "subject")
    with pytest.raises(ExternalAccountUnavailableError):
        await link_external_identity(db, _user(), _provider(enabled=False), "subject")

    db.scalar.assert_not_awaited()


async def test_link_external_identity_enforces_one_identity_per_provider() -> None:
    provider = _provider()
    user = _user()
    existing = UserAuthIdentity(
        id=uuid4(), user_id=user.id, provider_id=provider.id, subject="old-subject"
    )
    db = AsyncMock(spec=AsyncSession)
    db.scalar.side_effect = [None, existing]

    with pytest.raises(ExternalIdentityConflictError):
        await link_external_identity(db, user, provider, "new-subject")

    db.add.assert_not_called()


async def test_link_external_identity_is_idempotent_for_same_user() -> None:
    provider = _provider()
    user = _user()
    existing = UserAuthIdentity(
        id=uuid4(), user_id=user.id, provider_id=provider.id, subject="subject"
    )
    db = AsyncMock(spec=AsyncSession)
    db.scalar.return_value = existing

    assert await link_external_identity(db, user, provider, "subject") is existing
    db.add.assert_not_called()


async def test_unlink_external_identity_refuses_final_login_method() -> None:
    provider = _provider()
    user = _user(password_hash=None)
    linked = UserAuthIdentity(
        id=uuid4(), user_id=user.id, provider_id=provider.id, subject="subject"
    )
    db = AsyncMock(spec=AsyncSession)
    db.scalar.side_effect = [user, linked, 0]

    with pytest.raises(FinalLoginMethodError):
        await unlink_external_identity(db, user, provider)

    db.delete.assert_not_awaited()


@pytest.mark.parametrize("password_hash,other_identities", [("hash", 0), (None, 1)])
async def test_unlink_external_identity_accepts_another_login_method(
    password_hash: str | None, other_identities: int
) -> None:
    provider = _provider()
    user = _user(password_hash=password_hash)
    linked = UserAuthIdentity(
        id=uuid4(), user_id=user.id, provider_id=provider.id, subject="subject"
    )
    db = AsyncMock(spec=AsyncSession)
    db.scalar.side_effect = [user, linked, other_identities]

    assert await unlink_external_identity(db, user, provider)
    db.delete.assert_awaited_once_with(linked)
    db.flush.assert_awaited_once()


def test_identity_schema_enforces_both_uniqueness_rules() -> None:
    constraints = {
        tuple(column.name for column in constraint.columns)
        for constraint in UserAuthIdentity.__table__.constraints
        if isinstance(constraint, UniqueConstraint)
    }

    assert ("provider_id", "subject") in constraints
    assert ("user_id", "provider_id") in constraints
