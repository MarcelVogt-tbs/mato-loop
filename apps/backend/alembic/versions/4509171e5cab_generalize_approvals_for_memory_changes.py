"""generalize approvals for memory changes

Revision ID: 4509171e5cab
Revises: 03b339c79ea7
Create Date: 2026-08-16 19:41:45.944323
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "4509171e5cab"
down_revision: Union[str, None] = "03b339c79ea7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "approval_requests",
        sa.Column(
            "request_type",
            sa.String(length=40),
            server_default=sa.text("'agent_action'"),
            nullable=False,
        ),
    )
    op.add_column(
        "approval_requests", sa.Column("user_id", sa.Uuid(), nullable=True)
    )
    op.execute(
        sa.text(
            "UPDATE approval_requests AS approval "
            "SET user_id = session.user_id "
            "FROM chat_sessions AS session "
            "WHERE session.id = approval.session_id"
        )
    )
    op.alter_column("approval_requests", "user_id", nullable=False)
    op.alter_column(
        "approval_requests", "session_id", existing_type=sa.UUID(), nullable=True
    )
    op.alter_column(
        "approval_requests", "run_id", existing_type=sa.UUID(), nullable=True
    )
    op.drop_index(
        op.f("ix_approval_requests_status"), table_name="approval_requests"
    )
    op.create_index(
        op.f("ix_approval_requests_user_id"),
        "approval_requests",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        "ix_approval_requests_user_pending",
        "approval_requests",
        ["user_id", "created_at"],
        unique=False,
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.drop_constraint(
        op.f("fk_approval_requests_session_id_chat_sessions"),
        "approval_requests",
        type_="foreignkey",
    )
    op.drop_constraint(
        op.f("fk_approval_requests_run_id_agent_runs"),
        "approval_requests",
        type_="foreignkey",
    )
    op.create_foreign_key(
        op.f("fk_approval_requests_session_id_chat_sessions"),
        "approval_requests",
        "chat_sessions",
        ["session_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        op.f("fk_approval_requests_run_id_agent_runs"),
        "approval_requests",
        "agent_runs",
        ["run_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        op.f("fk_approval_requests_user_id_user_accounts"),
        "approval_requests",
        "user_accounts",
        ["user_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_check_constraint(
        op.f("ck_approval_requests_request_type_valid"),
        "approval_requests",
        "request_type IN ('agent_action', 'memory_change')",
    )
    op.create_check_constraint(
        op.f("ck_approval_requests_status_valid"),
        "approval_requests",
        "status IN ('pending', 'approved', 'denied', 'expired')",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("ck_approval_requests_status_valid"),
        "approval_requests",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_approval_requests_request_type_valid"),
        "approval_requests",
        type_="check",
    )
    op.drop_constraint(
        op.f("fk_approval_requests_user_id_user_accounts"),
        "approval_requests",
        type_="foreignkey",
    )
    op.drop_constraint(
        op.f("fk_approval_requests_run_id_agent_runs"),
        "approval_requests",
        type_="foreignkey",
    )
    op.drop_constraint(
        op.f("fk_approval_requests_session_id_chat_sessions"),
        "approval_requests",
        type_="foreignkey",
    )
    op.create_foreign_key(
        op.f("fk_approval_requests_run_id_agent_runs"),
        "approval_requests",
        "agent_runs",
        ["run_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        op.f("fk_approval_requests_session_id_chat_sessions"),
        "approval_requests",
        "chat_sessions",
        ["session_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.drop_index(
        "ix_approval_requests_user_pending",
        table_name="approval_requests",
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.drop_index(
        op.f("ix_approval_requests_user_id"), table_name="approval_requests"
    )
    op.create_index(
        op.f("ix_approval_requests_status"),
        "approval_requests",
        ["status"],
        unique=False,
    )
    op.execute(
        sa.text(
            "DELETE FROM approval_requests "
            "WHERE request_type = 'memory_change' OR session_id IS NULL OR run_id IS NULL"
        )
    )
    op.alter_column(
        "approval_requests", "run_id", existing_type=sa.UUID(), nullable=False
    )
    op.alter_column(
        "approval_requests", "session_id", existing_type=sa.UUID(), nullable=False
    )
    op.drop_column("approval_requests", "user_id")
    op.drop_column("approval_requests", "request_type")
