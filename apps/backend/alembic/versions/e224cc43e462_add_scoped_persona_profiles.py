"""add scoped persona profiles

Revision ID: e224cc43e462
Revises: 6bf7ebf0c5b3
Create Date: 2026-08-29 21:16:42.701674
"""

from typing import Sequence, Union
from uuid import UUID, uuid4

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "e224cc43e462"
down_revision: Union[str, None] = "6bf7ebf0c5b3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

DEFAULT_PERSONA_ID = UUID("00000000-0000-4000-8000-000000000002")


def _persona(name: str, instructions: str, persona_id: UUID | None = None) -> dict:
    return {
        "id": str(persona_id or uuid4()),
        "name": name,
        "instructions": instructions.strip(),
    }


def _personal_identity(data: object) -> dict:
    if not isinstance(data, dict):
        raise RuntimeError("invalid personal agent identity settings document")
    instructions = str(data.get("instructions", "")).strip()
    personas = [_persona("Eigene Persona", instructions)] if instructions else []
    return {
        "display_name": data.get("display_name", data.get("displayName", "MatoLoop")),
        "default_persona_id": personas[0]["id"] if personas else None,
        "personas": personas,
    }


def _unique_persona_name(personas: list[dict], name: object, profile_id: object) -> str:
    candidate = str(name or "Agenten-Persona").strip() or "Agenten-Persona"
    used = {str(persona.get("name", "")).casefold() for persona in personas}
    if candidate.casefold() not in used:
        return candidate
    suffix = str(profile_id)[:8]
    numbered = f"{candidate} ({suffix})"
    index = 2
    while numbered.casefold() in used:
        numbered = f"{candidate} ({suffix}-{index})"
        index += 1
    return numbered


def _migrate_profiles(
    data: object,
    identity: dict,
    *,
    personal: bool,
    old_identity_instructions: str = "",
) -> dict:
    if not isinstance(data, dict) or not isinstance(data.get("profiles"), list):
        raise RuntimeError("invalid agent profiles settings document")
    result = dict(data)
    migrated = []
    personas = identity["personas"]
    for source in data["profiles"]:
        if not isinstance(source, dict):
            raise RuntimeError("invalid agent profile settings entry")
        profile = dict(source)
        text = str(profile.pop("persona", "")).strip()
        mode = profile.pop("persona_mode", profile.pop("personaMode", "extend"))
        if text:
            instructions = text
            if personal and mode != "replace" and old_identity_instructions:
                instructions = f"{old_identity_instructions.strip()}\n\n{text}"
            persona = _persona(
                _unique_persona_name(personas, profile.get("name"), profile.get("id")),
                instructions,
            )
            personas.append(persona)
            profile["persona_id"] = persona["id"]
        else:
            profile["persona_id"] = (
                str(DEFAULT_PERSONA_ID) if personal and mode == "replace" else None
            )
        migrated.append(profile)
    result["profiles"] = migrated
    return result


def _settings_table() -> sa.Table:
    return sa.table(
        "settings",
        sa.column("id", sa.Uuid()),
        sa.column("key", sa.String()),
        sa.column("user_id", sa.Uuid()),
        sa.column("data", JSONB()),
    )


def upgrade() -> None:
    connection = op.get_bind()
    settings = _settings_table()
    rows = list(connection.execute(sa.select(settings)).mappings())
    identity_rows = {
        row["user_id"]: row
        for row in rows
        if row["key"] == "agent-identity" and row["user_id"] is not None
    }
    if any(
        row["key"] == "agent-identity" and row["user_id"] is None for row in rows
    ):
        raise RuntimeError("unexpected global agent identity settings document")

    personal_identities = {
        user_id: _personal_identity(row["data"])
        for user_id, row in identity_rows.items()
    }
    old_instructions = {
        user_id: str(row["data"].get("instructions", "")).strip()
        for user_id, row in identity_rows.items()
    }
    instance_identity = {
        "display_name": "MatoLoop",
        "personal_display_names_enabled": True,
        "personal_personas_enabled": True,
        "default_persona_id": str(DEFAULT_PERSONA_ID),
        "personas": [_persona("Standard", "", DEFAULT_PERSONA_ID)],
    }

    for row in rows:
        if row["key"] != "agent-profiles":
            continue
        user_id = row["user_id"]
        if user_id is None:
            migrated = _migrate_profiles(
                row["data"], instance_identity, personal=False
            )
        else:
            identity = personal_identities.setdefault(
                user_id,
                {"display_name": None, "default_persona_id": None, "personas": []},
            )
            migrated = _migrate_profiles(
                row["data"],
                identity,
                personal=True,
                old_identity_instructions=old_instructions.get(user_id, ""),
            )
        connection.execute(
            settings.update().where(settings.c.id == row["id"]).values(data=migrated)
        )

    connection.execute(
        settings.insert().values(
            id=uuid4(), key="agent-identity", user_id=None, data=instance_identity
        )
    )
    for user_id, data in personal_identities.items():
        row = identity_rows.get(user_id)
        if row is None:
            connection.execute(
                settings.insert().values(
                    id=uuid4(), key="agent-identity", user_id=user_id, data=data
                )
            )
        else:
            connection.execute(
                settings.update().where(settings.c.id == row["id"]).values(data=data)
            )


def downgrade() -> None:
    connection = op.get_bind()
    settings = _settings_table()
    rows = list(connection.execute(sa.select(settings)).mappings())
    identity_rows = {
        row["user_id"]: row for row in rows if row["key"] == "agent-identity"
    }
    persona_by_scope = {
        user_id: {
            str(persona.get("id")): persona
            for persona in row["data"].get("personas", [])
            if isinstance(persona, dict)
        }
        for user_id, row in identity_rows.items()
        if isinstance(row["data"], dict)
    }
    instance_personas = persona_by_scope.get(None, {})

    for row in rows:
        if row["key"] != "agent-profiles":
            continue
        data = row["data"]
        if not isinstance(data, dict) or not isinstance(data.get("profiles"), list):
            raise RuntimeError("invalid agent profiles settings document")
        profiles = []
        scoped_personas = persona_by_scope.get(row["user_id"], {})
        for source in data["profiles"]:
            profile = dict(source)
            persona_id = str(profile.pop("persona_id", ""))
            persona = scoped_personas.get(persona_id) or instance_personas.get(persona_id)
            text = str(persona.get("instructions", "")) if persona else ""
            profile["persona"] = text
            profile["persona_mode"] = "replace" if persona_id and persona else "extend"
            profiles.append(profile)
        migrated = dict(data)
        migrated["profiles"] = profiles
        connection.execute(
            settings.update().where(settings.c.id == row["id"]).values(data=migrated)
        )

    for user_id, row in identity_rows.items():
        if user_id is None:
            connection.execute(settings.delete().where(settings.c.id == row["id"]))
            continue
        data = row["data"]
        default_id = str(data.get("default_persona_id", ""))
        persona = persona_by_scope.get(user_id, {}).get(default_id) or instance_personas.get(
            default_id
        )
        connection.execute(
            settings.update()
            .where(settings.c.id == row["id"])
            .values(
                data={
                    "display_name": data.get("display_name") or "MatoLoop",
                    "instructions": str(persona.get("instructions", "")) if persona else "",
                }
            )
        )
