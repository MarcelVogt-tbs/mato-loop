from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import urlsplit
from uuid import UUID

from fastapi import WebSocket
from sqlalchemy import select

from mato_loop_backend.auth.sessions import hash_token
from mato_loop_backend.config import get_runtime_config
from mato_loop_backend.db.session import AsyncSessionFactory
from mato_loop_backend.models import LoginSession, UserAccount


@dataclass(frozen=True, slots=True)
class _BrowserLogin:
    user: UserAccount
    login_id: UUID
    expires_at: datetime


async def _browser_login(websocket: WebSocket) -> _BrowserLogin | None:
    config = get_runtime_config()
    token = websocket.cookies.get(config.session_cookie_name)
    if not token:
        return None
    async with AsyncSessionFactory() as db:
        login = await db.scalar(
            select(LoginSession).where(
                LoginSession.token_hash == hash_token(token),
                LoginSession.expires_at > datetime.now(UTC),
            )
        )
        user = await db.get(UserAccount, login.user_id) if login is not None else None
        if login is None or user is None or not user.is_active:
            return None
        return _BrowserLogin(user, login.id, login.expires_at)


def _origin_allowed(websocket: WebSocket, configured_origins: list[str]) -> bool:
    origin = websocket.headers.get("origin")
    if origin is None:
        return False
    if origin in configured_origins:
        return True
    parsed = urlsplit(origin)
    host = websocket.headers.get("host")
    return (
        parsed.scheme in {"http", "https"}
        and parsed.path in {"", "/"}
        and not parsed.query
        and not parsed.fragment
        and host is not None
        and parsed.netloc.casefold() == host.casefold()
    )
