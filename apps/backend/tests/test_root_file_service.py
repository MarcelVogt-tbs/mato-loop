import hashlib
from io import BytesIO
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from mato_loop_backend.filesystem_search import RootFileSearch
from mato_loop_backend.session_storage import SessionStorage
from mato_loop_backend.workspace import (
    MAX_EDITABLE_TEXT_BYTES,
    FileRoot,
    RootFileError,
    RootFileService,
)


def make_service(
    tmp_path: Path, *, working_directory: str | None = "project"
) -> tuple[RootFileService, Path, Path]:
    sessions = SessionStorage(tmp_path / "sessions")
    session_id = uuid4()
    session_root = sessions.provision(session_id)
    workspace_home = tmp_path / "home"
    workspace_home.mkdir()
    workspace_root = workspace_home / "project"
    workspace_root.mkdir()
    service = RootFileService(
        session_id,
        session_storage=sessions,
        workspace_home=workspace_home,
        working_directory=working_directory,
    )
    return service, session_root, workspace_root


@pytest.mark.parametrize(
    "path",
    ["", "/etc/passwd", "../secret", "files/../secret", "files\\secret", "a//b", "a/./b", "a\x00b", "a\x7fb"],
)
def test_root_file_service_rejects_non_relative_posix_paths(tmp_path: Path, path: str) -> None:
    service, _, _ = make_service(tmp_path)

    with pytest.raises(RootFileError) as exc_info:
        service.list_entries(FileRoot.SESSION, path)

    assert exc_info.value.code == "file.invalidPath"


def test_root_file_service_requires_host_uuid_and_selected_workspace(tmp_path: Path) -> None:
    sessions = SessionStorage(tmp_path / "sessions")
    session_id = uuid4()
    sessions.provision(session_id)
    home = tmp_path / "home"
    home.mkdir()

    with pytest.raises(TypeError):
        RootFileService(  # type: ignore[arg-type]
            str(session_id), session_storage=sessions, workspace_home=home
        )

    service = RootFileService(session_id, session_storage=sessions, workspace_home=home)
    with pytest.raises(RootFileError) as exc_info:
        service.list_entries(FileRoot.WORKSPACE)

    assert exc_info.value.code == "file.workspaceNotSelected"


def test_root_file_service_lists_pages_and_reads_utf8(tmp_path: Path) -> None:
    service, session_root, _ = make_service(tmp_path)
    for name in ("c.txt", "a.txt", "b.txt"):
        (session_root / "files" / name).write_text(f"{name}\nzweite Zeile", encoding="utf-8")

    first = service.list_entries(FileRoot.SESSION, "files", offset=0, limit=2)
    second = service.list_directory(
        FileRoot.SESSION, "files", offset=first.next_offset or 0, limit=2
    )
    read = service.read_file(FileRoot.SESSION, "files/a.txt", offset=2, limit=1)

    assert [entry.name for entry in first.entries] == ["a.txt", "b.txt"]
    assert first.has_more is True
    assert first.next_offset == 2
    assert [entry.name for entry in second.entries] == ["c.txt"]
    assert second.has_more is False
    assert read.content == "2: zweite Zeile"


async def test_root_file_search_glob_and_grep_respect_subtree_hidden_and_ignore(
    tmp_path: Path,
) -> None:
    service, session_root, _ = make_service(tmp_path)
    files = session_root / "files"
    docs = files / "docs"
    docs.mkdir()
    (docs / "guide.md").write_text("Aurora\n", encoding="utf-8")
    (docs / "other.txt").write_text("Aurora\n", encoding="utf-8")
    (docs / ".hidden.md").write_text("Aurora hidden\n", encoding="utf-8")
    (docs / "ignored.md").write_text("Aurora ignored\n", encoding="utf-8")
    (docs / ".ignore").write_text("ignored.md\n", encoding="utf-8")
    search = RootFileSearch(service)

    globbed = await search.glob(FileRoot.SESSION, "files/docs", "*.md")
    grepped = await search.grep(FileRoot.SESSION, "files/docs", "Aurora", None)
    markdown = await search.grep(
        FileRoot.SESSION, "files/docs", "Aurora", "*.md"
    )

    assert globbed == {
        "paths": ["files/docs/guide.md", "files/docs/ignored.md"],
        "truncated": False,
    }
    assert [(match["path"], match["line"]) for match in grepped["matches"]] == [
        ("files/docs/.hidden.md", 1),
        ("files/docs/guide.md", 1),
        ("files/docs/other.txt", 1),
    ]
    assert {match["path"] for match in markdown["matches"]} == {
        "files/docs/.hidden.md",
        "files/docs/guide.md",
        "files/docs/ignored.md",
    }


async def test_root_file_search_rejects_invalid_regex_and_limits_results(
    tmp_path: Path,
) -> None:
    service, session_root, _ = make_service(tmp_path)
    files = session_root / "files"
    for index in range(101):
        (files / f"{index:03}.txt").write_text("match\n", encoding="utf-8")
    search = RootFileSearch(service)

    globbed = await search.glob(FileRoot.SESSION, "files", "*.txt")
    grepped = await search.grep(FileRoot.SESSION, "files", "match", None)

    assert len(globbed["paths"]) == 100 and globbed["truncated"] is True
    assert len(grepped["matches"]) == 100 and grepped["truncated"] is True
    with pytest.raises(RootFileError) as exc_info:
        await search.grep(FileRoot.SESSION, "files", "[", None)
    assert exc_info.value.code == "tool.invalidArguments"


async def test_root_file_search_never_traverses_symlinks(tmp_path: Path) -> None:
    service, session_root, _ = make_service(tmp_path)
    outside = tmp_path / "outside-search"
    outside.mkdir()
    (outside / "secret.txt").write_text("secret\n", encoding="utf-8")
    (session_root / "files" / "link").symlink_to(outside, target_is_directory=True)
    search = RootFileSearch(service)

    with pytest.raises(RootFileError) as parent_search:
        await search.glob(FileRoot.SESSION, "files", "*.txt")
    assert parent_search.value.code == "tool.executionFailed"
    assert await search.grep(FileRoot.SESSION, "files", "secret", None) == {
        "matches": [],
        "truncated": False,
    }
    with pytest.raises(RootFileError) as exc_info:
        await search.glob(FileRoot.SESSION, "files/link", "*.txt")
    assert exc_info.value.code == "file.symlinkForbidden"


def test_root_file_service_exposes_five_flat_session_directories(tmp_path: Path) -> None:
    service, session_root, _ = make_service(tmp_path)
    metadata_file = session_root / "generated" / "result.txt"
    metadata_file.write_text("Ergebnis", encoding="utf-8")

    root_entries = service.list_entries(FileRoot.SESSION)

    assert [entry.name for entry in root_entries.entries] == [
        "attachments",
        "derived",
        "downloads",
        "files",
        "generated",
    ]
    assert service.read_file(FileRoot.SESSION, "generated/result.txt").content == (
        "1: Ergebnis"
    )


@pytest.mark.parametrize("managed_directory", ["attachments", "generated", "downloads", "derived"])
def test_managed_session_directories_are_copyable_but_never_mutable(
    tmp_path: Path, managed_directory: str
) -> None:
    service, session_root, workspace_root = make_service(tmp_path)
    managed_file = session_root / managed_directory / "asset.txt"
    managed_file.write_text("Asset", encoding="utf-8")
    workspace_source = workspace_root / "source.txt"
    workspace_source.write_text("Source", encoding="utf-8")

    service.copy_path(
        FileRoot.SESSION,
        f"{managed_directory}/asset.txt",
        FileRoot.WORKSPACE,
        f"{managed_directory}.txt",
    )

    assert (workspace_root / f"{managed_directory}.txt").read_text(encoding="utf-8") == "Asset"
    for operation in (
        lambda: service.write_file(
            FileRoot.SESSION, f"{managed_directory}/asset.txt", "changed"
        ),
        lambda: service.create_directory(
            FileRoot.SESSION, f"{managed_directory}/new"
        ),
        lambda: service.delete_path(
            FileRoot.SESSION, f"{managed_directory}/asset.txt", recursive=False
        ),
        lambda: service.copy_path(
            FileRoot.WORKSPACE,
            "source.txt",
            FileRoot.SESSION,
            f"{managed_directory}/copied.txt",
        ),
        lambda: service.move_path(
            FileRoot.SESSION,
            f"{managed_directory}/asset.txt",
            FileRoot.WORKSPACE,
            f"moved-{managed_directory}.txt",
        ),
        lambda: service.move_path(
            FileRoot.WORKSPACE,
            "source.txt",
            FileRoot.SESSION,
            f"{managed_directory}/moved.txt",
        ),
    ):
        with pytest.raises(RootFileError) as exc_info:
            operation()
        assert exc_info.value.code == "file.sessionMutationForbidden"

    assert managed_file.read_text(encoding="utf-8") == "Asset"
    assert workspace_source.read_text(encoding="utf-8") == "Source"


def test_root_file_service_writes_atomically_and_only_below_session_files(tmp_path: Path) -> None:
    service, session_root, _ = make_service(tmp_path)

    service.create_directory(FileRoot.SESSION, "files/subdirectory")
    assert service.write_file(FileRoot.SESSION, "files/subdirectory/notes.txt", "alt") == (
        "files/subdirectory/notes.txt"
    )
    service.write_file(FileRoot.SESSION, "files/subdirectory/notes.txt", "neu")

    assert (session_root / "files" / "subdirectory" / "notes.txt").read_text(
        encoding="utf-8"
    ) == "neu"
    assert list((session_root / "files" / "subdirectory").glob("*.tmp")) == []
    with pytest.raises(RootFileError):
        service.write_file(FileRoot.SESSION, "files", "verboten")
    with pytest.raises(RootFileError):
        service.delete_path(FileRoot.SESSION, ".", recursive=True)


def test_replace_text_file_is_existing_only_revision_checked_and_exact_byte_limited(
    tmp_path: Path,
) -> None:
    service, session_root, workspace_root = make_service(tmp_path)
    target = session_root / "files" / "notes.txt"
    target.write_bytes(b"alt")

    revision = service.replace_text_file(
        FileRoot.SESSION,
        "files/notes.txt",
        "ä" * (MAX_EDITABLE_TEXT_BYTES // 2),
        hashlib.sha256(b"alt").hexdigest(),
    )

    assert target.stat().st_size == MAX_EDITABLE_TEXT_BYTES
    assert revision == hashlib.sha256(target.read_bytes()).hexdigest()
    with pytest.raises(RootFileError) as stale:
        service.replace_text_file(
            FileRoot.SESSION,
            "files/notes.txt",
            "changed",
            hashlib.sha256(b"alt").hexdigest(),
        )
    assert stale.value.code == "file.changed"
    with pytest.raises(RootFileError) as too_large:
        service.replace_text_file(
            FileRoot.SESSION,
            "files/notes.txt",
            "x" * (MAX_EDITABLE_TEXT_BYTES + 1),
            revision,
        )
    assert too_large.value.code == "file.tooLarge"
    with pytest.raises(RootFileError) as missing:
        service.replace_text_file(
            FileRoot.SESSION,
            "files/missing.txt",
            "new",
            hashlib.sha256(b"").hexdigest(),
        )
    assert missing.value.code == "file.fileNotFound"
    assert not (session_root / "files" / "missing.txt").exists()

    workspace_target = workspace_root / "selected.txt"
    workspace_target.write_text("old", encoding="utf-8")
    service.replace_text_file(
        FileRoot.WORKSPACE,
        "selected.txt",
        "new",
        hashlib.sha256(b"old").hexdigest(),
    )
    assert workspace_target.read_text(encoding="utf-8") == "new"


def test_replace_text_file_rejects_invalid_targets_and_preserves_session_quota(
    tmp_path: Path,
) -> None:
    service, session_root, _ = make_service(tmp_path)
    files = session_root / "files"
    invalid = files / "invalid.txt"
    invalid.write_bytes(b"\xff")
    link = files / "link.txt"
    link.symlink_to(invalid)

    for path, code in (
        ("files/invalid.txt", "file.invalidEncoding"),
        ("files/link.txt", "file.symlinkForbidden"),
        ("files", "file.sessionMutationForbidden"),
    ):
        with pytest.raises(RootFileError) as exc_info:
            service.replace_text_file(
                FileRoot.SESSION,
                path,
                "new",
                hashlib.sha256(b"\xff").hexdigest(),
            )
        assert exc_info.value.code == code

    target = files / "quota.txt"
    target.write_text("old", encoding="utf-8")
    service.SESSION_QUOTA_BYTES = service.session_usage()
    with pytest.raises(RootFileError) as quota:
        service.replace_text_file(
            FileRoot.SESSION,
            "files/quota.txt",
            "larger",
            hashlib.sha256(b"old").hexdigest(),
        )
    assert quota.value.code == "file.sessionQuotaExceeded"
    assert target.read_text(encoding="utf-8") == "old"
    assert not list(files.glob(".upload-*.tmp"))


def test_root_file_service_uploads_only_below_session_files(tmp_path: Path) -> None:
    service, session_root, _ = make_service(tmp_path)

    entry = service.create_file(
        FileRoot.SESSION,
        "files/archive.bin",
        BytesIO(b"\x00\xff"),
        max_bytes=10,
    )

    assert entry.path == "files/archive.bin"
    assert service.open_file(FileRoot.SESSION, entry.path).read() == b"\x00\xff"
    assert (session_root / entry.path).stat().st_mode & 0o777 == 0o600
    with pytest.raises(RootFileError) as exc_info:
        service.create_file(
            FileRoot.SESSION,
            "generated/result.bin",
            BytesIO(b"result"),
            max_bytes=10,
        )
    assert exc_info.value.code == "file.sessionMutationForbidden"


def test_root_file_service_renames_only_mutable_entries_without_overwriting(
    tmp_path: Path,
) -> None:
    service, session_root, _ = make_service(tmp_path)
    files = session_root / "files"
    (files / "old.txt").write_text("old", encoding="utf-8")
    (files / "target.txt").write_text("target", encoding="utf-8")
    (files / "folder").mkdir()

    renamed_file = service.rename_path(
        FileRoot.SESSION, "files/old.txt", "renamed.txt"
    )
    renamed_directory = service.rename_path(
        FileRoot.SESSION, "files/folder", "renamed-folder"
    )

    assert renamed_file.path == "files/renamed.txt"
    assert renamed_directory.kind == "directory"
    with pytest.raises(RootFileError) as exc_info:
        service.rename_path(
            FileRoot.SESSION, "files/renamed.txt", "target.txt"
        )
    assert exc_info.value.code == "file.pathAlreadyExists"
    with pytest.raises(RootFileError) as exc_info:
        service.rename_path(
            FileRoot.SESSION, "generated", "renamed-generated"
        )
    assert exc_info.value.code == "file.sessionMutationForbidden"
    (files / "link").symlink_to(files / "renamed.txt")
    with pytest.raises(RootFileError) as exc_info:
        service.rename_path(FileRoot.SESSION, "files/link", "renamed-link")
    assert exc_info.value.code == "file.symlinkForbidden"
    assert (files / "renamed.txt").read_text(encoding="utf-8") == "old"
    assert (files / "target.txt").read_text(encoding="utf-8") == "target"


def test_root_file_service_never_follows_symlinks(tmp_path: Path) -> None:
    service, session_root, workspace_root = make_service(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("geheim", encoding="utf-8")
    link = session_root / "files" / "link"
    link.symlink_to(outside, target_is_directory=True)

    listed = service.list_entries(FileRoot.SESSION, "files")
    assert [(entry.name, entry.kind) for entry in listed.entries] == [("link", "symlink")]

    for operation in (
        lambda: service.list_entries(FileRoot.SESSION, "files/link"),
        lambda: service.read_file(FileRoot.SESSION, "files/link/secret.txt"),
        lambda: service.write_file(FileRoot.SESSION, "files/link/new.txt", "neu"),
        lambda: service.copy_path(
            FileRoot.SESSION, "files/link", FileRoot.WORKSPACE, "copied-link"
        ),
    ):
        with pytest.raises(RootFileError) as exc_info:
            operation()
        assert exc_info.value.code == "file.symlinkForbidden"

    service.delete_path(FileRoot.SESSION, "files/link", recursive=False)
    assert outside.is_dir()
    assert (outside / "secret.txt").is_file()
    assert not (workspace_root / "copied-link").exists()


def test_root_file_service_requires_explicit_recursive_directory_delete(tmp_path: Path) -> None:
    service, _, workspace_root = make_service(tmp_path)
    directory = workspace_root / "directory"
    directory.mkdir()
    (directory / "file.txt").write_text("Text", encoding="utf-8")

    with pytest.raises(RootFileError) as exc_info:
        service.delete_path(FileRoot.WORKSPACE, "directory", recursive=False)

    assert exc_info.value.code == "file.directoryNotEmpty"
    assert directory.exists()
    service.delete_path(FileRoot.WORKSPACE, "directory", recursive=True)
    assert not directory.exists()


def test_root_file_service_copies_and_moves_exact_targets_between_roots(tmp_path: Path) -> None:
    service, session_root, workspace_root = make_service(tmp_path)
    source = session_root / "files" / "source"
    source.mkdir()
    (source / "notes.txt").write_text("Notiz", encoding="utf-8")

    copied = service.copy_path(
        FileRoot.SESSION, "files/source", FileRoot.WORKSPACE, "copied"
    )
    moved = service.move_path(
        FileRoot.WORKSPACE, "copied", FileRoot.SESSION, "files/moved"
    )

    assert copied == "copied"
    assert moved == "files/moved"
    assert not (workspace_root / "copied").exists()
    assert (session_root / "files" / "moved" / "notes.txt").read_text(
        encoding="utf-8"
    ) == "Notiz"
    assert source.exists()

    with pytest.raises(RootFileError) as exc_info:
        service.copy_path(
            FileRoot.SESSION, "files/source", FileRoot.SESSION, "files/moved"
        )
    assert exc_info.value.code == "file.pathAlreadyExists"


def test_generated_asset_is_copy_source_but_never_move_source(tmp_path: Path) -> None:
    service, session_root, workspace_root = make_service(tmp_path)
    generated = session_root / "generated" / "answer.txt"
    generated.write_text("Antwort", encoding="utf-8")

    service.copy_path(
        FileRoot.SESSION,
        "generated/answer.txt",
        FileRoot.WORKSPACE,
        "answer.txt",
    )

    assert (workspace_root / "answer.txt").read_text(encoding="utf-8") == "Antwort"
    with pytest.raises(RootFileError) as exc_info:
        service.move_path(
            FileRoot.SESSION,
            "generated/answer.txt",
            FileRoot.WORKSPACE,
            "moved-answer.txt",
        )
    assert exc_info.value.code == "file.sessionMutationForbidden"
    assert generated.exists()


def test_copy_rejects_nested_symlink_without_partial_destination(tmp_path: Path) -> None:
    service, session_root, workspace_root = make_service(tmp_path)
    source = session_root / "files" / "source"
    source.mkdir()
    (source / "link").symlink_to(tmp_path, target_is_directory=True)

    with pytest.raises(RootFileError) as exc_info:
        service.copy_path(
            FileRoot.SESSION, "files/source", FileRoot.WORKSPACE, "destination"
        )

    assert exc_info.value.code == "file.symlinkForbidden"
    assert not (workspace_root / "destination").exists()


def test_copy_and_move_reject_destinations_inside_source_directory(tmp_path: Path) -> None:
    service, _, workspace_root = make_service(tmp_path)
    (workspace_root / "source").mkdir()

    for operation in (
        lambda: service.copy_path(
            FileRoot.WORKSPACE, "source", FileRoot.WORKSPACE, "source/copy"
        ),
        lambda: service.move_path(
            FileRoot.WORKSPACE, "source", FileRoot.WORKSPACE, "source/moved"
        ),
    ):
        with pytest.raises(RootFileError) as exc_info:
            operation()
        assert exc_info.value.code == "file.invalidDestination"

    assert list((workspace_root / "source").iterdir()) == []


def test_service_does_not_accept_a_missing_session_root(tmp_path: Path) -> None:
    sessions = SessionStorage(tmp_path / "sessions")
    home = tmp_path / "home"
    home.mkdir()
    service = RootFileService(
        UUID("00000000-0000-0000-0000-000000000001"),
        session_storage=sessions,
        workspace_home=home,
    )

    with pytest.raises(RootFileError) as exc_info:
        service.list_entries(FileRoot.SESSION)

    assert exc_info.value.code == "file.sessionNotProvisioned"


def test_session_quota_includes_managed_assets_and_subtracts_replaced_file(tmp_path: Path) -> None:
    service, session_root, _ = make_service(tmp_path)
    service.SESSION_QUOTA_BYTES = 10
    (session_root / "generated" / "metadata.bin").write_bytes(b"123456")
    target = session_root / "files" / "notes.txt"
    target.write_text("123", encoding="utf-8")

    service.write_file(FileRoot.SESSION, "files/notes.txt", "1234")

    assert target.read_text(encoding="utf-8") == "1234"
    with pytest.raises(RootFileError) as replacement_error:
        service.write_file(FileRoot.SESSION, "files/notes.txt", "12345")
    assert replacement_error.value.code == "file.sessionQuotaExceeded"
    assert target.read_text(encoding="utf-8") == "1234"

    with pytest.raises(RootFileError) as create_error:
        service.write_file(FileRoot.SESSION, "files/new.txt", "1")
    assert create_error.value.code == "file.sessionQuotaExceeded"
    assert not (session_root / "files" / "new.txt").exists()


def test_workspace_operations_are_not_subject_to_session_quota(tmp_path: Path) -> None:
    service, _, workspace_root = make_service(tmp_path)
    service.SESSION_QUOTA_BYTES = 0

    service.write_file(FileRoot.WORKSPACE, "source.txt", "workspace")
    service.copy_path(
        FileRoot.WORKSPACE, "source.txt", FileRoot.WORKSPACE, "copied.txt"
    )
    service.move_path(
        FileRoot.WORKSPACE, "copied.txt", FileRoot.WORKSPACE, "moved.txt"
    )

    assert (workspace_root / "source.txt").read_text(encoding="utf-8") == "workspace"
    assert (workspace_root / "moved.txt").read_text(encoding="utf-8") == "workspace"


def test_copy_and_workspace_move_into_session_enforce_total_quota(tmp_path: Path) -> None:
    service, session_root, workspace_root = make_service(tmp_path)
    service.SESSION_QUOTA_BYTES = 10
    (session_root / "generated" / "metadata.bin").write_bytes(b"123456")
    (workspace_root / "source.txt").write_bytes(b"12345")

    for operation, destination in (
        (
            lambda: service.copy_path(
                FileRoot.WORKSPACE,
                "source.txt",
                FileRoot.SESSION,
                "files/copied.txt",
            ),
            session_root / "files" / "copied.txt",
        ),
        (
            lambda: service.move_path(
                FileRoot.WORKSPACE,
                "source.txt",
                FileRoot.SESSION,
                "files/moved.txt",
            ),
            session_root / "files" / "moved.txt",
        ),
    ):
        with pytest.raises(RootFileError) as exc_info:
            operation()
        assert exc_info.value.code == "file.sessionQuotaExceeded"
        assert not destination.exists()
        assert (workspace_root / "source.txt").exists()


def test_session_move_has_no_quota_growth_but_session_copy_does(tmp_path: Path) -> None:
    service, session_root, _ = make_service(tmp_path)
    service.SESSION_QUOTA_BYTES = 8
    source = session_root / "files" / "source.bin"
    source.write_bytes(b"12345678")

    with pytest.raises(RootFileError) as copy_error:
        service.copy_path(
            FileRoot.SESSION,
            "files/source.bin",
            FileRoot.SESSION,
            "files/copied.bin",
        )
    assert copy_error.value.code == "file.sessionQuotaExceeded"

    service.move_path(
        FileRoot.SESSION,
        "files/source.bin",
        FileRoot.SESSION,
        "files/moved.bin",
    )

    assert not source.exists()
    assert (session_root / "files" / "moved.bin").read_bytes() == b"12345678"


def test_copy_and_move_limit_recursive_source_entries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, session_root, workspace_root = make_service(tmp_path)
    monkeypatch.setattr(RootFileService, "MAX_OPERATION_ENTRIES", 3)
    source = workspace_root / "source"
    source.mkdir()
    for index in range(3):
        (source / f"{index}.txt").write_text("", encoding="utf-8")

    for operation in (
        lambda: service.copy_path(
            FileRoot.WORKSPACE, "source", FileRoot.SESSION, "files/copied"
        ),
        lambda: service.move_path(
            FileRoot.WORKSPACE, "source", FileRoot.SESSION, "files/moved"
        ),
    ):
        with pytest.raises(RootFileError) as exc_info:
            operation()
        assert exc_info.value.code == "file.operationTooLarge"

    assert source.exists()
    assert not (session_root / "files" / "copied").exists()
    assert not (session_root / "files" / "moved").exists()


def test_copy_and_move_limit_source_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, session_root, workspace_root = make_service(tmp_path)
    monkeypatch.setattr(RootFileService, "MAX_OPERATION_BYTES", 4)
    (workspace_root / "source.bin").write_bytes(b"12345")

    for operation in (
        lambda: service.copy_path(
            FileRoot.WORKSPACE, "source.bin", FileRoot.SESSION, "files/copied.bin"
        ),
        lambda: service.move_path(
            FileRoot.WORKSPACE, "source.bin", FileRoot.SESSION, "files/moved.bin"
        ),
    ):
        with pytest.raises(RootFileError) as exc_info:
            operation()
        assert exc_info.value.code == "file.operationTooLarge"

    assert (workspace_root / "source.bin").exists()
    assert not (session_root / "files" / "copied.bin").exists()
    assert not (session_root / "files" / "moved.bin").exists()


def test_write_rechecks_session_quota_before_atomic_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, session_root, _ = make_service(tmp_path)
    service.SESSION_QUOTA_BYTES = 10
    usage = iter((0, 11))
    monkeypatch.setattr(service, "_session_usage", lambda: next(usage))

    with pytest.raises(RootFileError) as exc_info:
        service.write_file(FileRoot.SESSION, "files/notes.txt", "1")

    assert exc_info.value.code == "file.sessionQuotaExceeded"
    assert not (session_root / "files" / "notes.txt").exists()
    assert list((session_root / "files").iterdir()) == []
