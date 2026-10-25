import importlib.util
import json
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa


def test_browser_reset_removes_only_browser_consent(monkeypatch):
    path = Path(__file__).parents[1] / "alembic/versions/6b6ce77be67f_browser_permissions.py"
    spec = importlib.util.spec_from_file_location("browser_permissions_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    metadata = sa.MetaData()
    settings = sa.Table("settings", metadata, sa.Column("id", sa.Uuid(), primary_key=True), sa.Column("key", sa.String()), sa.Column("data", sa.JSON()))
    grants = sa.Table("permission_grants", metadata, sa.Column("id", sa.Uuid(), primary_key=True), sa.Column("capability", sa.String()), sa.Column("resource_type", sa.String()), sa.Column("resource_scope", sa.String()))
    approvals = sa.Table("approval_requests", metadata, sa.Column("request_type", sa.String()), sa.Column("capability", sa.String()), sa.Column("status", sa.String()), sa.Column("resolved_at", sa.DateTime()))
    browser_id, other_id = str(uuid4()), str(uuid4())
    engine = sa.create_engine("sqlite://")
    metadata.create_all(engine)
    try:
        with engine.begin() as connection:
            connection.execute(settings.insert(), [{"id": uuid4(), "key": "integrations", "data": {"instances": [{
                "id": browser_id, "integration_type": "browser", "adapter_type": "browser",
                "permissions": {"private_permissions": {"policies": {"browser.use": "auto"}}},
            }]}}])
            connection.execute(grants.insert(), [
                {"id": uuid4(), "capability": capability, "resource_type": resource, "resource_scope": json.dumps({"integrationId": integration})}
                for capability, resource, integration in [
                    ("browser.code", "integration_operation", browser_id),
                    ("category.write", "integration_resource", browser_id),
                    ("category.read", "integration_resource", browser_id),
                    ("category.write", "integration_resource", other_id),
                    ("category.write", "execution_path", browser_id),
                ]
            ])
            connection.execute(approvals.insert(), [
                {"request_type": "agent_action", "capability": capability, "status": "pending"}
                for capability in ("browser.read", "browser.write", "browser.use", "browser.code", "filesystem.write")
            ])
            monkeypatch.setattr(migration.op, "get_bind", lambda: connection)
            monkeypatch.setattr(migration.op, "execute", connection.execute)
            migration.upgrade()
            migration.downgrade()
            assert (
                connection.execute(sa.select(settings.c.data)).scalar_one()["instances"][0]["permissions"],
                connection.execute(sa.select(grants.c.resource_type)).scalars().all(),
                connection.execute(sa.select(approvals.c.status)).scalars().all(),
            ) == (
                {"private_permissions": {"policies": {}}},
                ["integration_resource", "execution_path"],
                ["expired", "expired", "expired", "expired", "pending"],
            )
    finally:
        engine.dispose()


def test_unified_grant_migration_removes_only_approval_created_grants(monkeypatch) -> None:
    path = (
        Path(__file__).parents[1]
        / "alembic/versions/fcb30fdb20f7_replace_legacy_permission_grants.py"
    )
    spec = importlib.util.spec_from_file_location("permission_grant_migration", path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    metadata = sa.MetaData()
    grants = sa.Table(
        "permission_grants",
        metadata,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("source_approval_id", sa.Uuid()),
    )
    explicit_id, approval_id = uuid4(), uuid4()
    engine = sa.create_engine("sqlite://")
    metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(grants.insert(), [
            {"id": explicit_id, "source_approval_id": None},
            {"id": uuid4(), "source_approval_id": approval_id},
        ])
        monkeypatch.setattr(migration.op, "execute", connection.execute)
        migration.upgrade()
        assert connection.execute(sa.select(grants.c.id)).scalars().all() == [explicit_id]
        migration.downgrade()
        assert connection.execute(sa.select(grants.c.id)).scalars().all() == [explicit_id]
    engine.dispose()


def test_grant_scope_migration_resets_consent_without_expiring_memory_proposals(monkeypatch):
    path = Path(__file__).parents[1] / "alembic/versions/ab5df5b19220_grant_scopes.py"
    spec = importlib.util.spec_from_file_location("grant_scopes_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    metadata = sa.MetaData()
    grants = sa.Table("permission_grants", metadata, sa.Column("id", sa.Uuid(), primary_key=True),
                      sa.Column("capability", sa.String()), sa.Column("resource_type", sa.String()))
    approvals = sa.Table("approval_requests", metadata, sa.Column("request_type", sa.String()),
                         sa.Column("status", sa.String()), sa.Column("resolved_at", sa.DateTime()))
    engine = sa.create_engine("sqlite://")
    metadata.create_all(engine)
    try:
        with engine.begin() as connection:
            connection.execute(grants.insert(), [
                {"id": uuid4(), "capability": capability, "resource_type": resource}
                for capability, resource in [
                    ("category.write", "integration_resource"), ("category.write", "execution_path"),
                    ("category.read", "shell_computer"), ("category.delete", "shell_computer"),
                    ("category.read", "execution_path"), ("category.delete", "session_path"),
                    ("execution.directory", "execution_directory"), ("resource.write", "shell_action"),
                ]
            ])
            connection.execute(approvals.insert(), [
                {"request_type": kind, "status": status} for kind, status in [
                    ("agent_action", "pending"), ("agent_action", "approved"), ("memory_change", "pending"),
                ]
            ])
            monkeypatch.setattr(migration.op, "execute", connection.execute)
            migration.upgrade()
            migration.downgrade()
            assert (connection.execute(sa.select(grants)).all(), connection.execute(sa.select(approvals.c.request_type, approvals.c.status)).all()) == (
                [], [("agent_action", "expired"), ("agent_action", "approved"), ("memory_change", "pending")],
            )
    finally:
        engine.dispose()


def test_permission_migration_preserves_settings_and_resets_navigation(monkeypatch):
    path = Path(__file__).parents[1] / "alembic/versions/9cee124e2dfb_simplify_permissions.py"
    spec = importlib.util.spec_from_file_location("connection_availability_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    owner, other = uuid4(), uuid4()
    shared, personal, foreign = (str(uuid4()) for _ in range(3))
    builtin, profile = "00000000-0000-4000-8000-000000000001", str(uuid4())
    documents = {
        ("tools", None): {"private_permissions": {"policies": {"filesystem.read": "disabled"}}, "tool_availability": {"shell_run": False}},
        ("agent-profiles", None): {"builtin_access_overrides": {builtin: {"integration_availability": {shared: True}}}},
        ("agent-profiles", owner): {"profiles": [{"id": profile, "overrides": {
            "instructions": "Keep", "integrationAvailability": {shared: False, personal: True, foreign: True},
        }}]},
        ("integrations", None): {"instances": [{"id": shared, "profile_permissions": {builtin: {"available": False}}}]},
        ("integrations", owner): {"instances": [{"id": personal, "permissions": {"private_permissions": {"policies": {"calendar.read": "ask"}}}}]},
        ("integrations", other): {"instances": [{"id": foreign}]},
    }
    metadata = sa.MetaData()
    settings = sa.Table("settings", metadata, sa.Column("id", sa.Uuid(), primary_key=True), sa.Column("key", sa.String()), sa.Column("user_id", sa.Uuid()), sa.Column("data", sa.JSON()))
    grants = sa.Table("permission_grants", metadata, sa.Column("id", sa.Uuid(), primary_key=True), sa.Column("capability", sa.String()), sa.Column("run_id", sa.Uuid()))
    engine = sa.create_engine("sqlite://")
    metadata.create_all(engine)
    try:
        with engine.begin() as connection:
            connection.execute(settings.insert(), [{"id": uuid4(), "key": key, "user_id": user, "data": data} for (key, user), data in documents.items()])
            connection.execute(grants.insert(), [
                {"id": uuid4(), "capability": capability, "run_id": run_id}
                for capability, run_id in [("execution.directory", None), ("execution.directory", uuid4()), ("resource.read", None)]
            ])
            monkeypatch.setattr(migration.op, "get_bind", lambda: connection)
            migration.upgrade()
            migration.downgrade()
            actual = {(row.key, row.user_id): row.data for row in connection.execute(sa.select(settings))}
            assert actual == {
                ("tools", None): {**documents["tools", None], "private_permission_preset": "ask_changes", "public_permission_preset": "ask_all"},
                ("agent-profiles", None): {"builtin_access_overrides": {builtin: {}}},
                ("agent-profiles", owner): {"profiles": [{"id": profile, "overrides": {"instructions": "Keep"}}]},
                ("integrations", None): documents["integrations", None],
                ("integrations", owner): {"instances": [{"id": personal, "permissions": {"private_permissions": {"policies": {"calendar.read": "ask"}}}, "profile_permissions": {profile: {"available": True}}}]},
                ("integrations", other): documents["integrations", other],
            }
            assert connection.execute(sa.select(grants.c.capability)).scalars().all() == ["resource.read"]
    finally:
        engine.dispose()


@pytest.mark.parametrize("policies", [{}, {"filesystem.read": "disabled", "session.files.read": "auto", "shell.write": "auto"}])
def test_tool_defaults_migration_keeps_only_explicit_overrides(policies):
    path = Path(__file__).parents[1] / "alembic/versions/9cee124e2dfb_simplify_permissions.py"
    spec = importlib.util.spec_from_file_location("tool_defaults_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    original = {"private_permissions": {"policies": policies}}
    assert migration.migrate_documents({("tools", None): original}) == {
        ("tools", None): {**original, "private_permission_preset": "ask_changes", "public_permission_preset": "ask_all"},
    }


def test_new_presets_reset_choices_and_preserve_explicit_delete_rules(monkeypatch):
    path = Path(__file__).parents[1] / "alembic/versions/39856f53cf7e_tool_presets.py"
    spec = importlib.util.spec_from_file_location("tool_presets_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    metadata = sa.MetaData()
    settings = sa.Table("settings", metadata, sa.Column("id", sa.Uuid(), primary_key=True), sa.Column("key", sa.String()), sa.Column("data", sa.JSON()))
    documents = {
        "tools": {"private_permission_preset": "auto_all", "public_permission_preset": "ask_changes_sensitive",
            "private_permissions": {"policies": {"filesystem.delete": "disabled", "media.generate.image": "ask"}},
            "public_permissions": {"policies": {"filesystem.delete": "auto", "session.files.delete": "ask"}}},
        "agent-profiles": {"profiles": [{"overrides": {"public_permissions": {"policies": {"filesystem.delete": "disabled"}}}}]},
    }
    engine = sa.create_engine("sqlite://")
    metadata.create_all(engine)
    try:
        with engine.begin() as connection:
            connection.execute(settings.insert(), [{"id": uuid4(), "key": key, "data": data} for key, data in documents.items()])
            monkeypatch.setattr(migration.op, "get_bind", lambda: connection)
            migration.upgrade()
            actual = {row.key: row.data for row in connection.execute(sa.select(settings))}
            assert actual == {
                "tools": {**documents["tools"], "private_permission_preset": "ask_changes", "public_permission_preset": "balanced",
                    "private_permissions": {"policies": {"filesystem.delete": "disabled", "session.files.delete": "disabled", "media.generate.image": "ask"}}},
                "agent-profiles": {"profiles": [{"overrides": {"public_permissions": {"policies": {"filesystem.delete": "disabled", "session.files.delete": "disabled"}}}}]},
            }
            migration.downgrade()
            assert connection.execute(sa.select(settings.c.data).where(settings.c.key == "tools")).scalar_one()["public_permission_preset"] == "ask_all"
    finally:
        engine.dispose()
