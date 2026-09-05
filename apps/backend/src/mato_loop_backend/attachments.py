"""Validation and persistent storage for general session attachments."""

from __future__ import annotations

import asyncio
import hashlib
import re
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
    document_mime_type,
    process_attachment,
    signed_audio_format,
    signed_document_format,
    validate_pcm_wav,
)
from mato_loop_backend.models import ChatSession, Message, MessageAttachment
from mato_loop_backend.session_storage import SessionStorage, SessionStorageError
from mato_loop_backend.settings import InstanceLimits
from mato_loop_backend.video_processing import (
    VideoProcessingError,
    probe_media,
    video_container_candidate,
)
from mato_loop_backend.workspace import FileRevision, FileRoot, RootFileError, RootFileService

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


class AttachmentError(Exception):
    def __init__(self, code: str, *, diagnostic: str | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.diagnostic = diagnostic


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
            "pageNumber",
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
            "voiceMessage",
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


def processing_limits(settings: InstanceLimits) -> AttachmentProcessingLimits:
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


@dataclass(frozen=True)
class _ManagedWrite:
    source: FileRevision
    companion: FileRevision | None
    created_directories: tuple[str, ...]


class AttachmentService:
    def __init__(
        self,
        session_storage: SessionStorage | Path | None = None,
        *,
        settings: InstanceLimits | None = None,
    ) -> None:
        self.sessions = (
            SessionStorage(session_storage)
            if isinstance(session_storage, Path)
            else session_storage or SessionStorage()
        )
        self.settings = settings

    def _root_files(self, session_id: UUID, file_root_id: UUID) -> RootFileService:
        project_id = None if file_root_id == session_id else file_root_id
        try:
            self.sessions.session_path(
                session_id, project_id=project_id, require_exists=True
            )
            service = RootFileService(
                session_id,
                project_id=project_id,
                session_storage=self.sessions,
                session_quota_bytes=(
                    self.settings.session_quota_bytes if self.settings else None
                ),
            )
            if UUID(service.root_identity(FileRoot.SESSION)) != file_root_id:
                raise RootFileError("file.rootChanged", "File root changed")
            return service
        except (RootFileError, SessionStorageError) as exc:
            raise AttachmentError("attachment.notFound") from exc

    @staticmethod
    def _create_file(
        root_files: RootFileService, path: str, data: bytes
    ) -> tuple[FileRevision, list[str]]:
        parts = PurePosixPath(path).parts
        created_directories: list[str] = []
        for index in range(1, len(parts)):
            directory = "/".join(parts[:index])
            try:
                root_files.create_directory(FileRoot.SESSION, directory)
            except RootFileError as exc:
                if exc.code != "file.pathAlreadyExists" or root_files.path_kind(
                    FileRoot.SESSION, directory
                ) != "directory":
                    raise
            else:
                created_directories.append(directory)
        try:
            root_files.create_file(
                FileRoot.SESSION,
                path,
                BytesIO(data),
                max_bytes=RootFileService.MAX_OPERATION_BYTES,
            )
        except BaseException:
            for directory in reversed(created_directories):
                try:
                    root_files.delete_path(
                        FileRoot.SESSION, directory, recursive=False
                    )
                except RootFileError:
                    pass
            raise
        return root_files.list_revisions(path)[0], created_directories

    @staticmethod
    def _discard_write(root_files: RootFileService, written: _ManagedWrite) -> None:
        revisions = [written.companion, written.source]
        seen: set[tuple[UUID, int]] = set()
        for revision in revisions:
            if revision is None or (revision.file_id, revision.revision) in seen:
                continue
            seen.add((revision.file_id, revision.revision))
            root_files.discard_output_revision(revision.file_id, revision.revision)
        for directory in reversed(written.created_directories):
            try:
                root_files.delete_path(FileRoot.SESSION, directory, recursive=False)
            except RootFileError:
                pass

    @staticmethod
    def _companion_metadata(root_id: UUID, revision: FileRevision) -> dict:
        return {
            "companionFileRootId": str(root_id),
            "companionFileId": str(revision.file_id),
            "companionFileRevision": revision.revision,
            "companionFilePath": revision.path,
            "companionSizeBytes": revision.size_bytes,
            "companionSha256": revision.sha256,
        }

    async def create_image(self, db: AsyncSession, message: Message, data: bytes, mime_type: str, **kwargs) -> MessageAttachment:
        return await self.create_attachment(db, message, data, mime_type, kind="image", **kwargs)

    async def create_audio(self, db: AsyncSession, message: Message, data: bytes, mime_type: str, **kwargs) -> MessageAttachment:
        return await self.create_attachment(db, message, data, mime_type, kind="audio", **kwargs)

    async def create_reference(
        self,
        db: AsyncSession,
        message: Message,
        *,
        file_root_id: UUID,
        file_id: UUID,
        file_revision: int,
        file_path: str,
        filename: str,
        position: int,
        category: str,
        metadata: dict | None = None,
    ) -> MessageAttachment:
        locked_session = await db.scalar(
            select(ChatSession)
            .join(Message, Message.session_id == ChatSession.id)
            .where(ChatSession.id == message.session_id, Message.id == message.id)
            .with_for_update()
        )
        if locked_session is None:
            raise AttachmentError("attachment.messageNotFound")
        root_files = RootFileService(
            message.session_id,
            project_id=locked_session.project_id,
            session_storage=self.sessions,
        )
        if UUID(root_files.root_identity(FileRoot.SESSION)) != file_root_id:
            raise AttachmentError("attachment.notFound")
        try:
            revision = await asyncio.to_thread(
                root_files.file_revision, file_id, file_revision
            )
        except RootFileError as exc:
            raise AttachmentError("attachment.notFound") from exc
        if revision.path != file_path:
            raise AttachmentError("attachment.notFound")
        attachment = MessageAttachment(
            message_id=message.id,
            position=position,
            kind="file",
            category=category,
            purpose="artifact",
            processing_status="ready",
            mime_type="application/octet-stream",
            size_bytes=revision.size_bytes,
            sha256=revision.sha256,
            file_root_id=file_root_id,
            file_id=file_id,
            file_revision=file_revision,
            file_path=file_path,
            filename=filename,
            metadata_json=_safe_metadata(metadata),
        )
        db.add(attachment)
        await db.flush()
        return attachment

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
        source_file_root_id: UUID | None = None,
        file_id: UUID | None = None,
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
                processed = await asyncio.to_thread(
                    process_attachment,
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
            derived_files = {} if kind == "audio" else processed.derived_files
            safe_metadata.update(processed.metadata)
            safe_metadata["format"] = processed.format
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
        if purpose == "attachment" and (
            category != "user_upload"
            or source_file_root_id is not None
            or file_id is not None
        ):
            raise AttachmentError("attachment.invalidCategory")
        if processing_status not in {None, "pending", "ready", "failed"}:
            raise AttachmentError("attachment.invalidProcessingStatus")

        locked_session = await db.scalar(
            select(ChatSession)
            .join(Message, Message.session_id == ChatSession.id)
            .where(ChatSession.id == message.session_id, Message.id == message.id)
            .with_for_update()
        )
        if locked_session is None:
            raise AttachmentError("attachment.messageNotFound")
        root_files = RootFileService(
            message.session_id,
            project_id=locked_session.project_id,
            session_storage=self.sessions,
            session_quota_bytes=session_quota_bytes,
        )
        try:
            used_bytes = await asyncio.to_thread(root_files.session_usage)
        except RootFileError as exc:
            raise AttachmentError("attachment.storageInvalid") from exc
        companion_bytes = 0 if kind == "text" else sum(map(len, derived_files.values()))
        if int(used_bytes or 0) + 2 * (len(data) + companion_bytes) > session_quota_bytes:
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

        def write_managed() -> _ManagedWrite:
            root_id = UUID(root_files.root_identity(FileRoot.SESSION))
            created_directories: list[str] = []
            source: FileRevision | None = None
            companion: FileRevision | None = None
            try:
                if file_id is not None and source_file_root_id == root_id:
                    source = root_files.append_output_revision(file_id, data)
                else:
                    directory = _CATEGORY_DIRECTORIES[category]
                    source, directories = self._create_file(
                        root_files,
                        f"{directory}/{attachment_id}/{cleaned_filename}",
                        data,
                    )
                    created_directories.extend(directories)

                if kind == "text":
                    companion = source
                elif derived_files:
                    name, companion_data = next(iter(derived_files.items()))
                    suffix = PurePosixPath(name).suffix
                    source_path = PurePosixPath(source.path)
                    candidate = str(source_path.with_name(f"{source_path.stem}{suffix}"))
                    if candidate == source.path:
                        candidate = str(
                            source_path.with_name(
                                f"{source_path.stem}-{attachment_id.hex[:8]}{suffix}"
                            )
                        )
                    try:
                        companion, directories = self._create_file(
                            root_files, candidate, companion_data
                        )
                    except RootFileError as exc:
                        if exc.code != "file.pathAlreadyExists":
                            raise
                        candidate = str(
                            source_path.with_name(
                                f"{source_path.stem}-{attachment_id.hex[:8]}{suffix}"
                            )
                        )
                        companion, directories = self._create_file(
                            root_files, candidate, companion_data
                        )
                    created_directories.extend(directories)
                return _ManagedWrite(source, companion, tuple(created_directories))
            except BaseException:
                if source is not None:
                    self._discard_write(
                        root_files,
                        _ManagedWrite(source, companion, tuple(created_directories)),
                    )
                raise

        write_task = asyncio.create_task(asyncio.to_thread(write_managed))
        try:
            written = await asyncio.shield(write_task)
        except asyncio.CancelledError:
            written = await write_task
            await asyncio.to_thread(self._discard_write, root_files, written)
            raise
        except RootFileError as exc:
            if exc.code == "file.sessionQuotaExceeded":
                raise AttachmentError("attachment.sessionQuotaExceeded") from exc
            raise AttachmentError("attachment.storageInvalid") from exc

        root_id = UUID(root_files.root_identity(FileRoot.SESSION))
        if written.companion is not None:
            safe_metadata.update(
                self._companion_metadata(root_id, written.companion)
            )
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
            file_root_id=root_id,
            file_id=written.source.file_id,
            file_revision=written.source.revision,
            file_path=written.source.path,
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
            await asyncio.to_thread(self._discard_write, root_files, written)
            raise
        return attachment

    async def verified_path(self, attachment: MessageAttachment, session_id: UUID) -> Path:
        path = self.revision_path(
            session_id,
            attachment.file_root_id,
            attachment.file_id,
            attachment.file_revision,
            expected_size=attachment.size_bytes,
            expected_sha256=attachment.sha256,
        )

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

    def revision_path(
        self,
        session_id: UUID,
        file_root_id: UUID,
        file_id: UUID,
        file_revision: int,
        *,
        expected_path: str | None = None,
        expected_size: int | None = None,
        expected_sha256: str | None = None,
    ) -> Path:
        try:
            root_files = self._root_files(session_id, file_root_id)
            revision = root_files.file_revision(file_id, file_revision)
            if (
                expected_path is not None
                and revision.path != expected_path
                or expected_size is not None
                and revision.size_bytes != expected_size
                or expected_sha256 is not None
                and revision.sha256 != expected_sha256
            ):
                raise AttachmentError("attachment.notFound")
            return root_files.revision_path(file_id, file_revision)
        except (RootFileError, TypeError, ValueError) as exc:
            raise AttachmentError("attachment.notFound") from exc

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
        try:
            processed = await asyncio.to_thread(
                process_attachment,
                data,
                kind="audio",
                mime_type=attachment.mime_type,
                filename=attachment.filename or "audio",
                limits=AttachmentProcessingLimits(
                    max_media_bytes=ABSOLUTE_MAX_FILE_ATTACHMENT_BYTES,
                    max_audio_duration_seconds=3_600,
                ),
            )
        except AttachmentProcessingError as exc:
            raise AttachmentError("attachment.notFound") from exc
        if processed is None or (
            attachment.duration_ms is not None
            and processed.duration_ms != attachment.duration_ms
        ):
            raise AttachmentError("attachment.notFound")
        return processed.derived_files.get("audio.wav", data)

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

    def _verified_companion_bytes(
        self,
        attachment: MessageAttachment,
        session_id: UUID,
    ) -> bytes | None:
        metadata = attachment.metadata_json or {}
        values = {
            "root": metadata.get("companionFileRootId"),
            "file": metadata.get("companionFileId"),
            "revision": metadata.get("companionFileRevision"),
            "path": metadata.get("companionFilePath"),
            "size": metadata.get("companionSizeBytes"),
            "sha256": metadata.get("companionSha256"),
        }
        if not any(value is not None for value in values.values()):
            return None
        if (
            not isinstance(values["root"], str)
            or not isinstance(values["file"], str)
            or not isinstance(values["revision"], int)
            or isinstance(values["revision"], bool)
            or values["revision"] < 1
            or not isinstance(values["path"], str)
            or not isinstance(values["size"], int)
            or isinstance(values["size"], bool)
            or values["size"] < 0
            or not isinstance(values["sha256"], str)
        ):
            raise AttachmentError("attachment.notFound")
        try:
            root_id = UUID(values["root"])
            file_id = UUID(values["file"])
            if root_id != attachment.file_root_id:
                raise AttachmentError("attachment.notFound")
            path = self.revision_path(
                session_id,
                root_id,
                file_id,
                values["revision"],
                expected_size=values["size"],
                expected_sha256=values["sha256"],
            )
            data = path.read_bytes()
        except (OSError, TypeError, ValueError) as exc:
            raise AttachmentError("attachment.notFound") from exc
        if len(data) != values["size"] or hashlib.sha256(data).hexdigest() != values["sha256"]:
            raise AttachmentError("attachment.notFound")
        return data

    def read_derived_text(
        self, attachment: MessageAttachment, session_id: UUID
    ) -> str:
        if attachment.kind not in {"text", "document"} or attachment.processing_status != "ready":
            raise AttachmentError("attachment.notFound")
        try:
            source_path = self.revision_path(
                session_id,
                attachment.file_root_id,
                attachment.file_id,
                attachment.file_revision,
                expected_size=attachment.size_bytes,
                expected_sha256=attachment.sha256,
            )
            digest = hashlib.sha256()
            size = 0
            with source_path.open("rb") as source:
                while chunk := source.read(1024 * 1024):
                    size += len(chunk)
                    if size > attachment.size_bytes:
                        raise AttachmentError("attachment.notFound")
                    digest.update(chunk)
        except OSError as exc:
            raise AttachmentError("attachment.notFound") from exc
        if size != attachment.size_bytes or digest.hexdigest() != attachment.sha256:
            raise AttachmentError("attachment.notFound")
        data = self._verified_companion_bytes(attachment, session_id)
        if data is None:
            raise AttachmentError("attachment.notFound")
        try:
            return data.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise AttachmentError("attachment.notFound") from exc

    async def discard(self, attachment: MessageAttachment, session_id: UUID) -> None:
        root_files = self._root_files(session_id, attachment.file_root_id)
        metadata = attachment.metadata_json or {}
        companion_id = metadata.get("companionFileId")
        companion_revision = metadata.get("companionFileRevision")

        def discard() -> None:
            if isinstance(companion_id, str) and isinstance(companion_revision, int):
                parsed = UUID(companion_id)
                if (parsed, companion_revision) != (
                    attachment.file_id,
                    attachment.file_revision,
                ):
                    root_files.discard_output_revision(parsed, companion_revision)
            root_files.discard_output_revision(
                attachment.file_id, attachment.file_revision
            )
            parent = PurePosixPath(attachment.file_path).parent
            if parent.name == str(attachment.id):
                try:
                    root_files.delete_path(
                        FileRoot.SESSION, str(parent), recursive=False
                    )
                except RootFileError:
                    pass

        await asyncio.to_thread(discard)
