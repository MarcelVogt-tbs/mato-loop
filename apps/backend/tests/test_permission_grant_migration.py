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
