from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.auth.identity import AuthenticatedIdentity
from mato_loop_backend.models import AuthenticationProvider, UserAccount, UserAuthIdentity


class ExternalAccountUnavailableError(Exception):
    pass


class ExternalIdentityConflictError(Exception):
    pass


class FinalLoginMethodError(Exception):
    pass


def _validate_subject(subject: str) -> None:
    if not isinstance(subject, str) or not subject or len(subject) > 500:
        raise ExternalIdentityConflictError


async def resolve_external_identity(
    db: AsyncSession,
    provider: AuthenticationProvider,
    identity: AuthenticatedIdentity,
) -> UserAccount:
    if not provider.enabled:
        raise ExternalAccountUnavailableError
    _validate_subject(identity.subject)
    linked = await db.scalar(
        select(UserAuthIdentity).where(
            UserAuthIdentity.provider_id == provider.id,
            UserAuthIdentity.subject == identity.subject,
        )
    )
    if linked is not None:
        user = await db.get(UserAccount, linked.user_id)
        if user is None:
            raise ExternalAccountUnavailableError
        return user

    if not provider.auto_create_users:
        raise ExternalAccountUnavailableError
    username = identity.username.strip() if isinstance(identity.username, str) else ""
    if not username or len(username) > 120:
        raise ExternalAccountUnavailableError
    if await db.scalar(
        select(UserAccount.id).where(
            func.lower(UserAccount.username) == func.lower(username)
        )
    ):
        raise ExternalAccountUnavailableError

    user = UserAccount(
        username=username,
        password_hash=None,
        is_admin=False,
        is_active=True,
    )
    db.add(user)
    await db.flush()
    db.add(
        UserAuthIdentity(
            user_id=user.id,
            provider_id=provider.id,
            subject=identity.subject,
        )
    )
    await db.flush()
    return user


async def link_external_identity(
    db: AsyncSession,
    user: UserAccount,
    provider: AuthenticationProvider,
    subject: str,
) -> UserAuthIdentity:
    if not provider.enabled or not user.is_active:
        raise ExternalAccountUnavailableError
    _validate_subject(subject)
    linked = await db.scalar(
        select(UserAuthIdentity).where(
            UserAuthIdentity.provider_id == provider.id,
            UserAuthIdentity.subject == subject,
        )
    )
    if linked is not None:
        if linked.user_id != user.id:
            raise ExternalIdentityConflictError
        return linked
    if await db.scalar(
        select(UserAuthIdentity).where(
            UserAuthIdentity.user_id == user.id,
            UserAuthIdentity.provider_id == provider.id,
        )
    ):
        raise ExternalIdentityConflictError
    linked = UserAuthIdentity(user_id=user.id, provider_id=provider.id, subject=subject)
    db.add(linked)
    await db.flush()
    return linked


async def unlink_external_identity(
    db: AsyncSession,
    user: UserAccount,
    provider: AuthenticationProvider,
) -> bool:
    locked_user = await db.scalar(
        select(UserAccount).where(UserAccount.id == user.id).with_for_update()
    )
    if locked_user is None:
        raise ExternalAccountUnavailableError
    linked = await db.scalar(
        select(UserAuthIdentity).where(
            UserAuthIdentity.user_id == user.id,
            UserAuthIdentity.provider_id == provider.id,
        )
    )
    if linked is None:
        return False
    if locked_user.password_hash is None and not await db.scalar(
        select(func.count(UserAuthIdentity.id)).where(
            UserAuthIdentity.user_id == user.id,
            UserAuthIdentity.id != linked.id,
        )
    ):
        raise FinalLoginMethodError
    await db.delete(linked)
    await db.flush()
    return True
