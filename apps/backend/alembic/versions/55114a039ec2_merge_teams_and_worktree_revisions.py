"""merge Teams and worktree revisions

Revision ID: 55114a039ec2
Revises: 753a58f32cd7, a38897f9bae6
Create Date: 2026-11-19 07:20:11.520386
"""

from collections.abc import Sequence


revision: str = '55114a039ec2'
down_revision: tuple[str, str] = ('753a58f32cd7', 'a38897f9bae6')
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
