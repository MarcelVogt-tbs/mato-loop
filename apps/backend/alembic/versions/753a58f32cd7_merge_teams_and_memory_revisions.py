"""merge teams and memory revisions

Revision ID: 753a58f32cd7
Revises: b331a555fc78, 52d5772e7b3e
Create Date: 2026-11-18 18:50:30.503823
"""

from collections.abc import Sequence

revision: str = '753a58f32cd7'
down_revision: tuple[str, str] = ('b331a555fc78', '52d5772e7b3e')
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
