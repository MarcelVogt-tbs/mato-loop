import hashlib
import json
from contextlib import contextmanager
from io import BytesIO
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from mato_loop_backend.config import RuntimeConfig
from mato_loop_backend.filesystem_search import RootFileSearch
from mato_loop_backend.session_storage import SessionStorage
from mato_loop_backend.workspace import (
    MAX_EDITABLE_TEXT_BYTES,
    FileRoot,
    RootFileError,
    RootFileService,
)


def make_service(tmp_path: Path) -> tuple[RootFileService, Path]:
    storage = SessionStorage(tmp_path / "sessions")
    session_id = uuid4()
    root = storage.provision(session_id)
    return RootFileService(session_id, session_storage=storage), root


def revision_preconditions(service: RootFileService, path: str) -> dict:
    revisions = service.list_revisions(path)
    current = next(item for item in revisions if item.current)
    return {
        "expected_file_id": current.file_id,
        "expected_current_revision": current.revision,
        "expected_current_sha256": current.sha256,
    }


@pytest.mark.parametrize(
    "path",
    ["", "/etc/passwd", "../secret", "files/../secret", "files\\secret", "a//b", "a/./b", "a\x00b"],
)
def test_rejects_non_relative_posix_paths(tmp_path: Path, path: str) -> None:
    service, _ = make_service(tmp_path)

    with pytest.raises(RootFileError) as exc_info:
        service.list_entries(FileRoot.SESSION, path)

    assert exc_info.value.code == "file.invalidPath"


def test_requires_host_uuid_and_rejects_every_local_workspace_operation(
    tmp_path: Path,
) -> None:
    storage = SessionStorage(tmp_path / "sessions")
    session_id = uuid4()
    storage.provision(session_id)
    with pytest.raises(TypeError):
        RootFileService(str(session_id), session_storage=storage)  # type: ignore[arg-type]

    service = RootFileService(session_id, session_storage=storage)
    operations = (
        lambda: service.root_identity(FileRoot.WORKSPACE),
        lambda: service.list_entries(FileRoot.WORKSPACE),
        lambda: service.read_bytes(FileRoot.WORKSPACE, "file.txt", limit=10),
        lambda: service.write_file(FileRoot.WORKSPACE, "file.txt", "value"),
        lambda: service.create_directory(FileRoot.WORKSPACE, "directory"),
        lambda: service.copy_path(
            FileRoot.SESSION, "source", FileRoot.WORKSPACE, "destination"
        ),
    )
    for operation in operations:
        with pytest.raises(RootFileError) as exc_info:
            operation()
        assert exc_info.value.code == "file.workspaceNotSelected"


def test_construction_and_workspace_rejection_do_not_create_data_home(
    tmp_path: Path,
) -> None:
    config = RuntimeConfig(data_dir=tmp_path)
    storage = SessionStorage(config.sessions_dir)
    session_id = uuid4()
    storage.provision(session_id)

    service = RootFileService(session_id, session_storage=storage)
    with pytest.raises(RootFileError):
        service.list_entries(FileRoot.WORKSPACE)

    assert not (tmp_path / "home").exists()


async def test_lists_pages_reads_utf8_and_searches_session_files(tmp_path: Path) -> None:
    service, root = make_service(tmp_path)
    files = root / "files"
    for name in ("c.txt", "a.txt", "b.txt"):
        (files / name).write_text(f"{name}\nAurora", encoding="utf-8")

    first = service.list_entries(FileRoot.SESSION, ".", offset=0, limit=2)
    second = service.list_entries(
        FileRoot.SESSION, ".", offset=first.next_offset or 0, limit=2
    )
    read = service.read_file(FileRoot.SESSION, "a.txt", offset=2, limit=1)
    search = RootFileSearch(service)

    assert [entry.name for entry in first.entries] == ["a.txt", "b.txt"]
    assert [entry.name for entry in second.entries] == ["c.txt"]
    assert read.content == "2: Aurora"
    assert read.sha256 == hashlib.sha256(b"a.txt\nAurora").hexdigest()
    assert (await search.glob(FileRoot.SESSION, ".", "*.txt"))["paths"] == [
        "a.txt",
        "b.txt",
        "c.txt",
    ]


def test_mutations_are_atomic_and_limited_to_session_files(tmp_path: Path) -> None:
    service, root = make_service(tmp_path)
    service.create_directory(FileRoot.SESSION, "subdirectory")
    service.write_file(FileRoot.SESSION, "subdirectory/notes.txt", "old")
    service.write_file(
        FileRoot.SESSION,
        "subdirectory/notes.txt",
        "new",
        mode="replace",
        expected_sha256=hashlib.sha256(b"old").hexdigest(),
    )
    target = root / "files" / "subdirectory" / "notes.txt"

    assert target.read_text(encoding="utf-8") == "new"
    assert not list(target.parent.glob("*.tmp"))
    assert not (root / "subdirectory").exists()
    with pytest.raises(RootFileError) as exc_info:
        service.delete_path(FileRoot.SESSION, ".", recursive=True)
    assert exc_info.value.code == "file.rootMutationForbidden"


def test_agent_upsert_and_exact_edit_preserve_line_endings(tmp_path: Path) -> None:
    service, root = make_service(tmp_path)

    service.upsert_text_file(FileRoot.SESSION, "notes.txt", "one\r\ntwo\r\n")
    service.upsert_text_file(FileRoot.SESSION, "notes.txt", "one\r\ntwo\r\ntwo\r\n")
    with pytest.raises(RootFileError) as exc_info:
        service.edit_text_file(FileRoot.SESSION, "notes.txt", "two\n", "three\n")
    assert exc_info.value.code == "file.editTextNotUnique"

    service.edit_text_file(
        FileRoot.SESSION,
        "notes.txt",
        "two\n",
        "three\n",
        replace_all=True,
    )

    assert (root / "files" / "notes.txt").read_bytes() == b"one\r\nthree\r\nthree\r\n"


def test_file_deletion_removes_complete_history(tmp_path: Path) -> None:
    service, root = make_service(tmp_path)
    service.create_file(
        FileRoot.SESSION, "plan.md", BytesIO(b"# Plan\n"), max_bytes=100
    )
    service.replace_text_file(
        FileRoot.SESSION,
        "plan.md",
        "# Updated\n",
        hashlib.sha256(b"# Plan\n").hexdigest(),
    )

    original_revisions = service.list_revisions("plan.md")
    file_id = original_revisions[0].file_id
    revisions = service.list_revisions("plan.md")
    assert [item.revision for item in revisions] == [2, 1]
    assert revisions[0].current is True
    assert revisions[1].current is False
    with service.open_revision("plan.md", 2) as updated:
        assert updated.read() == b"# Updated\n"
    assert (root / "files" / "plan.md").read_bytes() == b"# Updated\n"

    service.delete_path(FileRoot.SESSION, "plan.md", recursive=False)

    assert service.list_revisions("plan.md") == ()
    with pytest.raises(RootFileError) as unavailable:
        service.open_revision_by_id(file_id, 2)
    assert unavailable.value.code == "file.revisionNotFound"
    assert not (root / "revisions" / str(file_id)).exists()


def test_restore_truncates_later_revisions_and_derivatives_without_reusing_numbers(
    tmp_path: Path,
) -> None:
    service, root = make_service(tmp_path)
    service.write_file(FileRoot.SESSION, "notes.txt", "one")
    service.replace_text_file(
        FileRoot.SESSION, "notes.txt", "two", hashlib.sha256(b"one").hexdigest()
    )
    service.replace_text_file(
        FileRoot.SESSION, "notes.txt", "three", hashlib.sha256(b"two").hexdigest()
    )
    file_id = service.list_revisions("notes.txt")[0].file_id
    service.write_revision_derivatives(file_id, 2, {"two.bin": b"two"})
    service.write_revision_derivatives(file_id, 3, {"three.bin": b"three"})
    history = root / "revisions" / str(file_id)
    index = json.loads((history / "index.json").read_text(encoding="utf-8"))
    del index["maxRevision"]  # Old indexes infer their high-water mark.
    (history / "index.json").write_text(json.dumps(index), encoding="utf-8")

    restored = service.restore_revision(
        "notes.txt", 1, **revision_preconditions(service, "notes.txt")
    )

    assert restored.current_revision == 1
    assert restored.sha256 == hashlib.sha256(b"one").hexdigest()
    assert [item.revision for item in service.list_revisions("notes.txt")] == [1]
    assert (root / "files" / "notes.txt").read_text(encoding="utf-8") == "one"
    assert not (history / "2").exists()
    assert not (history / "3").exists()
    assert not (history / "derived" / "2").exists()
    assert not (history / "derived" / "3").exists()
    with pytest.raises(RootFileError):
        service.open_revision_by_id(file_id, 3)
    assert json.loads((history / "index.json").read_text(encoding="utf-8"))[
        "maxRevision"
    ] == 3

    service.replace_text_file(
        FileRoot.SESSION, "notes.txt", "four", hashlib.sha256(b"one").hexdigest()
    )
    assert [item.revision for item in service.list_revisions("notes.txt")] == [4, 1]


def test_project_sessions_share_files_and_revision_identity(tmp_path: Path) -> None:
    storage = SessionStorage(tmp_path / "sessions")
    project_id = uuid4()
    first_id = uuid4()
    second_id = uuid4()
    storage.provision(first_id, project_id)
    storage.provision(second_id, project_id)
    first = RootFileService(first_id, session_storage=storage, project_id=project_id)
    second = RootFileService(second_id, session_storage=storage, project_id=project_id)

    first.write_file(FileRoot.SESSION, "shared.txt", "shared")

    assert second.root_identity(FileRoot.SESSION) == str(project_id)
    assert second.read_file(FileRoot.SESSION, "shared.txt").content == "1: shared"
    assert second.list_revisions("shared.txt")[0].current is True


def test_upload_replace_rename_copy_and_move_preserve_session_contract(
    tmp_path: Path,
) -> None:
    service, root = make_service(tmp_path)
    created = service.create_file(
        FileRoot.SESSION,
        "source.txt",
        BytesIO(b"old"),
        max_bytes=10,
    )
    revision = service.replace_text_file(
        FileRoot.SESSION,
        created.path,
        "new",
        hashlib.sha256(b"old").hexdigest(),
    )
    renamed = service.rename_path(FileRoot.SESSION, created.path, "renamed.txt")
    copied = service.copy_path(
        FileRoot.SESSION,
        renamed.path,
        FileRoot.SESSION,
        "copied.txt",
    )
    moved = service.move_path(
        FileRoot.SESSION,
        renamed.path,
        FileRoot.SESSION,
        "moved.txt",
    )

    assert revision == hashlib.sha256(b"new").hexdigest()
    assert copied == "copied.txt"
    assert moved == "moved.txt"
    assert not (root / "files" / renamed.path).exists()
    assert (root / "files" / copied).read_text(encoding="utf-8") == "new"
    assert (root / "files" / moved).read_text(encoding="utf-8") == "new"


def test_never_follows_symlinks_and_deletes_only_the_link(tmp_path: Path) -> None:
    service, root = make_service(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("secret", encoding="utf-8")
    link = root / "files" / "link"
    link.symlink_to(outside, target_is_directory=True)

    listed = service.list_entries(FileRoot.SESSION, ".")
    assert [(entry.name, entry.kind) for entry in listed.entries] == [
        ("link", "symlink")
    ]
    with pytest.raises(RootFileError) as exc_info:
        service.read_file(FileRoot.SESSION, "link/secret.txt")
    assert exc_info.value.code == "file.symlinkForbidden"

    service.delete_path(FileRoot.SESSION, "link", recursive=False)
    assert outside.is_dir()
    assert (outside / "secret.txt").is_file()


def test_revision_storage_rejects_symlinks(tmp_path: Path) -> None:
    service, root = make_service(tmp_path)
    service.write_file(FileRoot.SESSION, "notes.txt", "safe")
    history = next((root / "revisions").iterdir())
    (history / "1").unlink()
    (history / "1").symlink_to(tmp_path / "outside")
    (tmp_path / "outside").write_text("secret", encoding="utf-8")

    with pytest.raises(RootFileError) as exc_info:
        service.list_revisions("notes.txt")

    assert exc_info.value.code == "file.revisionInvalid"


def test_revision_limit_and_quota_fail_without_partial_publication(tmp_path: Path) -> None:
    service, root = make_service(tmp_path)
    target = root / "files" / "notes.txt"
    target.write_bytes(b"old")

    with pytest.raises(RootFileError) as stale:
        service.replace_text_file(
            FileRoot.SESSION,
            "notes.txt",
            "new",
            hashlib.sha256(b"stale").hexdigest(),
        )
    assert stale.value.code == "file.changed"
    with pytest.raises(RootFileError) as too_large:
        service.replace_text_file(
            FileRoot.SESSION,
            "notes.txt",
            "x" * (MAX_EDITABLE_TEXT_BYTES + 1),
            hashlib.sha256(b"old").hexdigest(),
        )
    assert too_large.value.code == "file.tooLarge"

    service.SESSION_QUOTA_BYTES = service.session_usage()
    with pytest.raises(RootFileError) as quota:
        service.write_file(FileRoot.SESSION, "new.txt", "x")
    assert quota.value.code == "file.sessionQuotaExceeded"
    assert target.read_bytes() == b"old"
    assert not (root / "files" / "new.txt").exists()
    assert not list((root / "files").glob(".*.tmp"))


def test_session_usage_holds_session_operation_lock(tmp_path: Path, monkeypatch) -> None:
    service, _ = make_service(tmp_path)
    locked = False

    @contextmanager
    def operation_lock(*roots):
        nonlocal locked
        assert roots == (FileRoot.SESSION,)
        locked = True
        try:
            yield
        finally:
            locked = False

    def usage() -> int:
        assert locked
        return 17

    monkeypatch.setattr(service, "_session_operation_lock", operation_lock)
    monkeypatch.setattr(service, "_session_usage", usage)

    assert service.session_usage() == 17
    assert locked is False


def test_missing_session_root_is_rejected(tmp_path: Path) -> None:
    service = RootFileService(
        UUID("00000000-0000-0000-0000-000000000001"),
        session_storage=SessionStorage(tmp_path / "sessions"),
    )

    with pytest.raises(RootFileError) as exc_info:
        service.list_entries(FileRoot.SESSION)

    assert exc_info.value.code == "file.sessionNotProvisioned"
