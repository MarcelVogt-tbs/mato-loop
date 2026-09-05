"""unify tool policy defaults

Revision ID: 601278fe1db7
Revises: eefc44080927
Create Date: 2026-09-04 22:13:08.841491
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB


revision: str = "601278fe1db7"
down_revision: Union[str, None] = "eefc44080927"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


PLAN_AGENT_PROFILE_ID = "00000000-0000-4000-8000-000000000003"
BUILD_AGENT_PROFILE_ID = "00000000-0000-4000-8000-000000000002"
_SPLIT_FETCH_CAPABILITIES = ("network.fetch.public", "network.fetch.private")
_OLD_DEFAULTS = {
    "filesystem.read": "auto",
    "filesystem.write": "ask",
    "filesystem.delete": "ask",
    "session.files.write": "auto",
    "network.search": "auto",
    "session.read": "ask",
    "session.search": "ask",
    "automation.read": "ask",
    "automation.create": "ask",
    "automation.update": "ask",
    "automation.run": "ask",
    "automation.delete": "disabled",
    "memory.read": "auto",
    "memory.write": "ask",
    "skills.load": "auto",
    "skills.write": "ask",
    "settings.user_profile.update": "ask",
    "settings.agent_identity.update": "ask",
    "shell.read": "auto",
    "shell.write": "ask",
    "system.info": "auto",
    "coding.analyze": "ask",
    "coding.plan.create": "ask",
    "coding.plan.read": "auto",
    "coding.execute.enqueue": "ask",
    "media.generate.image": "disabled",
    "media.edit.image": "ask",
    "media.generate.speech": "disabled",
    "media.generate.audio": "disabled",
    "media.transcribe.audio": "disabled",
    "media.analyze.video": "ask",
    "mail.read": "ask",
    "mail.drafts": "ask",
    "mail.send": "ask",
    "mail.delete": "ask",
    "calendar.read": "ask",
    "calendar.write": "ask",
    "calendar.delete": "ask",
    "tasks.read": "ask",
    "tasks.write": "ask",
    "tasks.delete": "ask",
    "knowledge.read": "ask",
    "knowledge.write": "ask",
    "knowledge.delete": "ask",
    "mealie.accounts.read": "ask",
    "recipes.read": "ask",
    "meal_plan.read": "ask",
    "meal_plan.write": "ask",
    "meal_plan.delete": "ask",
    "git.read": "auto",
    "git.stage": "ask",
    "git.clone": "ask",
    "git.commit": "ask",
    "cad.render": "auto",
}


def _settings_table() -> sa.TableClause:
    return sa.table(
        "settings",
        sa.column("id", sa.Uuid()),
        sa.column("key", sa.String()),
        sa.column("data", JSONB()),
    )


def _collapse_fetch_policy(policies: dict) -> dict:
    result = dict(policies or {})
    unified = result.get("network.fetch")
    public_present = "network.fetch.public" in result
    private_present = "network.fetch.private" in result
    public = result.pop("network.fetch.public", "auto")
    private = result.pop("network.fetch.private", "ask")
    if unified is None and (public_present or private_present):
        if (public, private) != ("auto", "ask"):
            rank = {"disabled": 0, "ask": 1, "auto": 2}
            result["network.fetch"] = min(
                (str(public), str(private)), key=lambda value: rank.get(value, 0)
            )
    return result


def _migrate_permissions(source: object, *, strip_defaults: bool) -> dict:
    result = dict(source or {}) if isinstance(source, dict) else {}
    policies = _collapse_fetch_policy(result.get("policies", {}))
    if strip_defaults:
        for capability, default in _OLD_DEFAULTS.items():
            if policies.get(capability) == default:
                policies.pop(capability)
    result["policies"] = policies
    integrations = dict(
        result.pop("integrationPolicies", result.get("integration_policies", {})) or {}
    )
    result["integration_policies"] = {
        key: _collapse_fetch_policy(value) for key, value in integrations.items()
    }
    resources = dict(
        result.pop("resourcePolicies", result.get("resource_policies", {})) or {}
    )
    result["resource_policies"] = {
        integration_id: {
            resource_key: _collapse_fetch_policy(policies)
            for resource_key, policies in dict(integration_resources or {}).items()
        }
        for integration_id, integration_resources in resources.items()
    }
    return result


def _expand_fetch_policy(policies: dict) -> dict:
    result = dict(policies or {})
    unified = result.pop("network.fetch", None)
    if unified is not None:
        result["network.fetch.public"] = unified
        result["network.fetch.private"] = unified
    return result


def _downgrade_permissions(source: object) -> dict:
    result = dict(source or {}) if isinstance(source, dict) else {}
    result["policies"] = _expand_fetch_policy(result.get("policies", {}))
    result["integration_policies"] = {
        key: _expand_fetch_policy(value)
        for key, value in dict(result.get("integration_policies", {}) or {}).items()
    }
    result["resource_policies"] = {
        integration_id: {
            resource_key: _expand_fetch_policy(policies)
            for resource_key, policies in dict(integration_resources or {}).items()
        }
        for integration_id, integration_resources in dict(
            result.get("resource_policies", {}) or {}
        ).items()
    }
    return result


def _migrate_tool_data(source: object) -> dict:
    result = dict(source or {}) if isinstance(source, dict) else {}
    for scope in ("private_permissions", "public_permissions"):
        result[scope] = _migrate_permissions(result.get(scope), strip_defaults=True)
    web_fetch = dict(result.pop("webFetch", result.get("web_fetch", {})) or {})
    public_only = web_fetch.pop("publicAddressesOnly", None)
    availability = dict(
        result.pop(
            "resourceAvailability", result.pop("resource_availability", {})
        )
        or {}
    )
    if public_only is None:
        public_only = not bool(
            dict(availability.get("webfetch", {}) or {}).get(
                "network.fetch.private", False
            )
        )
    web_fetch["public_addresses_only"] = bool(public_only)
    result["web_fetch"] = web_fetch
    return result


def _downgrade_tool_data(source: object) -> dict:
    result = dict(source or {}) if isinstance(source, dict) else {}
    for scope in ("private_permissions", "public_permissions"):
        result[scope] = _downgrade_permissions(result.get(scope))
    web_fetch = dict(result.get("web_fetch", {}) or {})
    public_only = bool(web_fetch.pop("public_addresses_only", True))
    result["web_fetch"] = web_fetch
    result["resource_availability"] = {
        "webfetch": {
            "network.fetch.public": True,
            "network.fetch.private": not public_only,
        }
    }
    return result


def _migrate_profiles_data(source: object, *, upgrade: bool) -> dict:
    result = dict(source or {}) if isinstance(source, dict) else {}
    profiles = []
    for item in result.get("profiles", []):
        profile = dict(item)
        for scope in ("private_permissions", "public_permissions"):
            permissions = (
                _migrate_permissions(profile.get(scope), strip_defaults=False)
                if upgrade
                else _downgrade_permissions(profile.get(scope))
            )
            policies = permissions["policies"]
            profile_id = str(profile.get("id"))
            if profile_id == PLAN_AGENT_PROFILE_ID:
                if upgrade:
                    policies.pop("shell.read", None)
                else:
                    policies["shell.read"] = "auto"
            elif profile_id == BUILD_AGENT_PROFILE_ID:
                for capability in (
                    "filesystem.write",
                    "filesystem.delete",
                    "shell.write",
                ):
                    if upgrade:
                        policies.pop(capability, None)
                    else:
                        policies[capability] = "auto"
            profile[scope] = permissions
        profiles.append(profile)
    result["profiles"] = profiles
    return result


def _update_settings(*, upgrade: bool) -> None:
    connection = op.get_bind()
    settings = _settings_table()
    rows = connection.execute(
        sa.select(settings.c.id, settings.c.key, settings.c.data).where(
            settings.c.key.in_(("tools", "agent-profiles"))
        )
    ).mappings()
    for row in rows:
        if row["key"] == "tools":
            data = (
                _migrate_tool_data(row["data"])
                if upgrade
                else _downgrade_tool_data(row["data"])
            )
        else:
            data = _migrate_profiles_data(row["data"], upgrade=upgrade)
        connection.execute(
            sa.update(settings).where(settings.c.id == row["id"]).values(data=data)
        )


def _clear_stale_permissions(*, upgrade: bool) -> None:
    capabilities = (
        _SPLIT_FETCH_CAPABILITIES if upgrade else ("network.fetch",)
    )
    grants = sa.table(
        "permission_grants", sa.column("capability", sa.String())
    )
    approvals = sa.table(
        "approval_requests",
        sa.column("request_type", sa.String()),
        sa.column("capability", sa.String()),
        sa.column("status", sa.String()),
        sa.column("resolved_at", sa.DateTime(timezone=True)),
    )
    op.execute(sa.delete(grants).where(grants.c.capability.in_(capabilities)))
    op.execute(
        sa.update(approvals)
        .where(
            approvals.c.request_type == "agent_action",
            approvals.c.status == "pending",
            approvals.c.capability.in_(capabilities),
        )
        .values(status="expired", resolved_at=sa.func.now())
    )


def upgrade() -> None:
    _update_settings(upgrade=True)
    _clear_stale_permissions(upgrade=True)


def downgrade() -> None:
    _update_settings(upgrade=False)
    _clear_stale_permissions(upgrade=False)
