"""Move integration availability to connections and reset navigation grants.

Revision ID: 9618efd925e3
Revises: d03794d64b81
Create Date: 2026-10-11 21:57:25.856386
"""

from copy import deepcopy
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision: str = '9618efd925e3'
down_revision: Union[str, None] = 'd03794d64b81'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def migrate_documents(documents: dict) -> dict:
    data = deepcopy(documents)
    connections = {
        (owner, instance["id"]): instance
        for (key, owner), document in data.items() if key == "integrations"
        for instance in document.get("instances", [])
    }
    for (key, owner), document in data.items():
        if key != "agent-profiles":
            continue
        profiles = {
            **document.get("builtin_access_overrides", {}),
            **{profile["id"]: profile.get("overrides", {}) for profile in document.get("profiles", [])},
        }
        for profile_id, overrides in profiles.items():
            assignments = {
                **(overrides.pop("integrationAvailability", None) or {}),
                **(overrides.pop("integration_availability", None) or {}),
            }
            for integration_id, available in assignments.items():
                instance = connections.get((owner, integration_id))
                if instance is not None:
                    target = instance.setdefault("profile_permissions", {}).setdefault(profile_id, {})
                    if target.get("available") is None:
                        target["available"] = available
    return data


def upgrade() -> None:
    settings = sa.table("settings", sa.column("id", sa.Uuid()), sa.column("key", sa.String()), sa.column("user_id", sa.Uuid()), sa.column("data", JSONB()))
    connection = op.get_bind()
    rows = list(connection.execute(sa.select(settings).where(settings.c.key.in_(("agent-profiles", "integrations")))).mappings())
    documents = {(row["key"], row["user_id"]): row["data"] for row in rows}
    migrated = migrate_documents(documents)
    for row in rows:
        scope = row["key"], row["user_id"]
        if migrated[scope] != documents[scope]:
            connection.execute(settings.update().where(settings.c.id == row["id"]).values(data=migrated[scope]))
    grants = sa.table("permission_grants", sa.column("capability", sa.String()))
    connection.execute(grants.delete().where(grants.c.capability == "execution.directory"))


def downgrade() -> None:
    raise RuntimeError("This cleanup cannot restore removed integration assignments or navigation grants.")
