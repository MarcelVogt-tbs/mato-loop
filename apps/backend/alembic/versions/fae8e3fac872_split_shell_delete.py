"""split shell delete

Revision ID: fae8e3fac872
Revises: 9622f0d9eb11
Create Date: 2026-10-16 11:44:41.336853
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'fae8e3fac872'
down_revision: Union[str, None] = '9622f0d9eb11'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    settings = sa.table("settings", sa.column("id", sa.Uuid()), sa.column("key", sa.String()), sa.column("data", sa.JSON()))
    connection = op.get_bind()
    for row in connection.execute(sa.select(settings).where(settings.c.key.in_(("tools", "agent-profiles")))).mappings():
        data = _copy_shell_delete_policy(row["data"])
        if data != row["data"]:
            connection.execute(settings.update().where(settings.c.id == row["id"]).values(data=data))


def _copy_shell_delete_policy(value):
    if isinstance(value, list):
        return [_copy_shell_delete_policy(item) for item in value]
    if not isinstance(value, dict):
        return value
    result = {key: _copy_shell_delete_policy(item) for key, item in value.items()}
    policies = result.get("policies")
    if isinstance(policies, dict) and policies.get("shell.write") in {"auto", "ask", "disabled"}:
        policies.setdefault("shell.delete", policies["shell.write"])
    return result


def downgrade() -> None:
    # Keep explicit overrides: copied and subsequently edited values are indistinguishable.
    pass
