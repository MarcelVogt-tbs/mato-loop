from __future__ import annotations

import wave
from io import BytesIO
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from PIL import Image
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.agent.context import message_dict
from mato_loop_backend.attachments import (
    MAX_FILE_ATTACHMENT_BYTES,
    MAX_IMAGE_ATTACHMENT_BYTES,
    SESSION_ATTACHMENT_QUOTA_BYTES,
    AttachmentError,
    AttachmentService,
    classify_attachment,
    validate_audio,
    validate_image,
)
from mato_loop_backend.models import ChatSession, Message
from mato_loop_backend.session_storage import SessionStorage
from mato_loop_backend.video_processing import MediaInfo
from mato_loop_backend.workspace import RootFileService


def image_bytes(image_format: str) -> bytes:
    buffer = BytesIO()
    mode = "RGB" if image_format == "JPEG" else "RGBA"
    Image.new(mode, (3, 2), (20, 40, 60)).save(buffer, format=image_format)
    return buffer.getvalue()


def audio_bytes(*, frames: int = 80, sample_rate: int = 8_000) -> bytes:
    buffer = BytesIO()
    with wave.open(buffer, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(sample_rate)
        audio.writeframes(b"\x00\x00" * frames)
    return buffer.getvalue()


def provisioned_storage(tmp_path: Path, session_id):
    sessions = SessionStorage(tmp_path / "sessions")
    sessions.provision(session_id)
    return sessions


def chat_session(session_id, *, project_id=None) -> ChatSession:
    return ChatSession(
        id=session_id,
        user_id=uuid4(),
        project_id=project_id,
        title="Test",
    )


def test_pcm_wav_is_strictly_validated() -> None:
    result = validate_audio(audio_bytes(), "audio/wav")

    assert result.duration_ms == 10
    assert result.channels == 1
    assert result.sample_rate == 8_000
    assert result.sample_width == 2
    assert result.sha256


def test_invalid_or_oversized_audio_is_rejected() -> None:
    with pytest.raises(AttachmentError, match="attachment.invalidAudio"):
        validate_audio(b"RIFF\x00\x00\x00\x00WAVE", "audio/wav")
    with pytest.raises(AttachmentError, match="attachment.tooLarge"):
        validate_audio(b"x" * (MAX_FILE_ATTACHMENT_BYTES + 1), "audio/wav")


@pytest.mark.parametrize(
    ("image_format", "mime_type"),
    [("PNG", "image/png"), ("JPEG", "image/jpeg"), ("WEBP", "image/webp"), ("GIF", "image/gif")],
)
def test_allowed_images_are_verified(image_format: str, mime_type: str) -> None:
    result = validate_image(image_bytes(image_format), mime_type)

    assert result.mime_type == mime_type
    assert (result.width, result.height) == (3, 2)
    assert len(result.sha256) == 64


def test_image_magic_must_match_declared_mime_type() -> None:
    with pytest.raises(AttachmentError) as exc_info:
        validate_image(image_bytes("PNG"), "image/jpeg")
    assert exc_info.value.code == "attachment.mimeMismatch"


def test_corrupt_image_with_known_magic_is_rejected() -> None:
    with pytest.raises(AttachmentError) as exc_info:
        validate_image(b"\x89PNG\r\n\x1a\ncorrupt", "image/png")
    assert exc_info.value.code == "attachment.invalidImage"
    assert exc_info.value.diagnostic.startswith("verify:")


def test_attachment_classification_does_not_trust_browser_mime() -> None:
    assert classify_attachment(image_bytes("PNG"), "text/plain", "../bild.png").kind == "image"
    text = classify_attachment(b"print('ok')\n", "application/octet-stream", "source.py")
    assert (text.kind, text.mime_type, text.filename) == ("text", "text/plain", "source.py")
    assert classify_attachment(b"%PDF-1.7\n", "text/plain", "report.bin").kind == "document"
    assert classify_attachment(b"%PDF-1.7\n", "application/pdf", "report.pdf").kind == "document"
    generic = classify_attachment(b"\x00\xffdata", "image/png", "../unsafe.bin")
    assert (generic.kind, generic.mime_type, generic.filename) == (
        "file",
        "application/octet-stream",
        "unsafe.bin",
    )


def test_empty_and_oversized_general_files_are_rejected() -> None:
    with pytest.raises(AttachmentError, match="attachment.empty"):
        classify_attachment(b"", "application/octet-stream", "empty")
    with pytest.raises(AttachmentError, match="attachment.tooLarge"):
        classify_attachment(b"x" * (MAX_FILE_ATTACHMENT_BYTES + 1), "text/plain", "large.txt")


async def test_service_persists_validated_metadata_and_relative_path(tmp_path: Path) -> None:
    session_id = uuid4()
    sessions = provisioned_storage(tmp_path, session_id)
    message = Message(id=uuid4(), session_id=session_id, role="assistant", content="Bild")
    db = AsyncMock(spec=AsyncSession)
    db.scalar.side_effect = [chat_session(session_id), None]
    data = image_bytes("PNG")

    attachment = await AttachmentService(sessions).create_image(
        db, message, data, "image/png", filename="../diagram.png"
    )

    assert attachment.kind == "image"
    assert attachment.category == "user_upload"
    assert attachment.purpose == "attachment"
    assert attachment.processing_status == "ready"
    assert attachment.filename == "diagram.png"
    assert attachment.file_root_id == session_id
    assert attachment.file_path == f"attachments/{attachment.id}/diagram.png"
    assert RootFileService(session_id, session_storage=sessions).revision_path(
        attachment.file_id, attachment.file_revision
    ).read_bytes() == data


async def test_service_validates_and_persists_artifact_purpose(tmp_path: Path) -> None:
    session_id = uuid4()
    sessions = provisioned_storage(tmp_path, session_id)
    message = Message(id=uuid4(), session_id=session_id, role="tool", content="Datei")
    db = AsyncMock(spec=AsyncSession)
    db.scalar.side_effect = [chat_session(session_id), None]

    attachment = await AttachmentService(sessions).create_attachment(
        db,
        message,
        b"\x00\x01tool-output",
        "application/octet-stream",
        kind="file",
        category="generated",
        filename="result.bin",
        purpose="artifact",
    )

    assert attachment.purpose == "artifact"
    assert attachment.category == "generated"
    assert attachment.mime_type == "application/octet-stream"
    assert attachment.file_root_id == session_id
    assert attachment.file_id is not None
    assert attachment.file_revision == 1
    assert attachment.file_path is not None
    assert not attachment.file_path.startswith("files/")
    root_files = RootFileService(session_id, session_storage=sessions)
    assert root_files.revision_path(
        attachment.file_id, attachment.file_revision
    ).read_bytes() == b"\x00\x01tool-output"
    with pytest.raises(AttachmentError, match="attachment.invalidPurpose"):
        await AttachmentService(sessions).create_attachment(
            db,
            message,
            b"data",
            "application/octet-stream",
            kind="file",
            purpose="other",  # type: ignore[arg-type]
        )
    assert len(root_files.list_revisions(attachment.file_path)) == 1


async def test_service_references_existing_revision_without_copy(tmp_path: Path) -> None:
    session_id = uuid4()
    sessions = provisioned_storage(tmp_path, session_id)
    root_files = RootFileService(session_id, session_storage=sessions)
    root_files.write_file("session", "report.pdf", "report")
    revision = root_files.list_revisions("report.pdf")[0]
    message = Message(id=uuid4(), session_id=session_id, role="tool", content="Datei")
    db = AsyncMock(spec=AsyncSession)
    db.scalar.return_value = chat_session(session_id)

    attachment = await AttachmentService(sessions).create_reference(
        db,
        message,
        file_root_id=session_id,
        file_id=revision.file_id,
        file_revision=revision.revision,
        file_path=revision.path,
        filename="report.pdf",
        position=0,
        category="downloaded",
    )

    assert attachment.file_id == revision.file_id
    assert attachment.file_revision == revision.revision
    assert len(root_files.list_revisions("report.pdf")) == 1


async def test_image_read_revalidates_persisted_content(tmp_path: Path) -> None:
    session_id = uuid4()
    sessions = provisioned_storage(tmp_path, session_id)
    message = Message(id=uuid4(), session_id=session_id, role="user", content="Bild")
    db = AsyncMock(spec=AsyncSession)
    db.scalar.side_effect = [chat_session(session_id), None]
    service = AttachmentService(sessions)
    data = image_bytes("PNG")
    attachment = await service.create_image(db, message, data, "image/png")

    assert await service.read_image(attachment, session_id) == data

    attachment.sha256 = "0" * 64
    with pytest.raises(AttachmentError, match="attachment.notFound"):
        await service.read_image(attachment, session_id)


async def test_attachment_reference_survives_file_rename(tmp_path: Path) -> None:
    session_id = uuid4()
    sessions = provisioned_storage(tmp_path, session_id)
    message = Message(id=uuid4(), session_id=session_id, role="user", content="Bild")
    db = AsyncMock(spec=AsyncSession)
    db.scalar.side_effect = [chat_session(session_id), None]
    service = AttachmentService(sessions)
    data = image_bytes("PNG")
    attachment = await service.create_image(db, message, data, "image/png")

    RootFileService(session_id, session_storage=sessions).rename_path(
        "session", attachment.file_path, "renamed.png"
    )

    assert await service.read_image(attachment, session_id) == data


async def test_attachment_reads_exact_revision_only_in_current_root(tmp_path: Path) -> None:
    session_id = uuid4()
    project_id = uuid4()
    sessions = SessionStorage(tmp_path / "sessions")
    sessions.provision(session_id, project_id)
    message = Message(id=uuid4(), session_id=session_id, role="user", content="Bild")
    db = AsyncMock(spec=AsyncSession)
    db.scalar.side_effect = [chat_session(session_id, project_id=project_id), None]
    service = AttachmentService(sessions)
    original = image_bytes("PNG")
    attachment = await service.create_image(
        db, message, original, "image/png", filename="bild.png"
    )
    root_files = RootFileService(
        session_id, project_id=project_id, session_storage=sessions
    )
    assert await service.read_image(attachment, session_id, current_only=True) == original
    newer = image_bytes("JPEG")
    root_files.append_output_revision(attachment.file_id, newer)

    assert await service.read_image(attachment, session_id) == original
    with pytest.raises(AttachmentError, match="attachment.notFound"):
        await service.read_image(attachment, session_id, current_only=True)

    sessions.move_session(session_id, project_id, None)
    with pytest.raises(AttachmentError, match="attachment.notFound"):
        await service.read_image(attachment, session_id, current_only=True)
    with pytest.raises(AttachmentError, match="attachment.notFound"):
        await service.read_image(attachment, session_id)

    sessions.move_session(session_id, None, project_id)
    assert await service.read_image(attachment, session_id) == original


async def test_validated_text_reuses_source_revision_as_processing_input(
    tmp_path: Path,
) -> None:
    session_id = uuid4()
    sessions = provisioned_storage(tmp_path, session_id)
    message = Message(id=uuid4(), session_id=session_id, role="user", content="Text")
    db = AsyncMock(spec=AsyncSession)
    db.scalar.side_effect = [chat_session(session_id), None]
    service = AttachmentService(sessions)

    attachment = await service.create_attachment(
        db,
        message,
        b"plain text",
        "text/plain",
        kind="text",
        filename="notes.txt",
    )

    assert attachment.metadata_json["companionFileId"] == str(attachment.file_id)
    assert attachment.metadata_json["companionFileRevision"] == attachment.file_revision
    assert attachment.metadata_json["companionFilePath"] == attachment.file_path
    assert service.read_derived_text(attachment, session_id) == "plain text"
    files = sessions.session_path(session_id) / "files" / "attachments" / str(attachment.id)
    assert [path.name for path in files.iterdir()] == ["notes.txt"]


@pytest.mark.parametrize("change_companion", [False, True])
async def test_model_context_rejects_stale_source_or_companion(
    tmp_path: Path, change_companion: bool
) -> None:
    session_id = uuid4()
    sessions = provisioned_storage(tmp_path, session_id)
    message = Message(id=uuid4(), session_id=session_id, role="user", content="Inspect")
    db = AsyncMock(spec=AsyncSession)
    db.scalar.side_effect = [chat_session(session_id), None]
    service = AttachmentService(sessions)
    attachment = await service.create_attachment(
        db, message, b"original text", "text/plain", kind="text", filename="notes.txt"
    )
    message.attachments = [attachment]
    root_files = RootFileService(session_id, session_storage=sessions)
    companion = root_files.create_output_file("companion.txt", b"original text")
    attachment.metadata_json = service._companion_metadata(session_id, companion)
    assert service.read_derived_text(attachment, session_id, current_only=True) == "original text"
    root_files.append_output_revision(
        companion.file_id if change_companion else attachment.file_id, b"replacement text"
    )
    assert service.read_derived_text(attachment, session_id) == "original text"
    with pytest.raises(AttachmentError, match="attachment.notFound"):
        service.read_derived_text(attachment, session_id, current_only=True)
    projected = message_dict(message, include_attachment_text=True, attachment_service=service)
    assert "original text" not in projected["content"]
    assert "replacement text" not in projected["content"]


async def test_model_audio_rejects_old_identity_but_human_read_keeps_original(tmp_path: Path) -> None:
    session_id = uuid4()
    sessions = provisioned_storage(tmp_path, session_id)
    message = Message(id=uuid4(), session_id=session_id, role="user", content="Audio")
    db = AsyncMock(spec=AsyncSession)
    db.scalar.side_effect = [chat_session(session_id), None]
    service = AttachmentService(sessions)
    original = audio_bytes()
    attachment = await service.create_audio(db, message, original, "audio/wav")
    assert await service.read_audio(attachment, session_id, current_only=True) == original
    RootFileService(session_id, session_storage=sessions).append_output_revision(
        attachment.file_id, audio_bytes(frames=160)
    )
    assert await service.read_audio(attachment, session_id) == original
    with pytest.raises(AttachmentError, match="attachment.notFound"):
        await service.read_audio(attachment, session_id, current_only=True)


async def test_processing_companion_is_unavailable_after_source_deletion(
    tmp_path: Path,
) -> None:
    session_id = uuid4()
    sessions = provisioned_storage(tmp_path, session_id)
    message = Message(id=uuid4(), session_id=session_id, role="user", content="Text")
    db = AsyncMock(spec=AsyncSession)
    db.scalar.side_effect = [chat_session(session_id), None]
    service = AttachmentService(sessions)
    attachment = await service.create_attachment(
        db, message, b"plain text", "text/plain", kind="text", filename="notes.txt"
    )
    root_files = RootFileService(session_id, session_storage=sessions)
    companion = root_files.create_output_file("companion.txt", b"plain text")
    attachment.metadata_json = {
        "companionFileRootId": str(session_id),
        "companionFileId": str(companion.file_id),
        "companionFileRevision": companion.revision,
        "companionFilePath": companion.path,
        "companionSizeBytes": companion.size_bytes,
        "companionSha256": companion.sha256,
    }

    root_files.delete_path("session", attachment.file_path, recursive=False)

    with pytest.raises(AttachmentError, match="attachment.notFound"):
        service.read_derived_text(attachment, session_id)


async def test_processing_companion_is_unavailable_after_source_corruption(
    tmp_path: Path,
) -> None:
    session_id = uuid4()
    sessions = provisioned_storage(tmp_path, session_id)
    message = Message(id=uuid4(), session_id=session_id, role="user", content="Text")
    db = AsyncMock(spec=AsyncSession)
    db.scalar.side_effect = [chat_session(session_id), None]
    service = AttachmentService(sessions)
    attachment = await service.create_attachment(
        db, message, b"plain text", "text/plain", kind="text", filename="notes.txt"
    )

    service.revision_path(
        session_id, attachment.file_root_id, attachment.file_id, attachment.file_revision
    ).write_bytes(b"altered!!!")

    with pytest.raises(AttachmentError, match="attachment.notFound"):
        service.read_derived_text(attachment, session_id)


async def test_host_image_lineage_survives_untrusted_metadata_prefix(tmp_path: Path) -> None:
    session_id = uuid4()
    sessions = provisioned_storage(tmp_path, session_id)
    message = Message(id=uuid4(), session_id=session_id, role="tool", content="Bild")
    db = AsyncMock(spec=AsyncSession)
    db.scalar.side_effect = [chat_session(session_id), None]
    source_id = uuid4()

    attachment = await AttachmentService(sessions).create_image(
        db,
        message,
        image_bytes("PNG"),
        "image/png",
        metadata={
            **{f"untrusted{index}": index for index in range(32)},
            "authorizationSourceAttachmentIds": [str(source_id)],
            "sourceAttachmentIds": [str(source_id)],
        },
        category="generated",
        purpose="artifact",
    )

    assert attachment.metadata_json["authorizationSourceAttachmentIds"] == [
        str(source_id)
    ]
    assert attachment.metadata_json["sourceAttachmentIds"] == [str(source_id)]


async def test_service_persists_audio_duration_and_safe_metadata(tmp_path: Path) -> None:
    session_id = uuid4()
    sessions = provisioned_storage(tmp_path, session_id)
    message = Message(id=uuid4(), session_id=session_id, role="tool", content="Audio")
    db = AsyncMock(spec=AsyncSession)
    db.scalar.side_effect = [chat_session(session_id), None]

    attachment = await AttachmentService(sessions).create_attachment(
        db,
        message,
        audio_bytes(),
        "audio/wav",
        kind="audio",
        filename="../aufnahme.wav",
        metadata={"created": 123, "secret": "nicht speichern"},
        category="generated",
        purpose="artifact",
    )

    assert attachment.duration_ms == 10
    assert attachment.category == "generated"
    assert attachment.file_root_id == session_id
    assert attachment.file_id is not None
    assert attachment.file_revision == 1
    assert attachment.file_path is not None
    assert not attachment.file_path.startswith("files/")
    assert attachment.metadata_json == {
        "channels": 1,
        "sampleRate": 8_000,
        "sampleWidth": 2,
        "created": 123,
        "format": "wav",
    }


async def test_service_persists_video_metadata_without_derivatives(
    tmp_path: Path, monkeypatch
) -> None:
    session_id = uuid4()
    sessions = provisioned_storage(tmp_path, session_id)
    message = Message(id=uuid4(), session_id=session_id, role="user", content="Video")
    db = AsyncMock(spec=AsyncSession)
    db.scalar.side_effect = [chat_session(session_id), None]
    monkeypatch.setattr(
        "mato_loop_backend.attachment_processing.probe_video",
        lambda _data: MediaInfo("mp4", "video/mp4", 2_500, 640, 360, 0, 1),
    )

    attachment = await AttachmentService(sessions).create_attachment(
        db,
        message,
        b"\x00\x00\x00\x18ftypisomvideo",
        "video/mp4",
        kind="video",
        filename="clip.mp4",
    )

    assert (attachment.kind, attachment.duration_ms) == ("video", 2_500)
    assert (attachment.width, attachment.height) == (640, 360)
    assert attachment.metadata_json == {"format": "mp4"}
    assert not (
        sessions.session_path(session_id)
        / "files"
        / "attachments"
        / str(attachment.id)
        / "derived"
    ).exists()


async def test_audio_read_rejects_integrity_mismatch(tmp_path: Path) -> None:
    session_id = uuid4()
    sessions = provisioned_storage(tmp_path, session_id)
    message = Message(id=uuid4(), session_id=session_id, role="user", content="Audio")
    db = AsyncMock(spec=AsyncSession)
    db.scalar.side_effect = [chat_session(session_id), None]
    service = AttachmentService(sessions)
    attachment = await service.create_audio(db, message, audio_bytes(), "audio/wav")
    attachment.sha256 = "0" * 64

    with pytest.raises(AttachmentError, match="attachment.notFound"):
        await service.read_audio(attachment, session_id)


async def test_service_enforces_session_quota_before_writing(tmp_path: Path) -> None:
    session_id = uuid4()
    sessions = provisioned_storage(tmp_path, session_id)
    message = Message(id=uuid4(), session_id=session_id, role="assistant", content="Bild")
    db = AsyncMock(spec=AsyncSession)
    db.scalar.side_effect = [chat_session(session_id)]
    quota_directory = sessions.session_path(session_id) / "files" / str(uuid4())
    quota_directory.mkdir()
    quota_file = quota_directory / "quota.bin"
    with quota_file.open("wb") as handle:
        handle.truncate(SESSION_ATTACHMENT_QUOTA_BYTES)

    with pytest.raises(AttachmentError, match="attachment.sessionQuotaExceeded"):
        await AttachmentService(sessions).create_image(db, message, image_bytes("PNG"), "image/png")
    assert list((sessions.session_path(session_id) / "files").iterdir()) == [quota_directory]


async def test_project_attachment_quota_uses_shared_managed_root(tmp_path: Path) -> None:
    session_id = uuid4()
    project_id = uuid4()
    sessions = SessionStorage(tmp_path / "sessions")
    sessions.provision(session_id, project_id)
    message = Message(id=uuid4(), session_id=session_id, role="assistant", content="Bild")
    db = AsyncMock(spec=AsyncSession)
    db.scalar.side_effect = [chat_session(session_id, project_id=project_id), None]
    shared_file = sessions.logical_root_path(session_id, project_id) / "files" / "shared.bin"
    with shared_file.open("wb") as handle:
        handle.truncate(SESSION_ATTACHMENT_QUOTA_BYTES)

    with pytest.raises(AttachmentError, match="attachment.sessionQuotaExceeded"):
        await AttachmentService(sessions).create_image(
            db, message, image_bytes("PNG"), "image/png"
        )


async def test_service_removes_file_when_database_flush_fails(tmp_path: Path) -> None:
    session_id = uuid4()
    sessions = provisioned_storage(tmp_path, session_id)
    message = Message(id=uuid4(), session_id=session_id, role="assistant", content="Bild")
    db = AsyncMock(spec=AsyncSession)
    db.scalar.side_effect = [chat_session(session_id), None]
    db.flush.side_effect = RuntimeError("database failed")

    with pytest.raises(RuntimeError, match="database failed"):
        await AttachmentService(sessions).create_image(db, message, image_bytes("PNG"), "image/png")
    assert list((sessions.session_path(session_id) / "files").iterdir()) == []


def test_images_use_the_general_file_limit() -> None:
    assert MAX_IMAGE_ATTACHMENT_BYTES == MAX_FILE_ATTACHMENT_BYTES
    buffer = BytesIO()
    Image.new("RGB", (1500, 1500), (20, 40, 60)).save(
        buffer, format="PNG", compress_level=0
    )
    data = buffer.getvalue()

    assert len(data) > 5 * 1024 * 1024
    assert validate_image(data, "image/png").size_bytes == len(data)
