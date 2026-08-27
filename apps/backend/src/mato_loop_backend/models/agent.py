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
from sqlalchemy.orm import Mapped, mapped_column

from mato_loop_backend.db.base import Base
from mato_loop_backend.settings.models import TrustLevel


class AgentRun(Base):
    __tablename__ = "agent_runs"
    __table_args__ = (
        CheckConstraint(
            "trust_level IN ('public', 'private')", name="trust_level_valid"
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    session_id: Mapped[UUID] = mapped_column(
        ForeignKey("chat_sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="queued", index=True)
    input_message_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("messages.id", ondelete="SET NULL", use_alter=True), nullable=True, unique=True
    )
    result_message_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("messages.id", ondelete="SET NULL", use_alter=True), nullable=True, unique=True
    )
    required_skill: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    model_config_id: Mapped[UUID | None] = mapped_column(nullable=True)
    model_name: Mapped[str | None] = mapped_column(String(500), nullable=True)
    trust_level: Mapped[str] = mapped_column(
        String(20), nullable=False, default=TrustLevel.PUBLIC.value
    )
    pseudonymization_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    execution_agent_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("execution_agents.id", ondelete="SET NULL"), nullable=True, index=True
    )
    execution_agent_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    current_directory_snapshot: Mapped[str | None] = mapped_column(String(4096), nullable=True)
    execution_operations: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ApprovalRequest(Base):
    __tablename__ = "approval_requests"
    __table_args__ = (
        CheckConstraint(
            "request_type IN ('agent_action', 'memory_change')",
            name="request_type_valid",
        ),
        CheckConstraint(
            "status IN ('pending', 'approved', 'denied', 'expired')",
            name="status_valid",
        ),
        Index(
            "ix_approval_requests_user_pending",
            "user_id",
            "created_at",
            postgresql_where=text("status = 'pending'"),
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    request_type: Mapped[str] = mapped_column(
        String(40), nullable=False, default="agent_action", server_default=text("'agent_action'")
    )
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    session_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("chat_sessions.id", ondelete="SET NULL"), nullable=True, index=True
    )
    run_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    action_kind: Mapped[str] = mapped_column(String(80), nullable=False)
    risk_level: Mapped[str] = mapped_column(String(20), nullable=False, default="medium")
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    capability: Mapped[str] = mapped_column(String(120), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(80), nullable=False)
    resource_scope: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    available_decisions: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="pending")
    decision: Mapped[str | None] = mapped_column(String(40), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class QuestionRequest(Base):
    __tablename__ = "question_requests"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'answered', 'rejected', 'expired')",
            name="status_valid",
        ),
        UniqueConstraint("run_id", "tool_call_id"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    session_id: Mapped[UUID] = mapped_column(
        ForeignKey("chat_sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    run_id: Mapped[UUID] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    tool_call_id: Mapped[str] = mapped_column(String(200), nullable=False)
    questions: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    answers: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="pending", index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    answered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PermissionGrant(Base):
    __tablename__ = "permission_grants"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    session_id: Mapped[UUID] = mapped_column(
        ForeignKey("chat_sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    capability: Mapped[str] = mapped_column(String(120), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(80), nullable=False)
    resource_scope: Mapped[str] = mapped_column(Text, nullable=False)
    source_approval_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("approval_requests.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
