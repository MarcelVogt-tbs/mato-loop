"""browser permissions

Revision ID: 6b6ce77be67f
Revises: b68f89b53e5a
Create Date: 2026-10-25 13:52:39.295987
"""

import json
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '6b6ce77be67f'
down_revision: Union[str, None] = 'b68f89b53e5a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_BROWSER_CAPABILITIES = {"browser.read", "browser.write", "browser.use", "browser.code"}


def upgrade() -> None:
    browser_ids = _rewrite_settings()
    grants = sa.table(
        "permission_grants", sa.column("id", sa.Uuid()), sa.column("capability", sa.String()),
        sa.column("resource_type", sa.String()), sa.column("resource_scope", sa.String()),
    )
    connection = op.get_bind()
    removed = []
    for row in connection.execute(sa.select(grants).where(sa.or_(
        grants.c.capability.in_(_BROWSER_CAPABILITIES),
        grants.c.resource_type.in_(("integration_resource", "integration_operation")),
    ))).mappings():
        if row["capability"] not in _BROWSER_CAPABILITIES:
            try:
                integration_id = json.loads(row["resource_scope"]).get("integrationId")
            except (ValueError, TypeError, AttributeError):
                continue
            if integration_id not in browser_ids:
                continue
        removed.append(row["id"])
    if removed:
        connection.execute(grants.delete().where(grants.c.id.in_(removed)))
    op.execute(sa.text(
        "UPDATE approval_requests SET status = 'expired', resolved_at = CURRENT_TIMESTAMP "
        "WHERE request_type = 'agent_action' AND status = 'pending' "
        "AND capability IN ('browser.read', 'browser.write', 'browser.use', 'browser.code')"
    ))


def downgrade() -> None:
    # Discarded rules and consent cannot be restored safely.
    pass


def _policy_tree(source: dict) -> dict:
    if all(isinstance(value, str) for value in source.values()):
        return {key: value for key, value in source.items() if key not in _BROWSER_CAPABILITIES}
    return {key: _policy_tree(value) for key, value in source.items()}


def _permissions(source: dict) -> dict:
    return {
        key: _policy_tree(value)
        if key in {"policies", "integration_policies", "integrationPolicies", "resource_policies", "resourcePolicies"}
        else value
        for key, value in source.items()
    }


def _document(value):
    if isinstance(value, list):
        return [_document(item) for item in value]
    if not isinstance(value, dict):
        return value
    return {
        key: _permissions(item)
        if key in {"private_permissions", "public_permissions", "privatePermissions", "publicPermissions"} and isinstance(item, dict)
        else _document(item)
        for key, item in value.items()
    }


def _rewrite_settings() -> set[str]:
    settings = sa.table("settings", sa.column("id", sa.Uuid()), sa.column("key", sa.String()), sa.column("data", sa.JSON()))
    connection = op.get_bind()
    browser_ids = set()
    for row in connection.execute(sa.select(settings).where(settings.c.key.in_(("tools", "agent-profiles", "integrations")))).mappings():
        if row["key"] == "integrations":
            browser_ids.update(
                item["id"] for item in row["data"].get("instances", [])
                if item.get("integration_type") == item.get("adapter_type") == "browser"
            )
        data = _document(row["data"])
        if data != row["data"]:
            connection.execute(settings.update().where(settings.c.id == row["id"]).values(data=data))
    return browser_ids
