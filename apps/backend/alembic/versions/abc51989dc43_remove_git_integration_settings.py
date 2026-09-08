"""remove git integration settings

Revision ID: abc51989dc43
Revises: 49e718887770
Create Date: 2026-09-08 09:20:35.447656

Data-only cleanup. Deleted configuration and grants cannot be recovered;
downgrade intentionally does not restore them or reopen expired approvals.
"""

from copy import deepcopy
import json
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision: str = 'abc51989dc43'
down_revision: Union[str, None] = '49e718887770'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_GIT_CAPABILITIES = {"git.read", "git.stage", "git.clone", "git.commit"}


def _clean_permissions(data: dict, removed_ids: set[str]) -> None:
    policies = data.get("policies")
    if isinstance(policies, dict):
        for capability in _GIT_CAPABILITIES:
            policies.pop(capability, None)
    for field in ("integration_policies", "integrationPolicies",
                  "resource_policies", "resourcePolicies"):
        mapping = data.get(field)
        if not isinstance(mapping, dict):
            continue
        for integration_id in removed_ids:
            mapping.pop(integration_id, None)
        for value in mapping.values():
            if not isinstance(value, dict):
                continue
            policies = value.values() if field.startswith("resource") else (value,)
            for policy in policies:
                if isinstance(policy, dict):
                    for capability in _GIT_CAPABILITIES:
                        policy.pop(capability, None)


def _clean_settings(key: str, source: object, removed_ids: set[str]) -> object:
    if not isinstance(source, dict):
        return source
    data = deepcopy(source)
    if key == "integrations":
        instances = data.get("instances")
        if isinstance(instances, list):
            data["instances"] = [
                item for item in instances
                if not isinstance(item, dict)
                or (item.get("adapter_type") != "git" and item.get("integration_type") != "git")
            ]
        availability = data.get("adapter_availability")
        if isinstance(availability, dict):
            availability.pop("git", None)
        for field in ("allowed_personal_adapter_types", "disabled_integration_adapter_types"):
            if isinstance(data.get(field), list):
                data[field] = [item for item in data[field] if item != "git"]
        return data

    targets = data.get("profiles", []) if key == "agent-profiles" else [data]
    if not isinstance(targets, list):
        return data
    for target in targets:
        if not isinstance(target, dict):
            continue
        # Only known settings fields are traversed, never instructions or adapter config.
        _clean_permissions(target, removed_ids)
        for field in ("private_permissions", "privatePermissions",
                      "public_permissions", "publicPermissions"):
            permissions = target.get(field)
            if isinstance(permissions, dict):
                _clean_permissions(permissions, removed_ids)
        for field in ("tool_availability", "toolAvailability"):
            availability = target.get(field)
            if isinstance(availability, dict):
                for name in list(availability):
                    if name.startswith("git_"):
                        availability.pop(name)
        for field in ("integration_availability", "integrationAvailability",
                      "resource_labels", "resourceLabels",
                      "resource_availability", "resourceAvailability"):
            mapping = target.get(field)
            if not isinstance(mapping, dict):
                continue
            for integration_id in removed_ids:
                mapping.pop(integration_id, None)
            if field in ("resource_availability", "resourceAvailability"):
                for name in list(mapping):
                    if name.startswith("git_"):
                        mapping.pop(name)
                    elif isinstance(mapping[name], dict):
                        for capability in _GIT_CAPABILITIES:
                            mapping[name].pop(capability, None)
    return data


def _is_git_permission(row: sa.RowMapping, removed_ids: set[str]) -> bool:
    if row["capability"] in _GIT_CAPABILITIES:
        return True
    if row["resource_type"] != "integration_operation":
        return False
    try:
        scope = json.loads(row["resource_scope"])
    except (TypeError, ValueError):
        return False
    return (
        isinstance(scope, dict)
        and isinstance(scope.get("integrationId"), str)
        and scope["integrationId"] in removed_ids
    )


def upgrade() -> None:
    connection = op.get_bind()
    settings = sa.table(
        "settings",
        sa.column("id", sa.Uuid()),
        sa.column("user_id", sa.Uuid()),
        sa.column("key", sa.String()),
        sa.column("data", JSONB()),
    )
    rows = connection.execute(
        sa.select(settings).where(settings.c.key.in_(("integrations", "tools", "agent-profiles")))
    ).mappings().all()
    removed_by_owner = {}
    for row in rows:
        if row["key"] != "integrations" or not isinstance(row["data"], dict):
            continue
        instances = row["data"].get("instances")
        if not isinstance(instances, list):
            continue
        removed_by_owner.setdefault(row["user_id"], set()).update(
            item["id"] for item in instances
            if isinstance(item, dict) and isinstance(item.get("id"), str)
            and (item.get("adapter_type") == "git" or item.get("integration_type") == "git")
        )
    global_ids = removed_by_owner.get(None, set())
    for row in rows:
        removed_ids = global_ids | removed_by_owner.get(row["user_id"], set())
        data = _clean_settings(row["key"], row["data"], removed_ids)
        if data != row["data"]:
            connection.execute(sa.update(settings).where(settings.c.id == row["id"]).values(data=data))

    agents = sa.table(
        "execution_agents", sa.column("id", sa.Uuid()), sa.column("operations", JSONB())
    )
    for row in connection.execute(sa.select(agents)).mappings().all():
        if not isinstance(row["operations"], list):
            continue
        operations = [
            item for item in row["operations"]
            if not isinstance(item, str) or not item.startswith("integration.git.")
        ]
        if operations != row["operations"]:
            connection.execute(
                sa.update(agents).where(agents.c.id == row["id"]).values(operations=operations)
            )

    sessions = sa.table(
        "chat_sessions", sa.column("id", sa.Uuid()), sa.column("user_id", sa.Uuid())
    )
    for table_name in ("permission_grants", "approval_requests"):
        table = sa.table(
            table_name,
            sa.column("id", sa.Uuid()),
            sa.column("session_id", sa.Uuid()),
            sa.column("capability", sa.String()),
            sa.column("resource_type", sa.String()),
            sa.column("resource_scope", sa.Text()),
        )
        if table_name == "approval_requests":
            for column in (
                sa.column("user_id", sa.Uuid()),
                sa.column("request_type", sa.String()),
                sa.column("status", sa.String()),
                sa.column("resolved_at", sa.DateTime(timezone=True)),
            ):
                table.append_column(column)
            statement = sa.select(table).where(
                table.c.request_type == "agent_action", table.c.status == "pending"
            )
        else:
            statement = sa.select(table, sessions.c.user_id).select_from(
                table.join(sessions, table.c.session_id == sessions.c.id)
            )
        statement = statement.where(sa.or_(
            table.c.capability.in_(_GIT_CAPABILITIES),
            table.c.resource_type == "integration_operation",
        ))
        for row in connection.execute(statement).mappings().all():
            removed_ids = global_ids | removed_by_owner.get(row["user_id"], set())
            if not _is_git_permission(row, removed_ids):
                continue
            mutation = (
                sa.update(table).values(status="expired", resolved_at=sa.func.now())
                if table_name == "approval_requests" else sa.delete(table)
            )
            connection.execute(mutation.where(table.c.id == row["id"]))


def downgrade() -> None:
    # Removed configuration and grants are not backed up; expiration is final.
    pass
