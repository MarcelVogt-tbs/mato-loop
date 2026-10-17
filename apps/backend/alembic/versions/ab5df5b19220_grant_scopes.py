"""grant scopes

Revision ID: ab5df5b19220
Revises: 39856f53cf7e
Create Date: 2026-10-17 20:53:21.849348
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'ab5df5b19220'
down_revision: Union[str, None] = '39856f53cf7e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Reconfirm consent under the unified scopes without retaining legacy grant semantics.
    op.execute(sa.text("DELETE FROM permission_grants"))
    op.execute(sa.text(
        "UPDATE approval_requests SET status = 'expired', resolved_at = CURRENT_TIMESTAMP "
        "WHERE request_type = 'agent_action' AND status = 'pending'"
    ))


def downgrade() -> None:
    # Revoked consent cannot be restored safely.
    pass
