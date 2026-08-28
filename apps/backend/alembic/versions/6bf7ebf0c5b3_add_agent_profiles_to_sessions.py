"""add agent profiles to sessions

Revision ID: 6bf7ebf0c5b3
Revises: 8845b9185f20
Create Date: 2026-08-27 20:24:03.722639
"""

from typing import Sequence, Union
from uuid import UUID, uuid4

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "6bf7ebf0c5b3"
down_revision: Union[str, None] = "8845b9185f20"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

DEFAULT_AGENT_PROFILE_ID = UUID("00000000-0000-4000-8000-000000000001")
def _default_profile() -> dict:
    return {
        "id": str(DEFAULT_AGENT_PROFILE_ID),
        "name": "Default",
        "description": "Standardagent für Sessions ohne ausgewähltes Profil.",
        "instructions": "Follow the user's request and the shared persona.",
        "persona_mode": "extend",
        "private_permissions": {
            "policies": {},
            "integration_policies": {},
            "resource_policies": {},
        },
        "public_permissions": {
            "policies": {},
            "integration_policies": {},
            "resource_policies": {},
        },
    }


def _settings_table() -> sa.TableClause:
    return sa.table(
        "settings",
        sa.column("id", sa.Uuid()),
        sa.column("key", sa.String()),
        sa.column("user_id", sa.Uuid()),
        sa.column("data", JSONB()),
    )


def _install_default_profile() -> None:
    connection = op.get_bind()
    settings = _settings_table()
    row = connection.execute(
        sa.select(settings.c.id, settings.c.data).where(
            settings.c.key == "agent-profiles", settings.c.user_id.is_(None)
        )
    ).mappings().first()
    if row is None:
        connection.execute(
            sa.insert(settings),
            [
                {
                    "id": uuid4(),
                    "key": "agent-profiles",
                    "user_id": None,
                    "data": {
                        "personal_agents_enabled": True,
                        "profiles": [_default_profile()],
                    },
                }
            ],
        )
        return
    data = dict(row["data"] or {})
    profiles = [
        profile
        for profile in data.get("profiles", [])
        if str(profile.get("id")) != str(DEFAULT_AGENT_PROFILE_ID)
        and str(profile.get("name", "")).strip().casefold() != "default"
    ]
    if len(profiles) >= 100:
        raise RuntimeError("agent profile limit leaves no room for Default")
    data["profiles"] = [_default_profile(), *profiles]
    data.setdefault("personal_agents_enabled", True)
    connection.execute(
        sa.update(settings).where(settings.c.id == row["id"]).values(data=data)
    )


def _remove_default_profile() -> None:
    connection = op.get_bind()
    settings = _settings_table()
    row = connection.execute(
        sa.select(settings.c.id, settings.c.data).where(
            settings.c.key == "agent-profiles", settings.c.user_id.is_(None)
        )
    ).mappings().first()
    if row is None:
        return
    data = dict(row["data"] or {})
    data["profiles"] = [
        profile
        for profile in data.get("profiles", [])
        if str(profile.get("id")) != str(DEFAULT_AGENT_PROFILE_ID)
    ]
    connection.execute(
        sa.update(settings).where(settings.c.id == row["id"]).values(data=data)
    )


def upgrade() -> None:
    op.add_column(
        "chat_sessions",
        sa.Column(
            "agent_profile_id",
            sa.Uuid(),
            nullable=True,
            server_default=sa.text(f"'{DEFAULT_AGENT_PROFILE_ID}'::uuid"),
        ),
    )
    op.add_column(
        "permission_grants", sa.Column("agent_profile_id", sa.Uuid(), nullable=True)
    )
    op.add_column(
        "permission_grants", sa.Column("trust_level", sa.String(length=20), nullable=True)
    )
    op.create_check_constraint(
        "trust_level_valid",
        "permission_grants",
        "trust_level IS NULL OR trust_level IN ('public', 'private')",
    )
    _install_default_profile()
    op.execute(
        sa.text(
            "UPDATE chat_sessions SET agent_profile_id = :profile_id "
            "WHERE agent_profile_id IS NULL"
        ).bindparams(profile_id=DEFAULT_AGENT_PROFILE_ID)
    )
    op.alter_column("chat_sessions", "agent_profile_id", nullable=False)


def downgrade() -> None:
    _remove_default_profile()
    op.drop_constraint("trust_level_valid", "permission_grants", type_="check")
    op.drop_column("permission_grants", "trust_level")
    op.drop_column("permission_grants", "agent_profile_id")
    op.drop_column("chat_sessions", "agent_profile_id")
