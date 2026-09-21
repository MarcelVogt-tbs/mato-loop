from __future__ import annotations

import time
from uuid import UUID

PRESENCE_TTL_SECONDS = 15.0
_active_users: dict[UUID, float] = {}


def mark_user_present(user_id: UUID) -> None:
    now = time.monotonic()
    _prune(now)
    _active_users[user_id] = now + PRESENCE_TTL_SECONDS


def user_is_present(user_id: UUID) -> bool:
    now = time.monotonic()
    _prune(now)
    return _active_users.get(user_id, 0) > now


def _prune(now: float) -> None:
    for key, expires_at in list(_active_users.items()):
        if expires_at <= now:
            del _active_users[key]
