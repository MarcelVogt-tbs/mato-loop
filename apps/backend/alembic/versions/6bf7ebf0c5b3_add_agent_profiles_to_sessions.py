"""add agent profiles and trust-scoped tool policies

Revision ID: 6bf7ebf0c5b3
Revises: 8845b9185f20
Create Date: 2026-08-27 20:24:03.722639
"""

from typing import Sequence, Union
from uuid import UUID, uuid4

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "6bf7ebf0c5b3"
down_revision: Union[str, None] = "8845b9185f20"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

DEFAULT_AGENT_PROFILE_ID = UUID("00000000-0000-4000-8000-000000000001")


def _webfetch_pair(value: object) -> tuple[str, str]:
    return {
        "disabled": ("disabled", "disabled"),
        "ask": ("ask", "ask"),
        "auto_public": ("auto", "ask"),
        "auto_allowed": ("auto", "auto"),
    }.get(str(value), ("auto", "ask"))


def _most_restrictive(values: list[str]) -> str:
    return min(values, key={"disabled": 0, "ask": 1, "auto": 2}.get)


def _migrate_tool_data(data: dict, model_policies: list[object] | None = None) -> dict:
    result = dict(data)
    policies = dict(result.pop("policies", {}) or {})
    policies.pop("network.fetch", None)
    integration_policies = dict(
        result.pop("integration_policies", result.pop("integrationPolicies", {})) or {}
    )
    resource_policies = dict(
        result.pop("resource_policies", result.pop("resourcePolicies", {})) or {}
    )
    web_fetch = dict(result.get("web_fetch", result.pop("webFetch", {})) or {})
    enabled = web_fetch.pop("enabled", True)
    old_policy = web_fetch.pop(
        "default_policy", web_fetch.pop("defaultPolicy", "auto_public")
    )
    web_fetch.pop("allowed_private_origins", None)
    web_fetch.pop("allowedPrivateOrigins", None)
    pairs = [_webfetch_pair("disabled" if enabled is False else old_policy)]
    pairs.extend(
        _webfetch_pair(value)
        for value in model_policies or []
        if value not in {None, "inherit"}
    )
    policies["network.fetch.public"] = _most_restrictive(
        [public for public, _private in pairs]
    )
    policies["network.fetch.private"] = _most_restrictive(
        [private for _public, private in pairs]
    )
    permissions = {
        "policies": policies,
        "integration_policies": integration_policies,
        "resource_policies": resource_policies,
    }
    result["private_permissions"] = permissions
    result["public_permissions"] = {
        key: dict(value) for key, value in permissions.items()
    }
    result["tool_availability"] = dict(result.get("tool_availability", {}))
    result["resource_availability"] = {
        "webfetch": {
            "network.fetch.public": True,
            "network.fetch.private": False,
        }
    }
    result["web_fetch"] = web_fetch
    return result


def _downgrade_tool_data(data: dict) -> dict:
    result = dict(data)
    private = dict(result.pop("private_permissions", {}) or {})
    public = dict(result.pop("public_permissions", {}) or {})

    def flat_map(name: str) -> dict:
        def merge(left: object, right: object) -> object:
            if isinstance(left, dict) and isinstance(right, dict):
                return {
                    key: merge(left.get(key), right.get(key))
                    for key in left.keys() | right.keys()
                }
            if left is None:
                return right
            if right is None:
                return left
            return _most_restrictive([str(left), str(right)])

        merged = merge(
            dict(private.get(name, {}) or {}),
            dict(public.get(name, {}) or {}),
        )
        assert isinstance(merged, dict)
        return merged

    policies = flat_map("policies")
    public_fetch = _most_restrictive(
        [
            str(private.get("policies", {}).get("network.fetch.public", "auto")),
            str(public.get("policies", {}).get("network.fetch.public", "auto")),
        ]
    )
    private_fetch = _most_restrictive(
        [
            str(private.get("policies", {}).get("network.fetch.private", "ask")),
            str(public.get("policies", {}).get("network.fetch.private", "ask")),
        ]
    )
    policies.pop("network.fetch.public", None)
    policies.pop("network.fetch.private", None)
    result["policies"] = policies
    result["integration_policies"] = flat_map("integration_policies")
    result["resource_policies"] = flat_map("resource_policies")
    tool_available = result.pop("tool_availability", {}).get("webfetch", True)
    availability = result.pop("resource_availability", {}) or {}
    public_available = availability.get("webfetch", {}).get(
        "network.fetch.public", True
    )
    private_available = availability.get("webfetch", {}).get(
        "network.fetch.private", False
    )
    old_policy = (
        "disabled"
        if not tool_available
        or not public_available
        or public_fetch == private_fetch == "disabled"
        else "ask"
        if public_fetch != "auto"
        else "auto_allowed"
        if private_fetch == "auto" and private_available
        else "auto_public"
    )
    web_fetch = dict(result.get("web_fetch", {}) or {})
    web_fetch.update(
        {"enabled": old_policy != "disabled", "default_policy": old_policy}
    )
    web_fetch.setdefault("allowed_private_origins", [])
    result["web_fetch"] = web_fetch
    return result


def _strip_model_webfetch(data: dict) -> tuple[dict, list[object]]:
    result = dict(data)
    models = []
    policies = []
    for source in result.get("models", []):
        model = dict(source)
        policies.append(
            model.pop("webfetch_policy", model.pop("webfetchPolicy", "inherit"))
        )
        models.append(model)
    result["models"] = models
    return result, policies


def _default_profile() -> dict:
    return {
        "id": str(DEFAULT_AGENT_PROFILE_ID),
        "name": "Allgemein",
        "description": "Standardagent für Sessions ohne ausgewähltes Profil.",
        "persona": "",
        "instructions": "Follow the user's request and the shared persona.",
        "persona_mode": "extend",
        "private_permissions": {
            "policies": {},
            "integration_policies": {},
            "resource_policies": {},
        },
        "public_permissions": {
            "policies": {},
            "integration_policies": {},
            "resource_policies": {},
        },
    }


def _settings_table() -> sa.TableClause:
    return sa.table(
        "settings",
        sa.column("id", sa.Uuid()),
        sa.column("key", sa.String()),
        sa.column("user_id", sa.Uuid()),
        sa.column("data", JSONB()),
    )


def _install_default_profile() -> None:
    connection = op.get_bind()
    settings = _settings_table()
    row = connection.execute(
        sa.select(settings.c.id, settings.c.data).where(
            settings.c.key == "agent-profiles", settings.c.user_id.is_(None)
        )
    ).mappings().first()
    if row is None:
        connection.execute(
            sa.insert(settings),
            [
                {
                    "id": uuid4(),
                    "key": "agent-profiles",
                    "user_id": None,
                    "data": {
                        "personal_agents_enabled": True,
                        "profiles": [_default_profile()],
                    },
                }
            ],
        )
        return
    data = dict(row["data"] or {})
    profiles = [
        profile
        for profile in data.get("profiles", [])
        if str(profile.get("id")) != str(DEFAULT_AGENT_PROFILE_ID)
        and str(profile.get("name", "")).strip().casefold() not in {"default", "allgemein"}
    ]
    if len(profiles) >= 100:
        raise RuntimeError("agent profile limit leaves no room for Allgemein")
    data["profiles"] = [_default_profile(), *profiles]
    data.setdefault("personal_agents_enabled", True)
    connection.execute(
        sa.update(settings).where(settings.c.id == row["id"]).values(data=data)
    )


def _remove_default_profile() -> None:
    connection = op.get_bind()
    settings = _settings_table()
    row = connection.execute(
        sa.select(settings.c.id, settings.c.data).where(
            settings.c.key == "agent-profiles", settings.c.user_id.is_(None)
        )
    ).mappings().first()
    if row is None:
        return
    data = dict(row["data"] or {})
    data["profiles"] = [
        profile
        for profile in data.get("profiles", [])
        if str(profile.get("id")) != str(DEFAULT_AGENT_PROFILE_ID)
    ]
    connection.execute(
        sa.update(settings).where(settings.c.id == row["id"]).values(data=data)
    )


def upgrade() -> None:
    op.add_column(
        "chat_sessions",
        sa.Column(
            "agent_profile_id",
            sa.Uuid(),
            nullable=True,
            server_default=sa.text(f"'{DEFAULT_AGENT_PROFILE_ID}'::uuid"),
        ),
    )
    op.add_column(
        "permission_grants", sa.Column("agent_profile_id", sa.Uuid(), nullable=True)
    )
    op.add_column(
        "permission_grants", sa.Column("trust_level", sa.String(length=20), nullable=True)
    )
    op.create_check_constraint(
        "trust_level_valid",
        "permission_grants",
        "trust_level IS NULL OR trust_level IN ('public', 'private')",
    )
    connection = op.get_bind()
    settings = _settings_table()
    rows = connection.execute(
        sa.select(settings.c.id, settings.c.key, settings.c.user_id, settings.c.data).where(
            settings.c.key.in_(["ai", "tools"])
        )
    ).mappings().all()
    model_policies: dict[UUID | None, list[object]] = {}
    for row in rows:
        if row["key"] != "ai":
            continue
        migrated, policies = _strip_model_webfetch(dict(row["data"] or {}))
        model_policies[row["user_id"]] = policies
        connection.execute(
            sa.update(settings).where(settings.c.id == row["id"]).values(data=migrated)
        )
    tool_rows = {row["user_id"]: row for row in rows if row["key"] == "tools"}
    global_tool_data = dict(tool_rows.get(None, {}).get("data") or {})
    for owner_id, row in tool_rows.items():
        policies = [
            *(model_policies.get(None, []) if owner_id is not None else []),
            *model_policies.get(owner_id, []),
        ]
        data = _migrate_tool_data(dict(row["data"] or {}), policies)
        if owner_id is not None and any(
            value not in {None, "inherit"}
            for value in model_policies.get(owner_id, [])
        ):
            data["personal_tool_defaults_enabled"] = True
        connection.execute(
            sa.update(settings)
            .where(settings.c.id == row["id"])
            .values(data=data)
        )
    for owner_id, policies in model_policies.items():
        if owner_id in tool_rows or not any(
            value not in {None, "inherit"} for value in policies
        ):
            continue
        combined_policies = [
            *(model_policies.get(None, []) if owner_id is not None else []),
            *policies,
        ]
        data = _migrate_tool_data(
            global_tool_data if owner_id is not None else {}, combined_policies
        )
        if owner_id is not None:
            data["personal_tool_defaults_enabled"] = True
            for permissions in (
                data["private_permissions"],
                data["public_permissions"],
            ):
                permissions["integration_policies"] = {}
                permissions["resource_policies"] = {}
        connection.execute(
            sa.insert(settings),
            [{"id": uuid4(), "key": "tools", "user_id": owner_id, "data": data}],
        )
    op.execute(sa.text("DELETE FROM permission_grants WHERE capability = 'network.fetch'"))
    _install_default_profile()
    op.execute(
        sa.text(
            "UPDATE chat_sessions SET agent_profile_id = :profile_id "
            "WHERE agent_profile_id IS NULL"
        ).bindparams(profile_id=DEFAULT_AGENT_PROFILE_ID)
    )
    op.alter_column("chat_sessions", "agent_profile_id", nullable=False)


def downgrade() -> None:
    connection = op.get_bind()
    settings = _settings_table()
    rows = connection.execute(
        sa.select(settings.c.id, settings.c.data).where(settings.c.key == "tools")
    ).mappings().all()
    for row in rows:
        connection.execute(
            sa.update(settings)
            .where(settings.c.id == row["id"])
            .values(data=_downgrade_tool_data(dict(row["data"] or {})))
        )
    _remove_default_profile()
    op.drop_constraint("trust_level_valid", "permission_grants", type_="check")
    op.drop_column("permission_grants", "trust_level")
    op.drop_column("permission_grants", "agent_profile_id")
    op.drop_column("chat_sessions", "agent_profile_id")
