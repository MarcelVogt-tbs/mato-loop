"""mcp tool restrictions

Revision ID: c0d8c740723e
Revises: 20ed2d643118
Create Date: 2026-11-15 20:13:28.305512
"""

import hashlib
from collections.abc import Sequence
from copy import deepcopy
from uuid import UUID

import sqlalchemy as sa
from alembic import op

revision: str = 'c0d8c740723e'
down_revision: str | None = '20ed2d643118'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _availability(document):
    result = dict(document.get("tool_availability", {}))
    for name, available in document.get("toolAvailability", {}).items():
        result[name] = result.get(name, True) and available
    return result


def _disable(permissions, resource):
    values = permissions.pop("resource_availability", {})
    values.update(permissions.get("resourceAvailability", {}))
    values[resource] = False
    permissions["resourceAvailability"] = values


def migrate_settings(source: list[dict]) -> list[dict]:
    rows = deepcopy(source)
    connections = []
    names = {}
    for row in rows:
        if row["key"] != "integrations":
            continue
        for instance in row["data"].get("instances", []):
            if instance.get("adapter_type") not in {"mcp_streamable_http", "sonobin"}:
                continue
            connection_id = str(UUID(instance["id"]))
            tools = {}
            for tool in instance.get("config", {}).get("tools", []):
                original = tool["name"]
                digest = hashlib.sha256(original.encode()).hexdigest()
                name = f"mcp_{UUID(connection_id).hex}_{digest[:12]}"
                tools[name] = f"tool:{digest}"
                names[name] = connection_id
            connections.append((row["user_id"], instance, tools))

    global_tools = {}
    profiles = []
    for row in rows:
        if row["key"] == "tools" and row["user_id"] is None:
            global_tools = _availability(row["data"])
        elif row["key"] == "agent-profiles":
            for profile_id, override in row["data"].get("builtin_access_overrides", {}).items():
                profiles.append((None, profile_id, None, _availability(override)))
            for profile in row["data"].get("profiles", []):
                profiles.append((row["user_id"], profile["id"], profile.get("base_profile_id"),
                                 _availability(profile.get("overrides", profile))))
    bases = {profile_id: available for owner, profile_id, base, available in profiles if owner is None and base is None}

    for owner, instance, tools in connections:
        overrides = instance.get("profile_permissions", {})
        for name, resource in tools.items():
            if global_tools.get(name) is False:
                _disable(instance.setdefault("permissions", {}), resource)
                # A connection override must not undo a formerly global hard tool disable.
                for override in overrides.values():
                    _disable(override, resource)
            for profile_owner, profile_id, base, availability in profiles:
                if owner is not None and profile_owner not in (None, owner):
                    continue
                effective = {**bases.get(base, {}), **availability}
                if global_tools.get(name) is False or effective.get(name) is False:
                    instance["profile_permissions"] = overrides
                    override = overrides.setdefault(profile_id, {})
                    _disable(override, resource)
                elif availability.get(name) is True and bases.get(base, {}).get(name) is False:
                    # Preserve an explicit child exception to an inherited name restriction.
                    instance["profile_permissions"] = overrides
                    override = overrides.setdefault(profile_id, {})
                    values = override.pop("resource_availability", {})
                    values.update(override.get("resourceAvailability", {}))
                    values.setdefault(resource, True)
                    override["resourceAvailability"] = values

    # Only remove names whose original connection/tool binding was found. Unknown entries stay inert.
    def remove_names(document):
        if isinstance(document, list):
            return [remove_names(value) for value in document]
        if not isinstance(document, dict):
            return document
        return {key: {name: value for name, value in item.items() if name not in names}
                if key in {"tool_availability", "toolAvailability"} and isinstance(item, dict)
                else remove_names(item) for key, item in document.items()}

    for row in rows:
        if row["key"] in {"tools", "agent-profiles"}:
            row["data"] = remove_names(row["data"])
    return rows


def upgrade() -> None:
    settings = sa.table("settings", sa.column("id", sa.Uuid()), sa.column("key", sa.String()),
                        sa.column("user_id", sa.Uuid()), sa.column("data", sa.JSON()))
    bind = op.get_bind()
    before = [dict(row) for row in bind.execute(sa.select(settings).where(
        settings.c.key.in_(("tools", "agent-profiles", "integrations"))
    )).mappings()]
    originals = {row["id"]: row["data"] for row in before}
    for row in migrate_settings(before):
        if row["data"] != originals[row["id"]]:
            bind.execute(settings.update().where(settings.c.id == row["id"]).values(data=row["data"]))


def downgrade() -> None:
    # Resource restrictions also work with the previous runtime; do not restore name-bound gates.
    pass
