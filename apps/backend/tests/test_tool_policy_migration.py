import importlib.util
from pathlib import Path


def _migration_module(filename="601278fe1db7_unify_tool_policy_defaults.py"):
    path = (
        Path(__file__).parents[1]
        / "alembic/versions" / filename
    )
    spec = importlib.util.spec_from_file_location("tool_policy_migration", path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def test_browser_policy_reset_reaches_profiles_connections_and_resources():
    migration = _migration_module("6b6ce77be67f_browser_permissions.py")
    assert migration._document({
        "profiles": [{"overrides": {"privatePermissions": {"policies": {"browser.read": "auto", "filesystem.read": "disabled"}}}}],
        "instances": [{"profile_permissions": {"build": {"public_permissions": {
            "policies": {"browser.write": "ask", "browser.use": "auto"},
            "resource_policies": {"page": {"browser.read": "disabled"}},
        }}}}],
        "builtin_access_overrides": {"build": {"private_permissions": {
            "integration_policies": {"connection": {"browser.write": "auto", "browser.code": "ask"}},
        }}},
    }) == {
        "profiles": [{"overrides": {"privatePermissions": {"policies": {"filesystem.read": "disabled"}}}}],
        "instances": [{"profile_permissions": {"build": {"public_permissions": {
            "policies": {},
            "resource_policies": {"page": {}},
        }}}}],
        "builtin_access_overrides": {"build": {"private_permissions": {
            "integration_policies": {"connection": {}},
        }}},
    }


def test_shell_delete_split_preserves_explicit_overrides_only():
    migration = _migration_module("fae8e3fac872_split_shell_delete.py")
    assert migration._copy_shell_delete_policy({
        "profiles": [{"privatePermissions": {"policies": {"shell.write": "disabled"}}}],
        "builtin_access_overrides": {"build": {"public_permissions": {
            "policies": {"shell.write": "ask", "shell.delete": "auto"},
        }}},
        "private_permissions": {"policies": {}},
    }) == {
        "profiles": [{"privatePermissions": {"policies": {"shell.write": "disabled", "shell.delete": "disabled"}}}],
        "builtin_access_overrides": {"build": {"public_permissions": {
            "policies": {"shell.write": "ask", "shell.delete": "auto"},
        }}},
        "private_permissions": {"policies": {}},
    }


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
