import importlib.util
from pathlib import Path
from uuid import uuid4


def _migration_module():
    path = (
        Path(__file__).parents[1]
        / "alembic/versions/1a04e736b206_extract_user_profile_from_memories.py"
    )
    spec = importlib.util.spec_from_file_location("user_profile_migration", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Result:
    def __init__(self, rows=()):
        self.rows = rows

    def mappings(self):
        return self

    def all(self):
        return self.rows

    def first(self):
        return self.rows[0] if self.rows else None

    def scalars(self):
        return iter(self.rows)


class _Connection:
    def __init__(self, *results):
        self.results = iter(results)
        self.statements = []

    def execute(self, statement, params=None):
        self.statements.append((statement, params))
        return next(self.results, _Result())


def test_upgrade_moves_core_identity_memories_to_user_profile(monkeypatch) -> None:
    migration = _migration_module()
    user_id = uuid4()
    connection = _Connection(
        _Result(
            [
                {
                    "user_id": user_id,
                    "pin_kind": "preferred_name",
                    "audience": "both",
                    "content": "Viktor",
                }
            ]
        ),
        _Result(),
    )
    executed = []
    monkeypatch.setattr(migration.op, "get_bind", lambda: connection)
    monkeypatch.setattr(migration.op, "execute", executed.append)
    monkeypatch.setattr(migration.op, "f", lambda value: value)
    monkeypatch.setattr(migration.op, "drop_index", lambda *args, **kwargs: None)
    monkeypatch.setattr(migration.op, "drop_constraint", lambda *args, **kwargs: None)
    monkeypatch.setattr(migration.op, "drop_column", lambda *args, **kwargs: None)

    migration.upgrade()

    inserted = connection.statements[2][1]
    assert inserted == [
        {
            "id": inserted[0]["id"],
            "key": "user-profile",
            "user_id": user_id,
            "data": {
                "preferred_name": {
                    "both": "Viktor",
                    "private_only": None,
                    "public_only": None,
                },
                "form_of_address": {
                    "both": None,
                    "private_only": None,
                    "public_only": None,
                },
                "pronouns": {
                    "both": None,
                    "private_only": None,
                    "public_only": None,
                },
                "primary_language": {
                    "both": None,
                    "private_only": None,
                    "public_only": None,
                },
                "primary_residence": {
                    "both": None,
                    "private_only": None,
                    "public_only": None,
                },
            },
        }
    ]
    assert "payload" not in str(executed[0])
    assert "DELETE FROM memories" in str(executed[1])


def test_downgrade_restores_profile_as_pinned_identity_memory(monkeypatch) -> None:
    migration = _migration_module()
    user_id = uuid4()
    connection = _Connection(
        _Result(
            [
                {
                    "user_id": user_id,
                    "data": {
                        "preferred_name": {
                            "both": "Viktor",
                            "private_only": None,
                            "public_only": None,
                        }
                    },
                }
            ]
        ),
        _Result(),
    )
    monkeypatch.setattr(migration.op, "get_bind", lambda: connection)
    monkeypatch.setattr(migration.op, "add_column", lambda *args, **kwargs: None)
    monkeypatch.setattr(migration.op, "execute", lambda *args, **kwargs: None)
    monkeypatch.setattr(migration.op, "f", lambda value: value)
    monkeypatch.setattr(migration.op, "create_check_constraint", lambda *args, **kwargs: None)
    monkeypatch.setattr(migration.op, "create_index", lambda *args, **kwargs: None)

    migration.downgrade()

    insert = connection.statements[2][0]
    values = insert.compile().params
    assert values["user_id"] == user_id
    assert values["content"] == "Viktor"
    assert values["category"] == "identity"
    assert values["pinned"] is True
    assert values["pin_kind"] == "preferred_name"
