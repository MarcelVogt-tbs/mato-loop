from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
from uuid import UUID, uuid4

MAX_SESSION_SECRET_NAMES = 32
MAX_SESSIONS_WITH_SECRETS = 256


@dataclass(slots=True)
class _SessionSecret:
    id: UUID
    value: str = field(repr=False)


class SessionSecretStore:
    """Bounded process-local secrets that never outlive the backend process."""

    def __init__(self) -> None:
        self._sessions: OrderedDict[
            UUID, OrderedDict[str, _SessionSecret]
        ] = OrderedDict()

    def put(self, session_id: UUID, values: dict[str, str]) -> dict[str, str]:
        secrets = self._sessions.setdefault(session_id, OrderedDict())
        self._sessions.move_to_end(session_id)
        references: dict[str, str] = {}
        for name, value in values.items():
            secrets.pop(name, None)
            secret = _SessionSecret(uuid4(), value)
            secrets[name] = secret
            references[name] = f"session-secret:{secret.id}"
        while len(secrets) > MAX_SESSION_SECRET_NAMES:
            secrets.popitem(last=False)
        while len(self._sessions) > MAX_SESSIONS_WITH_SECRETS:
            self._sessions.popitem(last=False)
        return references

    def references(self, session_id: UUID) -> dict[str, str]:
        secrets = self._sessions.get(session_id)
        if secrets is None:
            return {}
        return {name: f"session-secret:{secret.id}" for name, secret in secrets.items()}

    def resolve(self, session_id: UUID, secret_id: UUID) -> str | None:
        secrets = self._sessions.get(session_id)
        if secrets is None:
            return None
        return next(
            (secret.value for secret in secrets.values() if secret.id == secret_id),
            None,
        )

    def clear_session(self, session_id: UUID) -> None:
        self._sessions.pop(session_id, None)

    def clear_sessions(self, session_ids: list[UUID] | tuple[UUID, ...]) -> None:
        for session_id in session_ids:
            self.clear_session(session_id)

    def clear_all(self) -> None:
        self._sessions.clear()


session_secrets = SessionSecretStore()
