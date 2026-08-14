"""add execution agent home override

Revision ID: 4758b6088ec8
Revises: 50332b512eee
Create Date: 2026-08-12 23:21:36.785898
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "4758b6088ec8"
down_revision: Union[str, None] = "50332b512eee"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "execution_agents",
        sa.Column(
            "home_override",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("execution_agents", "home_override")
