from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from mato_loop_backend.db.base import Base


class VoiceConnection(Base):
    __tablename__ = "voice_connections"
    __table_args__ = (
        ForeignKeyConstraint(["session_id", "user_id"], ["chat_sessions.id", "chat_sessions.user_id"], ondelete="CASCADE"),
        CheckConstraint("status IN ('starting', 'active', 'ended', 'failed')", name="status_valid"),
        Index("ix_voice_connections_active_session", "session_id", unique=True, postgresql_where=text("status IN ('starting', 'active')")),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    session_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    user_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    provider_id: Mapped[UUID] = mapped_column(nullable=False)
    model_id: Mapped[UUID] = mapped_column(nullable=False)
    call_id: Mapped[str | None] = mapped_column(String(210))
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="starting")
    error_code: Mapped[str | None] = mapped_column(String(120))
    lease_until: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class VoiceItem(Base):
    __tablename__ = "voice_items"
    __table_args__ = (UniqueConstraint("connection_id", "provider_item_id"),)
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    connection_id: Mapped[UUID] = mapped_column(ForeignKey("voice_connections.id", ondelete="CASCADE"), nullable=False)
    provider_item_id: Mapped[str] = mapped_column(String(210), nullable=False)
    message_id: Mapped[UUID] = mapped_column(ForeignKey("messages.id", ondelete="CASCADE"), nullable=False, unique=True)
    final: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    interrupted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    audio_end_ms: Mapped[int | None] = mapped_column(Integer)


class VoiceDelegation(Base):
    __tablename__ = "voice_delegations"
    __table_args__ = (
        Index("ix_voice_delegations_active_source", "source_session_id", unique=True, postgresql_where=text("active")),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("user_accounts.id", ondelete="CASCADE"), nullable=False, index=True)
    source_session_id: Mapped[UUID | None] = mapped_column(ForeignKey("chat_sessions.id", ondelete="SET NULL"), index=True)
    work_session_id: Mapped[UUID | None] = mapped_column(ForeignKey("chat_sessions.id", ondelete="SET NULL"))
    run_id: Mapped[UUID | None] = mapped_column(ForeignKey("agent_runs.id", ondelete="SET NULL"))
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="starting")
    result_message_id: Mapped[UUID | None] = mapped_column(ForeignKey("messages.id", ondelete="SET NULL"))
    announced: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class VoiceOperation(Base):
    __tablename__ = "voice_operations"
    __table_args__ = (UniqueConstraint("connection_id", "provider_call_id"),)
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    connection_id: Mapped[UUID] = mapped_column(ForeignKey("voice_connections.id", ondelete="CASCADE"), nullable=False)
    provider_call_id: Mapped[str] = mapped_column(String(210), nullable=False)
    name: Mapped[str] = mapped_column(String(40), nullable=False)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
