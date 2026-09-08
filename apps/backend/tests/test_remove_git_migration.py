import importlib.util
import json
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from uuid import uuid4

import sqlalchemy as sa


def test_git_cleanup_is_scoped_data_only_and_preserves_history(monkeypatch) -> None:
    path = (
        Path(__file__).parents[1]
        / "alembic/versions/abc51989dc43_remove_git_integration_settings.py"
    )
    spec = importlib.util.spec_from_file_location("remove_git_migration", path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    assert migration.revision == "abc51989dc43"
    assert migration.down_revision == "49e718887770"

    metadata = sa.MetaData()
    settings = sa.Table(
        "settings", metadata,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid()),
        sa.Column("key", sa.String()),
        sa.Column("data", sa.JSON()),
    )
    agents = sa.Table(
        "execution_agents", metadata,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("operations", sa.JSON()),
    )
    sessions = sa.Table(
        "chat_sessions", metadata,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid()),
    )
    grants = sa.Table(
        "permission_grants", metadata,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("session_id", sa.Uuid()),
        sa.Column("capability", sa.String()),
        sa.Column("resource_type", sa.String()),
        sa.Column("resource_scope", sa.Text()),
    )
    approvals = sa.Table(
        "approval_requests", metadata,
        *(sa.Column(column.name, column.type, primary_key=column.primary_key) for column in grants.c),
        sa.Column("user_id", sa.Uuid()),
        sa.Column("request_type", sa.String()),
        sa.Column("status", sa.String()),
        sa.Column("resolved_at", sa.DateTime()),
        sa.Column("decision", sa.String()),
        sa.Column("payload", sa.JSON()),
        sa.Column("summary", sa.Text()),
    )
    messages = sa.Table(
        "messages", metadata,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("content", sa.JSON()),
    )
    owner, other = uuid4(), uuid4()
    global_git, personal_git, unrelated = (str(uuid4()) for _ in range(3))
    policies = {
        "git.read": "disabled", "git.stage": "auto", "git.clone": "ask",
        "git.commit": "auto", "shell.write": "ask", "github.read": "auto",
    }
    kept_policies = {"shell.write": "ask", "github.read": "auto"}
    dynamic = {"git.read": "git_commit", "instructions": "Use git.read and git_commit"}
    tool_data = {
        "tool_availability": {"git_status": False, "git_future": True, "shell": True, "github": False},
        "toolAvailability": {"git_diff": True, "webfetch": False},
        "private_permissions": {
            "policies": policies,
            "integration_policies": {key: policies for key in (global_git, personal_git, unrelated)},
            "resource_policies": {
                key: {"git.read": policies, "repo": policies}
                for key in (global_git, personal_git, unrelated)
            },
            "unknown": dynamic,
        },
        "publicPermissions": {
            "policies": policies,
            "integrationPolicies": {key: policies for key in (global_git, personal_git, unrelated)},
            "resourcePolicies": {key: {"repo": policies} for key in (global_git, personal_git, unrelated)},
        },
        "resource_labels": {key: dynamic for key in (global_git, personal_git, unrelated)},
        "resourceAvailability": {
            global_git: {"git.read": True}, personal_git: {"git.commit": True},
            unrelated: {"git.stage": False, "calendar.read": True},
            "git_status": {"git.read": True},
        },
        "shell_prefixes": {"allowed": ["git status", "git diff"]},
        "unknown": dynamic,
    }
    rows, expected_settings = [], {}
    for user_id, git_id in ((None, global_git), (owner, personal_git), (other, None)):
        removed = {global_git, personal_git} if user_id == owner else {global_git}
        kept_ids = {global_git, personal_git, unrelated} - removed
        kept_instance = {
            "id": personal_git if user_id == other else unrelated,
            "adapter_type": "caldav", "integration_type": "calendar",
            "name": "git", "config": dynamic,
        }
        integrations = {
            "instances": [kept_instance, None, "git"],
            "adapter_availability": {"git": "disabled", "caldav": "instance_only"},
            "allowed_personal_adapter_types": ["git", "caldav", "github"],
            "disabled_integration_adapter_types": ["git", "openscad", "git"],
            "personal_integrations_enabled": False,
            "unknown": dynamic,
        }
        expected_integrations = deepcopy(integrations)
        expected_integrations["adapter_availability"].pop("git")
        expected_integrations["allowed_personal_adapter_types"] = ["caldav", "github"]
        expected_integrations["disabled_integration_adapter_types"] = ["openscad"]
        if git_id is not None:
            integrations["instances"] += [
                {"id": git_id, "adapter_type": "git", "config": dynamic},
                {"id": git_id, "integration_type": "git", "adapter_type": "legacy"},
                {"integration_type": "git"},
            ]
        expected_tools = deepcopy(tool_data)
        expected_tools["tool_availability"] = {"shell": True, "github": False}
        expected_tools["toolAvailability"] = {"webfetch": False}
        for field, integration_field, resource_field in (
            ("private_permissions", "integration_policies", "resource_policies"),
            ("publicPermissions", "integrationPolicies", "resourcePolicies"),
        ):
            permissions = expected_tools[field]
            permissions["policies"] = kept_policies
            permissions[integration_field] = {key: kept_policies for key in kept_ids}
            permissions[resource_field] = {
                key: {resource: kept_policies for resource in tool_data[field][resource_field][key]}
                for key in kept_ids
            }
        expected_tools["resource_labels"] = {key: dynamic for key in kept_ids}
        expected_tools["resourceAvailability"] = {unrelated: {"calendar.read": True}}
        if personal_git in kept_ids:
            expected_tools["resourceAvailability"][personal_git] = {}
        profiles, expected_profiles = [], []
        for profile_id in ("00000000-0000-4000-8000-000000000003", str(uuid4())):
            profile = {
                "id": profile_id, "name": "git_commit", "instructions": json.dumps(dynamic),
                "description": "Keep git.read", "private_permissions": tool_data["private_permissions"],
                "publicPermissions": tool_data["publicPermissions"],
                "integration_availability": {global_git: True, personal_git: False, unrelated: True},
                "integrationAvailability": {global_git: False, personal_git: True},
            }
            profiles.append(profile)
            expected_profile = deepcopy(profile)
            for field in ("private_permissions", "publicPermissions"):
                expected_profile[field] = expected_tools[field]
            for field in ("integration_availability", "integrationAvailability"):
                expected_profile[field] = {
                    key: value for key, value in profile[field].items() if key not in removed
                }
            expected_profiles.append(expected_profile)
        profiles += [None, "git", 42, {}, {"private_permissions": None}]
        expected_profiles += [None, "git", 42, {}, {"private_permissions": None}]
        for key, data, expected in (
            ("integrations", integrations, expected_integrations),
            ("tools", tool_data, expected_tools),
            ("agent-profiles", {"profiles": profiles}, {"profiles": expected_profiles}),
            ("general", tool_data, tool_data),
        ):
            row = {"id": uuid4(), "user_id": user_id, "key": key, "data": deepcopy(data)}
            rows.append(row)
            expected_settings[row["id"]] = {**row, "data": deepcopy(expected)}
    for key in ("integrations", "tools", "agent-profiles"):
        for data in (None, [], "git", {"profiles": None, "instances": False, "private_permissions": []}):
            row = {"id": uuid4(), "user_id": uuid4(), "key": key, "data": data}
            rows.append(row)
            expected_settings[row["id"]] = deepcopy(row)

    session_rows = [{"id": uuid4(), "user_id": user_id} for user_id in (owner, other)]
    grant_rows, approval_rows, removed_grants, expired_approvals = [], [], set(), set()
    for session in session_rows:
        for capability, resource_type, scope, removed in (
            *((capability, "integration_operation", "{}", True) for capability in policies if capability.startswith("git.")),
            ("calendar.read", "integration_operation", json.dumps({"integrationId": global_git}), True),
            ("calendar.read", "integration_operation", json.dumps({"integrationId": personal_git}), session["user_id"] == owner),
            ("calendar.read", "integration_operation", json.dumps({"integrationId": unrelated}), False),
            ("shell.write", "shell_command", json.dumps({"integrationId": global_git}), False),
            ("shell.write", "shell_command", "git commit", False),
            ("calendar.read", "integration_operation", "invalid git.read", False),
            ("calendar.read", "integration_operation", "[]", False),
            ("calendar.read", "integration_operation", '{"integrationId": []}', False),
        ):
            grant = {
                "id": uuid4(), "session_id": session["id"], "capability": capability,
                "resource_type": resource_type, "resource_scope": scope,
            }
            grant_rows.append(grant)
            if removed:
                removed_grants.add(grant["id"])
            for status, request_type in (
                ("pending", "agent_action"), ("approved", "agent_action"),
                ("denied", "agent_action"), ("expired", "agent_action"),
                ("pending", "memory_change"),
            ):
                approval = {
                    **grant, "id": uuid4(), "user_id": session["user_id"],
                    "session_id": None, "request_type": request_type, "status": status,
                    "resolved_at": None if status == "pending" else datetime(2026, 9, 1),
                    "decision": "allow_once" if status == "approved" else None,
                    "payload": dynamic, "summary": "Keep git.read history",
                }
                approval_rows.append(approval)
                if removed and status == "pending" and request_type == "agent_action":
                    expired_approvals.add(approval["id"])
    agent_rows = [
        {"id": uuid4(), "operations": ["integration.git.status", "shell.execute", "integration.git.commit", "integration.github.read", "integration.git", None]},
        {"id": uuid4(), "operations": ["integration.git.clone"]},
        {"id": uuid4(), "operations": []},
    ]
    message_rows = [{"id": uuid4(), "content": {"tool": "git_commit", "text": dynamic}}]
    before = deepcopy((rows, grant_rows, approval_rows, agent_rows, message_rows))
    engine = sa.create_engine("sqlite://")
    metadata.create_all(engine)
    with engine.begin() as connection:
        for table, data in (
            (settings, rows), (sessions, session_rows), (grants, grant_rows),
            (approvals, approval_rows), (agents, agent_rows), (messages, message_rows),
        ):
            connection.execute(table.insert(), data)
        monkeypatch.setattr(migration.op, "get_bind", lambda: connection)
        migration.upgrade()
        assert {row.id: dict(row) for row in connection.execute(sa.select(settings)).mappings()} == expected_settings
        assert list(connection.execute(sa.select(grants)).mappings()) == [
            row for row in grant_rows if row["id"] not in removed_grants
        ]
        actual_approvals = list(connection.execute(sa.select(approvals)).mappings())
        for actual, original in zip(actual_approvals, approval_rows, strict=True):
            if original["id"] in expired_approvals:
                assert actual["resolved_at"] is not None
                assert actual == {**original, "status": "expired", "resolved_at": actual["resolved_at"]}
            else:
                assert actual == original
        assert list(connection.execute(sa.select(agents)).mappings()) == [
            {**agent_rows[0], "operations": ["shell.execute", "integration.github.read", "integration.git", None]},
            {**agent_rows[1], "operations": []}, agent_rows[2],
        ]
        assert list(connection.execute(sa.select(messages)).mappings()) == message_rows
        assert list(connection.execute(sa.select(sessions)).mappings()) == session_rows
        snapshot = {table.name: list(connection.execute(sa.select(table)).mappings()) for table in metadata.tables.values()}
        migration.upgrade()
        migration.downgrade()
        assert {table.name: list(connection.execute(sa.select(table)).mappings()) for table in metadata.tables.values()} == snapshot
        assert (rows, grant_rows, approval_rows, agent_rows, message_rows) == before
    engine.dispose()
