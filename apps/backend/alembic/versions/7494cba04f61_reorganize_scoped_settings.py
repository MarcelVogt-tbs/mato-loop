"""reorganize scoped permissions and persona assignments

Revision ID: 7494cba04f61
Revises: d6550319a647
Create Date: 2026-10-05 13:46:22.480809
"""

from copy import deepcopy
from typing import Sequence, Union
from uuid import NAMESPACE_URL, uuid4, uuid5

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision: str = '7494cba04f61'
down_revision: Union[str, None] = 'd6550319a647'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


GENERAL_PROFILE_ID = "00000000-0000-4000-8000-000000000001"
DEFAULT_PERSONA_ID = "00000000-0000-4000-8000-000000000002"
INTEGRATION_TOOL_FIELDS = {
    "resource_labels", "default_calendar", "default_task_list", "web_search",
    "resourceLabels", "defaultCalendar", "defaultTaskList", "webSearch",
}


def migrate_documents(documents: dict) -> dict:
    data = deepcopy(documents)
    identity = data.setdefault(("agent-identity", None), {
        "default_persona_id": DEFAULT_PERSONA_ID,
        "personas": [{"id": DEFAULT_PERSONA_ID, "name": "Standard", "instructions": ""}],
    })
    global_personas = {item["id"] for item in identity.get("personas", [])}
    global_profiles = data.get(("agent-profiles", None), {})
    bases = global_profiles.get("builtin_access_overrides", {})
    identity["profile_personas"] = {
        profile_id: overrides["persona_id"]
        for profile_id, overrides in bases.items()
        if overrides.get("persona_id") in global_personas
    }
    for (key, owner), document in list(data.items()):
        if key != "agent-profiles" and not (key == "integrations" and owner is not None):
            continue
        personal = data.get(("agent-identity", owner), {})
        available = global_personas | {item["id"] for item in personal.get("personas", [])}
        profiles = document.get("profiles", []) if key == "agent-profiles" else [
            {"id": str(uuid5(NAMESPACE_URL, f"matoloop:tbs-desk:{item['id']}"))}
            for item in document.get("instances", []) if item.get("adapter_type") == "tbs-desk"
        ]
        assignments = {}
        for profile in profiles:
            selected = profile.get("overrides", {}).get("persona_id", bases.get(profile.get("base_profile_id"), {}).get("persona_id"))
            if selected not in available:
                selected = personal.get("default_persona_id") if owner is not None else None
            if selected in available:
                assignments[profile["id"]] = selected
        if assignments:
            data.setdefault(("agent-identity", owner), {}).setdefault("profile_personas", {}).update(assignments)
    for (key, owner), document in data.items():
        if key == "agent-identity":
            document.pop("personal_display_names_enabled", None)
            document.setdefault("profile_personas", {})
            if owner is not None:
                document.pop("default_persona_id", None)

    general = global_profiles.get("builtin_access_overrides", {}).get(GENERAL_PROFILE_ID, {})
    tools = data.setdefault(("tools", None), {})
    for field in ("private_permissions", "public_permissions"):
        # Old tool permission snapshots were ignored; only live General rules become defaults.
        tools[field] = {"policies": deepcopy(general.get(field, {}).get("policies", {}))}
    tools["shell_prefixes"] = deepcopy(general.get("shell_prefixes", {"read": [], "write": []}))

    for (key, owner), document in list(data.items()):
        if key != "integrations":
            continue
        profiles = data.get(("agent-profiles", owner), {})
        for instance in document.get("instances", []):
            integration_id = instance["id"]
            instance["permissions"] = {}
            instance["profile_permissions"] = {}
            for field in ("private_permissions", "public_permissions"):
                old = general.get(field, {}) if owner is None else {}
                instance["permissions"][field] = {
                    "policies": {**deepcopy(old.get("policies", {})), **deepcopy(old.get("integration_policies", {}).get(integration_id, {}))},
                    "resource_policies": deepcopy(old.get("resource_policies", {}).get(integration_id, {})),
                }
            if owner is not None:
                for profile in profiles.get("profiles", []):
                    overrides = profile.get("overrides", {})
                    selected = {}
                    for field in ("private_permissions", "public_permissions"):
                        old = overrides.get(field, {})
                        selected[field] = {
                            "policies": deepcopy(old.get("integration_policies", {}).get(integration_id, {})),
                            "resource_policies": deepcopy(old.get("resource_policies", {}).get(integration_id, {})),
                        }
                    if integration_id in overrides.get("integration_availability", {}):
                        selected["available"] = overrides["integration_availability"][integration_id]
                    if "available" in selected or any(branch["policies"] or branch["resource_policies"] for field, branch in selected.items() if field != "available"):
                        instance["profile_permissions"][profile["id"]] = selected

    for (key, owner), document in data.items():
        if key == "agent-profiles":
            entries = [*document.get("builtin_access_overrides", {}).values(), *(profile.get("overrides", {}) for profile in document.get("profiles", []))]
            for overrides in entries:
                overrides.pop("persona_id", None)
                for field in ("private_permissions", "public_permissions"):
                    if field in overrides:
                        overrides[field].pop("integration_policies", None)
                        overrides[field].pop("resource_policies", None)
            if owner is None:
                for profile in document.get("profiles", []):
                    for field in ("private_permissions", "public_permissions", "shell_prefixes"):
                        profile.get("overrides", {}).pop(field, None)
        elif key == "tools" and owner is not None:
            data[key, owner] = {field: value for field, value in document.items() if field in INTEGRATION_TOOL_FIELDS}
    for field in ("private_permissions", "public_permissions", "shell_prefixes"):
        general.pop(field, None)
    return data


def upgrade() -> None:
    settings = sa.table("settings", sa.column("id", sa.Uuid()), sa.column("key", sa.String()), sa.column("user_id", sa.Uuid()), sa.column("data", JSONB()))
    connection = op.get_bind()
    rows = list(connection.execute(sa.select(settings).where(settings.c.key.in_(("tools", "agent-profiles", "integrations", "agent-identity")))).mappings())
    by_scope = {(row["key"], row["user_id"]): row for row in rows}
    for scope, document in migrate_documents({scope: row["data"] for scope, row in by_scope.items()}).items():
        if scope in by_scope:
            connection.execute(settings.update().where(settings.c.id == by_scope[scope]["id"]).values(data=document))
        else:
            connection.execute(settings.insert().values(id=uuid4(), key=scope[0], user_id=scope[1], data=document))


def downgrade() -> None:
    # Default documents need no conversion; refuse to silently discard scoped rules.
    settings = sa.table("settings", sa.column("key", sa.String()), sa.column("data", JSONB()))
    rows = op.get_bind().execute(sa.select(settings).where(
        settings.c.key.in_(("tools", "integrations", "agent-identity"))
    )).mappings()
    for row in rows:
        data = row["data"]
        permissions = []
        if row["key"] == "tools":
            permissions = [data]
            configured = any(data.get("shell_prefixes", {}).values())
        elif row["key"] == "integrations":
            instances = data.get("instances", [])
            permissions = [instance.get("permissions", {}) for instance in instances]
            configured = any(instance.get("profile_permissions") for instance in instances)
        else:
            configured = bool(data.get("profile_personas"))
        if configured or any(
            branch.get(field, {}).get(rules)
            for branch in permissions
            for field in ("private_permissions", "public_permissions")
            for rules in ("policies", "resource_policies")
        ):
            raise RuntimeError("Downgrade requires default permissions and no profile persona assignments.")
