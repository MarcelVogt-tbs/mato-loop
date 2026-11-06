"""Protect foreign development data and deletion confirmation."""

import runpy
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest


@pytest.fixture
def instance():
    return SimpleNamespace(
        **runpy.run_path(str(Path(__file__).resolve().parents[3] / "scripts/dev-instance.py"))
    )


def test_foreign_checkout_is_rejected(instance, tmp_path, monkeypatch):
    runner = Mock(
        side_effect=[
            "container-id",
            '[{"Config":{"Labels":{"com.docker.compose.project.working_dir":"/another-checkout"}}}]',
        ]
    )
    monkeypatch.setitem(instance.inspect_instance.__globals__, "run", runner)
    with pytest.raises(SystemExit, match="belongs to another checkout"):
        instance.inspect_instance({"name": "shared"}, tmp_path)


def test_noninteractive_volume_deletion_is_rejected():
    script = Path(__file__).resolve().parents[3] / "scripts/dev.sh"
    functions = "compose()" + script.read_text().split("compose()", 1)[1].split("usage()", 1)[0]
    result = subprocess.run(
        [
            "bash",
            "-c",
            functions
            + """
PROJECT_ROOT=/unused
python3() { if [[ "$1" == -c ]]; then echo test-stack; fi; }
docker() { if [[ " $* " == *" config "* ]]; then echo '{}'; else exit 77; fi; }
compose down --volumes
""",
        ],
        input="",
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1 and "explicit non-interactive deletion" in result.stderr


@pytest.mark.parametrize("selection", ["referenced", "project"])
def test_foreign_volumes_are_rejected(instance, tmp_path, monkeypatch, selection):
    runner = Mock(
        side_effect=[
            "",
            f"volume-id\t{'shared' if selection == 'project' else 'other-stack'}\n",
            '[{"Labels":{"matoloop.dev.worktree":"/another-checkout"}}]',
        ]
    )
    monkeypatch.setitem(instance.inspect_instance.__globals__, "run", runner)
    volumes = {"db_data": {"name": "volume-id"}} if selection == "referenced" else {}
    with pytest.raises(SystemExit, match="volumes owned by another"):
        instance.inspect_instance({"name": "shared", "volumes": volumes}, tmp_path)
