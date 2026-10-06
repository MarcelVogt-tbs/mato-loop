"""remove read response notifications

Revision ID: b92e7594d532
Revises: ee5db377dca5
Create Date: 2026-10-06 15:00:26.900547
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b92e7594d532"
down_revision: str | None = "ee5db377dca5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(sa.text("DELETE FROM notifications WHERE kind = 'session.response' AND read_at IS NOT NULL"))


def downgrade() -> None:
    # Removed read notices are not reconstructed; their canonical messages remain.
    pass
