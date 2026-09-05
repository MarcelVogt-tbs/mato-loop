"""restrict plan profile writes

Revision ID: eefc44080927
Revises: 6f4d178cd11e
Create Date: 2026-09-04 17:04:09.392159
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision: str = "eefc44080927"
down_revision: Union[str, None] = "6f4d178cd11e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

PLAN_AGENT_PROFILE_ID = "00000000-0000-4000-8000-000000000003"


def _settings_table() -> sa.TableClause:
    return sa.table(
        "settings",
        sa.column("id", sa.Uuid()),
        sa.column("key", sa.String()),
        sa.column("data", JSONB()),
    )


def _update_plan_policies(*, upgrade: bool) -> None:
    connection = op.get_bind()
    settings = _settings_table()
    rows = connection.execute(
        sa.select(settings.c.id, settings.c.data).where(
            settings.c.key == "agent-profiles"
        )
    ).mappings()
    for row in rows:
        data = dict(row["data"] or {})
        profiles = []
        changed = False
        for source in data.get("profiles", []):
            profile = dict(source)
            if str(profile.get("id")) == PLAN_AGENT_PROFILE_ID:
                for scope in ("private_permissions", "public_permissions"):
                    permissions = dict(profile.get(scope, {}) or {})
                    policies = dict(permissions.get("policies", {}) or {})
                    if upgrade:
                        policies.update(
                            {
                                "filesystem.write": "disabled",
                                "filesystem.delete": "disabled",
                                "session.files.write": "auto",
                                "shell.read": "auto",
                                "shell.write": "disabled",
                                "git.stage": "disabled",
                                "git.clone": "disabled",
                                "git.commit": "disabled",
                            }
                        )
                    else:
                        policies.update(
                            {
                                "filesystem.write": "ask",
                                "filesystem.delete": "ask",
                                "session.files.write": "auto",
                                "shell.write": "ask",
                            }
                        )
                        for capability in (
                            "shell.read",
                            "git.stage",
                            "git.clone",
                            "git.commit",
                        ):
                            policies.pop(capability, None)
                    permissions["policies"] = policies
                    profile[scope] = permissions
                changed = True
            profiles.append(profile)
        if changed:
            data["profiles"] = profiles
            connection.execute(
                sa.update(settings).where(settings.c.id == row["id"]).values(data=data)
            )


def upgrade() -> None:
    _update_plan_policies(upgrade=True)


def downgrade() -> None:
    _update_plan_policies(upgrade=False)
