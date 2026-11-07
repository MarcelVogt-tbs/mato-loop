from __future__ import annotations

import asyncio
import hashlib
import mimetypes
from dataclasses import dataclass
from types import SimpleNamespace

import mato_loop_backend.files.access as files_access
import mato_loop_backend.tools.access as tools_access
from mato_loop_backend.media.attachments import (
    AttachmentService,
    image_mime_type,
    validate_image,
)
from mato_loop_backend.models import MessageAttachment
from mato_loop_backend.settings import TrustLevel
from mato_loop_backend.tools.arguments import FileReference
from mato_loop_backend.tools.context import ToolContext
from mato_loop_backend.tools.contracts import ToolResult


@dataclass(frozen=True)
class MediaFile:
    path: str
    data: bytes
    mime_type: str
    sha256: str
    trust_level: TrustLevel
    attachment: MessageAttachment | SimpleNamespace | None


async def read_media_file(
    reference: FileReference, context: ToolContext, *, kind: str, limit: int,
) -> MediaFile | ToolResult:
    if files_access.delegated_session_files(context, reference.root):
        return ToolResult.error("tool.unavailable")
    path = files_access.file_path(context, reference.root, reference.path)
    request = files_access.filesystem_permission(
        context, root=reference.root, path=path, capability="filesystem.read",
        action_kind=context.tool_call.name, action_label="Mediendatei lesen",
    )
    if not await tools_access.allow(context, request):
        return ToolResult.error("permission.denied")
    attachment = (
        await AttachmentService(run_id=context.run_id).resolve_path(context.db, context.root_files, path)
        if reference.root == "session" else None
    )
    if attachment is not None:
        if (attachment.metadata_json or {}).get("protectedInput") is True:
            return ToolResult.error("permission.denied")
        if attachment.processing_status != "ready" or attachment.kind not in {kind, "file"}:
            return ToolResult.error("attachment.unsupportedType")
    data = await files_access.read_bytes(context, reference.root, path, limit=limit)
    digest = hashlib.sha256(data).hexdigest()
    if attachment is not None and (attachment.sha256 != digest or attachment.size_bytes != len(data)):
        return ToolResult.error("attachment.storageInvalid")
    mime_type = (
        attachment.mime_type if attachment is not None
        else mimetypes.guess_type(path)[0] or "application/octet-stream"
    )
    if kind == "image":
        validated = await asyncio.to_thread(validate_image, data, image_mime_type(data) or "", max_bytes=limit)
        mime_type = validated.mime_type
    return MediaFile(
        path, data, mime_type, digest,
        TrustLevel(attachment.message.trust_level) if attachment is not None else context.model_trust_level,
        attachment,
    )
