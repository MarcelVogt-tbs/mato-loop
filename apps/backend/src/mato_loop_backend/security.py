from __future__ import annotations

import hashlib
import json
import secrets
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any
from uuid import UUID

from cryptography.fernet import Fernet
from fastapi import Cookie, Depends, status
from pwdlib import PasswordHash
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.api.errors import ApiError
from mato_loop_backend.config import get_runtime_config
from mato_loop_backend.db.session import get_db_session
from mato_loop_backend.models import Credential, LoginSession, UserAccount

password_hash = PasswordHash.recommended()


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def hash_password(password: str) -> str:
    return password_hash.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    return password_hash.verify(password, hashed)


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


class CredentialCipher:
    def __init__(self, key_path: Path | None = None) -> None:
        config = get_runtime_config()
        self.key_path = key_path or config.secrets_dir / "credential.key"
        self._fernet = Fernet(self._load_or_create_key())

    def _load_or_create_key(self) -> bytes:
        self.key_path.parent.mkdir(parents=True, exist_ok=True)
        if self.key_path.exists():
            return self.key_path.read_bytes().strip()
        key = Fernet.generate_key()
        self.key_path.write_bytes(key + b"\n")
        self.key_path.chmod(0o600)
        return key

    def encrypt(self, payload: dict[str, Any]) -> bytes:
        serialized = json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode()
        return self._fernet.encrypt(serialized)

    def decrypt(self, payload: bytes) -> dict[str, Any]:
        value = json.loads(self._fernet.decrypt(payload))
        if not isinstance(value, dict):
            raise ValueError("Credential payload must be an object")
        return value


async def store_credential(
    db: AsyncSession,
    *,
    owner_id: UUID | None,
    kind: str,
    label: str,
    payload: dict[str, Any],
    metadata: dict[str, Any] | None = None,
    credential: Credential | None = None,
) -> Credential:
    cipher = CredentialCipher()
    if credential is None:
        credential = Credential(
            user_id=owner_id,
            kind=kind,
            label=label,
            encrypted_payload=cipher.encrypt(payload),
            metadata_json=metadata or {},
        )
        db.add(credential)
    else:
        if credential.user_id != owner_id:
            raise ValueError("Credential scope mismatch")
        credential.kind = kind
        credential.label = label
        credential.encrypted_payload = cipher.encrypt(payload)
        credential.metadata_json = metadata or {}
    await db.flush()
    return credential


async def load_credential(
    db: AsyncSession,
    credential_id: UUID,
    *,
    owner_id: UUID | None,
    populate_existing: bool = False,
) -> Credential | None:
    credential = await db.get(
        Credential, credential_id, populate_existing=populate_existing
    )
    return (
        credential
        if credential is not None and getattr(credential, "user_id", object()) == owner_id
        else None
    )


def scoped_credentials(owner_id: UUID | None):
    return select(Credential).where(
        Credential.user_id.is_(None)
        if owner_id is None
        else Credential.user_id == owner_id
    )


async def delete_credential(
    db: AsyncSession, credential_id: UUID, *, owner_id: UUID | None
) -> bool:
    credential = await load_credential(db, credential_id, owner_id=owner_id)
    if credential is None:
        return False
    await db.delete(credential)
    return True


def read_credential(credential: Credential) -> dict[str, Any]:
    return CredentialCipher().decrypt(credential.encrypted_payload)
