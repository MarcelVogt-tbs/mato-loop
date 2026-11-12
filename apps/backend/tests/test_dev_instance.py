"""Protect development resource ownership and explicit deletion in dev.sh."""

import subprocess
from pathlib import Path

import pytest
import yaml

SCRIPT = Path(__file__).resolve().parents[3] / "scripts/dev.sh"
FUNCTIONS = SCRIPT.read_text().split("resource_ids()", 1)[1].split('\ncd "$PROJECT_ROOT"', 1)[0]


@pytest.mark.parametrize(
    "operation,fault,expected",
    [
        ("verify_resources test", "", ""),
        ("verify_resources test", "foreign", "Foreign or unverified"),
        ("verify_resources test", "unknown", "Foreign or unverified"),
        ("cleanup --project test --all --yes", "legacy-network", "Retaining unverified network"),
        ("prepare_caches ' cache-downloads-v1 ' check", "cache", "Foreign or unverified cache"),
        ("compose run backend true", "legacy-layout", "Legacy app_data layout"),
        ("cleanup --project test --all", "", "requires --yes"),
        ("cleanup --project test --all --yes", "running", "Stop the stack"),
        ("cleanup --project test --all --yes", "foreign", "Foreign or unverified"),
        ("cleanup --project test --all --yes", "changed", "Stack changed"),
        ("cleanup --project test --dependencies --yes", "", "REMOVE volume deps"),
        ("cleanup --project test --all --yes", "", "REMOVE volume data"),
        ("compose down --volumes", "", "explicit non-interactive deletion"),
    ],
)
def test_dev_shell_guards(operation, fault, expected):
    result = subprocess.run(
        [
            "bash",
            "-c",
            "set -euo pipefail\nresource_ids()"
            + FUNCTIONS
            + r"""
PROJECT_ROOT=/worktree
MATO_LOOP_DEV_REPOSITORY=/repo/.git
MATO_LOOP_DEV_CACHE_PREFIX=cache
fault=$1

docker() {
    case "$*" in
        'compose '*'config --format json') echo '{"name":"test","volumes":{"app_data":{"name":"data"}}}' ;;
        'container ls -q --filter label=com.docker.compose.project=test -a') echo container ;;
        'container ls '*'status=running') [[ "$fault" != running ]] || echo container ;;
        'volume ls -q --filter label=com.docker.compose.project=test') printf 'deps\ndata\n' ;;
        'network ls '*) [[ "$fault" != legacy-network ]] || echo network ;;
        'network inspect '*) echo '<no value>' ;;
        'volume inspect cache-downloads-v1'|'volume inspect data') : ;;
        *inspect*matoloop.dev.layout*) [[ "$fault" == legacy-layout ]] || echo consolidated-v1 ;;
        *inspect*matoloop.dev.worktree*)
            case "$fault" in foreign) echo /foreign ;; unknown) echo '<no value>' ;; *) echo /worktree ;; esac ;;
        *inspect*matoloop.dev.repository*matoloop.dev.cache*) echo '/foreign|uv|' ;;
        *inspect*com.docker.compose.project.working_dir*) echo '<no value>' ;;
        *inspect*matoloop.dev.repository*) if [[ "$fault" == foreign ]]; then echo /foreign/.git; else echo /repo/.git; fi ;;
        *inspect*matoloop.dev.cache*) echo '<no value>' ;;
        *inspect*com.docker.compose.volume*deps) echo dev_data ;;
        *inspect*com.docker.compose.volume*data) echo db_data ;;
        'inspect '*) if [[ "$fault" == changed ]]; then echo "[{\"revision\":$BASHPID}]"; else echo '[{}]'; fi ;;
        'container rm '*|'volume rm '*) echo "REMOVE $1 $3" ;;
        *) echo "Unexpected Docker call: $*" >&2; return 77 ;;
    esac
}
eval "$2"
""",
            "test",
            fault,
            operation,
        ],
        input="",
        capture_output=True,
        text=True,
    )
    deleting = "--yes" in operation and fault in ("", "legacy-network")
    assert result.returncode == (0 if not expected or deleting else 1), result.stderr
    assert expected in result.stdout + result.stderr
    if not deleting:
        assert "REMOVE" not in result.stdout
    elif "--dependencies" in operation:
        assert "REMOVE volume data" not in result.stdout


def test_cleanup_snapshot_ignores_mount_order():
    result = subprocess.run(
        ["bash", "-c", "set -euo pipefail\nresource_ids()" + FUNCTIONS + r'''
docker() { echo '[{"Mounts":[{"Destination":"/b"},{"Destination":"/a"}]}]'; }
before=$(resource_snapshot test)
docker() { echo '[{"Mounts":[{"Destination":"/a"},{"Destination":"/b"}]}]'; }
test "$before" = "$(resource_snapshot test)"
'''], capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr


def test_frontend_generated_paths_use_initialized_private_subpaths():
    config = yaml.safe_load((SCRIPT.parents[1] / "docker-compose.dev.yaml").read_text())
    frontend = config["services"]["frontend"]
    generated = {
        mount["target"].rsplit("/", 1)[-1]: mount["volume"]["subpath"]
        for mount in frontend["volumes"]
        if isinstance(mount, dict) and mount["source"] == "dev_data"
    }
    assert generated == {
        "node_modules": "frontend/node_modules",
        ".quasar": "frontend/quasar",
        "dist": "frontend/dist",
        ".yarn": "frontend/yarn",
    }
    initializer = config["services"]["storage-init"]["command"][0]
    for subpath in generated.values():
        assert f"/dev-data/{subpath}" in initializer
    assert set(config["volumes"]) == {"db_data", "app_data", "dev_data", "download_cache"}
