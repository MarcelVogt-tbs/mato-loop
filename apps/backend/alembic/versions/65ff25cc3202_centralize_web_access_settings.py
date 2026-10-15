"""Reset shared web access settings to their defaults.

Revision ID: 65ff25cc3202
Revises: 9cee124e2dfb
Create Date: 2026-10-14 14:08:24.856115
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '65ff25cc3202'
down_revision: Union[str, None] = '9cee124e2dfb'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(sa.text("""
        UPDATE settings
        SET data = jsonb_set(
            data - 'web_access' - 'webAccess' - 'webFetch',
            '{web_fetch}',
            (COALESCE(data->'webFetch', '{}'::jsonb) || COALESCE(data->'web_fetch', '{}'::jsonb))
                - ARRAY['public_addresses_only', 'publicAddressesOnly', 'user_agent',
                        'userAgent', 'max_response_mib', 'maxResponseMiB', 'engine']
        )
        WHERE key = 'tools' AND user_id IS NULL
    """))


def downgrade() -> None:
    # Data-only reset; previous values require a backup to restore.
    pass
