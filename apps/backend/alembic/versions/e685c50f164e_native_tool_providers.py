"""native tool providers

Revision ID: e685c50f164e
Revises: 48a4ae563505
Create Date: 2026-11-01 22:08:30.397892
"""

from copy import deepcopy
from typing import Sequence, Union
from uuid import uuid4

from alembic import op
import sqlalchemy as sa


revision: str = 'e685c50f164e'
down_revision: Union[str, None] = '48a4ae563505'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def migrate_settings(rows: list[dict]) -> list[dict]:
    rows = deepcopy(rows)
    profiles = [row for row in rows if row["key"] == "agent-profiles"]
    global_profiles = next((row for row in profiles if row["user_id"] is None), None)
    for row in rows[:]:
        if row["key"] != "integrations" or row["user_id"] is not None:
            continue
        for instance in row["data"].get("instances", []):
            tool = {("webfetch", "jina"): "webfetch", ("search", "searxng"): "web_search"}.get(
                (instance.get("integration_type"), instance.get("adapter_type"))
            )
            if tool is None:
                continue
            for profile_id, permissions in instance.get("profile_permissions", {}).items():
                if permissions.get("available") is not False:
                    continue
                targets = [profile["overrides"] for document in profiles
                           for profile in document["data"].get("profiles", [])
                           if profile["id"] == profile_id or (
                               profile.get("base_profile_id") == profile_id
                               and instance.get("profile_permissions", {}).get(profile["id"], {}).get("available") is not True
                           )]
                if profile_id in {
                    f"00000000-0000-4000-8000-{suffix:012d}" for suffix in (1, 3, 4, 5)
                }:
                    if global_profiles is None:
                        global_profiles = {"id": uuid4(), "key": "agent-profiles", "user_id": None, "data": {}}
                        rows.append(global_profiles)
                        profiles.append(global_profiles)
                    targets.append(global_profiles["data"].setdefault("builtin_access_overrides", {}).setdefault(profile_id, {}))
                for target in targets:
                    target.setdefault("toolAvailability", target.pop("tool_availability", {}))[tool] = False
            instance["permissions"] = {}
            instance["profile_permissions"] = {}
    return rows


def upgrade() -> None:
    settings = sa.table("settings", sa.column("id", sa.Uuid), sa.column("key", sa.String),
                        sa.column("user_id", sa.Uuid), sa.column("data", sa.JSON))
    bind = op.get_bind()
    before = [dict(row) for row in bind.execute(sa.select(settings).where(
        settings.c.key.in_(["integrations", "agent-profiles"])
    )).mappings()]
    originals = {row["id"]: row for row in before}
    for row in migrate_settings(before):
        if row["id"] not in originals:
            bind.execute(settings.insert().values(**row))
        elif row["data"] != originals[row["id"]]["data"]:
            bind.execute(settings.update().where(settings.c.id == row["id"]).values(data=row["data"]))


def downgrade() -> None:
    # Keep migrated tool restrictions; reconstructing obsolete provider gates could grant access.
    pass
