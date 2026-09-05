import importlib.util
from pathlib import Path


def _migration_module():
    path = (
        Path(__file__).parents[1]
        / "alembic/versions/601278fe1db7_unify_tool_policy_defaults.py"
    )
    spec = importlib.util.spec_from_file_location("tool_policy_migration", path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def test_migration_applies_new_defaults_and_preserves_custom_overrides() -> None:
    migration = _migration_module()

    migrated = migration._migrate_tool_data(
        {
            "private_permissions": {
                "policies": {
                    "calendar.read": "ask",
                    "calendar.write": "auto",
                    "network.fetch.public": "auto",
                    "network.fetch.private": "ask",
                }
            },
            "public_permissions": {
                "policies": {
                    "calendar.read": "disabled",
                    "network.fetch.public": "disabled",
                    "network.fetch.private": "auto",
                }
            },
            "resource_availability": {
                "webfetch": {
                    "network.fetch.public": True,
                    "network.fetch.private": True,
                }
            },
        }
    )

    assert migrated["private_permissions"]["policies"] == {
        "calendar.write": "auto"
    }
    assert migrated["public_permissions"]["policies"] == {
        "calendar.read": "disabled",
        "network.fetch": "disabled",
    }
    assert migrated["web_fetch"]["public_addresses_only"] is False
    assert "resource_availability" not in migrated


def test_migration_updates_reserved_profiles_without_touching_custom_profiles() -> None:
    migration = _migration_module()
    migrated = migration._migrate_profiles_data(
        {
            "profiles": [
                {
                    "id": migration.BUILD_AGENT_PROFILE_ID,
                    "private_permissions": {
                        "policies": {"filesystem.write": "auto"}
                    },
                    "public_permissions": {
                        "policies": {"shell.write": "auto"}
                    },
                },
                {
                    "id": "custom",
                    "private_permissions": {
                        "policies": {
                            "network.fetch.public": "auto",
                            "network.fetch.private": "disabled",
                        }
                    },
                    "public_permissions": {},
                },
            ]
        },
        upgrade=True,
    )

    assert migrated["profiles"][0]["private_permissions"]["policies"] == {}
    assert migrated["profiles"][0]["public_permissions"]["policies"] == {}
    assert migrated["profiles"][1]["private_permissions"]["policies"] == {
        "network.fetch": "disabled"
    }


def test_downgrade_restores_split_fetch_configuration() -> None:
    migration = _migration_module()
    downgraded = migration._downgrade_tool_data(
        {
            "private_permissions": {"policies": {"network.fetch": "auto"}},
            "public_permissions": {"policies": {"network.fetch": "ask"}},
            "web_fetch": {"public_addresses_only": False},
        }
    )

    assert downgraded["private_permissions"]["policies"] == {
        "network.fetch.public": "auto",
        "network.fetch.private": "auto",
    }
    assert downgraded["public_permissions"]["policies"] == {
        "network.fetch.public": "ask",
        "network.fetch.private": "ask",
    }
    assert downgraded["resource_availability"]["webfetch"] == {
        "network.fetch.public": True,
        "network.fetch.private": True,
    }
