import hashlib
import json
import os
import shutil
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
    assert await search.glob(FileRoot.SESSION, ".", "**/*.md") == {
        "paths": [], "truncated": False,
    }


async def test_glob_preserves_execution_failures(tmp_path: Path, monkeypatch) -> None:
    service, _ = make_service(tmp_path)
    search = RootFileSearch(service)

    async def failed_run(*args):
        return 2, False

    monkeypatch.setattr(search, "_run", failed_run)
    with pytest.raises(RootFileError) as exc_info:
        await search.glob(FileRoot.SESSION, ".", "*.txt")
    assert exc_info.value.code == "tool.executionFailed"


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


def test_activate_revision_preserves_history_derivatives_and_high_water_mark(
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

    activated = service.activate_revision(
        "notes.txt", 1, **revision_preconditions(service, "notes.txt")
    )

    assert activated.current_revision == 1
    assert activated.sha256 == hashlib.sha256(b"one").hexdigest()
    assert [item.revision for item in service.list_revisions("notes.txt")] == [3, 2, 1]
    assert (root / "files" / "notes.txt").read_text(encoding="utf-8") == "one"
    assert (history / "2").exists()
    assert (history / "3").exists()
    assert (history / "derived" / "2").exists()
    assert (history / "derived" / "3").exists()
    assert json.loads((history / "index.json").read_text(encoding="utf-8"))[
        "maxRevision"
    ] == 3

    service.activate_revision(
        "notes.txt", 3, **revision_preconditions(service, "notes.txt")
    )
    assert (root / "files" / "notes.txt").read_text(encoding="utf-8") == "three"

    service.replace_text_file(
        FileRoot.SESSION, "notes.txt", "four", hashlib.sha256(b"three").hexdigest()
    )
    assert [item.revision for item in service.list_revisions("notes.txt")] == [4, 3, 2, 1]


def test_discard_output_revision_restores_the_previously_active_revision(
    tmp_path: Path,
) -> None:
    service, _root = make_service(tmp_path)
    service.write_file(FileRoot.SESSION, "notes.txt", "one")
    service.upsert_text_file(FileRoot.SESSION, "notes.txt", "two")
    service.upsert_text_file(FileRoot.SESSION, "notes.txt", "three")
    service.activate_revision(
        "notes.txt", 1, **revision_preconditions(service, "notes.txt")
    )

    revision = service.append_output_revision(
        service.working_revision("notes.txt").file_id, b"temporary"
    )
    service.discard_output_revision(revision.file_id, revision.revision)

    assert service.read_bytes(FileRoot.SESSION, "notes.txt", limit=100) == b"one"
    assert [
        (item.revision, item.current) for item in service.list_revisions("notes.txt")
    ] == [(3, False), (2, False), (1, True)]


def test_revision_history_rejects_invalid_previous_revision(tmp_path: Path) -> None:
    service, root = make_service(tmp_path)
    service.write_file(FileRoot.SESSION, "notes.txt", "one")
    revision = service.append_output_revision(
        service.working_revision("notes.txt").file_id, b"temporary"
    )
    index_path = root / "revisions" / str(revision.file_id) / "index.json"
    index = json.loads(index_path.read_text(encoding="utf-8"))
    index["revisions"][-1]["previousRevision"] = revision.revision
    index_path.write_text(json.dumps(index), encoding="utf-8")

    with pytest.raises(RootFileError) as invalid:
        service.list_revisions("notes.txt")
    assert invalid.value.code == "file.revisionInvalid"


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


def run_service(service: RootFileService, run_id: UUID) -> RootFileService:
    return RootFileService(
        service.session_id, project_id=service.project_id, run_id=run_id,
        session_storage=service.session_storage,
    )


async def test_run_coalesces_text_binary_and_live_search_across_restart(tmp_path: Path) -> None:
    published, root = make_service(tmp_path)
    published.write_file(FileRoot.SESSION, "old.txt", "original")
    original = published.list_revisions("old.txt")[0]
    run_id = uuid4()
    working = run_service(published, run_id)
    working.upsert_text_file(FileRoot.SESSION, "old.txt", "first")
    working.upsert_text_file(FileRoot.SESSION, "new.txt", "draft")
    working.edit_text_file(FileRoot.SESSION, "new.txt", "draft", "final")
    binary = working.create_output_file("image.bin", b"first")
    working.append_output_revision(binary.file_id, b"final binary")
    assert binary.revision is None
    assert published.list_revisions("new.txt") == ()
    assert published.list_revisions("old.txt") == (original,)
    assert (root / "revisions" / str(original.file_id) / "1").read_bytes() == b"original"
    assert not (root / "revisions" / str(original.file_id) / "2").exists()
    assert (root / "files" / "old.txt").read_bytes() == b"first"
    assert published.working_revision("old.txt").revision is None
    assert published.read_bytes(FileRoot.SESSION, "new.txt", limit=100) == b"final"
    assert not (root / "revisions" / ".runs").exists()

    working = run_service(published, run_id)
    working.upsert_text_file(FileRoot.SESSION, "old.txt", "last")
    assert working.pending_run_ids() == (run_id,)
    assert working.read_file(FileRoot.SESSION, "new.txt").content == "1: final"
    assert working.resolve(FileRoot.SESSION, "old.txt").read_bytes() == b"last"
    assert {entry.path for entry in working.list_entries(FileRoot.SESSION).entries} == {
        "new.txt", "old.txt", binary.path,
    }
    assert (await RootFileSearch(working).glob(FileRoot.SESSION, ".", "*.txt"))["paths"] == [
        "new.txt", "old.txt",
    ]
    assert (await RootFileSearch(working).grep(FileRoot.SESSION, ".", "final", "*.txt"))["matches"] == [
        {"path": "new.txt", "line": 1, "text": "final"},
    ]
    with working.open_working_file(binary.file_id) as handle:
        assert handle.read() == b"final binary"
    finalized = published.finalize_run(run_id)
    assert len(finalized) == 3
    assert published.finalize_run(run_id) == finalized
    assert published.pending_run_ids() == ()
    assert [item.revision for item in published.list_revisions("old.txt")] == [2, 1]
    assert [item.revision for item in published.list_revisions("new.txt")] == [1]
    assert published.read_bytes(FileRoot.SESSION, binary.path, limit=100) == b"final binary"
    with working.open_working_file(binary.file_id) as handle:
        assert handle.read() == b"final binary"
    with pytest.raises(RootFileError):
        working.upsert_text_file(FileRoot.SESSION, "old.txt", "too late")


@pytest.mark.parametrize("other_run", [False, True])
def test_busy_file_and_subtree_reject_competing_mutations(tmp_path: Path, other_run: bool) -> None:
    published, _root = make_service(tmp_path)
    published.create_directory(FileRoot.SESSION, "dir")
    published.write_file(FileRoot.SESSION, "dir/file.txt", "base")
    published.write_file(FileRoot.SESSION, "source.txt", "copy source")
    preconditions = revision_preconditions(published, "dir/file.txt")
    working = run_service(published, uuid4())
    working.upsert_text_file(FileRoot.SESSION, "dir/file.txt", "live")
    other = run_service(published, uuid4()) if other_run else published
    for operation in (
        lambda: other.upsert_text_file(FileRoot.SESSION, "dir/file.txt", "outside"),
        lambda: other.create_file(FileRoot.SESSION, "dir/file.txt", BytesIO(b"outside"), max_bytes=100),
        lambda: other.append_output_revision(preconditions["expected_file_id"], b"outside"),
        lambda: other.rename_path(FileRoot.SESSION, "dir", "moved"),
        lambda: other.delete_path(FileRoot.SESSION, "dir", recursive=True),
        lambda: other.activate_revision("dir/file.txt", 1, **preconditions),
        lambda: other.copy_path(FileRoot.SESSION, "source.txt", FileRoot.SESSION, "dir/file.txt"),
        lambda: other.move_path(FileRoot.SESSION, "source.txt", FileRoot.SESSION, "dir"),
    ):
        with pytest.raises(RootFileError) as busy:
            operation()
        assert busy.value.code == "file.changed"
    assert other.read_bytes(FileRoot.SESSION, "dir/file.txt", limit=100) == b"live"
    working.upsert_text_file(FileRoot.SESSION, "dir/file.txt", "final")
    published.finalize_run(working.run_id)
    other.upsert_text_file(FileRoot.SESSION, "dir/file.txt", "available")


def test_own_run_delete_purges_ownership_and_never_revives_paths(tmp_path: Path) -> None:
    published, _root = make_service(tmp_path)
    published.create_directory(FileRoot.SESSION, "dir")
    published.write_file(FileRoot.SESSION, "dir/old.txt", "old")
    first = run_service(published, uuid4())
    first.upsert_text_file(FileRoot.SESSION, "dir/old.txt", "first")
    first.write_file(FileRoot.SESSION, "dir/new.txt", "new")
    first.write_file(FileRoot.SESSION, "draft.txt", "unpublished")
    draft = first.working_revision("draft.txt")
    first.delete_path(FileRoot.SESSION, "draft.txt", recursive=False)
    first.delete_path(FileRoot.SESSION, "dir", recursive=True)
    assert published.finalize_run(first.run_id) == ()
    assert published.pending_run_ids() == ()
    assert published.list_entries(FileRoot.SESSION).entries == ()
    with pytest.raises(RootFileError):
        first.open_working_file(draft.file_id)


@pytest.mark.parametrize("failure", ["intent", "blob", "index"])
@pytest.mark.parametrize("existing", [False, True])
def test_run_publication_recovers_each_durable_boundary(tmp_path: Path, monkeypatch, failure: str, existing: bool) -> None:
    published, root = make_service(tmp_path)
    if existing:
        published.write_file(FileRoot.SESSION, "file.txt", "old")
    run_id = uuid4()
    working = run_service(published, run_id)
    working.upsert_text_file(FileRoot.SESSION, "file.txt", "final")
    draft = working.working_revision("file.txt")
    replace = published._replace_private_file

    def replace_file(path, content):
        replace(path, content)
        state = json.loads(content)["run"]["state"] if path.name == "index.json" else None
        if (failure == "intent" and state == "publishing") or (failure == "index" and state == "final") or (failure == "blob" and path.name.isdigit()):
            raise RuntimeError("interrupted")

    monkeypatch.setattr(published, "_replace_private_file", replace_file)
    with pytest.raises(RuntimeError, match="interrupted"):
        published.finalize_run(run_id)
    restarted = RootFileService(published.session_id, session_storage=published.session_storage)
    finalized = restarted.finalize_run(run_id)
    assert [(item.file_id, item.revision) for item in finalized] == [(draft.file_id, 2 if existing else 1)]
    assert restarted.finalize_run(run_id) == finalized
    assert restarted.read_bytes(FileRoot.SESSION, "file.txt", limit=100) == b"final"
    assert [item.revision for item in restarted.list_revisions("file.txt")] == ([2, 1] if existing else [1])
    assert {path.name for path in (root / "revisions" / str(draft.file_id)).iterdir()} == ({"index.json", "1", "2"} if existing else {"index.json", "1"})


def test_run_noop_and_concurrent_create(tmp_path: Path) -> None:
    published, _root = make_service(tmp_path)
    published.write_file(FileRoot.SESSION, "file.txt", "same")
    first = run_service(published, uuid4())
    second = run_service(published, uuid4())
    first.upsert_text_file(FileRoot.SESSION, "file.txt", "different")
    first.upsert_text_file(FileRoot.SESSION, "file.txt", "same")
    first.write_file(FileRoot.SESSION, "new.txt", "first")
    with pytest.raises(RootFileError) as busy:
        second.write_file(FileRoot.SESSION, "new.txt", "second")
    assert busy.value.code == "file.changed"
    published.finalize_run(first.run_id)
    assert len(published.list_revisions("file.txt")) == 1
    assert published.finalize_run(second.run_id) == ()
    assert published.read_bytes(FileRoot.SESSION, "new.txt", limit=100) == b"first"


def test_first_run_edit_preserves_unindexed_baseline_and_reserves_quota(tmp_path: Path) -> None:
    public, root = make_service(tmp_path)
    target = root / "files" / "manual.txt"
    target.write_bytes(b"original" * 1000)
    working = run_service(public, uuid4())
    working.SESSION_QUOTA_BYTES = 17000  # Fits live + final, but not the baseline too.
    with pytest.raises(RootFileError) as quota:
        working.upsert_text_file(FileRoot.SESSION, "manual.txt", "edited" * 1000)
    assert quota.value.code == "file.sessionQuotaExceeded"
    assert target.read_bytes() == b"original" * 1000
    assert list((root / "revisions").iterdir()) == []
    working.SESSION_QUOTA_BYTES = RootFileService.SESSION_QUOTA_BYTES
    working.upsert_text_file(FileRoot.SESSION, "manual.txt", "edited" * 1000)
    original = public.list_revisions("manual.txt")[0]
    assert original.revision == 1
    with public.open_revision("manual.txt", 1) as handle:
        assert handle.read() == b"original" * 1000
    working.upsert_text_file(FileRoot.SESSION, "manual.txt", "final")
    assert public.finalize_run(working.run_id)[0].revision == 2
    public.activate_revision("manual.txt", 1, **revision_preconditions(public, "manual.txt"))
    assert target.read_bytes() == b"original" * 1000


def test_noop_run_receipt_survives_later_runs_and_revision_activation(tmp_path: Path) -> None:
    public, root = make_service(tmp_path)
    public.write_file(FileRoot.SESSION, "color.txt", "red")
    first, second = run_service(public, uuid4()), run_service(public, uuid4())
    first.upsert_text_file(FileRoot.SESSION, "color.txt", "blue")
    first.upsert_text_file(FileRoot.SESSION, "color.txt", "red")
    original = public.finalize_run(first.run_id)[0]
    second.upsert_text_file(FileRoot.SESSION, "color.txt", "green")
    public.finalize_run(second.run_id)
    restarted = run_service(public, uuid4())
    retried = restarted.finalize_run(first.run_id)
    assert len(retried) == 1 and retried[0] == public.file_revision(original.file_id, 1)
    assert retried[0].sha256 == original.sha256
    history = root / "revisions" / str(original.file_id)
    assert {path.name for path in history.iterdir()} == {"index.json", "1", "2"}
    public.activate_revision("color.txt", 1, **revision_preconditions(public, "color.txt"))
    assert restarted.finalize_run(first.run_id) == (original,)
    assert restarted.finalize_run(second.run_id)[0].revision == 2
    receipts = json.loads((history / "index.json").read_bytes())["runReceipts"]
    assert receipts == {
        f"{public.session_id}/{first.run_id}": 1,
        f"{public.session_id}/{second.run_id}": 2,
    }
    public.delete_path(FileRoot.SESSION, "color.txt", recursive=False)
    assert restarted.finalize_run(first.run_id) == ()
    assert not history.exists()


@pytest.mark.parametrize("existing", [False, True])
@pytest.mark.parametrize("replaced", [False, True])
def test_interrupted_live_replacement_recovers_only_surviving_bytes(tmp_path: Path, monkeypatch, existing: bool, replaced: bool) -> None:
    published, root = make_service(tmp_path)
    if existing:
        published.write_file(FileRoot.SESSION, "file.txt", "old")
    working = run_service(published, uuid4())
    replace = os.replace
    link = os.link

    def interrupted_replace(source, target, **kwargs):
        if target == "file.txt":
            if replaced:
                replace(source, target, **kwargs)
            raise RuntimeError("interrupted")
        return replace(source, target, **kwargs)

    def interrupted_link(source, target, **kwargs):
        if replaced:
            link(source, target, **kwargs)
        raise RuntimeError("interrupted")

    with monkeypatch.context() as patch:
        patch.setattr(os, "replace", interrupted_replace)
        patch.setattr(os, "link", interrupted_link)
        with pytest.raises(RuntimeError):
            working.upsert_text_file(FileRoot.SESSION, "file.txt", "new")
    assert published.pending_run_ids() == (working.run_id,)
    result = published.finalize_run(working.run_id)
    assert published.finalize_run(working.run_id) == result
    assert published.pending_run_ids() == ()
    target = root / "files" / "file.txt"
    if not existing and not replaced:
        assert not target.exists() and not list((root / "revisions").iterdir())
    else:
        assert target.read_bytes() == (b"new" if replaced else b"old")
        assert len(published.list_revisions("file.txt")) == (2 if existing and replaced else 1)


def test_quota_reserves_final_snapshot_against_other_writers(tmp_path: Path) -> None:
    published, _root = make_service(tmp_path)
    working = run_service(published, uuid4())
    working.write_file(FileRoot.SESSION, "file.txt", "x" * 2000)
    reserve = working._history_for_path("file.txt")[1]["run"]["reserveBytes"]
    published.SESSION_QUOTA_BYTES = published.session_usage() + reserve
    with pytest.raises(RootFileError) as quota:
        published.write_file(FileRoot.SESSION, "other.txt", "x")
    assert quota.value.code == "file.sessionQuotaExceeded"
    assert published.finalize_run(working.run_id)[0].revision == 1
    assert published.session_usage() <= published.SESSION_QUOTA_BYTES


def test_manual_host_writes_remain_live_and_are_not_overwritten_at_finalization(tmp_path: Path) -> None:
    published, root = make_service(tmp_path)
    published.write_file(FileRoot.SESSION, "file.txt", "published")
    working = run_service(published, uuid4())
    working.upsert_text_file(FileRoot.SESSION, "file.txt", "run")
    target = root / "files" / "file.txt"
    target.write_bytes(b"manual")
    assert working.read_bytes(FileRoot.SESSION, "file.txt", limit=100) == b"manual"
    assert published.finalize_run(working.run_id)[0].sha256 == hashlib.sha256(b"manual").hexdigest()
    assert target.read_bytes() == b"manual"
    with published.open_revision("file.txt", 1) as handle:
        assert handle.read() == b"published"


def test_run_quota_and_failed_index_write_keep_previous_state(tmp_path: Path, monkeypatch) -> None:
    published, root = make_service(tmp_path)
    working = run_service(published, uuid4())
    working.write_file(FileRoot.SESSION, "file.txt", "retained")
    draft = working.working_revision("file.txt")
    working.SESSION_QUOTA_BYTES = working.session_usage()
    with pytest.raises(RootFileError) as quota:
        working.replace_text_file(FileRoot.SESSION, "file.txt", "x" * 1000, draft.sha256)
    assert quota.value.code == "file.sessionQuotaExceeded"
    working.SESSION_QUOTA_BYTES = RootFileService.SESSION_QUOTA_BYTES

    def interrupted(path, data):
        path.write_bytes(data[:20])
        raise RuntimeError("interrupted")

    monkeypatch.setattr(working, "_write_private_file", interrupted)
    with pytest.raises(RuntimeError):
        working.replace_text_file(FileRoot.SESSION, "file.txt", "lost", draft.sha256)
    with run_service(published, working.run_id).open_working_file(draft.file_id) as handle:
        assert handle.read() == b"retained"
    assert published.list_revisions("file.txt") == ()
    assert not list((root / "revisions").glob("*/1"))


def test_project_runs_guard_shared_file_identity(tmp_path: Path) -> None:
    storage = SessionStorage(tmp_path / "sessions")
    project_id, first_id, second_id = uuid4(), uuid4(), uuid4()
    storage.provision(first_id, project_id)
    storage.provision(second_id, project_id)
    first = RootFileService(first_id, session_storage=storage, project_id=project_id, run_id=uuid4())
    second = RootFileService(second_id, session_storage=storage, project_id=project_id, run_id=uuid4())
    first.write_file(FileRoot.SESSION, "shared.txt", "first")
    with pytest.raises(RootFileError):
        second.write_file(FileRoot.SESSION, "shared.txt", "second")
    assert second.finalize_run(first.run_id) == ()
    assert second.pending_run_ids() == ()
    first.finalize_run(first.run_id)
    assert second.finalize_run(second.run_id) == ()
    assert second.read_bytes(FileRoot.SESSION, "shared.txt", limit=100) == b"first"
    assert first.read_bytes(FileRoot.SESSION, "shared.txt", limit=100) == b"first"


def test_run_storage_rejects_symlinked_private_root(tmp_path: Path) -> None:
    published, root = make_service(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "revisions").rmdir()
    (root / "revisions").symlink_to(outside, target_is_directory=True)
    from mato_loop_backend.session_storage import SessionStorageError

    with pytest.raises((RootFileError, SessionStorageError)):
        run_service(published, uuid4()).write_file(FileRoot.SESSION, "file.txt", "unsafe")
    assert list(outside.iterdir()) == []


@pytest.mark.parametrize("existing", [False, True])
def test_run_rename_move_and_directory_copy_keep_one_content_revision(tmp_path: Path, existing: bool) -> None:
    published, root = make_service(tmp_path)
    published.create_directory(FileRoot.SESSION, "source")
    published.create_directory(FileRoot.SESSION, "source/nested")
    published.create_directory(FileRoot.SESSION, "target")
    if existing:
        published.write_file(FileRoot.SESSION, "source/nested/file.txt", "base")
    working = run_service(published, uuid4())
    working.upsert_text_file(FileRoot.SESSION, "source/nested/file.txt", "draft")
    file_id = working.working_revision("source/nested/file.txt").file_id
    renamed = working.rename_path(FileRoot.SESSION, "source/nested/file.txt", "renamed.txt")
    assert renamed.path == "source/nested/renamed.txt"
    working.move_path(FileRoot.SESSION, "source", FileRoot.SESSION, "target/moved")
    working.copy_path(FileRoot.SESSION, "target/moved", FileRoot.SESSION, "copy")
    restarted = run_service(published, working.run_id)
    assert restarted.working_revision("target/moved/nested/renamed.txt").file_id == file_id
    assert restarted.working_revision("copy/nested/renamed.txt").file_id != file_id
    restarted.upsert_text_file(FileRoot.SESSION, "target/moved/nested/renamed.txt", "final")
    assert published.list_revisions("copy/nested/renamed.txt") == ()
    assert not (root / "files" / "source").exists()
    finalized = published.finalize_run(working.run_id)
    assert len(finalized) == 2
    assert published.finalize_run(working.run_id) == finalized
    assert len(published.list_revisions("target/moved/nested/renamed.txt")) == (2 if existing else 1)
    assert published.read_bytes(FileRoot.SESSION, "target/moved/nested/renamed.txt", limit=100) == b"final"
    assert published.read_bytes(FileRoot.SESSION, "copy/nested/renamed.txt", limit=100) == b"draft"


@pytest.mark.parametrize("selected", [1, 2])
def test_activation_waits_for_finalization_including_own_run(tmp_path: Path, selected: int) -> None:
    published, _root = make_service(tmp_path)
    published.write_file(FileRoot.SESSION, "file.txt", "one")
    published.upsert_text_file(FileRoot.SESSION, "file.txt", "two")
    first = run_service(published, uuid4())
    first.upsert_text_file(FileRoot.SESSION, "file.txt", "first")
    draft = first.working_revision("file.txt")
    for service in (published, first):
        with pytest.raises(RootFileError) as busy:
            service.activate_revision("file.txt", selected, **revision_preconditions(published, "file.txt"))
        assert busy.value.code == "file.changed"
    with first.open_working_file(draft.file_id) as handle:
        assert handle.read() == b"first"
    published.finalize_run(first.run_id)
    published.activate_revision("file.txt", selected, **revision_preconditions(published, "file.txt"))
    assert published.finalize_run(first.run_id)[0].revision == 3
    assert published.read_bytes(FileRoot.SESSION, "file.txt", limit=100) == (b"one" if selected == 1 else b"two")


def test_failed_directory_copy_rolls_back_only_its_new_files(tmp_path: Path, monkeypatch) -> None:
    published, root = make_service(tmp_path)
    published.create_directory(FileRoot.SESSION, "source")
    working = run_service(published, uuid4())
    working.write_file(FileRoot.SESSION, "source/one.txt", "one")
    working.write_file(FileRoot.SESSION, "source/two.txt", "two")
    stage = working._stage_initial_history

    def interrupted(path, data):
        if path == "copy/two.txt":
            raise RuntimeError("interrupted")
        return stage(path, data)

    monkeypatch.setattr(working, "_stage_initial_history", interrupted)
    with pytest.raises(RuntimeError):
        working.copy_path(FileRoot.SESSION, "source", FileRoot.SESSION, "copy")
    assert not (root / "files" / "copy").exists()
    assert {item[1]["path"] for item in working._revision_indexes()} == {"source/one.txt", "source/two.txt"}


def test_live_reads_do_not_materialize_or_limit_unrelated_outputs(tmp_path: Path, monkeypatch) -> None:
    published, root = make_service(tmp_path)
    published.create_directory(FileRoot.SESSION, "project")
    published.create_directory(FileRoot.SESSION, "generated")
    published.write_file(FileRoot.SESSION, "generated/preview.off", "x" * 100)
    working = run_service(published, uuid4())
    working.write_file(FileRoot.SESSION, "project/source.scad", "cube();")
    working.MAX_OPERATION_BYTES = 10
    monkeypatch.setattr(working, "_replace_private_file", lambda *_: pytest.fail("Read wrote a file"))
    assert working.read_bytes(FileRoot.SESSION, "project/source.scad", limit=100) == b"cube();"
    assert [entry.name for entry in working.list_entries(FileRoot.SESSION, "project").entries] == ["source.scad"]
    assert {entry.name for entry in working.list_entries(FileRoot.SESSION).entries} == {"project", "generated"}
    assert not (root / "revisions" / ".runs").exists()


def test_scoped_reads_leave_open_directory_view_intact(tmp_path: Path) -> None:
    published, _root = make_service(tmp_path)
    working = run_service(published, uuid4())
    working.write_file(FileRoot.SESSION, "one.txt", "one")
    working.write_file(FileRoot.SESSION, "two.txt", "two")
    with working.open_directory_descriptor(FileRoot.SESSION, ".") as descriptor:
        assert working.read_bytes(FileRoot.SESSION, "one.txt", limit=100) == b"one"
        assert set(os.listdir(descriptor)) == {"one.txt", "two.txt"}


def test_live_replacement_preserves_open_descriptors_and_checks_hash(tmp_path: Path) -> None:
    published, _root = make_service(tmp_path)
    working = run_service(published, uuid4())
    working.write_file(FileRoot.SESSION, "report.txt", "previous draft")
    old_scope = working.resolve(FileRoot.SESSION, ".")
    old_file = working.resolve(FileRoot.SESSION, "report.txt")
    old_revision = working.working_revision("report.txt")
    assert old_scope.joinpath("report.txt").read_text() == "previous draft"
    with old_file.open("rb") as opened:
        working.upsert_text_file(FileRoot.SESSION, "report.txt", "current draft")
        assert old_scope.joinpath("report.txt").read_bytes() == b"current draft"
        assert opened.read() == b"previous draft"  # Already-open descriptors cannot be revoked.
    assert working.read_bytes(FileRoot.SESSION, "report.txt", limit=100) == b"current draft"
    with pytest.raises(RootFileError) as stale:
        working.open_working_file(old_revision.file_id, expected_sha256=old_revision.sha256)
    assert stale.value.code == "file.changed"


@pytest.mark.parametrize("source_published,child_published", [(False, False), (False, True), (True, False), (True, True)])
def test_source_delete_purges_owned_companion_drafts_and_histories(
    tmp_path: Path, source_published: bool, child_published: bool,
) -> None:
    published, root = make_service(tmp_path)
    working = run_service(published, uuid4())
    (published if source_published else working).write_file(FileRoot.SESSION, "report.pdf", "source")
    (published if child_published else working).write_file(FileRoot.SESSION, "report.md", "companion")
    source = working.working_revision("report.pdf")
    child = working.working_revision("report.md")
    working.register_derived_file(source.file_id, child.file_id)
    working.register_derived_file(source.file_id, child.file_id)
    working.upsert_text_file(FileRoot.SESSION, "report.md", "latest companion")
    working.resolve(FileRoot.SESSION, ".")
    published.write_file(FileRoot.SESSION, "report-copy.md", "unowned")
    with pytest.raises(RootFileError) as busy:
        published.delete_path(FileRoot.SESSION, "report.pdf", recursive=False)
    assert busy.value.code == "file.changed"
    assert (root / "files" / "report.pdf").exists()
    assert (root / "files" / "report.md").exists()
    restarted = run_service(published, working.run_id)
    restarted.delete_path(FileRoot.SESSION, "report.pdf", recursive=False)
    assert restarted.finalize_run(working.run_id) == ()
    assert [entry.name for entry in restarted.list_entries(FileRoot.SESSION).entries] == ["report-copy.md"]
    assert not (root / "revisions" / str(source.file_id)).exists()
    assert not (root / "revisions" / str(child.file_id)).exists()
    assert restarted._derived_links() == {}
    assert restarted.pending_run_ids() == ()


def test_ownership_is_single_parent_acyclic_and_identity_based(tmp_path: Path) -> None:
    service, _root = make_service(tmp_path)
    ids = {}
    for name in ("source", "child", "grandchild", "other"):
        service.write_file(FileRoot.SESSION, name, name)
        ids[name] = service.working_revision(name).file_id
    service.register_derived_file(ids["source"], ids["child"])
    service.register_derived_file(ids["child"], ids["grandchild"])
    for source, child in (("grandchild", "source"), ("source", "source"), ("other", "child")):
        with pytest.raises(RootFileError) as invalid:
            service.register_derived_file(ids[source], ids[child])
        assert invalid.value.code == "file.changed"
    service.rename_path(FileRoot.SESSION, "child", "renamed-child")
    service.copy_path(FileRoot.SESSION, "renamed-child", FileRoot.SESSION, "copied-child")
    service.delete_path(FileRoot.SESSION, "source", recursive=False)
    assert {entry.name for entry in service.list_entries(FileRoot.SESSION).entries} == {"other", "copied-child"}
    assert service._derived_links() == {}


@pytest.mark.parametrize("discard", [False, True])
def test_source_activation_and_discard_preserve_companion_files(tmp_path: Path, discard: bool) -> None:
    service, root = make_service(tmp_path)
    service.write_file(FileRoot.SESSION, "report.pdf", "source one")
    service.write_file(FileRoot.SESSION, "report.md", "child one")
    source = service.working_revision("report.pdf").file_id
    child = service.working_revision("report.md").file_id
    service.register_derived_file(source, child)
    service.upsert_text_file(FileRoot.SESSION, "report.pdf", "source two")
    service.upsert_text_file(FileRoot.SESSION, "report.md", "child two")
    service.register_derived_file(source, child)
    service.write_revision_derivatives(child, 2, {"preview.txt": b"later preview"})
    service.write_file(FileRoot.SESSION, "later-only.md", "later only")
    later = service.working_revision("later-only.md").file_id
    service.register_derived_file(source, later)
    service.write_file(FileRoot.SESSION, "unowned.md", "not a companion")
    links = service._derived_links()
    if discard:
        service.discard_output_revision(source, 2)
    else:
        service.activate_revision("report.pdf", 1, **revision_preconditions(service, "report.pdf"))
    assert service.read_bytes(FileRoot.SESSION, "report.pdf", limit=100) == b"source one"
    assert [item.revision for item in service.list_revisions("report.pdf")] == ([1] if discard else [2, 1])
    assert service.read_bytes(FileRoot.SESSION, "report.md", limit=100) == b"child two"
    assert [item.revision for item in service.list_revisions("report.md")] == [2, 1]
    assert (root / "revisions" / str(child) / "2").read_bytes() == b"child two"
    assert (root / "revisions" / str(child) / "derived" / "2" / "preview.txt").read_bytes() == b"later preview"
    assert (root / "files" / "later-only.md").read_bytes() == b"later only"
    assert (root / "files" / "unowned.md").read_bytes() == b"not a companion"
    assert service._derived_links() == links
    service.upsert_text_file(FileRoot.SESSION, "report.md", "child three")
    assert service.working_revision("report.md").revision == 3
    service.delete_path(FileRoot.SESSION, "report.pdf", recursive=False)
    assert not (root / "files" / "report.md").exists()
    assert not (root / "files" / "later-only.md").exists()


@pytest.mark.parametrize("discard", [False, True])
@pytest.mark.parametrize("failure", ["write", "index", "target", "fsync_file", "fsync_history", "fsync_staged"])
def test_activation_and_discard_io_failure_keep_file_history_caches_and_companions(
    tmp_path: Path, monkeypatch, failure: str, discard: bool,
) -> None:
    service, root = make_service(tmp_path)
    service.write_file(FileRoot.SESSION, "source", "one")
    service.upsert_text_file(FileRoot.SESSION, "source", "two")
    source = service.working_revision("source").file_id
    service.write_file(FileRoot.SESSION, "companion", "must survive")
    service.register_derived_file(source, service.working_revision("companion").file_id)
    service.write_revision_derivatives(source, 2, {"preview.txt": b"current preview"})
    before = {path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()}
    failed = False
    original_write, original_replace, original_fsync = service._write_private_file, os.replace, service._fsync_directory

    def write(path, data):
        nonlocal failed
        if failure == "write" and not failed:
            failed = True
            raise OSError("injected write failure")
        return original_write(path, data)

    def replace(src, dst, *args, **kwargs):
        nonlocal failed
        selected = root / "files" / "source" if failure == "target" else root / "revisions" / str(source) / "index.json"
        if failure in {"index", "target"} and Path(dst) == selected and not failed:
            failed = True
            raise OSError("injected replace failure")
        return original_replace(src, dst, *args, **kwargs)

    def fsync(path):
        nonlocal failed
        selected = (
            failure == "fsync_file" and path == root / "files"
            or failure == "fsync_history" and path == root / "revisions" / str(source)
            or failure == "fsync_staged" and (
                path.name == "derived" and path.parent.name.startswith(".discard-")
                if discard else path == root / "revisions" / str(source)
            )
        )
        if selected and not failed:
            failed = True
            raise OSError("injected fsync failure")
        return original_fsync(path)

    monkeypatch.setattr(service, "_write_private_file", write)
    monkeypatch.setattr(os, "replace", replace)
    monkeypatch.setattr(service, "_fsync_directory", fsync)
    with pytest.raises(OSError, match="injected"):
        if discard:
            service.discard_output_revision(source, 2)
        else:
            service.activate_revision("source", 1, **revision_preconditions(service, "source"))
    assert failed
    assert {path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()} == before
    assert service.working_revision("source").revision == 2


def test_activation_preserves_unmapped_child_edits_and_shared_derivations(tmp_path: Path) -> None:
    service, _root = make_service(tmp_path)
    service.write_file(FileRoot.SESSION, "source", "one")
    service.write_file(FileRoot.SESSION, "child", "one")
    service.write_file(FileRoot.SESSION, "shared", "same")
    source, child, shared = (service.working_revision(path).file_id for path in ("source", "child", "shared"))
    service.register_derived_file(source, child)
    service.register_derived_file(source, shared)
    service.upsert_text_file(FileRoot.SESSION, "source", "two")
    service.upsert_text_file(FileRoot.SESSION, "child", "two")
    service.register_derived_file(source, child)
    service.register_derived_file(source, shared)
    service.upsert_text_file(FileRoot.SESSION, "child", "independent edit")
    service.activate_revision("source", 1, **revision_preconditions(service, "source"))
    assert [item.revision for item in service.list_revisions("child")] == [3, 2, 1]
    assert service.read_bytes(FileRoot.SESSION, "child", limit=100) == b"independent edit"
    assert service.read_bytes(FileRoot.SESSION, "shared", limit=100) == b"same"


def test_activation_does_not_interfere_with_companion_run(tmp_path: Path) -> None:
    service, _root = make_service(tmp_path)
    service.write_file(FileRoot.SESSION, "source", "one")
    service.upsert_text_file(FileRoot.SESSION, "source", "two")
    service.write_file(FileRoot.SESSION, "child", "one")
    service.register_derived_file(service.working_revision("source").file_id, service.working_revision("child").file_id)
    working = run_service(service, uuid4())
    working.upsert_text_file(FileRoot.SESSION, "child", "work in progress")

    service.activate_revision("source", 1, **revision_preconditions(service, "source"))

    assert service.read_bytes(FileRoot.SESSION, "child", limit=100) == b"work in progress"
    assert service.pending_run_ids() == (working.run_id,)
    assert service.finalize_run(working.run_id)[0].revision == 2


def test_discard_cleanup_failure_is_success_and_retried(tmp_path: Path, monkeypatch, caplog) -> None:
    service, root = make_service(tmp_path)
    service.write_file(FileRoot.SESSION, "source", "one")
    service.upsert_text_file(FileRoot.SESSION, "source", "two")
    original = shutil.rmtree

    def fail_cleanup(path, *args, **kwargs):
        if Path(path).name.startswith(".delete-owned-"):
            raise OSError("injected cleanup failure")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(shutil, "rmtree", fail_cleanup)
    service.discard_output_revision(service.working_revision("source").file_id, 2)

    assert (root / "files" / "source").read_bytes() == b"one"
    assert [item.revision for item in service.list_revisions("source")] == [1]
    assert list((root / "revisions").glob(".delete-owned-*"))
    assert "output revision; trash cleanup deferred" in caplog.text
    monkeypatch.setattr(shutil, "rmtree", original)
    service.list_revisions("source")
    assert not list((root / "revisions").glob(".delete-owned-*"))


def test_failed_discard_rollback_keeps_recovery_bytes(tmp_path: Path, monkeypatch) -> None:
    service, root = make_service(tmp_path)
    service.write_file(FileRoot.SESSION, "source", "one")
    service.upsert_text_file(FileRoot.SESSION, "source", "two")
    history = root / "revisions" / str(service.working_revision("source").file_id)
    original = os.replace
    index_replaced = False

    def fail_replace(src, dst, *args, **kwargs):
        nonlocal index_replaced
        if Path(dst) == root / "files" / "source":
            raise OSError("injected target failure")
        if Path(dst) == history / "index.json":
            if index_replaced:
                raise OSError("injected rollback failure")
            index_replaced = True
        return original(src, dst, *args, **kwargs)

    monkeypatch.setattr(os, "replace", fail_replace)
    with pytest.raises(OSError, match="rollback failure"):
        service.discard_output_revision(service.working_revision("source").file_id, 2)

    service.session_usage()
    trash = next((root / "revisions").glob(".discard-*"))
    assert (trash / "revisions" / "2").read_bytes() == b"two"
    assert (root / "files" / "source").read_bytes() == b"two"
    assert not list((root / "revisions").glob(".delete-owned-*"))


def test_run_derivation_bindings_resolve_after_restart_and_finalization(tmp_path: Path) -> None:
    published, _root = make_service(tmp_path)
    published.write_file(FileRoot.SESSION, "source", "one")
    published.write_file(FileRoot.SESSION, "child", "one")
    source, child = (published.working_revision(path).file_id for path in ("source", "child"))
    published.register_derived_file(source, child)
    working = run_service(published, uuid4())
    working.upsert_text_file(FileRoot.SESSION, "source", "two")
    working.upsert_text_file(FileRoot.SESSION, "child", "two")
    working.register_derived_file(source, child)
    published.finalize_run(working.run_id)
    restarted = RootFileService(published.session_id, session_storage=published.session_storage)
    links = restarted._derived_links()
    restarted.activate_revision("source", 1, **revision_preconditions(restarted, "source"))
    assert restarted.read_bytes(FileRoot.SESSION, "child", limit=100) == b"two"
    assert restarted._derived_links() == links


def test_verified_backfill_revision_pairs_are_idempotent_and_quota_checked(tmp_path: Path) -> None:
    service, _root = make_service(tmp_path)
    for path in ("source", "child"):
        service.write_file(FileRoot.SESSION, path, "one")
        service.upsert_text_file(FileRoot.SESSION, path, "two")
    source, child = (service.working_revision(path).file_id for path in ("source", "child"))
    service.SESSION_QUOTA_BYTES = service.session_usage()
    with pytest.raises(RootFileError) as quota:
        service.register_derived_file(source, child, source_revision=1, derived_revision=1)
    assert quota.value.code == "file.sessionQuotaExceeded"
    assert service._derived_links() == {}
    service.SESSION_QUOTA_BYTES = RootFileService.SESSION_QUOTA_BYTES
    for _ in range(2):
        service.register_derived_file(source, child, source_revision=1, derived_revision=1)
    assert service._derived_links()[str(child)]["bindings"] == [{"source": 1, "derived": 1}]
    for kwargs in ({"source_revision": 1}, {"source_revision": True, "derived_revision": 1}, {"source_revision": 3, "derived_revision": 1}):
        with pytest.raises(RootFileError):
            service.register_derived_file(source, child, **kwargs)


def test_companion_deletion_rejects_symlinked_parent_without_touching_outside(tmp_path: Path) -> None:
    service, root = make_service(tmp_path)
    service.create_directory(FileRoot.SESSION, "companions")
    service.write_file(FileRoot.SESSION, "source.pdf", "source")
    service.write_file(FileRoot.SESSION, "companions/source.md", "companion")
    service.register_derived_file(service.working_revision("source.pdf").file_id, service.working_revision("companions/source.md").file_id)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "source.md").write_bytes(b"outside")
    (root / "files" / "companions").rename(root / "files" / "moved-companions")
    (root / "files" / "companions").symlink_to(outside, target_is_directory=True)
    with pytest.raises(RootFileError) as symlink:
        service.delete_path(FileRoot.SESSION, "source.pdf", recursive=False)
    assert symlink.value.code == "file.symlinkForbidden"
    assert (outside / "source.md").read_bytes() == b"outside"
    assert (root / "files" / "source.pdf").read_bytes() == b"source"


@pytest.mark.parametrize("own_cache", [False, True])
def test_activation_does_not_follow_derivative_directory_symlink(tmp_path: Path, own_cache: bool) -> None:
    service, root = make_service(tmp_path)
    service.write_file(FileRoot.SESSION, "source", "one")
    service.write_file(FileRoot.SESSION, "child", "one")
    source, child = (service.working_revision(path).file_id for path in ("source", "child"))
    service.register_derived_file(source, child)
    service.upsert_text_file(FileRoot.SESSION, "source", "two")
    service.upsert_text_file(FileRoot.SESSION, "child", "two")
    service.register_derived_file(source, child)
    outside = tmp_path / "outside"
    (outside / "2").mkdir(parents=True)
    (outside / "2" / "keep").write_bytes(b"safe")
    (root / "revisions" / str(source if own_cache else child) / "derived").symlink_to(outside, target_is_directory=True)
    service.activate_revision("source", 1, **revision_preconditions(service, "source"))
    assert service.working_revision("source").revision == 1
    assert (outside / "2" / "keep").read_bytes() == b"safe"
    assert service.working_revision("child").revision == 2
