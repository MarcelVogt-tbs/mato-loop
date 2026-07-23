from __future__ import annotations

from io import BytesIO
from pathlib import Path
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from PIL import Image
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.attachments import (
    MAX_ATTACHMENT_BYTES,
    SESSION_ATTACHMENT_QUOTA_BYTES,
    AttachmentError,
    AttachmentService,
    AttachmentStorage,
    AttachmentStorageError,
    validate_image,
)
from mato_loop_backend.models import Message


def image_bytes(image_format: str) -> bytes:
    buffer = BytesIO()
    mode = "RGB" if image_format == "JPEG" else "RGBA"
    Image.new(mode, (3, 2), (20, 40, 60)).save(buffer, format=image_format)
    return buffer.getvalue()


@pytest.mark.parametrize(
    ("image_format", "mime_type"),
    [
        ("PNG", "image/png"),
        ("JPEG", "image/jpeg"),
        ("WEBP", "image/webp"),
        ("GIF", "image/gif"),
    ],
)
def test_allowed_images_are_verified(image_format: str, mime_type: str) -> None:
    data = image_bytes(image_format)

    result = validate_image(data, mime_type)

    assert result.mime_type == mime_type
    assert (result.width, result.height) == (3, 2)
    assert result.size_bytes == len(data)
    assert len(result.sha256) == 64


def test_image_magic_must_match_declared_mime_type() -> None:
    with pytest.raises(AttachmentError) as exc_info:
        validate_image(image_bytes("PNG"), "image/jpeg")

    assert exc_info.value.code == "attachment.mimeMismatch"


def test_corrupt_image_with_known_magic_is_rejected() -> None:
    with pytest.raises(AttachmentError) as exc_info:
        validate_image(b"\x89PNG\r\n\x1a\ncorrupt", "image/png")

    assert exc_info.value.code == "attachment.invalidImage"


def test_attachment_size_limit_is_enforced_before_image_decode() -> None:
    with pytest.raises(AttachmentError) as exc_info:
        validate_image(b"x" * (MAX_ATTACHMENT_BYTES + 1), "image/png")

    assert exc_info.value.code == "attachment.tooLarge"


def test_storage_writes_atomically_with_restrictive_permissions(tmp_path: Path) -> None:
    storage = AttachmentStorage(tmp_path / "attachments")
    data = image_bytes("PNG")

    storage_key = storage.write(data)
    target = storage.path(storage_key)

    assert target.read_bytes() == data
    assert target.stat().st_mode & 0o777 == 0o600
    assert storage.root.stat().st_mode & 0o777 == 0o700
    assert target.parent.stat().st_mode & 0o777 == 0o700
    assert not list(storage.root.rglob("*.tmp"))


async def test_service_persists_validated_metadata_and_file(tmp_path: Path) -> None:
    session_id = uuid4()
    message = Message(id=uuid4(), session_id=session_id, role="assistant", content="Bild")
    db = AsyncMock(spec=AsyncSession)
    db.scalar.side_effect = [session_id, 0, None]
    storage = AttachmentStorage(tmp_path / "attachments")
    service = AttachmentService(storage)
    data = image_bytes("PNG")

    attachment = await service.create_image(
        db,
        message,
        data,
        "image/png",
        filename="../diagram.png",
    )

    assert attachment.message_id == message.id
    assert attachment.position == 0
    assert attachment.kind == "image"
    assert attachment.filename == "diagram.png"
    assert (attachment.width, attachment.height) == (3, 2)
    assert storage.path(attachment.storage_key).read_bytes() == data
    db.add.assert_called_once_with(attachment)
    db.flush.assert_awaited_once()


async def test_service_enforces_session_quota_before_writing(tmp_path: Path) -> None:
    session_id = uuid4()
    message = Message(id=uuid4(), session_id=session_id, role="assistant", content="Bild")
    db = AsyncMock(spec=AsyncSession)
    db.scalar.side_effect = [session_id, SESSION_ATTACHMENT_QUOTA_BYTES]
    storage = AttachmentStorage(tmp_path / "attachments")

    with pytest.raises(AttachmentError) as exc_info:
        await AttachmentService(storage).create_image(
            db, message, image_bytes("PNG"), "image/png"
        )

    assert exc_info.value.code == "attachment.sessionQuotaExceeded"
    assert not storage.root.exists()
    db.add.assert_not_called()


async def test_service_removes_file_when_database_flush_fails(tmp_path: Path) -> None:
    session_id = uuid4()
    message = Message(id=uuid4(), session_id=session_id, role="assistant", content="Bild")
    db = AsyncMock(spec=AsyncSession)
    db.scalar.side_effect = [session_id, 0, None]
    db.flush.side_effect = RuntimeError("database failed")
    storage = AttachmentStorage(tmp_path / "attachments")

    with pytest.raises(RuntimeError, match="database failed"):
        await AttachmentService(storage).create_image(
            db, message, image_bytes("PNG"), "image/png"
        )

    assert not list(storage.root.rglob("*.*"))


async def test_reconcile_removes_only_unreferenced_files(tmp_path: Path) -> None:
    storage = AttachmentStorage(tmp_path / "attachments")
    known = storage.write(image_bytes("PNG"))
    orphan = storage.write(image_bytes("JPEG"))
    result = Mock()
    result.all.return_value = [known]
    db = AsyncMock(spec=AsyncSession)
    db.scalars.return_value = result

    await AttachmentService(storage).reconcile(db, orphan_grace_seconds=0)

    assert storage.path(known).is_file()
    with pytest.raises(AttachmentStorageError):
        storage.path(orphan)
