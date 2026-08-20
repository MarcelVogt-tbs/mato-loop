"""extend reasoning effort levels

Revision ID: 83588852dbeb
Revises: 0a5a42090d75
Create Date: 2026-08-20 20:18:05.202736
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "83588852dbeb"
down_revision: Union[str, None] = "0a5a42090d75"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint("reasoning_effort_valid", "chat_sessions", type_="check")
    op.create_check_constraint(
        "reasoning_effort_valid",
        "chat_sessions",
        "reasoning_effort IS NULL OR reasoning_effort IN "
        "('none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max', 'ultra')",
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE chat_sessions SET reasoning_effort = 'high' "
            "WHERE reasoning_effort IN ('max', 'ultra')"
        )
    )
    op.drop_constraint("reasoning_effort_valid", "chat_sessions", type_="check")
    op.create_check_constraint(
        "reasoning_effort_valid",
        "chat_sessions",
        "reasoning_effort IS NULL OR reasoning_effort IN "
        "('none', 'minimal', 'low', 'medium', 'high', 'xhigh')",
    )
