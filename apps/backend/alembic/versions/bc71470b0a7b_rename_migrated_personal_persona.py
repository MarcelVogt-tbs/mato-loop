"""rename migrated personal persona

Revision ID: bc71470b0a7b
Revises: e224cc43e462
Create Date: 2026-08-30 09:01:07.300669
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB


revision: str = "bc71470b0a7b"
down_revision: Union[str, None] = "e224cc43e462"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _settings_table() -> sa.Table:
    return sa.table(
        "settings",
        sa.column("id", sa.Uuid()),
        sa.column("key", sa.String()),
        sa.column("user_id", sa.Uuid()),
        sa.column("data", JSONB()),
    )


def _renamed_data(data: object) -> dict | None:
    if not isinstance(data, dict) or not isinstance(data.get("personas"), list):
        raise RuntimeError("invalid personal agent identity settings document")
    personas = data["personas"]
    if any(
        isinstance(persona, dict) and persona.get("name") == "Eigene Persona"
        for persona in personas
    ):
        return None
    renamed = False
    result_personas = []
    for source in personas:
        if not isinstance(source, dict):
            raise RuntimeError("invalid personal persona profile")
        persona = dict(source)
        if persona.get("name") == "Bisherige Persona":
            persona["name"] = "Eigene Persona"
            renamed = True
        result_personas.append(persona)
    if not renamed:
        return None
    result = dict(data)
    result["personas"] = result_personas
    return result


def upgrade() -> None:
    connection = op.get_bind()
    settings = _settings_table()
    rows = connection.execute(
        sa.select(settings).where(
            settings.c.key == "agent-identity", settings.c.user_id.is_not(None)
        )
    ).mappings()
    for row in rows:
        data = _renamed_data(row["data"])
        if data is not None:
            connection.execute(
                settings.update().where(settings.c.id == row["id"]).values(data=data)
            )


def downgrade() -> None:
    # The preceding revision now creates the corrected name directly.
    pass
