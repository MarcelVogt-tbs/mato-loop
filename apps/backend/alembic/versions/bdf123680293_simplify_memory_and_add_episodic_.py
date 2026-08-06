"""Simplify memory and add episodic retrieval.

Revision ID: bdf123680293
Revises: 5ef5d644ca00
Create Date: 2026-08-05 20:12:24.619596
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

revision: str = "bdf123680293"
down_revision: str | None = "5ef5d644ca00"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "message_search_chunks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("message_id", sa.Uuid(), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "search_vector",
            postgresql.TSVECTOR(),
            sa.Computed(
                "to_tsvector('simple'::regconfig, coalesce(content, ''))",
                persisted=True,
            ),
            nullable=False,
        ),
        sa.Column("profile_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(length=120), nullable=True),
        sa.Column("embedding", Vector(dim=384), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(status = 'ready' AND embedding IS NOT NULL) OR "
            "(status <> 'ready' AND embedding IS NULL)",
            name=op.f("ck_message_search_chunks_ready_vector_valid"),
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'ready', 'failed')",
            name=op.f("ck_message_search_chunks_status_valid"),
        ),
        sa.CheckConstraint(
            "attempts >= 0",
            name=op.f("ck_message_search_chunks_attempts_nonnegative"),
        ),
        sa.CheckConstraint(
            "chunk_index >= 0",
            name=op.f("ck_message_search_chunks_chunk_index_nonnegative"),
        ),
        sa.ForeignKeyConstraint(
            ["message_id"],
            ["messages.id"],
            name=op.f("fk_message_search_chunks_message_id_messages"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_message_search_chunks")),
        sa.UniqueConstraint(
            "message_id",
            "chunk_index",
            name=op.f("uq_message_search_chunks_message_id"),
        ),
    )
    op.create_index(
        op.f("ix_message_search_chunks_message_id"),
        "message_search_chunks",
        ["message_id"],
        unique=False,
    )
    op.create_index(
        "ix_message_search_chunks_queue",
        "message_search_chunks",
        ["status", "next_attempt_at"],
        unique=False,
    )
    op.create_index(
        "ix_message_search_chunks_search_vector",
        "message_search_chunks",
        ["search_vector"],
        unique=False,
        postgresql_using="gin",
    )
    op.drop_index(
        op.f("ix_memory_exposures_agent_run_id"), table_name="memory_exposures"
    )
    op.drop_index(
        op.f("ix_memory_exposures_memory_id"), table_name="memory_exposures"
    )
    op.drop_index(
        op.f("ix_memory_exposures_status_exposed"), table_name="memory_exposures"
    )
    op.drop_table("memory_exposures")
    op.add_column(
        "memories",
        sa.Column(
            "pinned",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )
    op.execute(
        "UPDATE memories SET pinned = true "
        "WHERE tier_locked = true AND retrieval_level = 'instant'"
    )
    op.create_index(
        op.f("ix_memories_pinned"), "memories", ["pinned"], unique=False
    )
    op.drop_column("memories", "tier_locked")
    op.drop_column("memories", "retrieval_level")
    op.drop_column("memories", "last_used_at")
    op.drop_column("memories", "retrieval_level_changed_at")
    op.drop_column("memories", "tier_locked_at")


def downgrade() -> None:
    op.add_column(
        "memories",
        sa.Column(
            "tier_locked_at",
            postgresql.TIMESTAMP(timezone=True),
            autoincrement=False,
            nullable=True,
        ),
    )
    op.add_column(
        "memories",
        sa.Column(
            "retrieval_level_changed_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            autoincrement=False,
            nullable=False,
        ),
    )
    op.add_column(
        "memories",
        sa.Column(
            "last_used_at",
            postgresql.TIMESTAMP(timezone=True),
            autoincrement=False,
            nullable=True,
        ),
    )
    op.add_column(
        "memories",
        sa.Column(
            "retrieval_level",
            sa.VARCHAR(length=20),
            server_default="retrieve",
            autoincrement=False,
            nullable=False,
        ),
    )
    op.add_column(
        "memories",
        sa.Column(
            "tier_locked",
            sa.BOOLEAN(),
            server_default=sa.text("false"),
            autoincrement=False,
            nullable=False,
        ),
    )
    op.execute(
        "UPDATE memories SET retrieval_level = 'instant', tier_locked = true "
        "WHERE pinned = true"
    )
    op.create_check_constraint(
        op.f("ck_memories_retrieval_level_valid"),
        "memories",
        "retrieval_level IN ('instant', 'retrieve')",
    )
    op.alter_column("memories", "retrieval_level", server_default=None)
    op.drop_index(op.f("ix_memories_pinned"), table_name="memories")
    op.drop_column("memories", "pinned")
    op.create_table(
        "memory_exposures",
        sa.Column("id", sa.UUID(), autoincrement=False, nullable=False),
        sa.Column("memory_id", sa.UUID(), autoincrement=False, nullable=False),
        sa.Column(
            "memory_content_hash",
            sa.VARCHAR(length=64),
            autoincrement=False,
            nullable=False,
        ),
        sa.Column("agent_run_id", sa.UUID(), autoincrement=False, nullable=False),
        sa.Column(
            "assistant_message_id", sa.UUID(), autoincrement=False, nullable=True
        ),
        sa.Column("status", sa.VARCHAR(length=20), autoincrement=False, nullable=False),
        sa.Column(
            "exposed_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            autoincrement=False,
            nullable=False,
        ),
        sa.Column(
            "evaluated_at",
            postgresql.TIMESTAMP(timezone=True),
            autoincrement=False,
            nullable=True,
        ),
        sa.CheckConstraint(
            "status::text = ANY (ARRAY['pending'::character varying, "
            "'used'::character varying, 'unused'::character varying]::text[])",
            name=op.f("ck_memory_exposures_status_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["agent_run_id"],
            ["agent_runs.id"],
            name=op.f("fk_memory_exposures_agent_run_id_agent_runs"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["assistant_message_id"],
            ["messages.id"],
            name=op.f("fk_memory_exposures_assistant_message_id_messages"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["memory_id"],
            ["memories.id"],
            name=op.f("fk_memory_exposures_memory_id_memories"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_memory_exposures")),
        sa.UniqueConstraint(
            "memory_id",
            "agent_run_id",
            name=op.f("uq_memory_exposures_memory_id"),
        ),
    )
    op.create_index(
        op.f("ix_memory_exposures_status_exposed"),
        "memory_exposures",
        ["status", "exposed_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_memory_exposures_memory_id"),
        "memory_exposures",
        ["memory_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_memory_exposures_agent_run_id"),
        "memory_exposures",
        ["agent_run_id"],
        unique=False,
    )
    op.drop_index(
        "ix_message_search_chunks_search_vector",
        table_name="message_search_chunks",
        postgresql_using="gin",
    )
    op.drop_index(
        "ix_message_search_chunks_queue", table_name="message_search_chunks"
    )
    op.drop_index(
        op.f("ix_message_search_chunks_message_id"),
        table_name="message_search_chunks",
    )
    op.drop_table("message_search_chunks")
