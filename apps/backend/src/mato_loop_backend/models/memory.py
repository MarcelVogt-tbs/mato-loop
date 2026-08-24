from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Computed,
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
from sqlalchemy.dialects.postgresql import TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column, relationship

from mato_loop_backend.db.base import Base

if TYPE_CHECKING:
    from mato_loop_backend.models.session import ChatSession, Message


class MemorySourceType(StrEnum):
    TOOL_CALL = "tool_call"
    DREAMING = "dreaming"
    MANUAL = "manual"


class MemoryCategory(StrEnum):
    CONTEXT = "context"
    FACT = "fact"
    GOAL = "goal"
    IDENTITY = "identity"
    PREFERENCE = "preference"


class MemoryAudience(StrEnum):
    PRIVATE_ONLY = "private_only"
    PUBLIC_ONLY = "public_only"
    BOTH = "both"


class MemoryPinKind(StrEnum):
    PREFERRED_NAME = "preferred_name"
    FORM_OF_ADDRESS = "form_of_address"
    PRONOUNS = "pronouns"
    PRIMARY_LANGUAGE = "primary_language"
    PRIMARY_RESIDENCE = "primary_residence"


class MemoryMode(StrEnum):
    FULL = "full"
    READ_ONLY = "read_only"
    DISABLED = "disabled"


class MemoryDreamStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    BLOCKED = "blocked"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class MemoryEmbeddingStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    READY = "ready"
    FAILED = "failed"


class MemoryDreamTrigger(StrEnum):
    SCHEDULED = "scheduled"
    MANUAL = "manual"


class MemoryRecord(Base):
    __tablename__ = "memories"
    __table_args__ = (
        CheckConstraint(
            "source_type IN ('tool_call', 'dreaming', 'manual')",
            name="source_type_valid",
        ),
        CheckConstraint(
            "audience IN ('private_only', 'public_only', 'both')",
            name="audience_valid",
        ),
        CheckConstraint(
            "category IN ('context', 'fact', 'goal', 'identity', 'preference')",
            name="category_valid",
        ),
        CheckConstraint(
            "pin_kind IS NULL OR pin_kind IN "
            "('preferred_name', 'form_of_address', 'pronouns', "
            "'primary_language', 'primary_residence')",
            name="pin_kind_valid",
        ),
        CheckConstraint(
            "pin_kind IS NULL OR (category = 'identity' AND pinned)",
            name="pin_kind_identity_pinned",
        ),
        CheckConstraint("revision >= 1", name="revision_positive"),
        Index("ix_memories_search_vector", "search_vector", postgresql_using="gin"),
        Index("ix_memories_user_category", "user_id", "category"),
        Index(
            "uq_memories_user_normalized_content_hash",
            "user_id",
            "normalized_content_hash",
            unique=True,
        ),
        Index(
            "uq_memories_tool_call_provenance",
            "source_run_id",
            "source_tool_call_id",
            unique=True,
            postgresql_where=text("source_type = 'tool_call'"),
        ),
        Index(
            "uq_memories_user_pin_kind_audience",
            "user_id",
            "pin_kind",
            "audience",
            unique=True,
            postgresql_where=text("pin_kind IS NOT NULL"),
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source_session_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("chat_sessions.id", ondelete="SET NULL"), nullable=True, index=True
    )
    source_message_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("messages.id", ondelete="SET NULL"), nullable=True, index=True
    )
    source_run_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    source_dream_run_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("memory_dream_runs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    source_tool_call_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    source_type: Mapped[str] = mapped_column(
        String(20), nullable=False, default=MemorySourceType.MANUAL.value
    )
    pinned: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false"), index=True
    )
    audience: Mapped[str] = mapped_column(
        String(20), nullable=False, default=MemoryAudience.BOTH.value
    )
    pin_kind: Mapped[str | None] = mapped_column(String(40), nullable=True)
    category: Mapped[str] = mapped_column(String(20), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    search_vector: Mapped[Any] = mapped_column(
        TSVECTOR,
        Computed("to_tsvector('simple'::regconfig, coalesce(content, ''))", persisted=True),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    source_session: Mapped[ChatSession | None] = relationship()
    source_message: Mapped[Message | None] = relationship(foreign_keys=[source_message_id])
    source_messages: Mapped[list[MemorySourceMessage]] = relationship(
        cascade="all, delete-orphan", passive_deletes=True
    )
    embedding: Mapped[MemoryEmbedding | None] = relationship(
        back_populates="memory", cascade="all, delete-orphan", passive_deletes=True
    )


class MemoryEmbedding(Base):
    __tablename__ = "memory_embeddings"
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued', 'running', 'ready', 'failed')",
            name="status_valid",
        ),
        CheckConstraint("attempts >= 0", name="attempts_nonnegative"),
        CheckConstraint(
            "(status = 'ready' AND embedding IS NOT NULL) OR "
            "(status <> 'ready' AND embedding IS NULL)",
            name="ready_vector_valid",
        ),
        Index("ix_memory_embeddings_queue", "status", "next_attempt_at"),
    )

    memory_id: Mapped[UUID] = mapped_column(
        ForeignKey("memories.id", ondelete="CASCADE"), primary_key=True
    )
    profile_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=MemoryEmbeddingStatus.QUEUED.value
    )
    vector: Mapped[Any | None] = mapped_column("embedding", Vector(384), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(120), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    memory: Mapped[MemoryRecord] = relationship(back_populates="embedding")


class MemorySourceMessage(Base):
    __tablename__ = "memory_source_messages"
    __table_args__ = (UniqueConstraint("memory_id", "message_id"),)

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    memory_id: Mapped[UUID] = mapped_column(
        ForeignKey("memories.id", ondelete="CASCADE"), nullable=False, index=True
    )
    message_id: Mapped[UUID] = mapped_column(
        ForeignKey("messages.id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class MemoryDreamState(Base):
    __tablename__ = "memory_dream_states"
    __table_args__ = (UniqueConstraint("session_id"),)

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    session_id: Mapped[UUID] = mapped_column(
        ForeignKey("chat_sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    last_processed_message_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("messages.id", ondelete="SET NULL"), nullable=True
    )
    next_scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class MemoryDreamRun(Base):
    __tablename__ = "memory_dream_runs"
    __table_args__ = (
        UniqueConstraint(
            "session_id",
            "source_from_message_id",
            "source_to_message_id",
            name="uq_memory_dream_runs_source_range",
        ),
        CheckConstraint(
            "status IN ('queued', 'running', 'blocked', 'completed', 'failed', 'cancelled')",
            name="status_valid",
        ),
        CheckConstraint("trigger IN ('scheduled', 'manual')", name="trigger_valid"),
        CheckConstraint("attempts >= 0", name="attempts_nonnegative"),
        CheckConstraint("source_message_count >= 0", name="source_count_nonnegative"),
        CheckConstraint("candidate_count >= 0", name="candidate_count_nonnegative"),
        CheckConstraint("created_memory_count >= 0", name="created_count_nonnegative"),
        CheckConstraint("updated_memory_count >= 0", name="updated_count_nonnegative"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    session_id: Mapped[UUID] = mapped_column(
        ForeignKey("chat_sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    trigger: Mapped[str] = mapped_column(String(20), nullable=False)
    source_from_message_id: Mapped[UUID] = mapped_column(
        ForeignKey("messages.id", ondelete="CASCADE"), nullable=False
    )
    source_to_message_id: Mapped[UUID] = mapped_column(
        ForeignKey("messages.id", ondelete="CASCADE"), nullable=False
    )
    model_config_id: Mapped[UUID | None] = mapped_column(nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error: Mapped[str | None] = mapped_column(String(120), nullable=True)
    source_message_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    candidate_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_memory_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_memory_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    source_session: Mapped[ChatSession] = relationship()
