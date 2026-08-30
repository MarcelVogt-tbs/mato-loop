from mato_loop_backend.config import _environment_files


def test_environment_files(monkeypatch):
    monkeypatch.setenv("MATO_LOOP_ENVIRONMENT", "dev")
    assert [path.name for path in _environment_files()] == [".env.dev", ".env.dev.override"]

    monkeypatch.setenv("MATO_LOOP_ENVIRONMENT", "production")
    assert [path.name for path in _environment_files()] == [".env"]
