from copy import deepcopy
from unittest.mock import AsyncMock, Mock
from uuid import UUID, uuid4

import pytest
from document_helpers import document_bytes
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.files.service import RootFileError, RootFileService
from mato_loop_backend.files.storage import SessionStorage
from mato_loop_backend.media.attachments import AttachmentError, AttachmentService
from mato_loop_backend.models import ChatSession, Message, MessageAttachment


@pytest.mark.parametrize("state", ["published", "pending", "finalized"])
async def test_document_deletion_purges_companion_history_and_pending_files(tmp_path, state):
    session = ChatSession(id=uuid4(), user_id=uuid4())
    storage = SessionStorage(tmp_path / "sessions")
    storage.provision(session.id)
    run_id = uuid4()
    service = AttachmentService(storage, run_id=run_id if state != "published" else None)
    message = Message(id=uuid4(), session_id=session.id, role="tool", attachments=[])
    db = AsyncMock(spec=AsyncSession)
    db.scalar.return_value = session
    attachment = await service.create_attachment(
        db,
        message,
        document_bytes("docx"),
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        kind="document",
        filename="source.docx",
        position=0,
        purpose="artifact",
        category="generated",
    )
    companion_id = UUID(attachment.metadata_json["companionFileId"])
    companion_path = attachment.metadata_json["companionFilePath"]
    public = RootFileService(session.id, session_storage=storage)
    working = RootFileService(session.id, session_storage=storage, run_id=run_id)
    if state == "finalized":
        public.finalize_run(run_id)
    assert working.open_file("session", companion_path).read()
    if state == "pending":
        assert service.read_derived_text(attachment, session.id, current_only=True)
        with pytest.raises(RootFileError) as error:
            public.delete_path("session", attachment.file_path, recursive=False)
        assert error.value.code == "file.changed"
        working.delete_path("session", attachment.file_path, recursive=False)
    else:
        public.delete_path("session", attachment.file_path, recursive=False)
    assert public.list_revisions(attachment.file_path) == ()
    assert public.list_revisions(companion_path) == ()
    with pytest.raises(RootFileError):
        working.open_working_file(companion_id)
    assert public.finalize_run(run_id) == ()
    db.add.reset_mock()
    db.scalars.return_value = Mock(all=Mock(return_value=[message]))
    await service.finalize_run(db, session, run_id)
    db.add.assert_not_called()
    assert public.list_revisions(companion_path) == ()


@pytest.mark.parametrize("pending", [False, True])
@pytest.mark.parametrize("registered_before_failure", [False, True])
async def test_registration_failure_cannot_publish_orphan_outputs(
    tmp_path, monkeypatch, pending, registered_before_failure
):
    session = ChatSession(id=uuid4(), user_id=uuid4())
    storage = SessionStorage(tmp_path / "sessions")
    storage.provision(session.id)
    run_id = uuid4()
    db = AsyncMock(spec=AsyncSession)
    db.scalar.return_value = session
    service = AttachmentService(storage, run_id=run_id if pending else None)
    message = Message(id=uuid4(), session_id=session.id, role="tool")
    register = RootFileService.register_derived_file
    identities = []

    def fail(files, source_id, companion_id):
        identities.extend([source_id, companion_id])
        if registered_before_failure:
            register(files, source_id, companion_id)
        raise RootFileError("file.changed", "Registration failed")

    monkeypatch.setattr(RootFileService, "register_derived_file", fail)
    with pytest.raises(AttachmentError, match="attachment.storageInvalid"):
        await service.create_attachment(
            db,
            message,
            document_bytes("docx"),
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            kind="document",
            filename="source.docx",
            position=0,
            purpose="artifact",
            category="generated",
        )
    db.add.assert_not_called()
    files = RootFileService(session.id, session_storage=storage)
    assert len(identities) == 2
    assert files.finalize_run(run_id) == ()
    for file_id in identities:
        with pytest.raises(RootFileError):
            files.file_revision(file_id, 1)
    assert not any(
        path.is_file() for path in (storage.session_path(session.id) / "files").rglob("*")
    )


def legacy_attachment(files, *, kind="document"):
    source = files.create_output_file("source.bin", b"original source")
    companion = files.create_output_file("unrelated-name.wav", b"original companion")
    attachment = MessageAttachment(
        id=uuid4(),
        message_id=uuid4(),
        kind=kind,
        purpose="attachment",
        category="user_upload",
        processing_status="ready",
        mime_type="application/octet-stream",
        filename="source.bin",
        size_bytes=source.size_bytes,
        sha256=source.sha256,
        file_root_id=files.session_id,
        file_id=source.file_id,
        file_revision=source.revision,
        file_path=source.path,
        metadata_json=AttachmentService._companion_metadata(files.session_id, companion),
    )
    return attachment, source, companion


def stream_db(*rows):
    db = AsyncMock(spec=AsyncSession)
    result = AsyncMock()
    result.__aiter__.return_value = list(rows)
    db.stream.return_value = result
    return db, result


@pytest.mark.parametrize("kind", ["document", "audio"])
async def test_backfill_is_idempotent_preserves_legacy_bytes_and_binds_exact_revisions(
    tmp_path, monkeypatch, kind
):
    session_id = uuid4()
    storage = SessionStorage(tmp_path / "sessions")
    storage.provision(session_id)
    files = RootFileService(session_id, session_storage=storage)
    attachment, source, companion = legacy_attachment(files, kind=kind)
    lookalike = files.create_output_file("source.txt", b"not a companion")
    files.append_output_revision(source.file_id, b"new source")
    files.append_output_revision(companion.file_id, b"new companion")
    files.rename_path("session", companion.path, "renamed.txt")
    original = deepcopy(attachment.metadata_json)
    db, result = stream_db((attachment, session_id, False))
    service = AttachmentService(storage)
    register = RootFileService.register_derived_file
    bindings = []

    def record(files, source_id, child_id, **kwargs):
        bindings.append(kwargs)
        return register(files, source_id, child_id, **kwargs)

    monkeypatch.setattr(RootFileService, "register_derived_file", record)
    assert await service.backfill_derived_files(db) == 1
    ownership = (
        storage.session_path(session_id) / "revisions" / ".ownership" / f"{companion.file_id}.json"
    )
    saved = ownership.read_bytes()
    assert await service.backfill_derived_files(db) == 1
    assert ownership.read_bytes() == saved
    assert bindings == [{"source_revision": 1, "derived_revision": 1}] * 2
    assert attachment.metadata_json == original
    assert files.open_file("session", source.path).read() == b"new source"
    assert files.open_file("session", "renamed.txt").read() == b"new companion"
    assert db.stream.call_args.args[0].get_execution_options()["yield_per"] == 100
    result.close.assert_awaited()
    db.add.assert_not_called()
    db.commit.assert_not_awaited()
    files.delete_path("session", source.path, recursive=False)
    assert files.list_revisions("renamed.txt") == ()
    assert files.open_file("session", lookalike.path).read() == b"not a companion"


@pytest.mark.parametrize(
    "invalid",
    [
        "source_hash",
        "companion_hash",
        "root",
        "source_pruned",
        "companion_pruned",
        "invalid_id",
        "shared",
        "self",
    ],
)
async def test_backfill_skips_unverified_shared_or_pruned_references(tmp_path, caplog, invalid):
    session_id = uuid4()
    storage = SessionStorage(tmp_path / "sessions")
    storage.provision(session_id)
    files = RootFileService(session_id, session_storage=storage)
    attachment, source, companion = legacy_attachment(files)
    if invalid == "source_hash":
        attachment.sha256 = "0" * 64
    elif invalid == "companion_hash":
        attachment.metadata_json["companionSha256"] = "0" * 64
    elif invalid == "root":
        attachment.metadata_json["companionFileRootId"] = str(uuid4())
    elif invalid == "source_pruned":
        files.delete_path("session", source.path, recursive=False)
    elif invalid == "companion_pruned":
        files.delete_path("session", companion.path, recursive=False)
    elif invalid == "invalid_id":
        attachment.metadata_json["companionFileId"] = "private-invalid-id"
    elif invalid == "self":
        attachment.metadata_json = AttachmentService._companion_metadata(session_id, source)
    metadata = deepcopy(attachment.metadata_json)
    db, _ = stream_db((attachment, session_id, invalid == "shared"))
    assert await AttachmentService(storage).backfill_derived_files(db) == 0
    assert attachment.metadata_json == metadata
    assert "private-invalid-id" not in caplog.text
    assert not (storage.session_path(session_id) / "revisions" / ".ownership").exists()
    if invalid != "source_pruned":
        assert files.open_file("session", source.path).read() == b"original source"
        files.delete_path("session", source.path, recursive=False)
    if invalid != "companion_pruned":
        assert files.open_file("session", companion.path).read() == b"original companion"


async def test_failed_document_update_never_deletes_existing_source_history(tmp_path, monkeypatch):
    session = ChatSession(id=uuid4(), user_id=uuid4())
    storage = SessionStorage(tmp_path / "sessions")
    storage.provision(session.id)
    files = RootFileService(session.id, session_storage=storage)
    attachment, source, companion = legacy_attachment(files)
    files.register_derived_file(source.file_id, companion.file_id)
    db = AsyncMock(spec=AsyncSession)
    db.scalar.return_value = session
    run_id = uuid4()
    monkeypatch.setattr(
        RootFileService,
        "register_derived_file",
        Mock(side_effect=RootFileError("file.changed", "Registration failed")),
    )
    with pytest.raises(AttachmentError):
        await AttachmentService(storage, run_id=run_id).create_attachment(
            db,
            Message(id=uuid4(), session_id=session.id, role="tool"),
            document_bytes("docx"),
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            kind="document",
            filename="source.docx",
            position=0,
            purpose="artifact",
            category="generated",
            source_file_root_id=session.id,
            file_id=source.file_id,
        )
    files.finalize_run(run_id)
    assert files.open_revision_by_id(source.file_id, 1).read() == b"original source"
    assert files.open_revision_by_id(companion.file_id, 1).read() == b"original companion"
