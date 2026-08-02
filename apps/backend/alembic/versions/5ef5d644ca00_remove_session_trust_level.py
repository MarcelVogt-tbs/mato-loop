"""remove session trust level

Revision ID: 5ef5d644ca00
Revises: fdbffbb81c99
Create Date: 2026-08-01 20:53:48.572794
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "5ef5d644ca00"
down_revision: Union[str, None] = "fdbffbb81c99"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Existing summaries may contain resource data from the old aggregate trust policy.
    op.execute("DELETE FROM context_summaries")
    op.drop_column("chat_sessions", "trust_level")


def downgrade() -> None:
    op.add_column(
        "chat_sessions",
        sa.Column("trust_level", sa.VARCHAR(length=20), nullable=True),
    )
    op.execute(
        """
        UPDATE chat_sessions
        SET trust_level = CASE
            WHEN EXISTS (
                SELECT 1 FROM agent_runs
                WHERE agent_runs.session_id = chat_sessions.id
                  AND agent_runs.trust_level = 'private'
            ) OR EXISTS (
                SELECT 1 FROM messages
                WHERE messages.session_id = chat_sessions.id
                  AND messages.trust_level = 'private'
            ) THEN 'private'
            ELSE 'public'
        END
        """
    )
    op.alter_column("chat_sessions", "trust_level", nullable=False)
    op.create_check_constraint(
        "ck_chat_sessions_trust_level_valid",
        "chat_sessions",
        "trust_level IN ('public', 'private')",
    )
