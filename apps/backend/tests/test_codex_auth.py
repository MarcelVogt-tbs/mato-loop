from unittest.mock import AsyncMock
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.models import Credential
from mato_loop_backend.providers import codex_auth


async def test_refresh_codex_credential_persists_rotated_token(monkeypatch) -> None:
    credential = Credential(
        id=uuid4(),
        kind="codex-oauth",
        label="Codex",
        encrypted_payload=b"encrypted",
        metadata_json={"accountId": "account"},
    )
    db = AsyncMock(spec=AsyncSession)
    db.get.return_value = credential
    current = {"access": "old", "refresh": "refresh"}
    refreshed = {"access": "new", "refresh": "refresh"}
    monkeypatch.setattr(codex_auth, "read_credential", lambda _credential: current)
    monkeypatch.setattr(
        codex_auth.codex_oauth,
        "refresh_token",
        AsyncMock(return_value=refreshed),
    )
    store = AsyncMock()
    monkeypatch.setattr(codex_auth, "store_credential", store)

    assert (
        await codex_auth.refresh_codex_credential(db, credential, owner_id=None)
        == refreshed
    )

    store.assert_awaited_once_with(
        db,
        owner_id=None,
        kind="codex-oauth",
        label="Codex",
        payload=refreshed,
        metadata={"accountId": "account"},
        credential=credential,
    )
    db.commit.assert_awaited_once()
