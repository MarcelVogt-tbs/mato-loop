from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any
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
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column, relationship

from mato_loop_backend.db.base import Base
from mato_loop_backend.settings.models import TrustLevel


class WebSearchPolicy(StrEnum):
    REQUIRED = "required"
    AVAILABLE = "available"
    DISABLED = "disabled"


class TitleGenerationStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


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


class UserAccount(Base):
    __tablename__ = "user_accounts"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    username: Mapped[str] = mapped_column(String(120), nullable=False, unique=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class LoginSession(Base):
    __tablename__ = "login_sessions"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Credential(Base):
    __tablename__ = "credentials"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    kind: Mapped[str] = mapped_column(String(80), nullable=False)
    label: Mapped[str] = mapped_column(String(160), nullable=False)
    encrypted_payload: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, default=dict
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class ChatSession(Base):
    __tablename__ = "chat_sessions"
    __table_args__ = (
        CheckConstraint(
            "web_search_policy IN ('required', 'available', 'disabled')",
            name="web_search_policy_valid",
        ),
        CheckConstraint(
            "memory_mode IN ('full', 'read_only', 'disabled')",
            name="memory_mode_valid",
        ),
        CheckConstraint(
            "title_generation_status IN "
            "('pending', 'running', 'completed', 'failed', 'skipped')",
            name="title_generation_status_valid",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(240), nullable=False, default="Neue Session")
    title_generation_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=TitleGenerationStatus.PENDING.value
    )
    is_pinned: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    model_config_id: Mapped[UUID | None] = mapped_column(nullable=True)
    memory_mode: Mapped[str] = mapped_column(
        String(20), nullable=False, default=MemoryMode.FULL.value
    )
    web_search_policy: Mapped[str] = mapped_column(
        String(20), nullable=False, default=WebSearchPolicy.AVAILABLE.value
    )
    preferred_execution_agent_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("execution_agents.id", ondelete="SET NULL"), nullable=True, index=True
    )
    preferred_root_id: Mapped[UUID | None] = mapped_column(nullable=True)
    working_directory: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    current_directory: Mapped[str | None] = mapped_column(String(4096), nullable=True)
    workspace_access: Mapped[str] = mapped_column(String(20), nullable=False, default="read-only")
    private_integration_data_used: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    last_read_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    messages: Mapped[list[Message]] = relationship(
        back_populates="chat_session", cascade="all, delete-orphan", passive_deletes=True
    )
    context_summaries: Mapped[list[ContextSummary]] = relationship(
        back_populates="chat_session", cascade="all, delete-orphan", passive_deletes=True
    )


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
    kind: Mapped[str] = mapped_column(String(80), nullable=False)
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    deduplication_key: Mapped[str] = mapped_column(String(255), nullable=False)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (
        CheckConstraint(
            "trust_level IN ('public', 'private')", name="trust_level_valid"
        ),
        CheckConstraint(
            "generation_status IN ('completed', 'interrupted')",
            name="generation_status_valid",
        ),
        CheckConstraint(
            "(error_code IS NULL AND error_params IS NULL AND error_run_id IS NULL) OR "
            "(error_code IS NOT NULL AND role = 'assistant' AND error_run_id IS NOT NULL)",
            name="message_error_valid",
        ),
        UniqueConstraint("error_run_id"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    session_id: Mapped[UUID] = mapped_column(
        ForeignKey("chat_sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role: Mapped[str] = mapped_column(String(30), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    model_name: Mapped[str | None] = mapped_column(String(500), nullable=True)
    generation_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="completed"
    )
    trust_level: Mapped[str] = mapped_column(
        String(20), nullable=False, default=TrustLevel.PUBLIC.value, index=True
    )
    tool_call_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    tool_calls: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB, nullable=True)
    provider_data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    public_reasoning: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(120), nullable=True)
    error_params: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    error_run_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )

    chat_session: Mapped[ChatSession] = relationship(back_populates="messages")
    attachments: Mapped[list[MessageAttachment]] = relationship(
        back_populates="message",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="MessageAttachment.position",
    )
    search_chunks: Mapped[list[MessageSearchChunk]] = relationship(
        back_populates="message",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="MessageSearchChunk.chunk_index",
    )


class MessageAttachment(Base):
    __tablename__ = "message_attachments"
    __table_args__ = (
        CheckConstraint("position >= 0", name="position_nonnegative"),
        CheckConstraint(
            "kind IN ('image', 'audio', 'video', 'text', 'document', 'file')",
            name="kind_valid",
        ),
        CheckConstraint(
            "category IN ('user_upload', 'generated', 'downloaded')",
            name="category_valid",
        ),
        CheckConstraint(
            "purpose IN ('attachment', 'artifact')",
            name="purpose_valid",
        ),
        CheckConstraint(
            "processing_status IS NULL OR processing_status IN ('pending', 'ready', 'failed')",
            name="processing_status_valid",
        ),
        CheckConstraint("size_bytes > 0", name="size_bytes_positive"),
        CheckConstraint("width IS NULL OR width > 0", name="width_positive"),
        CheckConstraint("height IS NULL OR height > 0", name="height_positive"),
        CheckConstraint("duration_ms IS NULL OR duration_ms > 0", name="duration_ms_positive"),
        UniqueConstraint("message_id", "position"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    message_id: Mapped[UUID] = mapped_column(
        ForeignKey("messages.id", ondelete="CASCADE"), nullable=False, index=True
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False, default="image")
    category: Mapped[str] = mapped_column(
        String(20), nullable=False, default="user_upload", server_default="user_upload"
    )
    purpose: Mapped[str] = mapped_column(
        String(20), nullable=False, default="attachment", server_default="attachment"
    )
    processing_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    mime_type: Mapped[str] = mapped_column(String(100), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    # Keep the existing column for schema evolution while storing a relative path
    # inside the associated session tree.
    storage_path: Mapped[str] = mapped_column("storage_key", String(500), nullable=False, unique=True)
    filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    message: Mapped[Message] = relationship(back_populates="attachments")


class MessageSearchChunk(Base):
    __tablename__ = "message_search_chunks"
    __table_args__ = (
        CheckConstraint("chunk_index >= 0", name="chunk_index_nonnegative"),
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
        UniqueConstraint("message_id", "chunk_index"),
        Index("ix_message_search_chunks_search_vector", "search_vector", postgresql_using="gin"),
        Index("ix_message_search_chunks_queue", "status", "next_attempt_at"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    message_id: Mapped[UUID] = mapped_column(
        ForeignKey("messages.id", ondelete="CASCADE"), nullable=False, index=True
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    search_vector: Mapped[Any] = mapped_column(
        TSVECTOR,
        Computed("to_tsvector('simple'::regconfig, coalesce(content, ''))", persisted=True),
        nullable=False,
    )
    profile_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=MemoryEmbeddingStatus.QUEUED.value
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(120), nullable=True)
    vector: Mapped[Any | None] = mapped_column("embedding", Vector(384), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    message: Mapped[Message] = relationship(back_populates="search_chunks")


class ContextSummary(Base):
    __tablename__ = "context_summaries"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    session_id: Mapped[UUID] = mapped_column(
        ForeignKey("chat_sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    covered_through_message_id: Mapped[UUID] = mapped_column(
        ForeignKey("messages.id", ondelete="CASCADE"), nullable=False, index=True
    )
    previous_summary_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("context_summaries.id", ondelete="SET NULL"), nullable=True
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    estimated_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    model_config_id: Mapped[UUID | None] = mapped_column(nullable=True)
    is_fallback: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    chat_session: Mapped[ChatSession] = relationship(back_populates="context_summaries")


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
    execution_root_id: Mapped[UUID | None] = mapped_column(nullable=True)
    execution_root_revision: Mapped[str | None] = mapped_column(String(128), nullable=True)
    working_directory_snapshot: Mapped[str | None] = mapped_column(String(1000), nullable=True)
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

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    session_id: Mapped[UUID] = mapped_column(
        ForeignKey("chat_sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    run_id: Mapped[UUID] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    action_kind: Mapped[str] = mapped_column(String(80), nullable=False)
    risk_level: Mapped[str] = mapped_column(String(20), nullable=False, default="medium")
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    capability: Mapped[str] = mapped_column(String(120), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(80), nullable=False)
    resource_scope: Mapped[str] = mapped_column(String(1200), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    available_decisions: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="pending", index=True)
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
    resource_scope: Mapped[str] = mapped_column(String(1200), nullable=False)
    source_approval_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("approval_requests.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class MemoryRecord(Base):
    __tablename__ = "memories"
    __table_args__ = (
        CheckConstraint(
            "source_type IN ('tool_call', 'dreaming', 'manual')",
            name="source_type_valid",
        ),
        CheckConstraint(
            "trust_level IN ('public', 'private')",
            name="trust_level_valid",
        ),
        CheckConstraint(
            "category IN ('context', 'fact', 'goal', 'identity', 'preference')",
            name="category_valid",
        ),
        CheckConstraint("revision >= 1", name="revision_positive"),
        Index("ix_memories_search_vector", "search_vector", postgresql_using="gin"),
        Index("ix_memories_category", "category"),
        Index("uq_memories_normalized_content_hash", "normalized_content_hash", unique=True),
        Index(
            "uq_memories_tool_call_provenance",
            "source_run_id",
            "source_tool_call_id",
            unique=True,
            postgresql_where=text("source_type = 'tool_call'"),
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
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
    trust_level: Mapped[str] = mapped_column(
        String(20), nullable=False, default=TrustLevel.PUBLIC.value
    )
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
