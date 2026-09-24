from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import delete, select

from mato_loop_backend.db.session import AsyncSessionFactory
from mato_loop_backend.models import AgentRun, ChatSession, DiagnosticEvent, UserAccount

logger = logging.getLogger("mato_loop_backend.diagnostics")
RETENTION_DAYS = 14
MAX_PAYLOAD_BYTES = 1_000_000
# ponytail: process-wide cap; add per-user queues only if diagnostics throughput needs it.
MAX_PENDING_EVENTS = 1_000
_pending_events: set[asyncio.Task[None]] = set()


def _bounded_payload(payload: dict[str, Any]) -> dict[str, Any]:
    encoded = json.dumps(payload, ensure_ascii=False, default=str).encode()
    if len(encoded) <= MAX_PAYLOAD_BYTES:
        return json.loads(encoded)
    return {
        "truncated": True,
        "originalBytes": len(encoded),
        "availableKeys": sorted(payload),
    }


async def diagnostic_access_active(db: Any, user_id: UUID) -> bool:
    enabled = await db.scalar(
        select(UserAccount.id).where(
            UserAccount.id == user_id,
            UserAccount.is_active.is_(True),
            UserAccount.is_admin.is_(True),
            UserAccount.debug_token_hash.is_not(None),
        )
    )
    return isinstance(enabled, UUID)


async def record_diagnostic_event(
    user_id: UUID,
    event_type: str,
    *,
    component: str,
    session_id: UUID | None = None,
    run_id: UUID | None = None,
    correlation_id: str | UUID | None = None,
    payload: dict[str, Any] | None = None,
    occurred_at: datetime | None = None,
) -> None:
    """Persist bounded diagnostics only while the owner's debug access is active."""
    try:
        async with AsyncSessionFactory() as db:
            if not await diagnostic_access_active(db, user_id):
                return
            if run_id is not None:
                run_session_id = await db.scalar(
                    select(AgentRun.session_id)
                    .join(ChatSession, ChatSession.id == AgentRun.session_id)
                    .where(AgentRun.id == run_id, ChatSession.user_id == user_id)
                )
                if run_session_id is None or (
                    session_id is not None and run_session_id != session_id
                ):
                    return
            elif session_id is not None and await db.scalar(
                select(ChatSession.id).where(
                    ChatSession.id == session_id, ChatSession.user_id == user_id
                )
            ) is None:
                return
            db.add(
                DiagnosticEvent(
                    user_id=user_id,
                    session_id=session_id,
                    run_id=run_id,
                    component=component,
                    event_type=event_type,
                    correlation_id=str(correlation_id) if correlation_id is not None else None,
                    payload=_bounded_payload(payload or {}),
                    created_at=occurred_at or datetime.now(UTC),
                )
            )
            await db.commit()
    except Exception as exc:
        logger.error(
            "Could not persist diagnostic event %s (%s)", event_type, type(exc).__name__
        )


def schedule_diagnostic_event(*args: Any, **kwargs: Any) -> None:
    """Record diagnostics outside the agent's critical path."""
    if len(_pending_events) >= MAX_PENDING_EVENTS:
        logger.warning("Diagnostic event dropped because the pending limit was reached")
        return
    kwargs["occurred_at"] = datetime.now(UTC)
    task = asyncio.create_task(record_diagnostic_event(*args, **kwargs))
    _pending_events.add(task)
    task.add_done_callback(_pending_events.discard)


async def diagnostic_retention_loop() -> None:
    """Remove expired diagnostics independently of debug activity."""
    while True:
        try:
            async with AsyncSessionFactory() as db:
                await db.execute(
                    delete(DiagnosticEvent).where(
                        DiagnosticEvent.created_at
                        < datetime.now(UTC) - timedelta(days=RETENTION_DAYS)
                    )
                )
                await db.commit()
        except Exception as exc:
            logger.error("Could not remove expired diagnostic events (%s)", type(exc).__name__)
        await asyncio.sleep(3_600)
