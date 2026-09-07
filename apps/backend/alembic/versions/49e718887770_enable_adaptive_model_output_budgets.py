"""enable adaptive model output budgets

Revision ID: 49e718887770
Revises: 1a710f0c2b0f
Create Date: 2026-09-07 13:49:47.667826

All existing models, including manually capped models, switch to Auto once.
Old ceilings are intentionally not backed up and cannot be recovered. Downgrade
removes output settings (including subsequently entered ceilings) so the older
application uses its compatibility defaults; it does not restore historical caps.
"""

from copy import deepcopy
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision: str = '49e718887770'
down_revision: Union[str, None] = '1a710f0c2b0f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _update_output_limits(*, auto: bool) -> None:
    connection = op.get_bind()
    settings = sa.table(
        "settings",
        sa.column("id", sa.Uuid()),
        sa.column("key", sa.String()),
        sa.column("data", JSONB()),
    )
    rows = connection.execute(
        sa.select(settings.c.id, settings.c.data).where(settings.c.key == "ai")
    ).mappings()
    for row in rows:
        source = row["data"]
        if not isinstance(source, dict) or not isinstance(source.get("models"), list):
            continue
        data = deepcopy(source)
        for model in data["models"]:
            if not isinstance(model, dict):
                continue
            config = model.get("config")
            if isinstance(config, dict):
                had_legacy = "max_output_tokens" in config or "maxOutputTokens" in config
                config.pop("max_output_tokens", None)
                config.pop("maxOutputTokens", None)
                if auto and had_legacy:
                    config["max_output_tokens"] = None
            model.pop("maxOutputTokens", None)
            model.pop("max_output_tokens", None)
            if auto:
                model["max_output_tokens"] = None
        if data != source:
            connection.execute(
                sa.update(settings).where(settings.c.id == row["id"]).values(data=data)
            )


def upgrade() -> None:
    _update_output_limits(auto=True)


def downgrade() -> None:
    _update_output_limits(auto=False)
