from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from typing import Annotated
from uuid import UUID

from fastapi import Cookie, Depends, status
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.api.errors import ApiError
from mato_loop_backend.config import get_runtime_config
from mato_loop_backend.db.session import get_db_session
from mato_loop_backend.models import LoginSession, UserAccount


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


async def create_login_session(db: AsyncSession, user: UserAccount) -> tuple[str, datetime]:
    if not user.is_active:
        raise ApiError(status.HTTP_401_UNAUTHORIZED, "auth.accountDisabled")
    config = get_runtime_config()
    token = secrets.token_urlsafe(48)
    expires_at = datetime.now(UTC) + timedelta(days=config.session_duration_days)
    db.add(LoginSession(user_id=user.id, token_hash=hash_token(token), expires_at=expires_at))
    await db.flush()
    return token, expires_at


async def require_user(
    db: Annotated[AsyncSession, Depends(get_db_session)],
    session_token: Annotated[
        str | None, Cookie(alias=get_runtime_config().session_cookie_name)
    ] = None,
) -> UserAccount:
    if not session_token:
        raise ApiError(status.HTTP_401_UNAUTHORIZED, "auth.notAuthenticated")
    login = await db.scalar(
        select(LoginSession).where(
            LoginSession.token_hash == hash_token(session_token),
            LoginSession.expires_at > datetime.now(UTC),
        )
    )
    if login is None:
        raise ApiError(status.HTTP_401_UNAUTHORIZED, "auth.sessionExpired")
    user = await db.get(UserAccount, login.user_id)
    if user is None:
        raise ApiError(status.HTTP_401_UNAUTHORIZED, "auth.userMissing")
    if not user.is_active:
        raise ApiError(status.HTTP_401_UNAUTHORIZED, "auth.accountDisabled")
    return user


async def require_admin(
    user: Annotated[UserAccount, Depends(require_user)],
) -> UserAccount:
    if not user.is_admin:
        raise ApiError(status.HTTP_403_FORBIDDEN, "auth.adminRequired")
    return user


async def revoke_login_sessions(db: AsyncSession, user_id: UUID) -> None:
    await db.execute(delete(LoginSession).where(LoginSession.user_id == user_id))


async def remove_login_session(db: AsyncSession, token: str | None) -> None:
    if token:
        await db.execute(delete(LoginSession).where(LoginSession.token_hash == hash_token(token)))
