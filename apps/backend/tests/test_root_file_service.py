import hashlib
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
            FileRoot.SESSION, "files", FileRoot.WORKSPACE, "files"
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

    first = service.list_entries(FileRoot.SESSION, "files", offset=0, limit=2)
    second = service.list_entries(
        FileRoot.SESSION, "files", offset=first.next_offset or 0, limit=2
    )
    read = service.read_file(FileRoot.SESSION, "files/a.txt", offset=2, limit=1)
    search = RootFileSearch(service)

    assert [entry.name for entry in first.entries] == ["a.txt", "b.txt"]
    assert [entry.name for entry in second.entries] == ["c.txt"]
    assert read.content == "2: Aurora"
    assert (await search.glob(FileRoot.SESSION, "files", "*.txt"))["paths"] == [
        "files/a.txt",
        "files/b.txt",
        "files/c.txt",
    ]


def test_mutations_are_atomic_and_limited_to_session_files(tmp_path: Path) -> None:
    service, root = make_service(tmp_path)
    service.create_directory(FileRoot.SESSION, "files/subdirectory")
    service.write_file(FileRoot.SESSION, "files/subdirectory/notes.txt", "old")
    service.write_file(FileRoot.SESSION, "files/subdirectory/notes.txt", "new")
    target = root / "files" / "subdirectory" / "notes.txt"

    assert target.read_text(encoding="utf-8") == "new"
    assert not list(target.parent.glob("*.tmp"))
    for operation in (
        lambda: service.write_file(FileRoot.SESSION, "generated/result.txt", "x"),
        lambda: service.create_directory(FileRoot.SESSION, "attachments/new"),
        lambda: service.delete_path(FileRoot.SESSION, ".", recursive=True),
    ):
        with pytest.raises(RootFileError) as exc_info:
            operation()
        assert exc_info.value.code in {
            "file.rootMutationForbidden",
            "file.sessionMutationForbidden",
        }


def test_upload_replace_rename_copy_and_move_preserve_session_contract(
    tmp_path: Path,
) -> None:
    service, root = make_service(tmp_path)
    created = service.create_file(
        FileRoot.SESSION,
        "files/source.txt",
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
        "files/copied.txt",
    )
    moved = service.move_path(
        FileRoot.SESSION,
        renamed.path,
        FileRoot.SESSION,
        "files/moved.txt",
    )

    assert revision == hashlib.sha256(b"new").hexdigest()
    assert copied == "files/copied.txt"
    assert moved == "files/moved.txt"
    assert not (root / renamed.path).exists()
    assert (root / copied).read_text(encoding="utf-8") == "new"
    assert (root / moved).read_text(encoding="utf-8") == "new"


def test_never_follows_symlinks_and_deletes_only_the_link(tmp_path: Path) -> None:
    service, root = make_service(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("secret", encoding="utf-8")
    link = root / "files" / "link"
    link.symlink_to(outside, target_is_directory=True)

    listed = service.list_entries(FileRoot.SESSION, "files")
    assert [(entry.name, entry.kind) for entry in listed.entries] == [
        ("link", "symlink")
    ]
    with pytest.raises(RootFileError) as exc_info:
        service.read_file(FileRoot.SESSION, "files/link/secret.txt")
    assert exc_info.value.code == "file.symlinkForbidden"

    service.delete_path(FileRoot.SESSION, "files/link", recursive=False)
    assert outside.is_dir()
    assert (outside / "secret.txt").is_file()


def test_revision_limit_and_quota_fail_without_partial_publication(tmp_path: Path) -> None:
    service, root = make_service(tmp_path)
    target = root / "files" / "notes.txt"
    target.write_bytes(b"old")

    with pytest.raises(RootFileError) as stale:
        service.replace_text_file(
            FileRoot.SESSION,
            "files/notes.txt",
            "new",
            hashlib.sha256(b"stale").hexdigest(),
        )
    assert stale.value.code == "file.changed"
    with pytest.raises(RootFileError) as too_large:
        service.replace_text_file(
            FileRoot.SESSION,
            "files/notes.txt",
            "x" * (MAX_EDITABLE_TEXT_BYTES + 1),
            hashlib.sha256(b"old").hexdigest(),
        )
    assert too_large.value.code == "file.tooLarge"

    service.SESSION_QUOTA_BYTES = service.session_usage()
    with pytest.raises(RootFileError) as quota:
        service.write_file(FileRoot.SESSION, "files/new.txt", "x")
    assert quota.value.code == "file.sessionQuotaExceeded"
    assert target.read_bytes() == b"old"
    assert not (root / "files" / "new.txt").exists()
    assert not list((root / "files").glob(".*.tmp"))


def test_missing_session_root_is_rejected(tmp_path: Path) -> None:
    service = RootFileService(
        UUID("00000000-0000-0000-0000-000000000001"),
        session_storage=SessionStorage(tmp_path / "sessions"),
    )

    with pytest.raises(RootFileError) as exc_info:
        service.list_entries(FileRoot.SESSION)

    assert exc_info.value.code == "file.sessionNotProvisioned"
