"""move agent defaults to profiles

Revision ID: ea5060233818
Revises: 4b8fd8a179a9
Create Date: 2026-09-19 22:38:56.848421
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB


revision: str = "ea5060233818"
down_revision: Union[str, None] = "4b8fd8a179a9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    table = sa.table(
        "settings",
        sa.column("id", sa.Uuid()),
        sa.column("key", sa.String()),
        sa.column("data", JSONB()),
    )
    model_fields = {
        "default_model_id",
        "compaction_model_id",
        "title_generation_model_id",
        "title_vision_fallback_enabled",
        "skill_creation_model_id",
        "dreaming_model_id",
        "vision_model_id",
        "video_model_id",
        "image_generation_model_id",
        "speech_generation_model_id",
        "audio_generation_model_id",
        "audio_transcription_model_id",
    }
    tool_fields = {"private_permissions", "public_permissions", "shell_prefixes"}
    connection = op.get_bind()
    rows = connection.execute(
        sa.select(table.c.id, table.c.key, table.c.data).where(
            table.c.key.in_(("ai", "tools"))
        )
    )
    for row in rows:
        removed = model_fields if row.key == "ai" else tool_fields
        data = {
            key: value
            for key, value in dict(row.data or {}).items()
            if key not in removed
        }
        connection.execute(
            table.update().where(table.c.id == row.id).values(data=data)
        )


def downgrade() -> None:
    # Discarded defaults cannot be reconstructed; model defaults remain neutral.
    pass
