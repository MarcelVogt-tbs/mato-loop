from __future__ import annotations

import asyncio
from typing import Any
from uuid import UUID

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend import __version__
from mato_loop_backend.models import Credential
from mato_loop_backend.providers import codex_oauth
from mato_loop_backend.providers.base import ProviderError
from mato_loop_backend.security import load_credential, read_credential, store_credential

_refresh_lock = asyncio.Lock()


async def refresh_codex_credential(
    db: AsyncSession, credential: Credential, *, owner_id: UUID | None
) -> dict[str, Any]:
    # ponytail: one lock is enough while MatoLoop supports one Codex account.
    async with _refresh_lock:
        current = await load_credential(
            db, credential.id, owner_id=owner_id, populate_existing=True
        )
        if current is None:
            raise ProviderError(
                "ChatGPT Codex credential is missing",
                code="provider.authenticationFailed",
            )
        try:
            payload = read_credential(current)
        except Exception as exc:
            raise ProviderError(
                "ChatGPT Codex credential is invalid",
                code="provider.authenticationFailed",
            ) from exc
        try:
            refreshed = await codex_oauth.refresh_token(payload)
        except httpx.HTTPStatusError as exc:
            raise ProviderError(
                "ChatGPT Codex token refresh failed",
                status_code=exc.response.status_code,
                code=(
                    "provider.authenticationFailed"
                    if exc.response.status_code in {400, 401, 403}
                    else None
                ),
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderError(
                "ChatGPT Codex is unavailable", code="provider.unavailable"
            ) from exc
        except (KeyError, TypeError, ValueError) as exc:
            raise ProviderError(
                "ChatGPT Codex credential is invalid",
                code="provider.authenticationFailed",
            ) from exc
        if refreshed != payload:
            await store_credential(
                db,
                owner_id=owner_id,
                kind=current.kind,
                label=current.label,
                payload=refreshed,
                metadata=current.metadata_json,
                credential=current,
            )
            await db.commit()
        return refreshed


def codex_headers(
    payload: dict[str, Any],
    *,
    accept: str = "application/json",
    image_turn_id: str | None = None,
) -> dict[str, str]:
    headers = {
        "Authorization": f"Bearer {payload['access']}",
        "originator": "mato-loop",
        "version": codex_oauth.CODEX_COMPATIBILITY_VERSION,
        "User-Agent": f"MatoLoop/{__version__}",
        "Accept": accept,
        "Content-Type": "application/json",
    }
    if account_id := payload.get("account_id"):
        headers["ChatGPT-Account-ID"] = account_id
    if image_turn_id:
        headers["x-codex-image-turn-id"] = image_turn_id
    return headers
