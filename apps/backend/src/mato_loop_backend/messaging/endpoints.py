from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import exists, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.models import (
    AutomationRun,
    MessagingEndpoint,
    MessagingInbox,
    MessagingOutbox,
)

PAIRING_TTL = timedelta(minutes=15)


def pairing_code() -> str:
    return secrets.token_urlsafe(9)


def pairing_hash(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


def new_pairing(endpoint: MessagingEndpoint) -> str:
    code = pairing_code()
    endpoint.pairing_code_hash = pairing_hash(code)
    endpoint.pairing_expires_at = datetime.now(UTC) + PAIRING_TTL
    return code


def endpoint_payload(endpoint: MessagingEndpoint, *, code: str | None = None) -> dict:
    state = endpoint.channel_state or {}
    candidate = state.get("candidate")
    return {
        "id": str(endpoint.id),
        "name": endpoint.name,
        "provider": endpoint.provider,
        "enabled": endpoint.enabled,
        "hasCredential": endpoint.credential_id is not None,
        "externalHandle": endpoint.external_handle,
        "pairingState": "paired"
        if endpoint.paired_chat_id is not None
        else "pending"
        if endpoint.pairing_code_hash
        and endpoint.pairing_expires_at
        and endpoint.pairing_expires_at > datetime.now(UTC)
        else "unpaired",
        "connected": endpoint.last_error_code is None,
        "errorCode": endpoint.last_error_code,
        "pairedDisplayName": endpoint.paired_display_name,
        "currentSessionId": str(endpoint.current_session_id)
        if endpoint.current_session_id
        else None,
        "pairingExpiresAt": endpoint.pairing_expires_at.isoformat()
        if endpoint.pairing_expires_at
        else None,
        **({"pairingCode": code} if code else {}),
        **({"pairingCandidate": candidate} if candidate else {}),
    }


async def invalidate_pending_inbox(
    db: AsyncSession, endpoint_id: UUID, *, exclude_id: UUID | None = None
) -> None:
    conditions = [
        MessagingInbox.endpoint_id == endpoint_id,
        MessagingInbox.status.in_(("pending", "processing")),
    ]
    if exclude_id is not None:
        conditions.append(MessagingInbox.id != exclude_id)
    await db.execute(
        update(MessagingInbox)
        .where(*conditions)
        .values(
            status="failed",
            processing_at=None,
            next_attempt_at=None,
            error_code="channel.pairingChanged",
        )
    )
    await db.execute(
        update(MessagingInbox)
        .where(
            MessagingInbox.endpoint_id == endpoint_id,
            MessagingInbox.status == "completed",
            MessagingInbox.id != exclude_id if exclude_id is not None else True,
            ~exists(
                select(MessagingOutbox.id).where(MessagingOutbox.inbox_id == MessagingInbox.id)
            ),
        )
        .values(status="failed", error_code="channel.pairingChanged")
    )


async def stop_endpoint_work(
    db: AsyncSession, endpoint_id: UUID, *, exclude_inbox_id: UUID | None = None
) -> None:
    await invalidate_pending_inbox(db, endpoint_id, exclude_id=exclude_inbox_id)
    runs = list(
        (
            await db.scalars(
                select(AutomationRun).where(
                    AutomationRun.delivery_endpoint_id == endpoint_id,
                    AutomationRun.delivery_status == "pending",
                )
            )
        ).all()
    )
    for run in runs:
        run.delivery_channel_type = "app"
        run.delivery_endpoint_id = None
        run.delivery_destination = None
    items = list(
        (
            await db.scalars(
                select(MessagingOutbox).where(
                    MessagingOutbox.endpoint_id == endpoint_id,
                    MessagingOutbox.status.in_(("pending", "processing")),
                )
            )
        ).all()
    )
    for item in items:
        item.status, item.processing_at, item.next_attempt_at = "failed", None, None
        item.error_code = "channel.pairingChanged"
