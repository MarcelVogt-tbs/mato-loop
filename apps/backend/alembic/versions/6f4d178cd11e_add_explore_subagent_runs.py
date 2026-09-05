"""add explore subagent runs

Revision ID: 6f4d178cd11e
Revises: a5cb5f2808b8
Create Date: 2026-09-03 20:48:34.959937
"""

from typing import Sequence, Union
from uuid import UUID, uuid4

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "6f4d178cd11e"
down_revision: Union[str, None] = "a5cb5f2808b8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

PLAN_AGENT_PROFILE_ID = "00000000-0000-4000-8000-000000000003"
BUILD_AGENT_PROFILE_ID = "00000000-0000-4000-8000-000000000004"
DEFAULT_AGENT_PROFILE_ID = "00000000-0000-4000-8000-000000000001"


def _profile(profile_id: str, *, build: bool) -> dict:
    policies = (
        {
            "filesystem.write": "auto",
            "filesystem.delete": "auto",
            "shell.write": "auto",
        }
        if build
        else {
            "filesystem.write": "ask",
            "filesystem.delete": "ask",
            "session.files.write": "auto",
            "shell.write": "ask",
        }
    )
    return {
        "id": profile_id,
        "name": "Build" if build else "Plan",
        "description": (
            "Setzt Aufgaben um und führt notwendige Änderungen selbstständig aus."
            if build
            else "Erstellt klare, umsetzbare Pläne, ohne Änderungen ungefragt auszuführen."
        ),
        "instructions": (
            "Implement the user's request. Inspect the relevant context, make the smallest "
            "correct changes, and verify the result."
            if build
            else "Create a clear, actionable plan. Inspect the relevant context, identify risks "
            "and verification steps, and do not implement the plan unless the user explicitly "
            "asks you to."
        ),
        "private_permissions": {"policies": policies},
        "public_permissions": {"policies": policies},
    }


def _settings_table() -> sa.TableClause:
    return sa.table(
        "settings",
        sa.column("id", sa.Uuid()),
        sa.column("key", sa.String()),
        sa.column("user_id", sa.Uuid()),
        sa.column("data", JSONB()),
    )


def _install_profiles() -> None:
    connection = op.get_bind()
    settings = _settings_table()
    row = connection.execute(
        sa.select(settings.c.id, settings.c.data).where(
            settings.c.key == "agent-profiles", settings.c.user_id.is_(None)
        )
    ).mappings().first()
    profiles = [
        _profile(PLAN_AGENT_PROFILE_ID, build=False),
        _profile(BUILD_AGENT_PROFILE_ID, build=True),
    ]
    if row is None:
        connection.execute(
            sa.insert(settings),
            [{"id": uuid4(), "key": "agent-profiles", "user_id": None, "data": {"profiles": profiles}}],
        )
        return
    data = dict(row["data"] or {})
    reserved_ids = {PLAN_AGENT_PROFILE_ID, BUILD_AGENT_PROFILE_ID}
    existing = [
        profile
        for profile in data.get("profiles", [])
        if str(profile.get("id")) not in reserved_ids
    ]
    if len(existing) > 100:
        raise RuntimeError("agent profile limit leaves no room for Plan and Build")
    data["profiles"] = [*existing, *profiles]
    data.setdefault("personal_agents_enabled", True)
    connection.execute(sa.update(settings).where(settings.c.id == row["id"]).values(data=data))


def _remove_profiles() -> None:
    connection = op.get_bind()
    settings = _settings_table()
    chat_sessions = sa.table(
        "chat_sessions",
        sa.column("agent_profile_id", sa.Uuid()),
    )
    connection.execute(
        sa.update(chat_sessions)
        .where(
            chat_sessions.c.agent_profile_id.in_(
                [UUID(PLAN_AGENT_PROFILE_ID), UUID(BUILD_AGENT_PROFILE_ID)]
            )
        )
        .values(agent_profile_id=UUID(DEFAULT_AGENT_PROFILE_ID))
    )
    rows = connection.execute(
        sa.select(settings.c.id, settings.c.data).where(
            settings.c.key == "agent-profiles", settings.c.user_id.is_(None)
        )
    ).mappings()
    reserved_ids = {PLAN_AGENT_PROFILE_ID, BUILD_AGENT_PROFILE_ID}
    for row in rows:
        data = dict(row["data"] or {})
        data["profiles"] = [
            profile
            for profile in data.get("profiles", [])
            if str(profile.get("id")) not in reserved_ids
        ]
        connection.execute(sa.update(settings).where(settings.c.id == row["id"]).values(data=data))


def upgrade() -> None:
    op.add_column("agent_runs", sa.Column("parent_run_id", sa.Uuid(), nullable=True))
    op.add_column("agent_runs", sa.Column("agent_profile_id", sa.Uuid(), nullable=True))
    op.add_column("agent_runs", sa.Column("internal_request", sa.Text(), nullable=True))
    op.add_column("agent_runs", sa.Column("internal_transcript", JSONB(), nullable=True))
    op.add_column("agent_runs", sa.Column("internal_result", sa.Text(), nullable=True))
    op.create_index("ix_agent_runs_parent_run_id", "agent_runs", ["parent_run_id"])
    op.create_foreign_key(
        "fk_agent_runs_parent_run_id_agent_runs",
        "agent_runs",
        "agent_runs",
        ["parent_run_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_check_constraint(
        "ck_agent_runs_parent_not_self",
        "agent_runs",
        "parent_run_id IS NULL OR parent_run_id <> id",
    )
    op.create_check_constraint(
        "ck_agent_runs_internal_run_shape_valid",
        "agent_runs",
        "(parent_run_id IS NULL AND internal_request IS NULL AND internal_transcript IS NULL "
        "AND internal_result IS NULL) OR (parent_run_id IS NOT NULL AND input_message_id IS NULL "
        "AND result_message_id IS NULL AND internal_request IS NOT NULL "
        "AND internal_transcript IS NOT NULL)",
    )
    _install_profiles()


def downgrade() -> None:
    _remove_profiles()
    op.drop_constraint("ck_agent_runs_internal_run_shape_valid", "agent_runs", type_="check")
    op.drop_constraint("ck_agent_runs_parent_not_self", "agent_runs", type_="check")
    op.drop_constraint(
        "fk_agent_runs_parent_run_id_agent_runs", "agent_runs", type_="foreignkey"
    )
    op.drop_index("ix_agent_runs_parent_run_id", table_name="agent_runs")
    op.drop_column("agent_runs", "internal_result")
    op.drop_column("agent_runs", "internal_transcript")
    op.drop_column("agent_runs", "internal_request")
    op.drop_column("agent_runs", "agent_profile_id")
    op.drop_column("agent_runs", "parent_run_id")
