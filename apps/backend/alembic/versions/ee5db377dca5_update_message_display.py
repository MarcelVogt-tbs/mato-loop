"""update message display

Revision ID: ee5db377dca5
Revises: 2bf04bee4176
Create Date: 2026-10-06 13:04:42.460335
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = 'ee5db377dca5'
down_revision: Union[str, None] = '2bf04bee4176'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    connection = op.get_bind()
    settings = sa.table("settings", sa.column("id", sa.Uuid()), sa.column("key", sa.String()), sa.column("data", postgresql.JSONB()))
    for row in connection.execute(sa.select(settings).where(settings.c.key == "agent-profiles")).mappings():
        data = row["data"]
        values = list(data.get("builtin_access_overrides", {}).values())
        for profile in data.get("profiles", []):
            values.extend((profile, profile.get("overrides", {})))
        for value in values:
            value.pop("answer_display", None)
            value.pop("answerDisplay", None)
        connection.execute(settings.update().where(settings.c.id == row["id"]).values(data=data))
    for row in connection.execute(sa.select(settings).where(settings.c.key == "session-templates")).mappings():
        data = row["data"]
        for template in data.get("templates", []):
            template["message_size"] = "small" if template["id"] == "00000000-0000-4000-8000-000000000102" else "standard"
        connection.execute(settings.update().where(settings.c.id == row["id"]).values(data=data))


def downgrade() -> None:
    connection = op.get_bind()
    settings = sa.table("settings", sa.column("id", sa.Uuid()), sa.column("key", sa.String()), sa.column("data", postgresql.JSONB()))
    for row in connection.execute(sa.select(settings).where(settings.c.key == "session-templates")).mappings():
        data = row["data"]
        for template in data.get("templates", []):
            template.pop("message_size", None)
            template.pop("messageSize", None)
        connection.execute(settings.update().where(settings.c.id == row["id"]).values(data=data))
    connection.execute(sa.text("UPDATE chat_sessions SET session_profile = session_profile - 'message_size' - 'messageSize'"))
