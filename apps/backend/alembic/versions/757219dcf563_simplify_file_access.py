"""simplify file access

Revision ID: 757219dcf563
Revises: 65024025ef28
Create Date: 2026-08-21 22:36:44.428753
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "757219dcf563"
down_revision: str | None = "65024025ef28"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE settings
        SET data = jsonb_set(
            data,
            '{policies}',
            COALESCE(data->'policies', '{}'::jsonb) - 'draft.create'
        )
        WHERE key = 'tools'
          AND jsonb_typeof(COALESCE(data->'policies', '{}'::jsonb)) = 'object'
          AND data->'policies' ? 'draft.create'
        """
    )
    op.drop_column("chat_sessions", "workspace_access")


def downgrade() -> None:
    op.add_column(
        "chat_sessions",
        sa.Column(
            "workspace_access",
            sa.VARCHAR(length=20),
            server_default="read-only",
            nullable=False,
        ),
    )
    op.alter_column("chat_sessions", "workspace_access", server_default=None)
