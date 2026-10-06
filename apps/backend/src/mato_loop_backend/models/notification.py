from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from mato_loop_backend.db.base import Base

if TYPE_CHECKING:
    from mato_loop_backend.models.automation import AutomationRun


class Notification(Base):
    __tablename__ = "notifications"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "deduplication_key", name="uq_notifications_user_deduplication_key"
        ),
        Index("ix_notifications_user_created", "user_id", "created_at", "id"),
        Index(
            "ix_notifications_user_unread",
            "user_id",
            postgresql_where=text("read_at IS NULL"),
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="CASCADE"), nullable=False
    )
    session_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("chat_sessions.id", ondelete="SET NULL"), nullable=True, index=True
    )
    automation_run_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("automation_runs.id", ondelete="CASCADE", use_alter=True),
        nullable=True,
        index=True,
    )
    approval_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("approval_requests.id", ondelete="SET NULL"), nullable=True
    )
    kind: Mapped[str] = mapped_column(String(80), nullable=False)
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    deduplication_key: Mapped[str] = mapped_column(String(255), nullable=False)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    automation_run: Mapped[AutomationRun | None] = relationship(
        foreign_keys=[automation_run_id]
    )


class WebPushSubscription(Base):
    __tablename__ = "web_push_subscriptions"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "device_id", name="uq_web_push_subscriptions_user_device"
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    device_id: Mapped[UUID] = mapped_column(nullable=False)
    endpoint_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    encrypted_payload: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
