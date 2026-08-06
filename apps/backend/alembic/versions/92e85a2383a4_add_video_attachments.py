"""Add video attachments.

Revision ID: 92e85a2383a4
Revises: b58d600bedcc
Create Date: 2026-08-06 09:22:27.783002
"""

from collections.abc import Sequence

from alembic import op

revision: str = "92e85a2383a4"
down_revision: str | None = "b58d600bedcc"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("kind_valid", "message_attachments", type_="check")
    op.create_check_constraint(
        "kind_valid",
        "message_attachments",
        "kind IN ('image', 'audio', 'video', 'text', 'document', 'file')",
    )


def downgrade() -> None:
    op.drop_constraint("kind_valid", "message_attachments", type_="check")
    op.create_check_constraint(
        "kind_valid",
        "message_attachments",
        "kind IN ('image', 'audio', 'text', 'document', 'file')",
    )
