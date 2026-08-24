import json
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


def test_session_storage_provisions_standalone_and_project_trees_atomically(
    tmp_path: Path,
) -> None:
    storage = SessionStorage(tmp_path / "sessions")
    session_id = uuid4()
    project_id = uuid4()

    target = storage.provision(session_id)
    project_session = storage.provision(uuid4(), project_id)

    assert target == tmp_path / "sessions" / str(session_id)
    assert SESSION_DIRECTORIES == ()
    for directory in (storage.sessions_dir, storage.staging_dir, storage.trash_dir, target):
        assert directory.is_dir()
        assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    for relative_path in (*SESSION_DIRECTORIES, "files", "revisions"):
        directory = target / relative_path
        assert directory.is_dir()
        assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    assert list(project_session.iterdir()) == []
    assert {
        item.name for item in storage.logical_root_path(session_id, project_id).iterdir()
    } == {"files", "revisions", "sessions"}
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


def test_cleanup_removes_legacy_attachments_and_deleted_histories(tmp_path: Path) -> None:
    storage = SessionStorage(tmp_path / "sessions")
    session_id = uuid4()
    root = storage.provision(session_id)
    (root / "attachments" / "old").mkdir(parents=True)
    (root / "attachments" / "old" / "upload.bin").write_bytes(b"old")
    history = root / "revisions" / "deleted-file"
    history.mkdir()
    (history / "index.json").write_text(
        json.dumps({"deleted": True}), encoding="utf-8"
    )

    removed = storage.cleanup_legacy_attachment_storage({session_id: None})

    assert removed == 2
    assert not (root / "attachments").exists()
    assert not history.exists()


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


def test_standalone_session_files_and_revisions_join_project(tmp_path: Path) -> None:
    storage = SessionStorage(tmp_path / "sessions")
    session_id = uuid4()
    project_id = uuid4()
    source = storage.provision(session_id)
    (source / "files" / "docs").mkdir()
    (source / "files" / "docs" / "note.txt").write_text("session", encoding="utf-8")
    (source / "revisions" / "file-id").mkdir()
    (source / "revisions" / "file-id" / "1").write_text("revision", encoding="utf-8")
    project_session = storage.provision(uuid4(), project_id)
    project_root = storage.logical_root_path(project_session.name, project_id)
    (project_root / "files" / "shared.txt").write_text("project", encoding="utf-8")

    receipt = storage.stage_session_move(session_id, None, project_id)

    assert (source / "files" / "docs" / "note.txt").read_text() == "session"
    assert (project_root / "files" / "shared.txt").read_text() == "project"
    assert (project_root / "files" / "docs" / "note.txt").read_text() == "session"
    assert (project_root / "revisions" / "file-id" / "1").read_text() == "revision"
    assert list(receipt.target.iterdir()) == []

    storage.finalize_session_move(receipt)

    assert not source.exists()


def test_standalone_project_import_conflict_leaves_both_roots_unchanged(
    tmp_path: Path,
) -> None:
    storage = SessionStorage(tmp_path / "sessions")
    session_id = uuid4()
    project_id = uuid4()
    source = storage.provision(session_id)
    (source / "files" / "same.txt").write_text("session", encoding="utf-8")
    project_root = storage.logical_root_path(
        storage.provision(uuid4(), project_id).name, project_id
    )
    target = project_root / "files" / "same.txt"
    target.write_text("project", encoding="utf-8")

    with pytest.raises(SessionStorageError) as exc_info:
        storage.stage_session_move(session_id, None, project_id)

    assert exc_info.value.code == "file.pathAlreadyExists"
    assert (source / "files" / "same.txt").read_text() == "session"
    assert target.read_text() == "project"
    assert not storage.session_path(session_id, project_id=project_id).exists()


def test_no_replace_copy_does_not_delete_existing_destination(tmp_path: Path) -> None:
    source = tmp_path / "source.txt"
    target = tmp_path / "target.txt"
    source.write_text("session", encoding="utf-8")
    target.write_text("project", encoding="utf-8")

    with pytest.raises(FileExistsError):
        SessionStorage._copy_file_no_replace(source, target)

    assert target.read_text(encoding="utf-8") == "project"


def test_standalone_project_import_rolls_back_copied_files(tmp_path: Path) -> None:
    storage = SessionStorage(tmp_path / "sessions")
    session_id = uuid4()
    project_id = uuid4()
    source = storage.provision(session_id)
    (source / "files" / "nested").mkdir()
    (source / "files" / "nested" / "note.txt").write_text("session", encoding="utf-8")

    receipt = storage.stage_session_move(session_id, None, project_id)
    storage.rollback_session_move(receipt)

    assert (source / "files" / "nested" / "note.txt").read_text() == "session"
    assert (source / "files").is_dir()
    assert not storage.session_path(session_id, project_id=project_id).exists()
    assert not (storage.logical_root_path(session_id, project_id) / "files" / "nested").exists()


def test_standalone_project_import_does_not_rollback_changed_project_copy(
    tmp_path: Path,
) -> None:
    storage = SessionStorage(tmp_path / "sessions")
    session_id = uuid4()
    project_id = uuid4()
    source = storage.provision(session_id)
    (source / "files" / "note.txt").write_text("session", encoding="utf-8")
    receipt = storage.stage_session_move(session_id, None, project_id)
    imported = storage.logical_root_path(session_id, project_id) / "files" / "note.txt"
    imported.write_text("project change", encoding="utf-8")

    with pytest.raises(SessionStorageError) as exc_info:
        storage.rollback_session_move(receipt)

    assert exc_info.value.code == "sessionStorage.moveDataChanged"
    assert imported.read_text() == "project change"
    assert (source / "files" / "note.txt").read_text() == "session"
    assert receipt.journal is not None and receipt.journal.exists()


def test_project_with_pending_session_import_cannot_be_trashed(tmp_path: Path) -> None:
    storage = SessionStorage(tmp_path / "sessions")
    session_id = uuid4()
    project_id = uuid4()
    storage.provision(session_id)
    receipt = storage.stage_session_move(session_id, None, project_id)
    storage.abandon_session_move(receipt)

    with pytest.raises(SessionStorageError) as exc_info:
        storage.trash_project(project_id)

    assert exc_info.value.code == "sessionStorage.projectMovePending"
    assert list(receipt.target.iterdir()) == []


def test_standalone_project_import_enforces_combined_quota(tmp_path: Path) -> None:
    storage = SessionStorage(tmp_path / "sessions")
    session_id = uuid4()
    project_id = uuid4()
    source = storage.provision(session_id)
    (source / "files" / "session.bin").write_bytes(b"1234")
    project_root = storage.logical_root_path(
        storage.provision(uuid4(), project_id).name, project_id
    )
    (project_root / "files" / "project.bin").write_bytes(b"5678")

    with pytest.raises(SessionStorageError) as exc_info:
        storage.stage_session_move(session_id, None, project_id, quota_bytes=7)

    assert exc_info.value.code == "file.sessionQuotaExceeded"
    assert (source / "files" / "session.bin").exists()
    assert not storage.session_path(session_id, project_id=project_id).exists()


@pytest.mark.parametrize("committed", [False, True])
def test_reconciliation_recovers_interrupted_standalone_project_import(
    tmp_path: Path, committed: bool
) -> None:
    storage = SessionStorage(tmp_path / "sessions")
    session_id = uuid4()
    project_id = uuid4()
    source = storage.provision(session_id)
    (source / "files" / "note.txt").write_text("session", encoding="utf-8")
    receipt = storage.stage_session_move(session_id, None, project_id)
    storage._release_locks(receipt.lock_descriptors)

    storage.reconcile(
        {session_id: project_id if committed else None},
        projects={project_id},
        stale_staging_after_seconds=0,
    )

    project_file = storage.logical_root_path(session_id, project_id) / "files" / "note.txt"
    if committed:
        assert not source.exists()
        assert project_file.read_text() == "session"
        assert storage.session_path(session_id, project_id=project_id).is_dir()
    else:
        assert (source / "files" / "note.txt").read_text() == "session"
        assert not project_file.exists()
        assert not storage.session_path(session_id, project_id=project_id).exists()
    assert list(storage.staging_dir.iterdir()) == []


def test_empty_session_moves_project_to_project_to_standalone(tmp_path: Path) -> None:
    storage = SessionStorage(tmp_path / "sessions")
    session_id = uuid4()
    first_project_id = uuid4()
    second_project_id = uuid4()
    storage.provision(session_id)

    target = storage.move_session(session_id, None, first_project_id)

    assert target == (
        tmp_path / "projects" / str(first_project_id) / "sessions" / str(session_id)
    )
    assert storage.session_path(session_id, require_exists=True) == target
    assert list(target.iterdir()) == []

    second = storage.move_session(session_id, first_project_id, second_project_id)
    standalone = storage.move_session(session_id, second_project_id, None)

    assert not second.exists()
    assert standalone == tmp_path / "sessions" / str(session_id)
    assert standalone.joinpath("files").is_dir()
    assert standalone.joinpath("revisions").is_dir()


def test_reconciliation_relocates_session_to_database_project(tmp_path: Path) -> None:
    storage = SessionStorage(tmp_path / "sessions")
    session_id = uuid4()
    project_id = uuid4()
    stored_file = storage.provision(session_id) / "files" / "important.txt"
    stored_file.write_text("Inhalt", encoding="utf-8")

    result = storage.reconcile({session_id: project_id}, stale_staging_after_seconds=0)

    assert result.provisioned == ()
    storage.session_path(session_id, project_id=project_id, require_exists=True)
    assert storage.logical_root_path(session_id, project_id).joinpath(
        "files/important.txt"
    ).read_text() == "Inhalt"
    assert not storage.session_path(session_id, project_id=None).exists()


def test_reconciliation_restores_project_storage_from_trash(tmp_path: Path) -> None:
    storage = SessionStorage(tmp_path / "sessions")
    project_id = uuid4()
    session_id = uuid4()
    storage.provision(session_id, project_id)
    shared = storage.logical_root_path(session_id, project_id) / "files" / "shared.txt"
    shared.write_text("shared", encoding="utf-8")
    storage.remove_session(session_id, project_id)
    trashed = storage.trash_project(project_id)

    storage.reconcile({}, projects={project_id}, stale_staging_after_seconds=0)

    assert trashed is not None and not trashed.exists()
    assert shared.read_text(encoding="utf-8") == "shared"


def test_nonempty_project_tree_can_be_trashed_and_restored(tmp_path: Path) -> None:
    storage = SessionStorage(tmp_path / "sessions")
    project_id = uuid4()
    session_id = uuid4()
    storage.provision(session_id, project_id)
    shared = storage.logical_root_path(session_id, project_id) / "files" / "shared.txt"
    shared.write_text("shared", encoding="utf-8")

    trashed = storage.trash_project(project_id)

    assert trashed is not None
    assert not shared.exists()
    storage.restore_project(project_id, trashed)
    assert shared.read_text(encoding="utf-8") == "shared"


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
