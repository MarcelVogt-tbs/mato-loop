"""Validierung und persistenter Storage fuer Message-Bildattachments.

Der Service committet keine Transaktion. Schlaegt ein Commit nach erfolgreichem
``create_image`` fehl, muss der Aufrufer ``discard`` mit dem zurueckgegebenen
Attachment aufrufen. Ein Prozessabbruch zwischen Dateischreibvorgang und
Datenbank-Commit kann eine verwaiste Datei hinterlassen; Storage-Keys sind
zufaellig und werden weder in API-Payloads noch in Logs ausgegeben.
Das Datenbank-CASCADE entfernt nur Metadaten. Message- oder Session-Loeschungen
ausserhalb des Session-Routers muessen die Storage-Keys vorab erfassen und nach
dem Commit ueber ``delete_files`` entfernen.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
import stat
import time
import warnings
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from uuid import uuid4

from PIL import Image
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.config import get_runtime_config
from mato_loop_backend.models import ChatSession, Message, MessageAttachment

MAX_ATTACHMENT_BYTES = 5 * 1024 * 1024
SESSION_ATTACHMENT_QUOTA_BYTES = 100 * 1024 * 1024

logger = logging.getLogger(__name__)

_STORAGE_KEY_PATTERN = re.compile(r"^[0-9a-f]{2}/[0-9a-f]{32}$")
_FORMAT_MIME_TYPES = {
    "PNG": "image/png",
    "JPEG": "image/jpeg",
    "WEBP": "image/webp",
    "GIF": "image/gif",
}


class AttachmentError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class AttachmentStorageError(Exception):
    pass


@dataclass(frozen=True)
class ValidatedImage:
    mime_type: str
    width: int
    height: int
    size_bytes: int
    sha256: str


def _magic_mime_type(data: bytes) -> str | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    return None


def validate_image(data: bytes, mime_type: str) -> ValidatedImage:
    if not data:
        raise AttachmentError("attachment.empty")
    if len(data) > MAX_ATTACHMENT_BYTES:
        raise AttachmentError("attachment.tooLarge")

    claimed_mime_type = mime_type.strip().lower()
    magic_mime_type = _magic_mime_type(data)
    if magic_mime_type is None or claimed_mime_type not in _FORMAT_MIME_TYPES.values():
        raise AttachmentError("attachment.unsupportedType")
    if claimed_mime_type != magic_mime_type:
        raise AttachmentError("attachment.mimeMismatch")

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(data)) as image:
                pillow_mime_type = _FORMAT_MIME_TYPES.get(image.format or "")
                width, height = image.size
                image.verify()
            with Image.open(BytesIO(data)) as image:
                image.load()
    except (
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
        OSError,
        SyntaxError,
        ValueError,
    ) as exc:
        raise AttachmentError("attachment.invalidImage") from exc

    if pillow_mime_type != magic_mime_type or width <= 0 or height <= 0:
        raise AttachmentError("attachment.invalidImage")
    return ValidatedImage(
        mime_type=magic_mime_type,
        width=width,
        height=height,
        size_bytes=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
    )


def _safe_filename(filename: str | None) -> str | None:
    if filename is None:
        return None
    value = filename.replace("\\", "/").rsplit("/", 1)[-1].strip()
    value = "".join(character for character in value if ord(character) >= 32 and ord(character) != 127)
    return value[:255] or None


class AttachmentStorage:
    def __init__(self, root: Path | None = None) -> None:
        self.root = (root or get_runtime_config().attachments_dir).absolute()

    def _prepare_directory(self, directory: Path) -> None:
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        if directory.is_symlink() or not directory.is_dir():
            raise AttachmentStorageError("Ungueltiges Attachment-Storage-Verzeichnis")
        directory.chmod(0o700)

    def _path(self, storage_key: str) -> Path:
        if not _STORAGE_KEY_PATTERN.fullmatch(storage_key):
            raise AttachmentStorageError("Ungueltiger Attachment-Storage-Key")
        return self.root / storage_key

    def write(self, data: bytes) -> str:
        storage_id = uuid4().hex
        storage_key = f"{storage_id[:2]}/{storage_id}"
        target = self._path(storage_key)
        self._prepare_directory(self.root)
        self._prepare_directory(target.parent)
        temporary = target.parent / f".{storage_id}.{uuid4().hex}.tmp"
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(temporary, flags, 0o600)
        try:
            with os.fdopen(descriptor, "wb") as file:
                file.write(data)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, target)
            target.chmod(0o600)
            directory_descriptor = os.open(target.parent, os.O_RDONLY)
            try:
                os.fsync(directory_descriptor)
            finally:
                os.close(directory_descriptor)
        except Exception:
            temporary.unlink(missing_ok=True)
            target.unlink(missing_ok=True)
            raise
        return storage_key

    def path(self, storage_key: str) -> Path:
        target = self._path(storage_key)
        if self.root.is_symlink() or not self.root.is_dir():
            raise AttachmentStorageError("Ungueltiges Attachment-Storage-Verzeichnis")
        try:
            target.resolve(strict=True).relative_to(self.root.resolve(strict=True))
        except (FileNotFoundError, ValueError) as exc:
            raise AttachmentStorageError("Attachment-Datei nicht gefunden") from exc
        if target.is_symlink() or not target.is_file() or not stat.S_ISREG(target.stat().st_mode):
            raise AttachmentStorageError("Ungueltige Attachment-Datei")
        return target

    def delete(self, storage_key: str) -> None:
        target = self._path(storage_key)
        if not target.exists() and not target.is_symlink():
            return
        self.path(storage_key).unlink()
        try:
            target.parent.rmdir()
        except OSError:
            pass

    def keys(self, *, older_than_seconds: float = 0) -> set[str]:
        if not self.root.exists():
            return set()
        if self.root.is_symlink() or not self.root.is_dir():
            raise AttachmentStorageError("Ungueltiges Attachment-Storage-Verzeichnis")
        keys: set[str] = set()
        for directory in self.root.iterdir():
            if directory.is_symlink() or not directory.is_dir():
                continue
            for item in directory.iterdir():
                key = f"{directory.name}/{item.name}"
                old_enough = time.time() - item.stat().st_mtime >= older_than_seconds
                if (
                    _STORAGE_KEY_PATTERN.fullmatch(key)
                    and item.is_file()
                    and not item.is_symlink()
                    and old_enough
                ):
                    keys.add(key)
        return keys


class AttachmentService:
    def __init__(self, storage: AttachmentStorage | None = None) -> None:
        self.storage = storage or AttachmentStorage()

    async def create_image(
        self,
        db: AsyncSession,
        message: Message,
        data: bytes,
        mime_type: str,
        *,
        filename: str | None = None,
        position: int | None = None,
    ) -> MessageAttachment:
        """Validiert und persistiert ein Bild fuer eine bereits geflushte Message."""

        validated = validate_image(data, mime_type)
        locked_session_id = await db.scalar(
            select(ChatSession.id)
            .join(Message, Message.session_id == ChatSession.id)
            .where(ChatSession.id == message.session_id, Message.id == message.id)
            .with_for_update()
        )
        if locked_session_id is None:
            raise AttachmentError("attachment.messageNotFound")

        used_bytes = await db.scalar(
            select(func.coalesce(func.sum(MessageAttachment.size_bytes), 0))
            .join(Message, Message.id == MessageAttachment.message_id)
            .where(Message.session_id == message.session_id)
        )
        if int(used_bytes or 0) + validated.size_bytes > SESSION_ATTACHMENT_QUOTA_BYTES:
            raise AttachmentError("attachment.sessionQuotaExceeded")

        if position is None:
            maximum_position = await db.scalar(
                select(func.max(MessageAttachment.position)).where(
                    MessageAttachment.message_id == message.id
                )
            )
            position = int(maximum_position) + 1 if maximum_position is not None else 0
        if isinstance(position, bool) or position < 0:
            raise AttachmentError("attachment.invalidPosition")

        write_task = asyncio.create_task(asyncio.to_thread(self.storage.write, data))
        try:
            storage_key = await asyncio.shield(write_task)
        except asyncio.CancelledError:
            storage_key = await write_task
            await asyncio.to_thread(self.storage.delete, storage_key)
            raise
        attachment = MessageAttachment(
            message_id=message.id,
            position=position,
            kind="image",
            mime_type=validated.mime_type,
            size_bytes=validated.size_bytes,
            sha256=validated.sha256,
            storage_key=storage_key,
            filename=_safe_filename(filename),
            width=validated.width,
            height=validated.height,
        )
        db.add(attachment)
        try:
            await db.flush()
        except BaseException:
            await asyncio.to_thread(self.storage.delete, storage_key)
            raise
        return attachment

    async def discard(self, attachment: MessageAttachment) -> None:
        """Entfernt die Datei nach einem spaeteren Transaktions-Rollback."""

        await asyncio.to_thread(self.storage.delete, attachment.storage_key)

    async def delete_files(self, storage_keys: list[str]) -> None:
        for storage_key in storage_keys:
            try:
                await asyncio.to_thread(self.storage.delete, storage_key)
            except Exception:
                logger.exception("Attachment-Datei konnte nicht entfernt werden")

    async def reconcile(self, db: AsyncSession, *, orphan_grace_seconds: float = 86_400) -> None:
        known_keys = set((await db.scalars(select(MessageAttachment.storage_key))).all())
        stored_keys = await asyncio.to_thread(
            self.storage.keys, older_than_seconds=orphan_grace_seconds
        )
        await self.delete_files(sorted(stored_keys - known_keys))
