from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.auth.credentials import (
    CredentialCipher,
    delete_credential,
    load_credential,
    scoped_credentials,
)
from mato_loop_backend.auth.credentials import (
    store_credential as _store_credential,
)
from mato_loop_backend.auth.local import hash_password, password_hash, verify_password
from mato_loop_backend.auth.sessions import (
    create_login_session,
    hash_token,
    remove_login_session,
    require_admin,
    require_user,
    revoke_login_sessions,
)
from mato_loop_backend.models import Credential


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
    return await _store_credential(
        db,
        owner_id=owner_id,
        kind=kind,
        label=label,
        payload=payload,
        metadata=metadata,
        credential=credential,
        _cipher=CredentialCipher(),
    )


def read_credential(credential: Credential) -> dict[str, Any]:
    return CredentialCipher().decrypt(credential.encrypted_payload)

__all__ = [
    "CredentialCipher",
    "create_login_session",
    "delete_credential",
    "hash_password",
    "hash_token",
    "load_credential",
    "password_hash",
    "read_credential",
    "remove_login_session",
    "require_admin",
    "require_user",
    "revoke_login_sessions",
    "scoped_credentials",
    "store_credential",
    "verify_password",
]
