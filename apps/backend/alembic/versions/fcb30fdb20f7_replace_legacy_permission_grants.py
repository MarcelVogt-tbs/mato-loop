"""replace legacy permission grants

Revision ID: fcb30fdb20f7
Revises: dd6fa912bfce
Create Date: 2026-09-13 22:42:32.184854
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'fcb30fdb20f7'
down_revision: Union[str, None] = 'dd6fa912bfce'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Existing action-specific grants must not become broader resource grants silently.
    op.execute(
        sa.text(
            "DELETE FROM permission_grants WHERE source_approval_id IS NOT NULL"
        )
    )


def downgrade() -> None:
    # Deleted grants cannot be reconstructed safely.
    pass
