import importlib.util
from pathlib import Path
from uuid import uuid4


def _migration_module():
    path = (
        Path(__file__).parents[1]
        / "alembic/versions/e224cc43e462_add_scoped_persona_profiles.py"
    )
    spec = importlib.util.spec_from_file_location("agent_identity_migration", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_persona_migration_preserves_extend_and_empty_replace() -> None:
    migration = _migration_module()
    identity = {"personas": []}

    migrated = migration._migrate_profiles(
        {
            "profiles": [
                {
                    "id": "extend",
                    "name": "Recherche",
                    "persona": "Pruefe Quellen.",
                    "persona_mode": "extend",
                },
                {
                    "id": "replace",
                    "name": "Neutral",
                    "persona": "",
                    "persona_mode": "replace",
                },
            ]
        },
        identity,
        personal=True,
        old_identity_instructions="Sei freundlich.",
    )

    assert identity["personas"][0]["instructions"] == (
        "Sei freundlich.\n\nPruefe Quellen."
    )
    assert migrated["profiles"][0]["persona_id"] == identity["personas"][0]["id"]
    assert migrated["profiles"][1]["persona_id"] == str(
        migration.DEFAULT_PERSONA_ID
    )


def test_persona_migration_preserves_maximum_combined_instructions() -> None:
    migration = _migration_module()

    identity = {"personas": []}
    migration._migrate_profiles(
        {
            "profiles": [
                {
                    "id": "extend",
                    "name": "Lang",
                    "persona": "b" * 16_000,
                    "persona_mode": "extend",
                }
            ]
        },
        identity,
        personal=True,
        old_identity_instructions="a" * 16_000,
    )

    assert len(identity["personas"][0]["instructions"]) == 32_002


def test_persona_migration_resolves_repeated_name_suffixes() -> None:
    migration = _migration_module()
    personas = [
        {"name": "Agent"},
        {"name": "Agent (deadbeef)"},
        {"name": "Agent (deadbeef-2)"},
    ]

    assert migration._unique_persona_name(personas, "Agent", "deadbeef-rest") == (
        "Agent (deadbeef-3)"
    )


class _Result:
    def __init__(self, rows=()):
        self.rows = rows

    def mappings(self):
        return self

    def __iter__(self):
        return iter(self.rows)


class _Connection:
    def __init__(self, rows):
        self.rows = rows
        self.statements = []

    def execute(self, statement):
        self.statements.append(statement)
        return _Result(self.rows) if len(self.statements) == 1 else _Result()


def test_persona_downgrade_preserves_explicit_empty_persona(monkeypatch) -> None:
    migration = _migration_module()
    user_id = uuid4()
    rows = [
        {
            "id": uuid4(),
            "key": "agent-identity",
            "user_id": None,
            "data": {
                "default_persona_id": str(migration.DEFAULT_PERSONA_ID),
                "personas": [
                    {
                        "id": str(migration.DEFAULT_PERSONA_ID),
                        "name": "Standard",
                        "instructions": "",
                    }
                ],
            },
        },
        {
            "id": uuid4(),
            "key": "agent-identity",
            "user_id": user_id,
            "data": {"display_name": None, "default_persona_id": None, "personas": []},
        },
        {
            "id": uuid4(),
            "key": "agent-profiles",
            "user_id": user_id,
            "data": {
                "profiles": [
                    {
                        "id": "neutral",
                        "name": "Neutral",
                        "persona_id": str(migration.DEFAULT_PERSONA_ID),
                    }
                ]
            },
        },
    ]
    connection = _Connection(rows)
    monkeypatch.setattr(migration.op, "get_bind", lambda: connection)

    migration.downgrade()

    profile_data = connection.statements[1].compile().params["data"]
    assert profile_data["profiles"][0]["persona"] == ""
    assert profile_data["profiles"][0]["persona_mode"] == "replace"
