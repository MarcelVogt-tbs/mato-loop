from __future__ import annotations

import stat
import wave
from io import BytesIO
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from PIL import Image
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.attachments import (
    MAX_FILE_ATTACHMENT_BYTES,
    MAX_IMAGE_ATTACHMENT_BYTES,
    SESSION_ATTACHMENT_QUOTA_BYTES,
    AttachmentError,
    AttachmentService,
    AttachmentStorage,
    AttachmentStorageError,
    classify_attachment,
    validate_audio,
    validate_image,
)
from mato_loop_backend.models import Message
from mato_loop_backend.session_storage import SessionStorage
from mato_loop_backend.video_processing import MediaInfo


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
    return sessions, AttachmentStorage(sessions)


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


@pytest.mark.parametrize(
    ("category", "directory"),
    [
        ("user_upload", "attachments"),
        ("generated", "generated"),
        ("downloaded", "downloads"),
    ],
)
def test_storage_writes_flat_paths_atomically_with_restrictive_permissions(
    tmp_path: Path, category: str, directory: str
) -> None:
    session_id = uuid4()
    sessions, storage = provisioned_storage(tmp_path, session_id)
    attachment_id = uuid4()
    data = image_bytes("PNG")

    storage_path = storage.write(session_id, attachment_id, data, "bild.png", category)
    target = storage.path(session_id, storage_path)

    assert storage_path == f"{directory}/{attachment_id}/bild.png"
    assert target.read_bytes() == data
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert stat.S_IMODE(target.parent.stat().st_mode) == 0o700
    assert not list(sessions.sessions_dir.rglob("*.tmp"))


def test_storage_rejects_cross_session_paths_and_symlinks(tmp_path: Path) -> None:
    session_id = uuid4()
    other_id = uuid4()
    sessions, storage = provisioned_storage(tmp_path, session_id)
    sessions.provision(other_id)
    storage_path = storage.write(session_id, uuid4(), b"data", "file.bin", "generated")

    with pytest.raises(AttachmentStorageError):
        storage.path(other_id, storage_path)
    target = storage.path(session_id, storage_path)
    target.unlink()
    target.symlink_to(tmp_path / "outside")
    with pytest.raises(AttachmentStorageError):
        storage.path(session_id, storage_path)


async def test_service_persists_validated_metadata_and_relative_path(tmp_path: Path) -> None:
    session_id = uuid4()
    _sessions, storage = provisioned_storage(tmp_path, session_id)
    message = Message(id=uuid4(), session_id=session_id, role="assistant", content="Bild")
    db = AsyncMock(spec=AsyncSession)
    db.scalar.side_effect = [session_id, None]
    data = image_bytes("PNG")

    attachment = await AttachmentService(storage).create_image(
        db, message, data, "image/png", filename="../diagram.png"
    )

    assert attachment.kind == "image"
    assert attachment.category == "user_upload"
    assert attachment.purpose == "attachment"
    assert attachment.processing_status == "ready"
    assert attachment.filename == "diagram.png"
    assert attachment.storage_path.startswith("attachments/")
    assert storage.path(session_id, attachment.storage_path).read_bytes() == data


async def test_service_validates_and_persists_artifact_purpose(tmp_path: Path) -> None:
    session_id = uuid4()
    sessions, storage = provisioned_storage(tmp_path, session_id)
    message = Message(id=uuid4(), session_id=session_id, role="tool", content="Datei")
    db = AsyncMock(spec=AsyncSession)
    db.scalar.return_value = session_id

    attachment = await AttachmentService(storage).create_attachment(
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
    assert storage.path(session_id, attachment.storage_path).read_bytes() == b"\x00\x01tool-output"
    with pytest.raises(AttachmentError, match="attachment.invalidPurpose"):
        await AttachmentService(storage).create_attachment(
            db,
            message,
            b"data",
            "application/octet-stream",
            kind="file",
            purpose="other",  # type: ignore[arg-type]
        )
    assert len(list((sessions.session_path(session_id) / "generated").iterdir())) == 1


async def test_image_read_revalidates_persisted_content(tmp_path: Path) -> None:
    session_id = uuid4()
    _sessions, storage = provisioned_storage(tmp_path, session_id)
    message = Message(id=uuid4(), session_id=session_id, role="user", content="Bild")
    db = AsyncMock(spec=AsyncSession)
    db.scalar.side_effect = [session_id, None]
    service = AttachmentService(storage)
    data = image_bytes("PNG")
    attachment = await service.create_image(db, message, data, "image/png")

    assert await service.read_image(attachment, session_id) == data

    attachment.sha256 = "0" * 64
    with pytest.raises(AttachmentError, match="attachment.notFound"):
        await service.read_image(attachment, session_id)


async def test_service_persists_audio_duration_and_safe_metadata(tmp_path: Path) -> None:
    session_id = uuid4()
    _sessions, storage = provisioned_storage(tmp_path, session_id)
    message = Message(id=uuid4(), session_id=session_id, role="tool", content="Audio")
    db = AsyncMock(spec=AsyncSession)
    db.scalar.side_effect = [session_id, None]

    attachment = await AttachmentService(storage).create_attachment(
        db,
        message,
        audio_bytes(),
        "audio/wav",
        kind="audio",
        filename="../aufnahme.wav",
        metadata={"created": 123, "secret": "nicht speichern"},
        category="generated",
    )

    assert attachment.duration_ms == 10
    assert attachment.category == "generated"
    assert attachment.storage_path.startswith("generated/")
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
    sessions, storage = provisioned_storage(tmp_path, session_id)
    message = Message(id=uuid4(), session_id=session_id, role="user", content="Video")
    db = AsyncMock(spec=AsyncSession)
    db.scalar.side_effect = [session_id, None]
    monkeypatch.setattr(
        "mato_loop_backend.attachment_processing.probe_video",
        lambda _data: MediaInfo("mp4", "video/mp4", 2_500, 640, 360, 0, 1),
    )

    attachment = await AttachmentService(storage).create_attachment(
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
    assert list((sessions.session_path(session_id) / "derived").iterdir()) == []


async def test_audio_read_rejects_integrity_mismatch(tmp_path: Path) -> None:
    session_id = uuid4()
    _sessions, storage = provisioned_storage(tmp_path, session_id)
    message = Message(id=uuid4(), session_id=session_id, role="user", content="Audio")
    db = AsyncMock(spec=AsyncSession)
    db.scalar.side_effect = [session_id, None]
    service = AttachmentService(storage)
    attachment = await service.create_audio(db, message, audio_bytes(), "audio/wav")
    attachment.sha256 = "0" * 64

    with pytest.raises(AttachmentError, match="attachment.notFound"):
        await service.read_audio(attachment, session_id)


async def test_service_enforces_session_quota_before_writing(tmp_path: Path) -> None:
    session_id = uuid4()
    sessions, storage = provisioned_storage(tmp_path, session_id)
    message = Message(id=uuid4(), session_id=session_id, role="assistant", content="Bild")
    db = AsyncMock(spec=AsyncSession)
    db.scalar.side_effect = [session_id]
    quota_file = sessions.session_path(session_id) / "files" / "quota.bin"
    with quota_file.open("wb") as handle:
        handle.truncate(SESSION_ATTACHMENT_QUOTA_BYTES)

    with pytest.raises(AttachmentError, match="attachment.sessionQuotaExceeded"):
        await AttachmentService(storage).create_image(db, message, image_bytes("PNG"), "image/png")
    assert list((sessions.session_path(session_id) / "attachments").iterdir()) == []


async def test_service_removes_file_when_database_flush_fails(tmp_path: Path) -> None:
    session_id = uuid4()
    sessions, storage = provisioned_storage(tmp_path, session_id)
    message = Message(id=uuid4(), session_id=session_id, role="assistant", content="Bild")
    db = AsyncMock(spec=AsyncSession)
    db.scalar.side_effect = [session_id, None]
    db.flush.side_effect = RuntimeError("database failed")

    with pytest.raises(RuntimeError, match="database failed"):
        await AttachmentService(storage).create_image(db, message, image_bytes("PNG"), "image/png")
    assert list((sessions.session_path(session_id) / "attachments").iterdir()) == []


def test_images_use_the_general_file_limit() -> None:
    assert MAX_IMAGE_ATTACHMENT_BYTES == MAX_FILE_ATTACHMENT_BYTES
    buffer = BytesIO()
    Image.new("RGB", (1500, 1500), (20, 40, 60)).save(
        buffer, format="PNG", compress_level=0
    )
    data = buffer.getvalue()

    assert len(data) > 5 * 1024 * 1024
    assert validate_image(data, "image/png").size_bytes == len(data)
