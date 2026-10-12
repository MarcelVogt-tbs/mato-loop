import importlib.util
from pathlib import Path
from uuid import uuid4

import sqlalchemy as sa


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


def test_connection_availability_migration_and_navigation_reset(monkeypatch):
    path = Path(__file__).parents[1] / "alembic/versions/9618efd925e3_cleanup_permissions.py"
    spec = importlib.util.spec_from_file_location("connection_availability_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    owner, other = uuid4(), uuid4()
    shared, personal, foreign = (str(uuid4()) for _ in range(3))
    builtin, profile = "00000000-0000-4000-8000-000000000001", str(uuid4())
    documents = {
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
            actual = {(row.key, row.user_id): row.data for row in connection.execute(sa.select(settings))}
            assert actual == {
                ("agent-profiles", None): {"builtin_access_overrides": {builtin: {}}},
                ("agent-profiles", owner): {"profiles": [{"id": profile, "overrides": {"instructions": "Keep"}}]},
                ("integrations", None): documents["integrations", None],
                ("integrations", owner): {"instances": [{"id": personal, "permissions": {"private_permissions": {"policies": {"calendar.read": "ask"}}}, "profile_permissions": {profile: {"available": True}}}]},
                ("integrations", other): documents["integrations", other],
            }
            assert connection.execute(sa.select(grants.c.capability)).scalars().all() == ["resource.read"]
    finally:
        engine.dispose()
