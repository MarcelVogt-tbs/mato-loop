"""add session reasoning effort

Revision ID: 99777f8f87e9
Revises: 0b56a0dd9c41
Create Date: 2026-08-19 21:01:38.314193
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "99777f8f87e9"
down_revision: Union[str, None] = "0b56a0dd9c41"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "chat_sessions",
        sa.Column("reasoning_effort", sa.String(length=20), nullable=True),
    )
    op.create_check_constraint(
        "reasoning_effort_valid",
        "chat_sessions",
        "reasoning_effort IS NULL OR reasoning_effort IN "
        "('none', 'minimal', 'low', 'medium', 'high', 'xhigh')",
    )


def downgrade() -> None:
    op.drop_constraint("reasoning_effort_valid", "chat_sessions", type_="check")
    op.drop_column("chat_sessions", "reasoning_effort")
