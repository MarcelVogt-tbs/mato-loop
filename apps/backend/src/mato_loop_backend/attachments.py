"""Validation and persistent storage for general session attachments."""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
import shutil
import stat
import unicodedata
import warnings
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path, PurePosixPath
from typing import Literal
from uuid import UUID, uuid4

from PIL import Image
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.attachment_processing import (
    AttachmentProcessingError,
    AttachmentProcessingLimits,
    audio_mime_type,
    claimed_audio_format,
    claimed_document_format,
    derived_metadata,
    document_mime_type,
    process_attachment,
    signed_audio_format,
    signed_document_format,
    validate_pcm_wav,
)
from mato_loop_backend.models import ChatSession, Message, MessageAttachment
from mato_loop_backend.session_storage import SessionStorage, SessionStorageError
from mato_loop_backend.settings import FileMediaSettings
from mato_loop_backend.video_processing import (
    VideoProcessingError,
    probe_media,
    video_container_candidate,
)
from mato_loop_backend.workspace import RootFileError, RootFileService

MAX_FILE_ATTACHMENT_BYTES = 64 * 1024 * 1024
ABSOLUTE_MAX_FILE_ATTACHMENT_BYTES = 256 * 1024 * 1024
MAX_IMAGE_ATTACHMENT_BYTES = MAX_FILE_ATTACHMENT_BYTES
MAX_AUDIO_ATTACHMENT_BYTES = MAX_FILE_ATTACHMENT_BYTES
MAX_ATTACHMENTS_PER_MESSAGE = 8
SESSION_ATTACHMENT_QUOTA_BYTES = 500 * 1024 * 1024

_FORMAT_MIME_TYPES = {
    "PNG": "image/png",
    "JPEG": "image/jpeg",
    "MPO": "image/jpeg",
    "WEBP": "image/webp",
    "GIF": "image/gif",
}
_TEXT_EXTENSIONS = {
    ".c",
    ".cc",
    ".conf",
    ".cpp",
    ".css",
    ".csv",
    ".go",
    ".h",
    ".hpp",
    ".html",
    ".ini",
    ".java",
    ".js",
    ".json",
    ".jsx",
    ".log",
    ".md",
    ".php",
    ".properties",
    ".py",
    ".rb",
    ".rs",
    ".sh",
    ".sql",
    ".svg",
    ".toml",
    ".ts",
    ".tsx",
    ".txt",
    ".vue",
    ".xml",
    ".yaml",
    ".yml",
}
_CATEGORY_DIRECTORIES = {
    "user_upload": "attachments",
    "generated": "generated",
    "downloaded": "downloads",
}
_DERIVED_FILENAMES = {"audio.wav", "content.txt", "diagram.svg"}
_STORAGE_PATH_PATTERN = re.compile(
    r"^(attachments|generated|downloads)/"
    r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/([^/]+)$"
)


class AttachmentError(Exception):
    def __init__(self, code: str, *, diagnostic: str | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.diagnostic = diagnostic


class AttachmentStorageError(Exception):
    pass


@dataclass(frozen=True)
class ValidatedImage:
    mime_type: str
    width: int
    height: int
    size_bytes: int
    sha256: str


@dataclass(frozen=True)
class ValidatedAudio:
    mime_type: str
    duration_ms: int
    channels: int
    sample_rate: int
    sample_width: int
    frames: int
    size_bytes: int
    sha256: str


@dataclass(frozen=True)
class AttachmentInput:
    data: bytes
    mime_type: str
    filename: str | None = None
    kind: str = "file"
    metadata: dict | None = None


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


def image_mime_type(data: bytes) -> str | None:
    return _magic_mime_type(data)


def validate_image(
    data: bytes, mime_type: str, *, max_bytes: int = MAX_IMAGE_ATTACHMENT_BYTES
) -> ValidatedImage:
    if not data:
        raise AttachmentError("attachment.empty")
    if len(data) > max_bytes:
        raise AttachmentError("attachment.tooLarge")
    claimed = mime_type.strip().lower()
    magic = _magic_mime_type(data)
    if magic is None or claimed not in _FORMAT_MIME_TYPES.values():
        raise AttachmentError("attachment.unsupportedType")
    if claimed != magic:
        raise AttachmentError("attachment.mimeMismatch")
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        try:
            with Image.open(BytesIO(data)) as image:
                pillow_mime = _FORMAT_MIME_TYPES.get(image.format or "")
                width, height = image.size
                image.verify()
        except (
            Image.DecompressionBombError,
            Image.DecompressionBombWarning,
            OSError,
            SyntaxError,
            ValueError,
        ) as exc:
            raise AttachmentError(
                "attachment.invalidImage",
                diagnostic=f"verify:{type(exc).__name__}:{str(exc)[:200]}",
            ) from exc
        try:
            with Image.open(BytesIO(data)) as image:
                image.load()
        except (
            Image.DecompressionBombError,
            Image.DecompressionBombWarning,
            OSError,
            SyntaxError,
            ValueError,
        ) as exc:
            raise AttachmentError(
                "attachment.invalidImage",
                diagnostic=f"decode:{type(exc).__name__}:{str(exc)[:200]}",
            ) from exc
    if pillow_mime != magic or width <= 0 or height <= 0:
        raise AttachmentError("attachment.invalidImage", diagnostic="metadata")
    return ValidatedImage(magic, width, height, len(data), hashlib.sha256(data).hexdigest())


def validate_audio(
    data: bytes,
    mime_type: str,
    *,
    max_bytes: int = MAX_AUDIO_ATTACHMENT_BYTES,
    limits: AttachmentProcessingLimits | None = None,
) -> ValidatedAudio:
    if not data:
        raise AttachmentError("attachment.empty")
    if len(data) > max_bytes:
        raise AttachmentError("attachment.tooLarge")
    if mime_type.strip().lower() not in {"audio/wav", "audio/x-wav", "audio/wave"}:
        raise AttachmentError("attachment.unsupportedType")
    try:
        info = validate_pcm_wav(data, limits=limits)
    except AttachmentProcessingError as exc:
        raise AttachmentError(exc.code) from exc
    return ValidatedAudio(
        "audio/wav",
        info.duration_ms,
        info.channels,
        info.sample_rate,
        info.sample_width,
        info.frames,
        len(data),
        hashlib.sha256(data).hexdigest(),
    )


def safe_filename(filename: str | None, *, fallback: str = "attachment") -> str:
    value = unicodedata.normalize("NFKC", filename or "")
    value = value.replace("\\", "/").rsplit("/", 1)[-1]
    value = "".join(character for character in value if ord(character) >= 32 and ord(character) != 127)
    value = re.sub(r"[<>:\"|?*]", "_", value).strip(" .")
    if value in {"", ".", ".."}:
        value = fallback
    value = value[:255].rstrip(" .")
    while len(value.encode("utf-8")) > 240:
        value = value[:-1]
    return value or fallback


def _safe_metadata(metadata: dict | None) -> dict:
    if not metadata:
        return {}
    safe = {}
    for key, value in metadata.items():
        if key not in {
            "channels",
            "created",
            "background",
            "modelId",
            "modelName",
            "providerAdapter",
            "providerName",
            "quality",
            "sampleRate",
            "sampleWidth",
            "size",
            "authorizationSourceAttachmentIds",
            "sourceAttachmentIds",
            "sourceSha256",
            "targetTrustLevel",
        }:
            continue
        if isinstance(value, str):
            safe[key] = value[:500]
        elif key in {
            "authorizationSourceAttachmentIds",
            "sourceAttachmentIds",
        } and isinstance(value, list):
            limit = 100 if key == "authorizationSourceAttachmentIds" else 5
            safe[key] = [item[:100] for item in value[:limit] if isinstance(item, str)]
        elif isinstance(value, (bool, int, float)) or value is None:
            safe[key] = value
        if len(safe) == 32:
            break
    return safe


def processing_limits(settings: FileMediaSettings) -> AttachmentProcessingLimits:
    return AttachmentProcessingLimits(
        max_media_bytes=settings.max_upload_bytes,
        max_extracted_characters=settings.max_extracted_characters,
        max_pdf_pages=settings.max_pdf_pages,
        max_workbook_sheets=settings.max_workbook_sheets,
        max_workbook_cells=settings.max_workbook_cells,
        max_presentation_slides=settings.max_presentation_slides,
        max_zip_entries=settings.max_archive_entries,
        max_zip_uncompressed_bytes=settings.max_archive_uncompressed_bytes,
        max_zip_entry_bytes=settings.max_archive_entry_bytes,
        max_zip_compression_ratio=settings.max_archive_compression_ratio,
        max_audio_duration_seconds=settings.max_audio_duration_seconds,
        max_video_duration_seconds=settings.max_video_duration_seconds,
    )


def classify_attachment(
    data: bytes,
    browser_mime_type: str,
    filename: str | None,
    *,
    max_bytes: int = MAX_FILE_ATTACHMENT_BYTES,
    limits: AttachmentProcessingLimits | None = None,
) -> AttachmentInput:
    if not data:
        raise AttachmentError("attachment.empty")
    if len(data) > max_bytes:
        raise AttachmentError("attachment.tooLarge")
    cleaned_name = safe_filename(filename)
    image_mime = _magic_mime_type(data)
    if image_mime is not None:
        validate_image(data, image_mime, max_bytes=max_bytes)
        return AttachmentInput(data, image_mime, cleaned_name, "image")
    claimed = browser_mime_type.split(";", 1)[0].strip().lower()
    suffix = Path(cleaned_name).suffix.lower()
    video_candidate = video_container_candidate(data, claimed, suffix)
    if video_candidate:
        try:
            media_info = (
                probe_media(data)
                if limits is None and max_bytes == MAX_FILE_ATTACHMENT_BYTES
                else probe_media(
                    data,
                    max_bytes=max_bytes,
                    max_duration_seconds=(
                        limits.max_video_duration_seconds
                        if limits is not None
                        else 15 * 60
                    ),
                )
            )
        except VideoProcessingError as exc:
            raise AttachmentError(exc.code) from exc
        if media_info.has_video:
            return AttachmentInput(data, media_info.mime_type, cleaned_name, "video")
    signed_audio = signed_audio_format(data)
    audio_format = signed_audio or claimed_audio_format(claimed, suffix)
    if video_candidate and media_info.has_audio and audio_format is None:
        audio_format = "m4a" if media_info.format == "mp4" else "webm"
    if audio_format is not None:
        return AttachmentInput(
            data, audio_mime_type(audio_format), cleaned_name, "audio"
        )
    signed_document = signed_document_format(data, limits=limits)
    document_format = signed_document or claimed_document_format(claimed, suffix)
    if document_format is not None:
        return AttachmentInput(
            data, document_mime_type(document_format), cleaned_name, "document"
        )
    is_text_candidate = claimed.startswith("text/") or suffix in _TEXT_EXTENSIONS
    if is_text_candidate:
        try:
            data.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise AttachmentError("attachment.invalidText") from exc
        mime_type = claimed if claimed.startswith("text/") else "text/plain"
        return AttachmentInput(data, mime_type, cleaned_name, "text")
    return AttachmentInput(data, "application/octet-stream", cleaned_name, "file")


class AttachmentStorage:
    def __init__(self, session_storage: SessionStorage | Path | None = None) -> None:
        self.sessions = (
            SessionStorage(session_storage)
            if isinstance(session_storage, Path)
            else session_storage or SessionStorage()
        )

    @staticmethod
    def _relative_path(
        attachment_id: UUID, filename: str, category: str
    ) -> str:
        directory = _CATEGORY_DIRECTORIES.get(category)
        if directory is None:
            raise AttachmentStorageError("Invalid attachment category")
        return f"{directory}/{attachment_id}/{filename}"

    def _path(self, session_id: UUID, storage_path: str, *, require_exists: bool) -> Path:
        match = _STORAGE_PATH_PATTERN.fullmatch(storage_path)
        if (
            match is None
            or PurePosixPath(storage_path).is_absolute()
            or match.group(3) != safe_filename(match.group(3))
        ):
            raise AttachmentStorageError("Invalid attachment path")
        try:
            UUID(match.group(2))
            root = self.sessions.session_path(session_id, require_exists=True)
        except (SessionStorageError, ValueError) as exc:
            raise AttachmentStorageError("Invalid session storage") from exc
        target = root.joinpath(*PurePosixPath(storage_path).parts)
        if require_exists:
            try:
                parent_metadata = target.parent.lstat()
                metadata = target.lstat()
            except FileNotFoundError as exc:
                raise AttachmentStorageError("Attachment file not found") from exc
            if (
                stat.S_ISLNK(parent_metadata.st_mode)
                or not stat.S_ISDIR(parent_metadata.st_mode)
                or stat.S_ISLNK(metadata.st_mode)
                or not stat.S_ISREG(metadata.st_mode)
            ):
                raise AttachmentStorageError("Invalid attachment file")
        return target

    def write(
        self,
        session_id: UUID,
        attachment_id: UUID,
        data: bytes,
        filename: str,
        category: str,
    ) -> str:
        storage_path = self._relative_path(attachment_id, safe_filename(filename), category)
        target = self._path(session_id, storage_path, require_exists=False)
        try:
            target.parent.mkdir(mode=0o700)
            parent_metadata = target.parent.lstat()
        except OSError as exc:
            raise AttachmentStorageError("Attachment directory could not be created") from exc
        if stat.S_ISLNK(parent_metadata.st_mode) or not stat.S_ISDIR(parent_metadata.st_mode):
            raise AttachmentStorageError("Invalid attachment directory")
        target.parent.chmod(0o700)
        temporary = target.parent / f".{uuid4().hex}.tmp"
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(temporary, flags, 0o600)
        try:
            with os.fdopen(descriptor, "wb") as file:
                file.write(data)
                file.flush()
                os.fsync(file.fileno())
            if target.exists() or target.is_symlink():
                raise AttachmentStorageError("Attachment file already exists")
            os.replace(temporary, target)
            target.chmod(0o600)
            directory_descriptor = os.open(target.parent, os.O_RDONLY)
            try:
                os.fsync(directory_descriptor)
            finally:
                os.close(directory_descriptor)
        except BaseException:
            temporary.unlink(missing_ok=True)
            target.unlink(missing_ok=True)
            try:
                target.parent.rmdir()
            except OSError:
                pass
            raise
        return storage_path

    def path(self, session_id: UUID, storage_path: str) -> Path:
        return self._path(session_id, storage_path, require_exists=True)

    def delete(self, session_id: UUID, storage_path: str) -> None:
        try:
            target = self.path(session_id, storage_path)
        except AttachmentStorageError:
            return
        target.unlink()
        try:
            target.parent.rmdir()
        except OSError:
            pass

    def _derived_root(self, session_id: UUID) -> Path:
        try:
            root = self.sessions.session_path(session_id, require_exists=True) / "derived"
            metadata = root.lstat()
        except (OSError, SessionStorageError) as exc:
            raise AttachmentStorageError("Invalid derived storage") from exc
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            raise AttachmentStorageError("Invalid derived storage")
        return root

    def write_derived(
        self, session_id: UUID, attachment_id: UUID, files: dict[str, bytes]
    ) -> dict[str, str]:
        if not files:
            return {}
        if set(files) - _DERIVED_FILENAMES:
            raise AttachmentStorageError("Invalid derived filename")
        root = self._derived_root(session_id)
        target = root / str(attachment_id)
        temporary = root / f".{attachment_id}.{uuid4().hex}.tmp"
        if target.exists() or target.is_symlink():
            raise AttachmentStorageError("Derived directory already exists")
        try:
            temporary.mkdir(mode=0o700)
            for name, data in files.items():
                path = temporary / name
                flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
                descriptor = os.open(path, flags, 0o600)
                with os.fdopen(descriptor, "wb") as file:
                    file.write(data)
                    file.flush()
                    os.fsync(file.fileno())
                path.chmod(0o600)
            os.replace(temporary, target)
            target.chmod(0o700)
            directory_descriptor = os.open(root, os.O_RDONLY)
            try:
                os.fsync(directory_descriptor)
            finally:
                os.close(directory_descriptor)
        except BaseException:
            if temporary.exists() and not temporary.is_symlink():
                shutil.rmtree(temporary)
            if target.exists() and not target.is_symlink():
                shutil.rmtree(target)
            raise
        return {
            name: f"derived/{attachment_id}/{name}" for name in files
        }

    def derived_path(
        self, session_id: UUID, attachment_id: UUID, derived_path: str
    ) -> Path:
        expected_prefix = f"derived/{attachment_id}/"
        if not derived_path.startswith(expected_prefix):
            raise AttachmentStorageError("Invalid derived path")
        name = derived_path.removeprefix(expected_prefix)
        if name not in _DERIVED_FILENAMES:
            raise AttachmentStorageError("Invalid derived path")
        target = self._derived_root(session_id) / str(attachment_id) / name
        try:
            parent_metadata = target.parent.lstat()
            metadata = target.lstat()
        except OSError as exc:
            raise AttachmentStorageError("Derived file not found") from exc
        if (
            stat.S_ISLNK(parent_metadata.st_mode)
            or not stat.S_ISDIR(parent_metadata.st_mode)
            or stat.S_ISLNK(metadata.st_mode)
            or not stat.S_ISREG(metadata.st_mode)
        ):
            raise AttachmentStorageError("Invalid derived file")
        return target

    def delete_derived(self, session_id: UUID, attachment_id: UUID) -> None:
        try:
            target = self._derived_root(session_id) / str(attachment_id)
            metadata = target.lstat()
        except (AttachmentStorageError, FileNotFoundError):
            return
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            raise AttachmentStorageError("Invalid derived directory")
        shutil.rmtree(target)

    def derived_usage(self, session_id: UUID) -> int:
        total = 0
        root = self._derived_root(session_id)
        for directory in root.iterdir():
            metadata = directory.lstat()
            if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
                raise AttachmentStorageError("Invalid derived storage")
            for path in directory.iterdir():
                file_metadata = path.lstat()
                if stat.S_ISLNK(file_metadata.st_mode) or not stat.S_ISREG(file_metadata.st_mode):
                    raise AttachmentStorageError("Invalid derived storage")
                total += file_metadata.st_size
                if total > SESSION_ATTACHMENT_QUOTA_BYTES:
                    return total
        return total


class AttachmentService:
    def __init__(
        self,
        storage: AttachmentStorage | None = None,
        *,
        settings: FileMediaSettings | None = None,
    ) -> None:
        self.storage = storage or AttachmentStorage()
        self.settings = settings

    async def create_image(self, db: AsyncSession, message: Message, data: bytes, mime_type: str, **kwargs) -> MessageAttachment:
        return await self.create_attachment(db, message, data, mime_type, kind="image", **kwargs)

    async def create_audio(self, db: AsyncSession, message: Message, data: bytes, mime_type: str, **kwargs) -> MessageAttachment:
        return await self.create_attachment(db, message, data, mime_type, kind="audio", **kwargs)

    async def create_attachment(
        self,
        db: AsyncSession,
        message: Message,
        data: bytes,
        mime_type: str,
        *,
        kind: str,
        filename: str | None = None,
        position: int | None = None,
        metadata: dict | None = None,
        category: str = "user_upload",
        purpose: Literal["attachment", "artifact"] = "attachment",
        processing_status: str | None = "ready",
    ) -> MessageAttachment:
        if not data:
            raise AttachmentError("attachment.empty")
        max_bytes = (
            self.settings.max_upload_bytes
            if self.settings is not None and purpose == "attachment"
            else MAX_FILE_ATTACHMENT_BYTES
        )
        limits = (
            processing_limits(self.settings)
            if self.settings is not None and purpose == "attachment"
            else None
        )
        session_quota_bytes = (
            self.settings.session_quota_bytes
            if self.settings is not None
            else SESSION_ATTACHMENT_QUOTA_BYTES
        )
        if len(data) > max_bytes:
            raise AttachmentError("attachment.tooLarge")
        if purpose not in {"attachment", "artifact"}:
            raise AttachmentError("attachment.invalidPurpose")
        attachment_id = uuid4()
        cleaned_filename = safe_filename(filename, fallback=f"attachment-{attachment_id}")
        width = height = duration_ms = None
        safe_metadata = _safe_metadata(metadata)
        derived_files: dict[str, bytes] = {}
        if kind == "image":
            validated_image = validate_image(data, mime_type, max_bytes=max_bytes)
            mime_type = validated_image.mime_type
            width, height = validated_image.width, validated_image.height
        elif kind in {"audio", "video", "text", "document"}:
            try:
                processed = process_attachment(
                    data,
                    kind=kind,
                    mime_type=mime_type,
                    filename=cleaned_filename,
                    limits=limits,
                )
            except AttachmentProcessingError as exc:
                raise AttachmentError(exc.code) from exc
            if processed is None:
                raise AttachmentError("attachment.unsupportedType")
            mime_type = processed.mime_type
            duration_ms = processed.duration_ms
            width, height = processed.width, processed.height
            derived_files = processed.derived_files
            safe_metadata.update(processed.metadata)
            safe_metadata["format"] = processed.format
            safe_metadata.update(derived_metadata(derived_files))
            if kind == "audio":
                safe_metadata.update(
                    channels=processed.channels,
                    sampleRate=processed.sample_rate,
                    sampleWidth=processed.sample_width,
                )
        elif kind == "file":
            if purpose == "attachment":
                mime_type = "application/octet-stream"
        else:
            raise AttachmentError("attachment.unsupportedType")
        if kind == "audio" and duration_ms is None:
            raise AttachmentError("attachment.invalidAudio")
        if kind == "video" and (
            duration_ms is None or width is None or height is None or derived_files
        ):
            raise AttachmentError("attachment.invalidVideo")
        if kind == "audio":
            safe_metadata.update(
                channels=safe_metadata.get("channels"),
                sampleRate=safe_metadata.get("sampleRate"),
                sampleWidth=safe_metadata.get("sampleWidth"),
            )
        if category not in _CATEGORY_DIRECTORIES:
            raise AttachmentError("attachment.invalidCategory")
        if processing_status not in {None, "pending", "ready", "failed"}:
            raise AttachmentError("attachment.invalidProcessingStatus")

        locked_session_id = await db.scalar(
            select(ChatSession.id)
            .join(Message, Message.session_id == ChatSession.id)
            .where(ChatSession.id == message.session_id, Message.id == message.id)
            .with_for_update()
        )
        if locked_session_id is None:
            raise AttachmentError("attachment.messageNotFound")
        try:
            used_bytes = await asyncio.to_thread(
                RootFileService(
                    message.session_id,
                    session_storage=self.storage.sessions,
                    session_quota_bytes=session_quota_bytes,
                ).session_usage
            )
        except (RootFileError, AttachmentStorageError) as exc:
            raise AttachmentError("attachment.storageInvalid") from exc
        new_derived_bytes = sum(len(value) for value in derived_files.values())
        if (
            int(used_bytes or 0) + len(data) + new_derived_bytes
            > session_quota_bytes
        ):
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

        def write_files() -> tuple[str, dict[str, str]]:
            storage_path = self.storage.write(
                message.session_id,
                attachment_id,
                data,
                cleaned_filename,
                category,
            )
            try:
                paths = self.storage.write_derived(
                    message.session_id, attachment_id, derived_files
                )
            except BaseException:
                self.storage.delete(message.session_id, storage_path)
                raise
            return storage_path, paths

        write_task = asyncio.create_task(
            asyncio.to_thread(write_files)
        )
        try:
            storage_path, derived_paths = await asyncio.shield(write_task)
        except asyncio.CancelledError:
            storage_path, _derived_paths = await write_task
            await asyncio.to_thread(self.storage.delete, message.session_id, storage_path)
            await asyncio.to_thread(
                self.storage.delete_derived, message.session_id, attachment_id
            )
            raise
        if derived_paths:
            safe_metadata["derivedPath"] = next(iter(derived_paths.values()))
        attachment = MessageAttachment(
            id=attachment_id,
            message_id=message.id,
            position=position,
            kind=kind,
            category=category,
            purpose=purpose,
            processing_status=processing_status,
            mime_type=mime_type,
            size_bytes=len(data),
            sha256=hashlib.sha256(data).hexdigest(),
            storage_path=storage_path,
            filename=cleaned_filename,
            width=width,
            height=height,
            duration_ms=duration_ms,
            metadata_json=safe_metadata,
        )
        db.add(attachment)
        try:
            await db.flush()
        except BaseException:
            await asyncio.to_thread(self.storage.delete, message.session_id, storage_path)
            await asyncio.to_thread(
                self.storage.delete_derived, message.session_id, attachment_id
            )
            raise
        return attachment

    async def verified_path(self, attachment: MessageAttachment, session_id: UUID) -> Path:
        expected_directory = _CATEGORY_DIRECTORIES.get(attachment.category)
        path_parts = PurePosixPath(attachment.storage_path).parts
        if (
            expected_directory is None
            or len(path_parts) != 3
            or path_parts[0] != expected_directory
            or path_parts[1] != str(attachment.id)
        ):
            raise AttachmentError("attachment.notFound")
        try:
            path = self.storage.path(session_id, attachment.storage_path)
        except AttachmentStorageError as exc:
            raise AttachmentError("attachment.notFound") from exc

        def verify() -> bool:
            digest = hashlib.sha256()
            size = 0
            with path.open("rb") as file:
                while chunk := file.read(1024 * 1024):
                    size += len(chunk)
                    if size > attachment.size_bytes:
                        return False
                    digest.update(chunk)
            return size == attachment.size_bytes and digest.hexdigest() == attachment.sha256

        try:
            valid = await asyncio.to_thread(verify)
        except OSError as exc:
            raise AttachmentError("attachment.notFound") from exc
        if not valid:
            raise AttachmentError("attachment.notFound")
        return path

    async def read_audio(self, attachment: MessageAttachment, session_id: UUID) -> bytes:
        if (
            attachment.kind != "audio"
            or attachment.processing_status != "ready"
            or attachment.size_bytes > ABSOLUTE_MAX_FILE_ATTACHMENT_BYTES
        ):
            raise AttachmentError("attachment.notFound")
        path = await self.verified_path(attachment, session_id)
        try:
            data = await asyncio.to_thread(path.read_bytes)
        except OSError as exc:
            raise AttachmentError("attachment.notFound") from exc
        derived_path = (attachment.metadata_json or {}).get("derivedPath")
        if derived_path is not None:
            data = await asyncio.to_thread(
                self._verified_derived_bytes, attachment, session_id, "audio.wav"
            )
        try:
            validated = validate_audio(
                data,
                "audio/wav",
                max_bytes=ABSOLUTE_MAX_FILE_ATTACHMENT_BYTES,
                limits=AttachmentProcessingLimits(max_audio_duration_seconds=3_600),
            )
        except AttachmentError as exc:
            raise AttachmentError("attachment.notFound") from exc
        if attachment.duration_ms is not None and validated.duration_ms != attachment.duration_ms:
            raise AttachmentError("attachment.notFound")
        return data

    async def read_image(self, attachment: MessageAttachment, session_id: UUID) -> bytes:
        if (
            attachment.kind != "image"
            or attachment.processing_status != "ready"
            or attachment.size_bytes > ABSOLUTE_MAX_FILE_ATTACHMENT_BYTES
        ):
            raise AttachmentError("attachment.notFound")
        path = await self.verified_path(attachment, session_id)
        try:
            data = await asyncio.to_thread(path.read_bytes)
            validated = validate_image(
                data,
                attachment.mime_type,
                max_bytes=ABSOLUTE_MAX_FILE_ATTACHMENT_BYTES,
            )
        except (OSError, AttachmentError) as exc:
            raise AttachmentError("attachment.notFound") from exc
        if (
            (attachment.width is not None and validated.width != attachment.width)
            or (attachment.height is not None and validated.height != attachment.height)
        ):
            raise AttachmentError("attachment.notFound")
        return data

    def _verified_derived_bytes(
        self,
        attachment: MessageAttachment,
        session_id: UUID,
        expected_name: str,
    ) -> bytes:
        metadata = attachment.metadata_json or {}
        derived_path = metadata.get("derivedPath")
        expected_size = metadata.get("derivedSizeBytes")
        expected_sha256 = metadata.get("derivedSha256")
        if (
            not isinstance(derived_path, str)
            or not isinstance(expected_size, int)
            or isinstance(expected_size, bool)
            or expected_size < 0
            or not isinstance(expected_sha256, str)
            or metadata.get("derivedName") != expected_name
        ):
            raise AttachmentError("attachment.notFound")
        try:
            path = self.storage.derived_path(session_id, attachment.id, derived_path)
            if path.name != expected_name or path.stat().st_size != expected_size:
                raise AttachmentError("attachment.notFound")
            data = path.read_bytes()
        except (OSError, AttachmentStorageError) as exc:
            raise AttachmentError("attachment.notFound") from exc
        if len(data) != expected_size or hashlib.sha256(data).hexdigest() != expected_sha256:
            raise AttachmentError("attachment.notFound")
        return data

    def read_derived_text(
        self, attachment: MessageAttachment, session_id: UUID
    ) -> str:
        if attachment.kind not in {"text", "document"} or attachment.processing_status != "ready":
            raise AttachmentError("attachment.notFound")
        data = self._verified_derived_bytes(attachment, session_id, "content.txt")
        try:
            return data.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise AttachmentError("attachment.notFound") from exc

    async def discard(self, attachment: MessageAttachment, session_id: UUID) -> None:
        await asyncio.to_thread(self.storage.delete, session_id, attachment.storage_path)
        await asyncio.to_thread(self.storage.delete_derived, session_id, attachment.id)
