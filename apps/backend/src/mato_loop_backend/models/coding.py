from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from mato_loop_backend.db.base import Base


class ProjectPlan(Base):
    __tablename__ = "project_plans"
    __table_args__ = (UniqueConstraint("id", "project_id", name="uq_project_plans_id_project"),)

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    originating_session_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("chat_sessions.id", ondelete="SET NULL"), nullable=True, index=True
    )
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    markdown: Mapped[str] = mapped_column(Text, nullable=False)
    revision: Mapped[str] = mapped_column(String(64), nullable=False)
    working_directory: Mapped[str] = mapped_column(String(1000), nullable=False)
    analysis_model_key: Mapped[str] = mapped_column(String(600), nullable=False)
    external_session_id: Mapped[str | None] = mapped_column(String(300), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class ProjectPlanExecution(Base):
    __tablename__ = "project_plan_executions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["plan_id", "project_id"],
            ["project_plans.id", "project_plans.project_id"],
            ondelete="CASCADE",
            name="fk_plan_executions_plan_project",
        ),
        UniqueConstraint("project_id", "queue_position", name="uq_plan_execution_position"),
        CheckConstraint(
            "status IN ('queued', 'running', 'waiting_permission', 'completed', "
            "'failed', 'cancelled', 'outcome_unknown')",
            name="project_plan_execution_status_valid",
        ),
        CheckConstraint("queue_position > 0", name="project_plan_execution_position_positive"),
        Index(
            "uq_project_plan_execution_active",
            "project_id",
            unique=True,
            postgresql_where=text("status IN ('running', 'waiting_permission')"),
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    plan_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    queue_position: Mapped[int] = mapped_column(nullable=False)
    status: Mapped[str] = mapped_column(
        String(30), nullable=False, default="queued", server_default="queued", index=True
    )
    plan_revision: Mapped[str] = mapped_column(String(64), nullable=False)
    plan_snapshot: Mapped[str] = mapped_column(Text, nullable=False)
    working_directory: Mapped[str] = mapped_column(String(1000), nullable=False)
    model_key: Mapped[str] = mapped_column(String(600), nullable=False)
    external_session_id: Mapped[str | None] = mapped_column(String(300), nullable=True)
    pending_permission: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    result_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(120), nullable=True)
    error_params: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
