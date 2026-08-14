from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Integer, String, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from mato_loop_backend.db.base import Base


class ExecutionAgent(Base):
    __tablename__ = "execution_agents"
    __table_args__ = (
        CheckConstraint(
            "(registration_token_hash IS NULL) = (registration_expires_at IS NULL)",
            name="registration_complete",
        ),
        CheckConstraint(
            "(token_hash IS NULL) = (token_is_legacy IS NULL)",
            name="durable_token_classified",
        ),
        CheckConstraint(
            "token_hash IS NULL OR registration_token_hash IS NULL",
            name="credential_state_exclusive",
        ),
        CheckConstraint(
            "(home_directory IS NULL) = (home_root_id IS NULL) AND "
            "(home_directory IS NULL) = (home_root_revision IS NULL)",
            name="home_metadata_complete",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    platform: Mapped[str | None] = mapped_column(String(120), nullable=True)
    token_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True)
    token_is_legacy: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    registration_token_hash: Mapped[str | None] = mapped_column(
        String(64), nullable=True, unique=True
    )
    registration_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    protocol_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    operations: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    home_directory: Mapped[str | None] = mapped_column(String(4096), nullable=True)
    home_root_id: Mapped[UUID | None] = mapped_column(nullable=True)
    home_root_revision: Mapped[str | None] = mapped_column(String(64), nullable=True)
    home_relative_directory: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    home_override: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    revision: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )
    last_connected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    roots: Mapped[list[ExecutionAgentRoot]] = relationship(
        back_populates="agent",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="ExecutionAgentRoot.label",
    )


class ExecutionAgentRoot(Base):
    __tablename__ = "execution_agent_roots"

    execution_agent_id: Mapped[UUID] = mapped_column(
        ForeignKey("execution_agents.id", ondelete="CASCADE"), primary_key=True
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    label: Mapped[str] = mapped_column(String(160), nullable=False)
    revision: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    agent: Mapped[ExecutionAgent] = relationship(back_populates="roots")
