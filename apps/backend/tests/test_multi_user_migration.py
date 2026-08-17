import importlib.util
from pathlib import Path
from uuid import uuid4

import pytest


def _migration_module():
    path = (
        Path(__file__).parents[1]
        / "alembic/versions/57f1454a3fd7_add_multi_user_ownership_and_.py"
    )
    spec = importlib.util.spec_from_file_location("multi_user_migration", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Result:
    def __init__(self, scalar=None, rows=()):
        self.value = scalar
        self.rows = rows

    def scalar(self):
        return self.value

    def scalar_one(self):
        return self.value

    def scalars(self):
        return iter(self.rows)


class _Connection:
    def __init__(self, *results):
        self.results = iter(results)
        self.statements = []

    def execute(self, statement, params=None):
        self.statements.append((str(statement), params))
        return next(self.results)


def test_memory_owner_backfill_uses_all_provenance_and_rejects_conflicts(
    monkeypatch,
) -> None:
    migration = _migration_module()
    connection = _Connection(_Result(scalar=uuid4()))
    monkeypatch.setattr(migration.op, "get_bind", lambda: connection)

    with pytest.raises(RuntimeError, match="conflicting source ownership"):
        migration._backfill_memory_owners()

    statement = connection.statements[0][0]
    assert "source_session_id" in statement
    assert "source_message_id" in statement
    assert "source_run_id" in statement
    assert "source_dream_run_id" in statement
    assert "memory_source_messages" in statement


def test_memory_owner_backfill_rejects_ambiguous_ownerless_memories(
    monkeypatch,
) -> None:
    migration = _migration_module()
    connection = _Connection(
        _Result(),
        _Result(),
        _Result(scalar=1),
        _Result(rows=(uuid4(), uuid4())),
    )
    monkeypatch.setattr(migration.op, "get_bind", lambda: connection)

    with pytest.raises(RuntimeError, match="exactly one account"):
        migration._backfill_memory_owners()
