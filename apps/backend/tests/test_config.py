import os
import subprocess
import sys
from pathlib import Path

from mato_loop_backend.config import _environment_files


def test_environment_files(monkeypatch):
    monkeypatch.setenv("MATO_LOOP_ENVIRONMENT", "dev")
    assert [path.name for path in _environment_files()] == [".env.dev", ".env.dev.override"]

    monkeypatch.setenv("MATO_LOOP_ENVIRONMENT", "production")
    assert [path.name for path in _environment_files()] == [".env"]


def test_alembic_accepts_percent_encoded_database_password():
    backend = Path(__file__).parents[1]
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "base", "--sql"],
        cwd=backend,
        env={
            **os.environ,
            "MATO_LOOP_ENVIRONMENT": "production",
            "MATO_LOOP_DATABASE_URL": "postgresql+asyncpg://matoloop:pass%25word@db/matoloop",
        },
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
