import stat
from pathlib import Path
from uuid import uuid4

import pytest

from mato_loop_backend.config import RuntimeConfig
from mato_loop_backend.session_storage import (
    SESSION_DIRECTORIES,
    SessionStorage,
    SessionStorageError,
)


def test_runtime_config_derives_sessions_directory_from_data_directory(tmp_path: Path) -> None:
    config = RuntimeConfig(data_dir=tmp_path)

    assert config.sessions_dir == tmp_path / "sessions"
    assert config.projects_dir == tmp_path / "projects"


def test_session_storage_provisions_complete_private_tree_atomically(tmp_path: Path) -> None:
    storage = SessionStorage(tmp_path / "sessions")
    session_id = uuid4()

    target = storage.provision(session_id)

    assert target == tmp_path / "sessions" / str(session_id)
    assert SESSION_DIRECTORIES == (
        "files",
        "attachments",
        "generated",
        "downloads",
        "derived",
    )
    for directory in (storage.sessions_dir, storage.staging_dir, storage.trash_dir, target):
        assert directory.is_dir()
        assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    for relative_path in SESSION_DIRECTORIES:
        directory = target / relative_path
        assert directory.is_dir()
        assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    assert list(storage.staging_dir.iterdir()) == []

    assert storage.provision(session_id) == target


def test_session_storage_rejects_noncanonical_ids_and_symlinked_trees(tmp_path: Path) -> None:
    storage = SessionStorage(tmp_path / "sessions")

    with pytest.raises(SessionStorageError) as invalid:
        storage.provision("../escape")

    assert invalid.value.code == "sessionStorage.invalidSessionId"

    storage.prepare()
    session_id = uuid4()
    outside = tmp_path / "outside"
    outside.mkdir()
    storage.session_path(session_id).symlink_to(outside, target_is_directory=True)

    with pytest.raises(SessionStorageError) as symlinked:
        storage.provision(session_id)

    assert symlinked.value.code == "sessionStorage.invalidSessionTree"


def test_session_storage_can_trash_purge_and_remove_complete_trees(tmp_path: Path) -> None:
    storage = SessionStorage(tmp_path / "sessions")
    trashed_id = uuid4()
    removed_id = uuid4()
    (storage.provision(trashed_id) / "files" / "notes.txt").write_text(
        "Inhalt", encoding="utf-8"
    )
    storage.provision(removed_id)

    trashed = storage.trash_session(trashed_id)

    assert trashed is not None
    assert not storage.session_path(trashed_id).exists()
    assert (trashed / "files" / "notes.txt").read_text(encoding="utf-8") == "Inhalt"
    assert storage.purge_trash() == 1
    assert list(storage.trash_dir.iterdir()) == []

    assert storage.remove_session(removed_id) is True
    assert storage.remove_session(removed_id) is False


def test_session_storage_reconciles_missing_orphaned_and_staged_trees(tmp_path: Path) -> None:
    storage = SessionStorage(tmp_path / "sessions")
    expected_id = uuid4()
    orphaned_id = uuid4()
    storage.provision(orphaned_id)
    stale = storage.staging_dir / "abgebrochen"
    stale.mkdir()

    result = storage.reconcile({expected_id}, stale_staging_after_seconds=0)

    assert result.provisioned == (expected_id,)
    assert result.restored == ()
    assert result.trashed == (orphaned_id,)
    assert result.removed_staging_entries == 1
    assert result.removed_trash_entries == 1
    assert storage.session_path(expected_id, require_exists=True).is_dir()
    assert not storage.session_path(orphaned_id).exists()
    assert list(storage.trash_dir.iterdir()) == []


def test_reconciliation_restores_uncommitted_delete_from_trash(tmp_path: Path) -> None:
    storage = SessionStorage(tmp_path / "sessions")
    session_id = uuid4()
    stored_file = storage.provision(session_id) / "files" / "important.txt"
    stored_file.write_text("Inhalt", encoding="utf-8")
    storage.trash_session(session_id)

    result = storage.reconcile({session_id}, stale_staging_after_seconds=0)

    assert result.provisioned == ()
    assert result.restored == (session_id,)
    assert storage.session_path(session_id).joinpath("files/important.txt").read_text() == "Inhalt"
    assert list(storage.trash_dir.iterdir()) == []


def test_project_session_storage_moves_complete_tree_atomically(tmp_path: Path) -> None:
    storage = SessionStorage(tmp_path / "sessions")
    session_id = uuid4()
    first_project_id = uuid4()
    second_project_id = uuid4()
    stored_file = storage.provision(session_id) / "files" / "important.txt"
    stored_file.write_text("Inhalt", encoding="utf-8")

    target = storage.move_session(session_id, None, first_project_id)

    assert target == (
        tmp_path / "projects" / str(first_project_id) / "sessions" / str(session_id)
    )
    assert storage.session_path(session_id, require_exists=True) == target
    assert target.joinpath("files/important.txt").read_text() == "Inhalt"

    second = storage.move_session(session_id, first_project_id, second_project_id)
    standalone = storage.move_session(session_id, second_project_id, None)

    assert not second.exists()
    assert standalone == tmp_path / "sessions" / str(session_id)
    assert standalone.joinpath("files/important.txt").read_text() == "Inhalt"


def test_reconciliation_relocates_session_to_database_project(tmp_path: Path) -> None:
    storage = SessionStorage(tmp_path / "sessions")
    session_id = uuid4()
    project_id = uuid4()
    stored_file = storage.provision(session_id) / "files" / "important.txt"
    stored_file.write_text("Inhalt", encoding="utf-8")

    result = storage.reconcile({session_id: project_id}, stale_staging_after_seconds=0)

    assert result.provisioned == ()
    target = storage.session_path(
        session_id, project_id=project_id, require_exists=True
    )
    assert target.joinpath("files/important.txt").read_text() == "Inhalt"
    assert not storage.session_path(session_id, project_id=None).exists()


def test_project_storage_rejects_symlinked_project_root(tmp_path: Path) -> None:
    storage = SessionStorage(tmp_path / "sessions")
    storage.prepare()
    project_id = uuid4()
    outside = tmp_path / "outside"
    outside.mkdir()
    (storage.projects_dir / str(project_id)).symlink_to(outside, target_is_directory=True)

    with pytest.raises(SessionStorageError) as symlinked:
        storage.provision(uuid4(), project_id)

    assert symlinked.value.code == "sessionStorage.invalidDirectory"
