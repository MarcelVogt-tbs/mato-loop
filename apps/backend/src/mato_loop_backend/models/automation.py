from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from mato_loop_backend.db.base import Base


class Automation(Base):
    __tablename__ = "automations"
    __table_args__ = (
        CheckConstraint("revision >= 1", name="revision_positive"),
        Index("ix_automations_user_updated", "user_id", "updated_at", "id"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    execution_session_id: Mapped[UUID] = mapped_column(
        ForeignKey("chat_sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(240), nullable=False)
    task: Mapped[str] = mapped_column(Text, nullable=False)
    skill_id: Mapped[UUID | None] = mapped_column(nullable=True, index=True)
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    archive_after_success: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    archived_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    revision: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    schedules: Mapped[list[AutomationSchedule]] = relationship(
        back_populates="automation",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="AutomationSchedule.created_at, AutomationSchedule.id",
    )
    runs: Mapped[list[AutomationRun]] = relationship(
        back_populates="automation", cascade="all, delete-orphan", passive_deletes=True
    )


class AutomationSchedule(Base):
    __tablename__ = "automation_schedules"
    __table_args__ = (
        CheckConstraint(
            "(cron_expression IS NULL AND timezone IS NULL) OR "
            "(cron_expression IS NOT NULL AND timezone IS NOT NULL)",
            name="cron_timezone_pair",
        ),
        CheckConstraint(
            "valid_until IS NULL OR cron_expression IS NOT NULL",
            name="valid_until_requires_cron",
        ),
        UniqueConstraint("automation_id", "cron_expression", "timezone"),
        Index("ix_automation_schedules_due", "enabled", "next_run_at", "id"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    automation_id: Mapped[UUID] = mapped_column(
        ForeignKey("automations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    cron_expression: Mapped[str | None] = mapped_column(String(255), nullable=True)
    timezone: Mapped[str | None] = mapped_column(String(100), nullable=True)
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    next_run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    valid_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    automation: Mapped[Automation] = relationship(back_populates="schedules")


class AutomationRun(Base):
    __tablename__ = "automation_runs"
    __table_args__ = (
        CheckConstraint(
            "trigger IN ('scheduled', 'manual')", name="trigger_valid"
        ),
        CheckConstraint(
            "status IN ('queued', 'running', 'waiting_approval', 'waiting_input', "
            "'completed', 'failed', 'cancelled', 'skipped')",
            name="status_valid",
        ),
        CheckConstraint(
            "delivery_status IN ('not_requested', 'pending', 'delivered', 'failed', "
            "'cancelled')",
            name="delivery_status_valid",
        ),
        CheckConstraint("delivery_attempts >= 0", name="delivery_attempts_nonnegative"),
        CheckConstraint(
            "(delivery_channel_type = 'app' AND delivery_endpoint_id IS NULL "
            "AND delivery_destination IS NULL) OR "
            "(delivery_channel_type = 'endpoint' AND delivery_endpoint_id IS NOT NULL "
            "AND delivery_destination IS NOT NULL)",
            name="automation_run_delivery_channel_valid",
        ),
        Index("ix_automation_runs_status_created", "status", "created_at", "id"),
        Index(
            "ix_automation_runs_delivery_due",
            "delivery_status",
            "delivery_next_attempt_at",
            "id",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    automation_id: Mapped[UUID] = mapped_column(
        ForeignKey("automations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    automation_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    trigger: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="queued")
    scheduled_for: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    schedule_snapshots: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    definition_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    agent_run_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="SET NULL", use_alter=True),
        nullable=True,
        unique=True,
    )
    output_session_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("chat_sessions.id", ondelete="SET NULL"), nullable=True, index=True
    )
    output_message_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("messages.id", ondelete="SET NULL", use_alter=True), nullable=True, unique=True
    )
    delivery_channel_type: Mapped[str] = mapped_column(
        String(20), nullable=False, default="app", server_default="app"
    )
    delivery_endpoint_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("messaging_endpoints.id", ondelete="SET NULL"), nullable=True, index=True
    )
    delivery_destination: Mapped[str | None] = mapped_column(String(128), nullable=True)
    delivery_status: Mapped[str] = mapped_column(
        String(30), nullable=False, default="not_requested"
    )
    delivery_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    delivery_next_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    error_code: Mapped[str | None] = mapped_column(String(120), nullable=True)
    error_params: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    automation: Mapped[Automation] = relationship(back_populates="runs")
