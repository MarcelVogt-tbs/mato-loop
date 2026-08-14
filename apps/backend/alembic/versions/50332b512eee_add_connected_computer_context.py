"""add connected computer context

Revision ID: 50332b512eee
Revises: b4c151fc44ac
Create Date: 2026-08-12 22:28:15.939191
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "50332b512eee"
down_revision: Union[str, None] = "b4c151fc44ac"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "agent_runs",
        sa.Column("current_directory_snapshot", sa.String(length=4096), nullable=True),
    )
    op.add_column(
        "chat_sessions",
        sa.Column("current_directory", sa.String(length=4096), nullable=True),
    )
    op.add_column(
        "execution_agents",
        sa.Column("home_directory", sa.String(length=4096), nullable=True),
    )
    op.add_column("execution_agents", sa.Column("home_root_id", sa.Uuid(), nullable=True))
    op.add_column(
        "execution_agents",
        sa.Column("home_root_revision", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "execution_agents",
        sa.Column("home_relative_directory", sa.String(length=1000), nullable=True),
    )
    op.create_check_constraint(
        op.f("ck_execution_agents_home_metadata_complete"),
        "execution_agents",
        "(home_directory IS NULL) = (home_root_id IS NULL) AND "
        "(home_directory IS NULL) = (home_root_revision IS NULL)",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("ck_execution_agents_home_metadata_complete"),
        "execution_agents",
        type_="check",
    )
    op.drop_column("execution_agents", "home_relative_directory")
    op.drop_column("execution_agents", "home_root_revision")
    op.drop_column("execution_agents", "home_root_id")
    op.drop_column("execution_agents", "home_directory")
    op.drop_column("chat_sessions", "current_directory")
    op.drop_column("agent_runs", "current_directory_snapshot")
