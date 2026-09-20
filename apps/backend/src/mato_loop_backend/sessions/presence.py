from __future__ import annotations

import time
from uuid import UUID

SESSION_PRESENCE_TTL_SECONDS = 15.0
_active_sessions: dict[tuple[UUID, UUID], float] = {}


def mark_session_present(user_id: UUID, session_id: UUID) -> None:
    now = time.monotonic()
    _prune(now)
    _active_sessions[(user_id, session_id)] = now + SESSION_PRESENCE_TTL_SECONDS


def session_is_present(user_id: UUID, session_id: UUID) -> bool:
    now = time.monotonic()
    _prune(now)
    return _active_sessions.get((user_id, session_id), 0) > now


def _prune(now: float) -> None:
    for key, expires_at in list(_active_sessions.items()):
        if expires_at <= now:
            del _active_sessions[key]
