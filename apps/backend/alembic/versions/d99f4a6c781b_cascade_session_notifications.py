"""cascade session notifications

Revision ID: d99f4a6c781b
Revises: e685c50f164e
Create Date: 2026-11-02 15:23:15.092393
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd99f4a6c781b'
down_revision: Union[str, None] = 'e685c50f164e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint(op.f('fk_notifications_session_id_chat_sessions'), 'notifications', type_='foreignkey')
    op.create_foreign_key(op.f('fk_notifications_session_id_chat_sessions'), 'notifications', 'chat_sessions', ['session_id'], ['id'], ondelete='CASCADE')
    # These kinds always originate in a session; other session-less notices may be independent.
    op.execute(sa.text("""
        DELETE FROM notifications
        WHERE session_id IS NULL
          AND kind IN ('session.response', 'approval.requested', 'question.requested', 'agent.notice')
    """))


def downgrade() -> None:
    # Deleted notices cannot be reconstructed.
    op.drop_constraint(op.f('fk_notifications_session_id_chat_sessions'), 'notifications', type_='foreignkey')
    op.create_foreign_key(op.f('fk_notifications_session_id_chat_sessions'), 'notifications', 'chat_sessions', ['session_id'], ['id'], ondelete='SET NULL')
