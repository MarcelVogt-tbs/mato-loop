"""reset agent profiles for inheritance

Revision ID: 8cf1ced587b7
Revises: 012558511db3
Create Date: 2026-09-15 20:34:10.250246
"""

from typing import Sequence, Union
from uuid import NAMESPACE_URL, UUID, uuid5

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision: str = "8cf1ced587b7"
down_revision: Union[str, None] = "012558511db3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

CORE_IDS = (
    UUID("00000000-0000-4000-8000-000000000001"),
    UUID("00000000-0000-4000-8000-000000000003"),
    UUID("00000000-0000-4000-8000-000000000004"),
)


def _legacy_default_profiles() -> list[dict]:
    plan_policies = {
        "filesystem.read": "auto",
        "filesystem.write": "disabled",
        "filesystem.delete": "disabled",
        "session.files.write": "auto",
        "shell.read": "auto",
        "shell.write": "disabled",
    }
    build_policies = {
        "filesystem.read": "auto",
        "filesystem.write": "auto",
        "filesystem.delete": "auto",
        "shell.read": "auto",
        "shell.write": "auto",
    }
    return [
        {
            "id": "00000000-0000-4000-8000-000000000001",
            "name": "Allgemein",
            "description": "Standardagent für Sessions ohne ausgewähltes Profil.",
            "instructions": "Follow the user's request and the selected persona.",
        },
        {
            "id": "00000000-0000-4000-8000-000000000003",
            "name": "Plan",
            "description": (
                "Plant ohne erkannte Schreibaktionen. Liest im Arbeitsverzeichnis automatisch, "
                "auch mit öffentlichen Modellen; gelesene Inhalte werden an das Modell übermittelt."
            ),
            "instructions": (
                "Answer informational questions directly without inspecting, creating, or updating the "
                "canonical plan. Selecting Plan mode alone does not require a plan artifact. For requests "
                "that call for planning or preparing changes, understand the request and relevant context. "
                "Prefer the simplest approach that fully meets the request: reuse existing code and "
                "conventions, then standard-library or native features, then installed dependencies. Add "
                "abstractions, dependencies, configuration, or extension points only when current "
                "requirements justify them. Ask grouped questions about missing user decisions that "
                "materially affect scope, architecture, or acceptance; incorporate the answers and ask "
                "again when they reveal new required decisions. Decide ordinary implementation details "
                "and state material assumptions. Produce a plan that is quick to scan and sufficient to "
                "implement. Include only the recommended approach, affected files, implementation steps, "
                "and verification. Reference existing requirements instead of repeating them. Include "
                "risks and open decisions only when they affect implementation. Do not finish by merely "
                "listing blocking questions. Do not modify computer files or run commands classified as "
                "writes."
            ),
            "private_permissions": {"policies": plan_policies},
            "public_permissions": {"policies": plan_policies},
        },
        {
            "id": "00000000-0000-4000-8000-000000000004",
            "name": "Build",
            "description": (
                "Liest, ändert und löscht im Arbeitsverzeichnis automatisch und führt Shell-Befehle aus, "
                "auch mit öffentlichen Modellen; gelesene Inhalte werden an das Modell übermittelt."
            ),
            "instructions": (
                "Carry out the exact current or delegated task; keep an analysis-only task read-only. "
                "Understand the relevant context before changing it. Prefer the simplest solution that "
                "fully meets the request: reuse existing code and conventions, then standard-library or "
                "native features, then installed dependencies. Add abstractions, dependencies, "
                "configuration, or extension points only when current requirements justify them. Fix root "
                "causes, preserve required validation and security, and avoid unrelated refactors. For a "
                "request to implement the current session plan, read that plan and apply newer user "
                "instructions over it. Continue through coherent steps without waiting for another prompt. "
                "Ask only for a missing user decision that materially blocks the work; decide ordinary "
                "implementation details from the project. Verify the result appropriately and report the "
                "outcome, checks, and remaining blockers concisely."
            ),
            "private_permissions": {"policies": build_policies},
            "public_permissions": {"policies": build_policies},
        },
    ]


def _retained_profile_ids() -> tuple[UUID, ...]:
    profile_ids = set(CORE_IDS)
    rows = op.get_bind().execute(
        sa.text("SELECT data FROM settings WHERE key = 'integrations'")
    ).scalars()
    for data in rows:
        if not isinstance(data, dict) or not isinstance(data.get("instances"), list):
            continue
        for instance in data["instances"]:
            if not isinstance(instance, dict) or instance.get("adapter_type") != "tbs-desk":
                continue
            try:
                integration_id = UUID(str(instance["id"]))
            except (KeyError, TypeError, ValueError):
                continue
            profile_ids.add(uuid5(NAMESPACE_URL, f"matoloop:tbs-desk:{integration_id}"))
    return tuple(profile_ids)


def _reset_references() -> None:
    profile_ids = _retained_profile_ids()
    op.execute(
        sa.text(
            "DELETE FROM permission_grants WHERE agent_profile_id IS NOT NULL "
            "AND agent_profile_id NOT IN :profile_ids"
        ).bindparams(sa.bindparam("profile_ids", profile_ids, expanding=True))
    )
    op.execute(
        sa.text(
            "UPDATE chat_sessions SET agent_profile_id = :default_id "
            "WHERE agent_profile_id NOT IN :profile_ids"
        ).bindparams(
            sa.bindparam("default_id", CORE_IDS[0]),
            sa.bindparam("profile_ids", profile_ids, expanding=True),
        )
    )
    op.execute(
        sa.text(
            "UPDATE agent_runs SET agent_profile_id = :default_id "
            "WHERE status NOT IN ('completed', 'failed', 'cancelled') "
            "AND agent_profile_id NOT IN :profile_ids"
        ).bindparams(
            sa.bindparam("default_id", CORE_IDS[0]),
            sa.bindparam("profile_ids", profile_ids, expanding=True),
        )
    )


def upgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE settings SET data = jsonb_set(data, '{profiles}', '[]'::jsonb) "
            "WHERE key = 'agent-profiles'"
        )
    )
    _reset_references()


def downgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE settings SET data = jsonb_set(data, '{profiles}', "
            "CASE WHEN user_id IS NULL THEN :profiles ELSE '[]'::jsonb END) "
            "WHERE key = 'agent-profiles'"
        ).bindparams(
            sa.bindparam("profiles", _legacy_default_profiles(), type_=JSONB)
        )
    )
    _reset_references()
