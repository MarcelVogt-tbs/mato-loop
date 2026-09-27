from __future__ import annotations

import asyncio
import hashlib
import logging
import posixpath
from io import BytesIO
from typing import BinaryIO
from uuid import UUID

from sqlalchemy import and_, select

import mato_loop_backend.files.access as files_access
import mato_loop_backend.tools.access as tools_access
from mato_loop_backend.execution import (
    ExecutionClientError,
    WorkspaceDeleteResult,
    WorkspaceGlobResult,
    WorkspaceGrepResult,
    WorkspaceListResult,
    WorkspacePatchResult,
    WorkspacePathResult,
    WorkspaceReadResult,
    WorkspaceStatResult,
    WorkspaceWriteResult,
)
from mato_loop_backend.files.archives import (
    MAX_EXPORT_BYTES,
    MAX_EXPORT_ENTRIES,
    ArchiveEntry,
    ArchivePage,
    FileArchiveError,
    create_zip_archive,
)
from mato_loop_backend.files.search import RootFileSearch
from mato_loop_backend.files.service import (
    FileRevision,
    FileRoot,
    RootFileError,
    RootFileService,
    WorkingFileRevision,
)
from mato_loop_backend.media.attachments import (
    MAX_IMAGE_ATTACHMENT_BYTES,
    AttachmentService,
    image_mime_type,
    validate_image,
)
from mato_loop_backend.models import Message, MessageAttachment
from mato_loop_backend.providers.service import normalize_provider_image
from mato_loop_backend.settings import ToolExecutionPolicy
from mato_loop_backend.tools.actions import (
    _FILE_PROVIDE,
    _FILESYSTEM_DELETE,
    _FILESYSTEM_READ,
    _FILESYSTEM_WRITE,
    _SESSION_FILES_READ,
    _SESSION_FILES_WRITE,
)
from mato_loop_backend.tools.arguments import (
    ApplyPatchArguments,
    CopyPathArguments,
    CreateDirectoryArguments,
    DeletePathArguments,
    EditFileArguments,
    GlobArguments,
    GrepArguments,
    MovePathArguments,
    ProvideFileArguments,
    ReadArguments,
    WriteFileArguments,
)
from mato_loop_backend.tools.context import ToolContext as ToolContext
from mato_loop_backend.tools.contracts import ToolAttachment as ToolAttachment
from mato_loop_backend.tools.contracts import ToolDefinition as ToolDefinition
from mato_loop_backend.tools.contracts import ToolFileAction as ToolFileAction
from mato_loop_backend.tools.contracts import ToolResult as ToolResult

logger = logging.getLogger("mato_loop_backend.agent.tools")


ALL_TOOL_EXECUTION_POLICIES = tuple(ToolExecutionPolicy)


async def _read(arguments: ReadArguments, context: ToolContext) -> ToolResult:
    if files_access.delegated_session_files(context, arguments.root):
        return ToolResult.error("tool.unavailable")
    selected = await files_access.project_read_files(arguments, context)
    if selected is None:
        return ToolResult.error("permission.denied")
    root_files, target_session_id = selected
    request = files_access.filesystem_permission(
        context,
        root=arguments.root,
        path=arguments.path,
        capability="filesystem.read",
        action_kind="read",
        action_label="Pfad lesen",
        root_files=root_files,
        target_session_id=target_session_id,
    )
    if not await tools_access.allow(context, request):
        return ToolResult.error("permission.denied")
    normalized_path = (
        files_access.execution_path(context, arguments.path)
        if arguments.root == "workspace"
        else root_files.normalize_relative_path(arguments.path)
    )
    if arguments.root == "workspace":
        stat_result = await files_access.workspace_request(
            context,
            "filesystem.stat",
            {"path": normalized_path},
            WorkspaceStatResult,
        )
        files_access.require_workspace_path(stat_result.path, normalized_path)
        kind = stat_result.kind
    else:
        kind = root_files.path_kind(arguments.root, normalized_path)
    if kind == "directory":
        if arguments.root == "workspace":
            result = await files_access.workspace_request(
                context,
                "filesystem.list",
                {
                    "path": normalized_path,
                    "offset": arguments.offset - 1,
                    "limit": arguments.limit,
                },
                WorkspaceListResult,
            )
        else:
            result = root_files.list_directory(
                arguments.root,
                arguments.path,
                offset=arguments.offset - 1,
                limit=arguments.limit,
            )
        return ToolResult.json(
            {
                "root": arguments.root,
                "sessionId": str(target_session_id),
                "path": arguments.path,
                "entries": [
                    {
                        "name": entry.name,
                        "path": entry.path,
                        "kind": entry.kind,
                        "size": entry.size,
                        "modifiedAt": entry.modified_at,
                    }
                    for entry in result.entries
                ],
                "hasMore": result.has_more,
                "nextOffset": (
                    result.next_offset + 1 if result.next_offset is not None else None
                ),
                "limit": arguments.limit,
            }
        )
    if kind != "file":
        return ToolResult.error("file.unsupportedType")
    if arguments.root == "session" and target_session_id == context.session.id:
        try:
            selected_revision = root_files.working_revision(normalized_path)
        except RootFileError as exc:
            if exc.code != "file.revisionNotFound":
                raise
            selected_revision = None
        revision_filter = and_(
            selected_revision is not None,
            MessageAttachment.file_id
            == (selected_revision.file_id if selected_revision else None),
            MessageAttachment.file_revision
            == (selected_revision.revision if selected_revision else None),
        )
        filters = [
            revision_filter,
            MessageAttachment.kind == "image",
            MessageAttachment.processing_status == "ready",
            Message.session_id == context.session.id,
            Message.error_code.is_(None),
        ]
        attachment = await context.db.scalar(
            select(MessageAttachment)
            .join(Message, Message.id == MessageAttachment.message_id)
            .where(*filters)
        )
        if selected_revision is not None and selected_revision.revision is None:
            attachment = next((item for item in await AttachmentService(run_id=root_files.run_id).pending_for_run(context.db, context.session.id)
                               if item.file_id == selected_revision.file_id and item.sha256 == selected_revision.sha256
                               and item.kind == "image"), None)
        if attachment is not None:
            return ToolResult.json(
                {
                    "root": "session",
                    "path": normalized_path,
                    "imageAttachment": {
                        "id": str(attachment.id),
                        "filename": attachment.filename,
                        "kind": "image",
                        "mimeType": attachment.mime_type,
                    },
                    "contentIsUntrusted": True,
                }
            )
    if normalized_path.lower().endswith((".png", ".jpg", ".jpeg", ".webp", ".gif")):
        if arguments.root == "workspace":
            client = files_access.workspace_client(context)
            if not client.supports("filesystem.export"):
                return ToolResult.error("executionAgent.operationUnavailable")
            data = await client.read_bytes(normalized_path, limit=MAX_IMAGE_ATTACHMENT_BYTES)
        else:
            data = await asyncio.to_thread(
                root_files.read_bytes, arguments.root, normalized_path,
                limit=MAX_IMAGE_ATTACHMENT_BYTES,
            )
        if len(data) > MAX_IMAGE_ATTACHMENT_BYTES:
            return ToolResult.error("file.tooLarge")
        validated = validate_image(data, image_mime_type(data) or "")
        image, mime_type = normalize_provider_image(data, validated)
        result = ToolResult.json({
            "root": arguments.root,
            "path": normalized_path,
            "image": {"filename": posixpath.basename(normalized_path), "mimeType": mime_type},
            "contentIsUntrusted": True,
        })
        result.attachments = (ToolAttachment(
            image, mime_type, posixpath.basename(normalized_path),
            presentation="internal", ephemeral=True,
        ),)
        return result
    if arguments.root == "workspace":
        result = await files_access.workspace_request(
            context,
            "filesystem.read",
            {
                "path": normalized_path,
                "offset": arguments.offset,
                "limit": arguments.limit,
                "maxLineLength": 2_000,
            },
            WorkspaceReadResult,
        )
    else:
        result = root_files.read_file(
            arguments.root,
            arguments.path,
            offset=arguments.offset,
            limit=arguments.limit,
        )
    content = result.content
    notices = []
    if result.truncated_lines:
        lines = ", ".join(str(line) for line in result.truncated_lines)
        notices.append(f"[Lines {lines} were truncated after 2000 characters.]")
    if result.has_more:
        notices.append(
            f"[More lines are available. Continue with offset={result.next_offset}.]"
        )
    if notices:
        content += ("\n\n" if content else "") + "\n".join(notices)
    return ToolResult(
        content,
        {
            "root": arguments.root,
            "sessionId": str(target_session_id),
            "path": arguments.path,
            "offset": arguments.offset,
            "limit": arguments.limit,
            "linesRead": result.lines_read,
            "hasMore": result.has_more,
            "nextOffset": result.next_offset,
            "truncatedLines": list(result.truncated_lines),
            "sha256": result.sha256,
        },
    )


async def _glob(arguments: GlobArguments, context: ToolContext) -> ToolResult:
    if files_access.delegated_session_files(context, arguments.root):
        return ToolResult.error("tool.unavailable")
    selected = await files_access.project_read_files(arguments, context)
    if selected is None:
        return ToolResult.error("permission.denied")
    root_files, target_session_id = selected
    request = files_access.filesystem_permission(
        context,
        root=arguments.root,
        path=arguments.path,
        capability="filesystem.read",
        action_kind="glob",
        action_label="Dateien suchen",
        root_files=root_files,
        target_session_id=target_session_id,
    )
    if not await tools_access.allow(context, request):
        return ToolResult.error("permission.denied")
    if arguments.root == "workspace":
        result = await files_access.workspace_request(
            context,
            "filesystem.glob",
            {
                "path": files_access.execution_path(context, arguments.path),
                "pattern": arguments.pattern,
            },
            WorkspaceGlobResult,
        )
        payload = result.model_dump(mode="json", by_alias=True)
    else:
        payload = await RootFileSearch(root_files).glob(
            arguments.root, arguments.path, arguments.pattern
        )
    return ToolResult.json(
        {
            "root": arguments.root,
            "sessionId": str(target_session_id),
            "path": arguments.path,
            **payload,
        }
    )


async def _grep(arguments: GrepArguments, context: ToolContext) -> ToolResult:
    if files_access.delegated_session_files(context, arguments.root):
        return ToolResult.error("tool.unavailable")
    selected = await files_access.project_read_files(arguments, context)
    if selected is None:
        return ToolResult.error("permission.denied")
    root_files, target_session_id = selected
    request = files_access.filesystem_permission(
        context,
        root=arguments.root,
        path=arguments.path,
        capability="filesystem.read",
        action_kind="grep",
        action_label="Dateiinhalte durchsuchen",
        root_files=root_files,
        target_session_id=target_session_id,
    )
    if not await tools_access.allow(context, request):
        return ToolResult.error("permission.denied")
    if arguments.root == "workspace":
        result = await files_access.workspace_request(
            context,
            "filesystem.grep",
            {
                "path": files_access.execution_path(context, arguments.path),
                "pattern": arguments.pattern,
                **({"include": arguments.include} if arguments.include is not None else {}),
            },
            WorkspaceGrepResult,
        )
        payload = result.model_dump(mode="json", by_alias=True)
    else:
        payload = await RootFileSearch(root_files).grep(
            arguments.root, arguments.path, arguments.pattern, arguments.include
        )
    return ToolResult.json(
        {
            "root": arguments.root,
            "sessionId": str(target_session_id),
            "path": arguments.path,
            **payload,
        }
    )


async def _provide_file(
    arguments: ProvideFileArguments, context: ToolContext
) -> ToolResult:
    if context.session.parent_session_id is not None:
        return ToolResult.error("tool.unavailable")
    selected = await files_access.project_read_files(arguments, context)
    if selected is None:
        return ToolResult.error("permission.denied")
    root_files, target_session_id = selected
    output_files = context.root_files
    if output_files.run_id != context.run_id:
        output_files = RootFileService(
            context.session.id,
            project_id=context.session.project_id,
            run_id=context.run_id,
            session_storage=context.root_files.session_storage,
            session_quota_bytes=context.root_files.SESSION_QUOTA_BYTES,
            max_editable_text_bytes=context.root_files.max_editable_text_bytes,
        )
    if arguments.root == "workspace":
        client = files_access.workspace_client(context)
        if not all(
            client.supports(operation)
            for operation in ("filesystem.stat", "filesystem.export")
        ):
            return ToolResult.error("executionAgent.operationUnavailable")
    paths = list(dict.fromkeys(
        files_access.execution_path(context, path)
        if arguments.root == "workspace"
        else root_files.normalize_relative_path(path)
        for path in (arguments.paths or [arguments.path])
    ))
    async def authorize(path: str) -> bool:
        request = files_access.filesystem_permission(
            context,
            root=arguments.root,
            path=path,
            capability="file.provide",
            action_kind="provide_file",
            action_label="Datei bereitstellen",
            root_files=root_files,
            target_session_id=target_session_id,
        )
        return await tools_access.allow(context, request)

    for source in paths:
        if not await authorize(source):
            return ToolResult.error("permission.denied")

    path = paths[0]
    if arguments.root == "workspace":
        stat_result = await client.request(
            "filesystem.stat", {"path": path}, WorkspaceStatResult
        )
        files_access.require_workspace_path(stat_result.path, path)
        kind = stat_result.kind
    else:
        kind = await asyncio.to_thread(root_files.path_kind, arguments.root, path)

    if (
        arguments.root == "workspace"
        and arguments.paths is None
        and kind == "file"
        and stat_result.size == 0
    ):
        return ToolResult.error("attachment.empty")

    if arguments.paths is not None or kind == "directory":
        if arguments.root == "workspace" and not client.supports("filesystem.list"):
            return ToolResult.error("executionAgent.operationUnavailable")

        async def archive_stat_source(source: str) -> ArchiveEntry:
            if arguments.root == "workspace":
                item = await client.request(
                    "filesystem.stat", {"path": source}, WorkspaceStatResult
                )
                return ArchiveEntry(item.path, posixpath.basename(item.path), item.kind, item.size)
            item_kind = await asyncio.to_thread(root_files.path_kind, arguments.root, source)
            size = None
            if item_kind == "file":
                resolved = await asyncio.to_thread(root_files.resolve, arguments.root, source)
                size = resolved.stat(follow_symlinks=False).st_size
            return ArchiveEntry(source, posixpath.basename(source), item_kind, size)

        async def list_source(source: str, offset: int) -> ArchivePage:
            if arguments.root == "workspace":
                page = await client.request(
                    "filesystem.list",
                    {"path": source, "offset": offset, "limit": root_files.MAX_LIST_LIMIT},
                    WorkspaceListResult,
                )
            else:
                page = await asyncio.to_thread(
                    root_files.list_directory, arguments.root, source,
                    offset=offset, limit=root_files.MAX_LIST_LIMIT,
                )
            return ArchivePage(
                tuple(ArchiveEntry(item.path, item.name, item.kind, item.size) for item in page.entries),
                page.has_more,
                page.next_offset,
            )

        async def open_source(source: str, limit: int) -> BinaryIO:
            if arguments.root == "workspace":
                return await client.open_export(source, limit=limit)
            return await asyncio.to_thread(root_files.open_file, arguments.root, source)

        try:
            archive = await create_zip_archive(
                paths,
                stat_path=archive_stat_source,
                list_directory=list_source,
                open_file=open_source,
                file_list=arguments.paths is not None,
                root_contents=arguments.path == ".",
                max_source_bytes=MAX_EXPORT_BYTES,
                max_output_bytes=MAX_EXPORT_BYTES,
                max_entries=MAX_EXPORT_ENTRIES,
            )
        except FileArchiveError as exc:
            return ToolResult.error(exc.code, field=exc.field)
        try:
            revision = await files_access.create_output_stream(
                output_files, archive.filename, archive.content
            )
        finally:
            archive.content.close()
        result = ToolResult.json({
            "root": arguments.root,
            "filename": archive.filename,
            "sizeBytes": revision.size_bytes,
            "sha256": revision.sha256,
            "snapshotImported": True,
        })
        result.attachments = (
            _provided_reference(
                output_files, revision, archive.filename, mime_type="application/zip"
            ),
        )
        return result

    if arguments.root == "workspace":
        source = await client.open_export(path, limit=MAX_EXPORT_BYTES)
        try:
            revision = await files_access.create_output_stream(
                output_files, path.rsplit("/", 1)[-1], source
            )
        finally:
            source.close()
        result = ToolResult.json(
            {
                "root": arguments.root,
                "path": path,
                "filename": path.rsplit("/", 1)[-1],
                "sizeBytes": revision.size_bytes,
                "sha256": revision.sha256,
                "snapshotImported": True,
            }
        )
        result.attachments = (
            _provided_reference(output_files, revision, path.rsplit("/", 1)[-1]),
        )
        return result

    try:
        revision = root_files.working_revision(path)
    except RootFileError:
        return ToolResult.error("file.notAFile", field="path")
    if revision.size_bytes == 0:
        return ToolResult.error("attachment.empty")
    result = ToolResult.json(
        {
            "root": arguments.root,
            "sessionId": str(target_session_id),
            "path": path,
            "filename": path.rsplit("/", 1)[-1],
            "sizeBytes": revision.size_bytes,
            "sha256": revision.sha256,
        },
        metadata={"providedFile": True},
    )
    result.attachments = (
        ToolAttachment(
            b"",
            "application/octet-stream",
            path.rsplit("/", 1)[-1],
            kind="file",
            category="downloaded",
            file_root_id=UUID(root_files.root_identity(FileRoot.SESSION)),
            file_id=revision.file_id,
            file_revision=revision.revision,
            file_path=revision.path,
        ),
    )
    return result


def _provided_reference(
    root_files: RootFileService,
    revision: FileRevision | WorkingFileRevision,
    filename: str,
    *,
    mime_type: str = "application/octet-stream",
) -> ToolAttachment:
    return ToolAttachment(
        b"",
        mime_type,
        filename,
        kind="file",
        category="downloaded",
        file_root_id=UUID(root_files.root_identity(FileRoot.SESSION)),
        file_id=revision.file_id,
        file_revision=revision.revision,
        file_path=revision.path,
    )


async def _write_file(arguments: WriteFileArguments, context: ToolContext) -> ToolResult:
    if files_access.delegated_session_files(context, arguments.root):
        return ToolResult.error("tool.unavailable")
    if _omitted_file_content(arguments.content):
        return ToolResult.error("file.omittedContent", field="content")
    path = (
        files_access.execution_path(context, arguments.path)
        if arguments.root == "workspace"
        else context.root_files.normalize_relative_path(arguments.path)
    )
    if await files_access.active_file_is_dirty(context, arguments.root, path):
        return ToolResult.error("file.activeDirty", field="path")
    capability = (
        "session.files.write" if arguments.root == "session" else "filesystem.write"
    )
    request = files_access.filesystem_permission(
        context,
        root=arguments.root,
        path=path,
        capability=capability,
        action_kind="write_file",
        action_label="Datei schreiben",
    )
    if not await tools_access.allow(context, request):
        return ToolResult.error("permission.denied")
    if arguments.root == "workspace":
        result = await files_access.workspace_request(
            context,
            "filesystem.write",
            {
                "path": path,
                "content": arguments.content,
                **(
                    {"expectedSha256": arguments.expected_sha256}
                    if arguments.expected_sha256 is not None
                    else {}
                ),
            },
            WorkspaceWriteResult,
        )
        assert isinstance(result, WorkspaceWriteResult)
        files_access.require_workspace_path(result.path, path)
        return ToolResult.json(
            {
                "root": arguments.root,
                "written": result.path,
                "sizeBytes": result.size,
                "sha256": result.sha256,
            },
            file_actions=files_access.workspace_file_action(
                context, "write", result.path, result.size
            ),
        )
    written = await asyncio.to_thread(
        context.root_files.write_file,
        arguments.root,
        path,
        arguments.content,
        mode="replace" if arguments.expected_sha256 is not None else "create",
        expected_sha256=arguments.expected_sha256,
    )
    encoded = arguments.content.encode("utf-8")
    return ToolResult.json(
        {
            "root": arguments.root,
            "written": written,
            "sizeBytes": len(encoded),
            "sha256": hashlib.sha256(encoded).hexdigest(),
        },
        file_actions=files_access.regular_file_action(context, "write", arguments.root, written),
    )


def _omitted_file_content(content: str) -> bool:
    return content.startswith(
        ("[Persisted file content omitted: ", "[Persisted edit text omitted: ")
    )


async def _edit_file(arguments: EditFileArguments, context: ToolContext) -> ToolResult:
    if files_access.delegated_session_files(context, arguments.root):
        return ToolResult.error("tool.unavailable")
    if _omitted_file_content(arguments.old_text):
        return ToolResult.error("file.omittedContent", field="oldText")
    if _omitted_file_content(arguments.new_text):
        return ToolResult.error("file.omittedContent", field="newText")
    path = (
        files_access.execution_path(context, arguments.path)
        if arguments.root == "workspace"
        else context.root_files.normalize_relative_path(arguments.path)
    )
    if await files_access.active_file_is_dirty(context, arguments.root, path):
        return ToolResult.error("file.activeDirty", field="path")
    request = files_access.filesystem_permission(
        context,
        root=arguments.root,
        path=path,
        capability=(
            "session.files.write"
            if arguments.root == "session"
            else "filesystem.write"
        ),
        action_kind="edit_file",
        action_label="Datei bearbeiten",
    )
    if not await tools_access.allow(context, request):
        return ToolResult.error("permission.denied")
    if arguments.root == "workspace":
        result = await files_access.workspace_request(
            context,
            "filesystem.edit",
            {
                "path": path,
                "oldText": arguments.old_text,
                "newText": arguments.new_text,
                "replaceAll": arguments.replace_all,
                **(
                    {"expectedSha256": arguments.expected_sha256}
                    if arguments.expected_sha256 is not None
                    else {}
                ),
            },
            WorkspaceWriteResult,
        )
        assert isinstance(result, WorkspaceWriteResult)
        files_access.require_workspace_path(result.path, path)
        return ToolResult.json(
            {
                "root": arguments.root,
                "edited": result.path,
                "sizeBytes": result.size,
                "sha256": result.sha256,
            },
            file_actions=files_access.workspace_file_action(
                context, "write", result.path, result.size
            ),
        )
    edited = await asyncio.to_thread(
        context.root_files.edit_text_file,
        arguments.root,
        path,
        arguments.old_text,
        arguments.new_text,
        replace_all=arguments.replace_all,
        expected_sha256=arguments.expected_sha256,
    )
    edited_bytes = context.root_files.read_bytes(
        arguments.root, edited, limit=context.root_files.max_editable_text_bytes
    )
    return ToolResult.json(
        {
            "root": arguments.root,
            "edited": edited,
            "sizeBytes": len(edited_bytes),
            "sha256": hashlib.sha256(edited_bytes).hexdigest(),
        },
        file_actions=files_access.regular_file_action(context, "write", arguments.root, edited),
    )


def _workspace_patch(
    context: ToolContext,
    patch: str,
    expected_sha256: dict[str, str],
) -> tuple[str, dict[str, str], list[dict[str, str | None]]]:
    operations: list[dict[str, str | None]] = []
    rewritten = []
    prefixes = {
        "*** Add File: ": "add",
        "*** Update File: ": "update",
        "*** Delete File: ": "delete",
    }
    for line in patch.split("\n"):
        matched = next((prefix for prefix in prefixes if line.startswith(prefix)), None)
        if matched is not None:
            path = files_access.execution_path(context, line.removeprefix(matched))
            operations.append(
                {"operation": prefixes[matched], "path": path, "destination": None}
            )
            rewritten.append(f"{matched}{path}")
        elif line.startswith("*** Move to: "):
            if not operations or operations[-1]["operation"] != "update":
                raise ExecutionClientError("tool.invalidArguments")
            destination = files_access.execution_path(
                context, line.removeprefix("*** Move to: ")
            )
            operations[-1]["operation"] = "move"
            operations[-1]["destination"] = destination
            rewritten.append(f"*** Move to: {destination}")
        else:
            rewritten.append(line)
    if not operations:
        raise ExecutionClientError("tool.invalidArguments")
    revisions: dict[str, str] = {}
    for path, digest in expected_sha256.items():
        absolute = files_access.execution_path(context, path)
        if absolute in revisions:
            raise ExecutionClientError("tool.invalidArguments")
        revisions[absolute] = digest
    return "\n".join(rewritten), revisions, operations


async def _apply_patch(
    arguments: ApplyPatchArguments, context: ToolContext
) -> ToolResult:
    patch, revisions, operations = _workspace_patch(
        context, arguments.patch, arguments.expected_sha256
    )
    for operation in operations:
        source = str(operation["path"])
        if operation["operation"] != "add" and await files_access.active_file_is_dirty(
            context, "workspace", source
        ):
            return ToolResult.error("file.activeDirty", field="patch")
        requests = []
        if operation["operation"] in {"delete", "move"}:
            requests.append(
                files_access.filesystem_permission(
                    context,
                    root="workspace",
                    path=source,
                    capability="filesystem.delete",
                    action_kind="apply_patch",
                    action_label="Datei ändern",
                )
            )
        if operation["operation"] in {"add", "update"}:
            requests.append(
                files_access.filesystem_permission(
                    context,
                    root="workspace",
                    path=source,
                    capability="filesystem.write",
                    action_kind="apply_patch",
                    action_label="Datei ändern",
                )
            )
        if operation["destination"] is not None:
            requests.append(
                files_access.filesystem_permission(
                    context,
                    root="workspace",
                    path=str(operation["destination"]),
                    capability="filesystem.write",
                    action_kind="apply_patch",
                    action_label="Datei verschieben",
                )
            )
        for request in requests:
            if not await tools_access.allow(context, request):
                return ToolResult.error("permission.denied")
    result = await files_access.workspace_request(
        context,
        "filesystem.patch",
        {"patch": patch, "expectedSha256": revisions},
        WorkspacePatchResult,
    )
    assert isinstance(result, WorkspacePatchResult)
    if len(result.files) != len(operations) or any(
        file.operation != operation["operation"]
        or file.path != operation["path"]
        or file.destination != operation["destination"]
        for file, operation in zip(result.files, operations, strict=True)
    ):
        raise ExecutionClientError("executionAgent.protocolError")
    file_actions = tuple(
        files_access.workspace_file_action(
            context,
            "move" if file.operation == "move" else "write",
            file.destination or file.path,
            file.size,
        )[0]
        for file in result.files
        if file.operation != "delete"
    )
    return ToolResult.json(
        result.model_dump(mode="json", by_alias=True), file_actions=file_actions
    )


async def _create_directory(
    arguments: CreateDirectoryArguments, context: ToolContext
) -> ToolResult:
    if files_access.delegated_session_files(context, arguments.root):
        return ToolResult.error("tool.unavailable")
    request = files_access.filesystem_permission(
        context,
        root=arguments.root,
        path=arguments.path,
        capability=(
            "session.files.write" if arguments.root == "session" else "filesystem.write"
        ),
        action_kind="create_directory",
        action_label="Ordner erstellen",
    )
    if not await tools_access.allow(context, request):
        return ToolResult.error("permission.denied")
    if arguments.root == "workspace":
        path = files_access.execution_path(context, arguments.path)
        result = await files_access.workspace_request(
            context,
            "filesystem.mkdir",
            {
                "path": path,
                "parents": False,
            },
            WorkspacePathResult,
        )
        assert isinstance(result, WorkspacePathResult)
        files_access.require_workspace_path(result.path, path)
        return ToolResult.json({"root": arguments.root, "created": result.path})
    created = await asyncio.to_thread(
        context.root_files.create_directory, arguments.root, arguments.path
    )
    return ToolResult.json({"root": arguments.root, "created": created})


async def _delete_path(arguments: DeletePathArguments, context: ToolContext) -> ToolResult:
    if files_access.delegated_session_files(context, arguments.root):
        return ToolResult.error("tool.unavailable")
    request = files_access.filesystem_permission(
        context,
        root=arguments.root,
        path=arguments.path,
        capability="filesystem.delete",
        action_kind="delete_path",
        action_label="Pfad löschen",
    )
    if not await tools_access.allow(context, request):
        return ToolResult.error("permission.denied")
    if arguments.root == "workspace":
        await files_access.workspace_request(
            context,
            "filesystem.delete",
            {
                "path": files_access.execution_path(context, arguments.path),
                "recursive": arguments.recursive,
            },
            WorkspaceDeleteResult,
        )
    else:
        await asyncio.to_thread(
            context.root_files.delete_path,
            arguments.root,
            arguments.path,
            recursive=arguments.recursive,
        )
    return ToolResult.json({"root": arguments.root, "deleted": arguments.path})


async def _copy_path(arguments: CopyPathArguments, context: ToolContext) -> ToolResult:
    if files_access.delegated_session_files(
        context, arguments.source_root, arguments.destination_root
    ):
        return ToolResult.error("tool.unavailable")
    cross_root = arguments.source_root != arguments.destination_root
    if cross_root:
        client = files_access.workspace_client(context)
        required = (
            ("filesystem.stat", "filesystem.export")
            if arguments.source_root == "workspace"
            else ("filesystem.import",)
        )
        if not all(client.supports(operation) for operation in required):
            return ToolResult.error("executionAgent.operationUnavailable")
    source_request = files_access.filesystem_permission(
        context,
        root=arguments.source_root,
        path=arguments.source_path,
        capability="filesystem.read",
        action_kind="copy_path",
        action_label="Quelle kopieren",
    )
    destination_request = files_access.filesystem_permission(
        context,
        root=arguments.destination_root,
        path=arguments.destination_path,
        capability=(
            "session.files.write"
            if arguments.destination_root == "session"
            else "filesystem.write"
        ),
        action_kind="copy_path",
        action_label="Ziel erstellen",
    )
    if not await tools_access.allow(context, source_request) or not await tools_access.allow(context, destination_request):
        return ToolResult.error("permission.denied")
    if arguments.source_root == arguments.destination_root == "workspace":
        destination = files_access.execution_path(context, arguments.destination_path)
        result = await files_access.workspace_request(
            context,
            "filesystem.copy",
            {
                "path": files_access.execution_path(context, arguments.source_path),
                "destination": destination,
            },
            WorkspacePathResult,
        )
        assert isinstance(result, WorkspacePathResult)
        files_access.require_workspace_path(result.path, destination)
        file_actions: tuple[ToolFileAction, ...] = ()
        client = files_access.workspace_client(context)
        if client.supports("filesystem.stat"):
            try:
                stat_result = await client.request(
                    "filesystem.stat", {"path": result.path}, WorkspaceStatResult
                )
                if (
                    isinstance(stat_result, WorkspaceStatResult)
                    and stat_result.path == result.path
                    and stat_result.kind == "file"
                    and stat_result.size is not None
                ):
                    file_actions = files_access.workspace_file_action(
                        context, "copy", result.path, stat_result.size
                    )
            except ExecutionClientError:
                pass
        return ToolResult.json(
            {"root": arguments.destination_root, "copied": result.path},
            file_actions=file_actions,
        )
    if cross_root:
        source = (
            files_access.execution_path(context, arguments.source_path)
            if arguments.source_root == "workspace"
            else context.root_files.normalize_relative_path(arguments.source_path)
        )
        destination = (
            files_access.execution_path(context, arguments.destination_path)
            if arguments.destination_root == "workspace"
            else context.root_files.normalize_relative_path(arguments.destination_path)
        )
        client = files_access.workspace_client(context)
        if arguments.source_root == "workspace":
            data = await client.read_bytes(
                source, limit=context.root_files.MAX_OPERATION_BYTES
            )
            copied = await asyncio.to_thread(
                context.root_files.create_file,
                FileRoot.SESSION,
                destination,
                BytesIO(data),
                max_bytes=context.root_files.MAX_OPERATION_BYTES,
            )
            return ToolResult.json(
                {"root": arguments.destination_root, "copied": copied.path},
                file_actions=files_access.regular_file_action(
                    context, "copy", arguments.destination_root, copied.path
                ),
            )
        with context.root_files.open_file(FileRoot.SESSION, source) as handle:
            copied = await client.write_bytes(
                destination,
                handle,
                limit=context.root_files.MAX_OPERATION_BYTES,
            )
        return ToolResult.json(
            {"root": arguments.destination_root, "copied": copied.path},
            file_actions=files_access.workspace_file_action(
                context, "copy", copied.path, copied.size
            ),
        )
    copied = await asyncio.to_thread(
        context.root_files.copy_path,
        arguments.source_root,
        arguments.source_path,
        arguments.destination_root,
        arguments.destination_path,
    )
    return ToolResult.json(
        {"root": arguments.destination_root, "copied": copied},
        file_actions=files_access.regular_file_action(
            context, "copy", arguments.destination_root, copied
        ),
    )


async def _move_path(arguments: MovePathArguments, context: ToolContext) -> ToolResult:
    if files_access.delegated_session_files(
        context, arguments.source_root, arguments.destination_root
    ):
        return ToolResult.error("tool.unavailable")
    if arguments.source_root != arguments.destination_root:
        return ToolResult.error("file.crossBoundaryMoveUnavailable")
    source_request = files_access.filesystem_permission(
        context,
        root=arguments.source_root,
        path=arguments.source_path,
        capability=(
            "session.files.write"
            if arguments.source_root == "session"
            else "filesystem.delete"
        ),
        action_kind="move_path",
        action_label="Quelle verschieben",
    )
    destination_request = files_access.filesystem_permission(
        context,
        root=arguments.destination_root,
        path=arguments.destination_path,
        capability=(
            "session.files.write"
            if arguments.destination_root == "session"
            else "filesystem.write"
        ),
        action_kind="move_path",
        action_label="Ziel erstellen",
    )
    if not await tools_access.allow(context, source_request) or not await tools_access.allow(context, destination_request):
        return ToolResult.error("permission.denied")
    if arguments.source_root == arguments.destination_root == "workspace":
        destination = files_access.execution_path(context, arguments.destination_path)
        result = await files_access.workspace_request(
            context,
            "filesystem.move",
            {
                "path": files_access.execution_path(context, arguments.source_path),
                "destination": destination,
            },
            WorkspacePathResult,
        )
        assert isinstance(result, WorkspacePathResult)
        files_access.require_workspace_path(result.path, destination)
        file_actions: tuple[ToolFileAction, ...] = ()
        client = files_access.workspace_client(context)
        if client.supports("filesystem.stat"):
            try:
                stat_result = await client.request(
                    "filesystem.stat", {"path": result.path}, WorkspaceStatResult
                )
                if (
                    isinstance(stat_result, WorkspaceStatResult)
                    and stat_result.path == result.path
                    and stat_result.kind == "file"
                    and stat_result.size is not None
                ):
                    file_actions = files_access.workspace_file_action(
                        context, "move", result.path, stat_result.size
                    )
            except ExecutionClientError:
                pass
        return ToolResult.json(
            {"root": arguments.destination_root, "moved": result.path},
            file_actions=file_actions,
        )
    moved = await asyncio.to_thread(
        context.root_files.move_path,
        arguments.source_root,
        arguments.source_path,
        arguments.destination_root,
        arguments.destination_path,
    )
    return ToolResult.json(
        {"root": arguments.destination_root, "moved": moved},
        file_actions=files_access.regular_file_action(
            context, "move", arguments.destination_root, moved
        ),
    )


READ = ToolDefinition(
            "read",
            "Read a directory, UTF-8 text, or PNG/JPEG/WebP/GIF image. "
            "Pagination applies to directories and text. "
            "To inspect an image, read its source path in root=computer or root=files directly; "
            "do not copy it or call provide_file first.",
            ReadArguments,
            _read,
            tool_type="shared",
            group="filesystem",
            label="Pfad lesen",
            icon="description",
            actions=(_SESSION_FILES_READ, _FILESYSTEM_READ),
            alternative_action_groups=(
                frozenset({"session.files.read", "filesystem.read"}),
            ),
        )

GLOB = ToolDefinition(
            "glob",
            "Glob files recursively, max 100. Skip hidden/.git/symlinks. "
            "root=computer honors ignores; root=files overrides them. truncated=true: more/size limit.",
            GlobArguments,
            _glob,
            tool_type="shared",
            group="filesystem",
            label="Dateien suchen",
            icon="folder_open",
            actions=(_SESSION_FILES_READ, _FILESYSTEM_READ),
            alternative_action_groups=(
                frozenset({"session.files.read", "filesystem.read"}),
            ),
        )

GREP = ToolDefinition(
            "grep",
            "Regex in a file or recursively in a directory; include glob filters directories only. "
            "Explicit files bypass hidden/ignore discovery filters. Max 100 lines, 2000 characters/line. "
            "Skip .git/binary/symlinks. Directory root=computer skips hidden/ignored; "
            "root=files includes hidden; include overrides ignores. "
            "truncated=true: more/clipped text/size limit.",
            GrepArguments,
            _grep,
            tool_type="shared",
            group="filesystem",
            label="Dateiinhalte durchsuchen",
            icon="manage_search",
            actions=(_SESSION_FILES_READ, _FILESYSTEM_READ),
            alternative_action_groups=(
                frozenset({"session.files.read", "filesystem.read"}),
            ),
        )

PROVIDE_FILE = ToolDefinition(
            "provide_file",
            "On explicit file, image result, download, or ZIP request, provide path (file/folder) or paths "
            "(files). Folders/lists become ZIPs.",
            ProvideFileArguments,
            _provide_file,
            tool_type="shared",
            group="filesystem",
            label="Datei bereitstellen",
            icon="download",
            actions=(_FILE_PROVIDE,),
        )

WRITE_FILE = ToolDefinition(
            "write_file",
            "Write complete UTF-8 content to one file, creating or replacing it.",
            WriteFileArguments,
            _write_file,
            tool_type="shared",
            group="filesystem",
            label="Datei schreiben",
            icon="edit_document",
            actions=(_SESSION_FILES_WRITE, _FILESYSTEM_WRITE),
            alternative_action_groups=(
                frozenset({"session.files.write", "filesystem.write"}),
            ),
        )

EDIT_FILE = ToolDefinition(
            "edit_file",
            "Replace exact text in one existing UTF-8 file. oldText must be unique unless replaceAll is true.",
            EditFileArguments,
            _edit_file,
            tool_type="shared",
            group="filesystem",
            label="Datei bearbeiten",
            icon="edit_note",
            actions=(_SESSION_FILES_WRITE, _FILESYSTEM_WRITE),
            alternative_action_groups=(
                frozenset({"session.files.write", "filesystem.write"}),
            ),
        )

CREATE_DIRECTORY = ToolDefinition(
            "create_directory",
            "Create one directory; its parent must exist.",
            CreateDirectoryArguments,
            _create_directory,
            tool_type="shared",
            group="filesystem",
            label="Ordner erstellen",
            icon="create_new_folder",
            actions=(_SESSION_FILES_WRITE, _FILESYSTEM_WRITE),
            alternative_action_groups=(
                frozenset({"session.files.write", "filesystem.write"}),
            ),
        )

DELETE_PATH = ToolDefinition(
            "delete_path",
            "Delete one path; non-empty directories require recursive=true.",
            DeletePathArguments,
            _delete_path,
            tool_type="shared",
            group="filesystem",
            label="Pfad löschen",
            icon="delete_outline",
            actions=(_FILESYSTEM_DELETE,),
        )

COPY_PATH = ToolDefinition(
            "copy_path",
            "Copy a path to an exact new destination path; never overwrite.",
            CopyPathArguments,
            _copy_path,
            tool_type="shared",
            group="filesystem",
            label="Pfad kopieren",
            icon="content_copy",
            actions=(
                _SESSION_FILES_READ, _FILESYSTEM_READ,
                _SESSION_FILES_WRITE, _FILESYSTEM_WRITE,
            ),
            alternative_action_groups=(
                frozenset({"session.files.read", "filesystem.read"}),
                frozenset({"session.files.write", "filesystem.write"}),
            ),
        )

MOVE_PATH = ToolDefinition(
            "move_path",
            "Move a path to an exact new destination path; never overwrite.",
            MovePathArguments,
            _move_path,
            tool_type="shared",
            group="filesystem",
            label="Pfad verschieben",
            icon="drive_file_move",
            actions=(_FILESYSTEM_DELETE, _SESSION_FILES_WRITE, _FILESYSTEM_WRITE),
            alternative_action_groups=(
                frozenset({"session.files.write", "filesystem.delete"}),
                frozenset({"session.files.write", "filesystem.write"}),
            ),
        )

APPLY_PATCH = ToolDefinition(
            "apply_patch",
            "Apply one bounded patch to computer files in the current directory. Paths may be relative "
            "or absolute. Include expectedSha256 for every updated, moved, or deleted source file; add "
            "operations do not use a revision. Move operations cannot also edit content.",
            ApplyPatchArguments,
            _apply_patch,
            tool_type="computer",
            required_operation="filesystem.patch",
            group="filesystem",
            label="Patch anwenden",
            icon="difference",
            actions=(_FILESYSTEM_WRITE, _FILESYSTEM_DELETE),
            alternative_action_groups=(
                frozenset({"filesystem.write", "filesystem.delete"}),
            ),
        )
