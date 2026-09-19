from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from mato_loop_backend.db.base import Base


class Project(Base):
    __tablename__ = "projects"
    __table_args__ = (
        UniqueConstraint("id", "user_id", name="uq_projects_id_user_id"),
        UniqueConstraint(
            "user_id",
            "coding_integration_id",
            "coding_workspace_key",
            name="uq_projects_coding_workspace",
        ),
        CheckConstraint(
            "(coding_integration_id IS NULL AND coding_project_key IS NULL "
            "AND coding_workspace_key IS NULL) OR "
            "(coding_integration_id IS NOT NULL AND coding_project_key IS NOT NULL "
            "AND coding_workspace_key IS NOT NULL)",
            name="project_coding_binding_complete",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(240), nullable=False)
    instructions: Mapped[str] = mapped_column(
        Text, nullable=False, default="", server_default=""
    )
    default_execution_agent_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("execution_agents.id", ondelete="SET NULL"), nullable=True, index=True
    )
    default_current_directory: Mapped[str | None] = mapped_column(
        String(4096), nullable=True
    )
    coding_integration_id: Mapped[UUID | None] = mapped_column(nullable=True, index=True)
    coding_project_key: Mapped[str | None] = mapped_column(String(300), nullable=True)
    coding_workspace_key: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    coding_analysis_model_key: Mapped[str | None] = mapped_column(String(600), nullable=True)
    coding_analysis_model_variant: Mapped[str | None] = mapped_column(String(300), nullable=True)
    coding_execution_model_key: Mapped[str | None] = mapped_column(String(600), nullable=True)
    coding_execution_model_variant: Mapped[str | None] = mapped_column(String(300), nullable=True)
    coding_queue_pause_reason: Mapped[str | None] = mapped_column(String(80), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
