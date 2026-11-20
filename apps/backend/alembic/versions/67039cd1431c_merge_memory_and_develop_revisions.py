"""merge memory and develop revisions

Revision ID: 67039cd1431c
Revises: 55114a039ec2, 9bf863d33bbd
Create Date: 2026-11-19 22:37:56.942188
"""

from collections.abc import Sequence

revision: str = '67039cd1431c'
down_revision: tuple[str, str] = ('55114a039ec2', '9bf863d33bbd')
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
