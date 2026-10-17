"""tool presets

Revision ID: 39856f53cf7e
Revises: fae8e3fac872
Create Date: 2026-10-17 15:30:34.380482
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '39856f53cf7e'
down_revision: Union[str, None] = 'fae8e3fac872'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    settings = sa.table("settings", sa.column("id", sa.Uuid()), sa.column("key", sa.String()), sa.column("data", sa.JSON()))
    connection = op.get_bind()
    for row in connection.execute(sa.select(settings).where(settings.c.key.in_(("tools", "agent-profiles")))).mappings():
        data = _copy_session_delete_policy(row["data"])
        if row["key"] == "tools":
            data.update(private_permission_preset="ask_changes", public_permission_preset="balanced")
        if data != row["data"]:
            connection.execute(settings.update().where(settings.c.id == row["id"]).values(data=data))


def _copy_session_delete_policy(value):
    if isinstance(value, list):
        return [_copy_session_delete_policy(item) for item in value]
    if not isinstance(value, dict):
        return value
    result = {key: _copy_session_delete_policy(item) for key, item in value.items()}
    policies = result.get("policies")
    if isinstance(policies, dict) and policies.get("filesystem.delete") in {"auto", "ask", "disabled"}:
        policies.setdefault("session.files.delete", policies["filesystem.delete"])
    return result


def downgrade() -> None:
    settings = sa.table("settings", sa.column("id", sa.Uuid()), sa.column("key", sa.String()), sa.column("data", sa.JSON()))
    connection = op.get_bind()
    for row in connection.execute(sa.select(settings).where(settings.c.key == "tools")).mappings():
        data = dict(row["data"])
        for field in ("private_permission_preset", "public_permission_preset"):
            if data.get(field) == "balanced":
                data[field] = "ask_all"
        connection.execute(settings.update().where(settings.c.id == row["id"]).values(data=data))
    # Keep explicit overrides: copied and subsequently edited values are indistinguishable.
