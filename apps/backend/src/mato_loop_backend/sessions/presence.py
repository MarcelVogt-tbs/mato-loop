from __future__ import annotations

import time
from uuid import UUID

PRESENCE_TTL_SECONDS = 15.0
_active_users: dict[tuple[UUID, UUID | None], tuple[UUID | None, float]] = {}


def mark_user_present(
    user_id: UUID, *, client_id: UUID | None = None,
    session_id: UUID | None = None, active: bool = True,
) -> None:
    now = time.monotonic()
    _prune(now)
    key = (user_id, client_id)
    if active:
        _active_users[key] = (session_id, now + PRESENCE_TTL_SECONDS)
    else:
        _active_users.pop(key, None)


def user_is_present(user_id: UUID) -> bool:
    now = time.monotonic()
    _prune(now)
    return any(owner == user_id for owner, _client in _active_users)


def session_is_present(user_id: UUID, session_id: UUID) -> bool:
    _prune(time.monotonic())
    return any(
        owner == user_id and current_session == session_id
        for (owner, _client), (current_session, _expires) in _active_users.items()
    )


def _prune(now: float) -> None:
    for key, (_session_id, expires_at) in list(_active_users.items()):
        if expires_at <= now:
            del _active_users[key]
