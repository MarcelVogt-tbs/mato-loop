import importlib.util
import json
from pathlib import Path
from uuid import uuid4

import pytest


def _migration_module():
    path = (
        Path(__file__).parents[1]
        / "alembic/versions/db0ed8f44f4e_move_project_files_to_sessions.py"
    )
    spec = importlib.util.spec_from_file_location("project_file_migration", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Result:
    def __init__(self, rows=()):
        self.rows = rows

    def mappings(self):
        return self

    def __iter__(self):
        return iter(self.rows)

    def scalar_one(self):
        return self.rows[0]


class _Connection:
    def __init__(self, rows, *, session_rows=(), fail_insert_at=None):
        self.rows = rows
        self.session_rows = session_rows
        self.fail_insert_at = fail_insert_at
        self.insert_count = 0
        self.statements = []

    def execute(self, statement, params=None):
        sql = str(statement)
        self.statements.append((sql, params))
        if "SELECT EXISTS" in sql:
            return _Result([bool(self.rows or self.session_rows)])
        if "FROM projects AS project" in sql:
            return _Result(self.rows)
        if "FROM chat_sessions" in sql:
            return _Result(self.session_rows)
        self.insert_count += 1
        if self.insert_count == self.fail_insert_at:
            raise RuntimeError("database write failed")
        return _Result()


def _project(tmp_path, *, files=True, revisions=True):
    project_id = uuid4()
    root = tmp_path / "projects" / str(project_id)
    (root / "files").mkdir(parents=True)
    (root / "revisions").mkdir()
    if files:
        (root / "files" / "notes.txt").write_text("legacy", encoding="utf-8")
    if revisions:
        history = root / "revisions" / str(uuid4())
        history.mkdir()
        (history / "index.json").write_text(
            json.dumps({"path": "notes.txt", "run": None}), encoding="utf-8"
        )
    return project_id, root


def test_upgrade_moves_both_trees_and_creates_import_session(tmp_path, monkeypatch) -> None:
    migration = _migration_module()
    project_id, root = _project(tmp_path)
    user_id = uuid4()
    connection = _Connection(
        [{"id": project_id, "user_id": user_id, "existing_session_id": None}]
    )
    monkeypatch.setenv("MATO_LOOP_DATA_DIR", str(tmp_path))

    migration._migrate(connection)

    target = root / "sessions" / str(project_id)
    assert (target / "files" / "notes.txt").read_text(encoding="utf-8") == "legacy"
    assert (target / "revisions").is_dir()
    assert not (root / "files").exists()
    assert not (root / "revisions").exists()
    statement, params = connection.statements[2]
    assert "'skipped', false, 'full', 'available'" in statement
    assert params == {
        "id": project_id,
        "user_id": user_id,
        "title": "Übernommene Projektdateien",
    }


def test_upgrade_resumes_after_one_tree_was_moved(tmp_path, monkeypatch) -> None:
    migration = _migration_module()
    project_id, root = _project(tmp_path)
    target = root / "sessions" / str(project_id)
    target.mkdir(parents=True)
    migration._write_import_marker(target, project_id, {"files", "revisions"})
    (root / "files").rename(target / "files")
    connection = _Connection(
        [{"id": project_id, "user_id": uuid4(), "existing_session_id": None}]
    )
    monkeypatch.setenv("MATO_LOOP_DATA_DIR", str(tmp_path))

    migration._migrate(connection)

    assert (target / "files" / "notes.txt").is_file()
    assert (target / "revisions").is_dir()
    assert len(connection.statements) == 3


def test_upgrade_ignores_empty_legacy_roots(tmp_path, monkeypatch) -> None:
    migration = _migration_module()
    project_id, root = _project(tmp_path, files=False, revisions=False)
    connection = _Connection(
        [{"id": project_id, "user_id": uuid4(), "existing_session_id": None}]
    )
    monkeypatch.setenv("MATO_LOOP_DATA_DIR", str(tmp_path))

    migration._migrate(connection)

    assert not (root / "sessions").exists()
    assert len(connection.statements) == 2


@pytest.mark.parametrize("state", ["pending", "publishing"])
def test_upgrade_rejects_pending_managed_run_state(tmp_path, monkeypatch, state) -> None:
    migration = _migration_module()
    project_id, root = _project(tmp_path)
    index = next((root / "revisions").glob("*/index.json"))
    index.write_text(
        json.dumps({"path": "notes.txt", "run": {"state": state}}),
        encoding="utf-8",
    )
    connection = _Connection(
        [{"id": project_id, "user_id": uuid4(), "existing_session_id": None}]
    )
    monkeypatch.setenv("MATO_LOOP_DATA_DIR", str(tmp_path))

    with pytest.raises(RuntimeError, match="pending managed run state"):
        migration._migrate(connection)

    assert (root / "files" / "notes.txt").is_file()
    assert len(connection.statements) == 2


def test_upgrade_rejects_symlinks_and_destination_conflicts(tmp_path) -> None:
    migration = _migration_module()
    project_id, root = _project(tmp_path)
    (root / "files" / "link").symlink_to(root / "files" / "notes.txt")
    with pytest.raises(RuntimeError, match="symlink"):
        migration._prepare_project(root, project_id)

    (root / "files" / "link").unlink()
    target_files = root / "sessions" / str(project_id) / "files"
    target_files.mkdir(parents=True)
    with pytest.raises(RuntimeError, match="conflicts"):
        migration._prepare_project(root, project_id)


def test_upgrade_rolls_back_filesystem_when_database_insert_fails(tmp_path, monkeypatch) -> None:
    migration = _migration_module()
    projects = [_project(tmp_path), _project(tmp_path)]
    existing_session_id = uuid4()
    existing_session = projects[0][1] / "sessions" / str(existing_session_id)
    existing_session.mkdir(parents=True)
    connection = _Connection(
        [
            {"id": project_id, "user_id": uuid4(), "existing_session_id": None}
            for project_id, _root in projects
        ],
        session_rows=[{"id": existing_session_id, "project_id": projects[0][0]}],
        fail_insert_at=2,
    )
    monkeypatch.setenv("MATO_LOOP_DATA_DIR", str(tmp_path))

    with pytest.raises(RuntimeError, match="database write failed"):
        migration._migrate(connection)

    for _project_id, root in projects:
        assert (root / "files" / "notes.txt").is_file()
        assert (root / "revisions").is_dir()
        assert not (root / "sessions" / str(_project_id)).exists()
    assert list(existing_session.iterdir()) == []


def test_upgrade_initializes_existing_project_session_tree(tmp_path, monkeypatch) -> None:
    migration = _migration_module()
    project_id = uuid4()
    session_id = uuid4()
    root = tmp_path / "projects" / str(project_id) / "sessions" / str(session_id)
    root.mkdir(parents=True)
    connection = _Connection(
        [{"id": project_id, "user_id": uuid4(), "existing_session_id": None}],
        session_rows=[{"id": session_id, "project_id": project_id}],
    )
    monkeypatch.setenv("MATO_LOOP_DATA_DIR", str(tmp_path))

    migration._migrate(connection)

    assert (root / "files").is_dir()
    assert (root / "revisions").is_dir()


def test_upgrade_moves_project_plan_and_revision_to_owning_session(
    tmp_path, monkeypatch
) -> None:
    migration = _migration_module()
    project_id, root = _project(tmp_path)
    session_id = uuid4()
    old_path = f"plans/{session_id}/plan.md"
    plan = root / "files" / old_path
    plan.parent.mkdir(parents=True)
    plan.write_text("plan", encoding="utf-8")
    file_id = uuid4()
    history = root / "revisions" / str(file_id)
    history.mkdir()
    (history / "1").write_text("plan", encoding="utf-8")
    (history / "index.json").write_text(
        json.dumps({"fileId": str(file_id), "path": old_path, "run": None}),
        encoding="utf-8",
    )
    connection = _Connection(
        [{"id": project_id, "user_id": uuid4(), "existing_session_id": None}],
        session_rows=[{"id": session_id, "project_id": project_id}],
    )
    monkeypatch.setenv("MATO_LOOP_DATA_DIR", str(tmp_path))

    migration._migrate(connection)

    target = root / "sessions" / str(session_id)
    assert (target / "files" / "plan.md").read_text(encoding="utf-8") == "plan"
    moved_index = json.loads(
        (target / "revisions" / str(file_id) / "index.json").read_text(encoding="utf-8")
    )
    assert moved_index["path"] == "plan.md"
    update = next(item for item in connection.statements if "UPDATE message_attachments" in item[0])
    assert update[1] == {
        "session_id": session_id,
        "source_root_id": project_id,
        "old_path": old_path,
    }


def test_upgrade_moves_standalone_plan_to_canonical_path(tmp_path, monkeypatch) -> None:
    migration = _migration_module()
    session_id = uuid4()
    root = tmp_path / "sessions" / str(session_id)
    old_path = f"plans/{session_id}/plan.md"
    plan = root / "files" / old_path
    plan.parent.mkdir(parents=True)
    plan.write_text("standalone", encoding="utf-8")
    (root / "revisions").mkdir()
    connection = _Connection(
        [], session_rows=[{"id": session_id, "project_id": None}]
    )
    monkeypatch.setenv("MATO_LOOP_DATA_DIR", str(tmp_path))

    migration._migrate(connection)

    assert (root / "files" / "plan.md").read_text(encoding="utf-8") == "standalone"
    assert not (root / "files" / "plans").exists()

    resumed = _Connection(
        [], session_rows=[{"id": session_id, "project_id": None}]
    )
    migration._migrate(resumed)
    assert any("UPDATE message_attachments" in sql for sql, _params in resumed.statements)


def test_upgrade_rolls_back_plan_when_attachment_update_fails(tmp_path, monkeypatch) -> None:
    migration = _migration_module()
    session_id = uuid4()
    root = tmp_path / "sessions" / str(session_id)
    old_path = f"plans/{session_id}/plan.md"
    plan = root / "files" / old_path
    plan.parent.mkdir(parents=True)
    plan.write_text("standalone", encoding="utf-8")
    (root / "revisions").mkdir()
    connection = _Connection(
        [],
        session_rows=[{"id": session_id, "project_id": None}],
        fail_insert_at=1,
    )
    monkeypatch.setenv("MATO_LOOP_DATA_DIR", str(tmp_path))

    with pytest.raises(RuntimeError, match="database write failed"):
        migration._migrate(connection)

    assert plan.read_text(encoding="utf-8") == "standalone"
    assert not (root / "files" / "plan.md").exists()


def test_downgrade_allows_empty_storage(tmp_path, monkeypatch) -> None:
    migration = _migration_module()
    monkeypatch.setenv("MATO_LOOP_DATA_DIR", str(tmp_path))

    migration._downgrade(_Connection([]))


def test_downgrade_rejects_database_records(tmp_path, monkeypatch) -> None:
    migration = _migration_module()
    monkeypatch.setenv("MATO_LOOP_DATA_DIR", str(tmp_path))

    with pytest.raises(RuntimeError, match="requires empty"):
        migration._downgrade(_Connection([{"id": uuid4()}]))


def test_downgrade_rejects_orphaned_storage(tmp_path, monkeypatch) -> None:
    migration = _migration_module()
    orphan = tmp_path / "sessions" / str(uuid4())
    orphan.mkdir(parents=True)
    monkeypatch.setenv("MATO_LOOP_DATA_DIR", str(tmp_path))

    with pytest.raises(RuntimeError, match="requires empty"):
        migration._downgrade(_Connection([]))
