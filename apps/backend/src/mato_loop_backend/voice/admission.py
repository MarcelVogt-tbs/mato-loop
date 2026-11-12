from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Select

from mato_loop_backend.models import ChatSession, VoiceConnection, VoiceDelegation


async def active_session_task(db: AsyncSession, session: ChatSession) -> VoiceDelegation | None:
    return await db.scalar(select(VoiceDelegation).where(
        VoiceDelegation.source_session_id == session.id,
        VoiceDelegation.work_session_id == session.id,
        VoiceDelegation.user_id == session.user_id,
        VoiceDelegation.active.is_(True),
    ))


async def active_connection(db: AsyncSession, session_id: UUID) -> VoiceConnection | None:
    return await active_connections(db, select(VoiceConnection.session_id).where(VoiceConnection.session_id == session_id))


async def active_connections(db: AsyncSession, session_ids: Select) -> VoiceConnection | None:
    return await db.scalar(select(VoiceConnection).where(
        VoiceConnection.session_id.in_(session_ids),
        VoiceConnection.status.in_(("starting", "active")),
        VoiceConnection.lease_until > datetime.now(UTC),
    ))
