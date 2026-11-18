"""interactive profile

Revision ID: 9c2944bb95db
Revises: 197a2e0428fc
Create Date: 2026-11-17 21:31:06.209636
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision: str = '9c2944bb95db'
down_revision: Union[str, None] = '197a2e0428fc'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


CODING_ID = "00000000-0000-4000-8000-000000000102"
PLAN_ID = "00000000-0000-4000-8000-000000000003"
BUILD_ID = "00000000-0000-4000-8000-000000000004"
INTERACTIVE_ID = "00000000-0000-4000-8000-000000000006"


def _update_templates(data: dict, *, downgrade: bool = False) -> bool:
    changed = False
    previous = [PLAN_ID, BUILD_ID, INTERACTIVE_ID] if downgrade else [PLAN_ID, BUILD_ID]
    updated = [PLAN_ID, BUILD_ID] if downgrade else [PLAN_ID, BUILD_ID, INTERACTIVE_ID]
    for template in data.get("templates", []):
        key = "agentIds" if "agentIds" in template else "agent_ids"
        if (template.get("id") == CODING_ID and template.get(key) == previous
                and template.get("startAgentId", template.get("start_agent_id")) == PLAN_ID):
            template[key] = updated.copy()
            if downgrade:
                for models_key in ("agent_models", "agentModels"):
                    template.get(models_key, {}).pop(INTERACTIVE_ID, None)
            changed = True
    return changed


def _migrate(*, downgrade: bool = False) -> None:
    connection = op.get_bind()
    settings = sa.table("settings", sa.column("id", sa.Uuid()), sa.column("key", sa.String()), sa.column("data", JSONB()))
    for row in connection.execute(sa.select(settings).where(settings.c.key == "session-templates")).mappings():
        data = row["data"]
        if _update_templates(data, downgrade=downgrade):
            connection.execute(settings.update().where(settings.c.id == row["id"]).values(data=data))


def upgrade() -> None:
    _migrate()


def downgrade() -> None:
    _migrate(downgrade=True)
