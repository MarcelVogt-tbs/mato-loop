from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
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


class MessagingEndpoint(Base):
    __tablename__ = "messaging_endpoints"
    __table_args__ = (
        CheckConstraint(
            "(paired_user_id IS NULL) = (paired_chat_id IS NULL)",
            name="messaging_endpoint_pairing_complete",
        ),
        UniqueConstraint("credential_id"),
        Index(
            "uq_messaging_endpoints_active_identity",
            "provider",
            "external_identity",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index("ix_messaging_endpoints_poll", "provider", "enabled", "poll_lease_until"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    credential_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("credentials.id", ondelete="SET NULL"), nullable=True
    )
    current_session_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("chat_sessions.id", ondelete="SET NULL"), nullable=True, index=True
    )
    provider: Mapped[str] = mapped_column(String(30), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    external_identity: Mapped[str] = mapped_column(String(128), nullable=False)
    external_handle: Mapped[str | None] = mapped_column(String(160), nullable=True)
    paired_user_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    paired_chat_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    paired_display_name: Mapped[str | None] = mapped_column(String(160), nullable=True)
    pairing_code_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    pairing_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    poll_offset: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default=text("0")
    )
    poll_lease_owner: Mapped[str | None] = mapped_column(String(64), nullable=True)
    poll_lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_polled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error_code: Mapped[str | None] = mapped_column(String(120), nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class MessagingInbox(Base):
    __tablename__ = "messaging_inbox"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'processing', 'completed', 'failed')",
            name="messaging_inbox_status_valid",
        ),
        CheckConstraint("attempts >= 0", name="messaging_inbox_attempts_nonnegative"),
        UniqueConstraint("endpoint_id", "external_event_id"),
        Index(
            "uq_messaging_inbox_endpoint_processing",
            "endpoint_id",
            unique=True,
            postgresql_where=text("status = 'processing'"),
        ),
        Index("ix_messaging_inbox_due", "status", "next_attempt_at", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    endpoint_id: Mapped[UUID] = mapped_column(
        ForeignKey("messaging_endpoints.id", ondelete="CASCADE"), nullable=False, index=True
    )
    external_event_id: Mapped[str] = mapped_column(String(128), nullable=False)
    external_message_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending", server_default="pending"
    )
    attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    processing_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(120), nullable=True)
    session_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("chat_sessions.id", ondelete="SET NULL"), nullable=True, index=True
    )
    input_message_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("messages.id", ondelete="SET NULL"), nullable=True, unique=True
    )
    agent_run_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="SET NULL"), nullable=True, unique=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class MessagingOutbox(Base):
    __tablename__ = "messaging_outbox"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'processing', 'delivered', 'failed')",
            name="messaging_outbox_status_valid",
        ),
        CheckConstraint("attempts >= 0", name="messaging_outbox_attempts_nonnegative"),
        UniqueConstraint("endpoint_id", "deduplication_key"),
        Index("ix_messaging_outbox_due", "status", "next_attempt_at", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    endpoint_id: Mapped[UUID] = mapped_column(
        ForeignKey("messaging_endpoints.id", ondelete="CASCADE"), nullable=False, index=True
    )
    inbox_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("messaging_inbox.id", ondelete="SET NULL"), nullable=True, index=True
    )
    automation_run_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("automation_runs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    source_message_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("messages.id", ondelete="SET NULL"), nullable=True, index=True
    )
    destination: Mapped[str] = mapped_column(String(128), nullable=False)
    deduplication_key: Mapped[str] = mapped_column(String(240), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending", server_default="pending"
    )
    attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    processing_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(120), nullable=True)
    external_message_ids: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
