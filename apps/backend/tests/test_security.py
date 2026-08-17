from pathlib import Path
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend import security
from mato_loop_backend.models import Credential
from mato_loop_backend.security import (
    CredentialCipher,
    hash_password,
    load_credential,
    store_credential,
    verify_password,
)


def test_password_hash_roundtrip() -> None:
    hashed = hash_password("a sufficiently long password")

    assert verify_password("a sufficiently long password", hashed)
    assert not verify_password("wrong password", hashed)


def test_credential_cipher_persists_key(tmp_path: Path) -> None:
    key_path = tmp_path / "secrets" / "credential.key"
    first = CredentialCipher(key_path)
    encrypted = first.encrypt({"api_key": "secret"})

    second = CredentialCipher(key_path)

    assert second.decrypt(encrypted) == {"api_key": "secret"}
    assert key_path.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("owner_id", [None, uuid4()])
async def test_credential_reads_require_exact_owner(owner_id) -> None:
    credential = Credential(
        id=uuid4(),
        user_id=owner_id,
        kind="test",
        label="Test",
        encrypted_payload=b"encrypted",
        metadata_json={},
    )
    db = AsyncMock(spec=AsyncSession)
    db.get.return_value = credential

    assert await load_credential(db, credential.id, owner_id=owner_id) is credential
    other_owner = uuid4() if owner_id is None else None
    assert await load_credential(db, credential.id, owner_id=other_owner) is None


@pytest.mark.parametrize("owner_id", [None, uuid4()])
async def test_credential_writes_set_explicit_owner(monkeypatch, owner_id) -> None:
    monkeypatch.setattr(
        security,
        "CredentialCipher",
        lambda: type("Cipher", (), {"encrypt": lambda _self, _payload: b"encrypted"})(),
    )
    db = AsyncMock(spec=AsyncSession)

    credential = await store_credential(
        db,
        owner_id=owner_id,
        kind="test",
        label="Test",
        payload={"secret": "value"},
    )

    assert credential.user_id == owner_id
    db.add.assert_called_once_with(credential)


async def test_credential_update_rejects_mismatched_owner(monkeypatch) -> None:
    owner_id = uuid4()
    credential = Credential(
        id=uuid4(),
        user_id=owner_id,
        kind="test",
        label="Original",
        encrypted_payload=b"original",
        metadata_json={},
    )
    monkeypatch.setattr(security, "CredentialCipher", lambda: object())
    db = AsyncMock(spec=AsyncSession)

    with pytest.raises(ValueError, match="Credential scope mismatch"):
        await store_credential(
            db,
            owner_id=None,
            kind="changed",
            label="Changed",
            payload={"secret": "value"},
            credential=credential,
        )

    assert credential.kind == "test"
    db.flush.assert_not_awaited()
