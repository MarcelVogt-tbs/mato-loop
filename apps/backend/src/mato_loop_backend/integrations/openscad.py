from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import stat
import struct
import zipfile
from dataclasses import replace
from pathlib import PurePosixPath
from tempfile import SpooledTemporaryFile
from typing import Any, BinaryIO, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from mato_loop_backend.agent.tool_contracts import ToolAction
from mato_loop_backend.attachments import (
    MAX_FILE_ATTACHMENT_BYTES,
    AttachmentError,
    validate_image,
)
from mato_loop_backend.execution.client import (
    ExecutionClient,
    ExecutionClientError,
    ExecutionResult,
    _relative_path,
)
from mato_loop_backend.execution.manager import (
    ExecutionAgentError,
    execution_agent_connections,
)
from mato_loop_backend.execution.transfers import MAX_TRANSFER_BYTES, transfer_tickets
from mato_loop_backend.integrations.contracts import (
    IntegrationAdapterDefinition,
    IntegrationContributions,
    ToolContribution,
)
from mato_loop_backend.models import AgentRun
from mato_loop_backend.settings import (
    IntegrationInstance,
    IntegrationSettings,
    SettingsStore,
    ToolExecutionPolicy,
)
from mato_loop_backend.workspace import FileRoot, RootFileError, RootFileService

_VIEWS = ("isometric", "front", "right", "top")
_MAX_RENDER_ATTACHMENTS_BYTES = MAX_FILE_ATTACHMENT_BYTES
_RENDER_TIMEOUT_SECONDS = 300.0
_EXPORT_TIMEOUT_SECONDS = 120.0
_DIRECTORY_FLAGS = (
    os.O_RDONLY
    | getattr(os, "O_DIRECTORY", 0)
    | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_CLOEXEC", 0)
)
_FILE_FLAGS = (
    os.O_RDONLY
    | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_CLOEXEC", 0)
    | getattr(os, "O_NONBLOCK", 0)
)


def _camel(value: str) -> str:
    first, *rest = value.split("_")
    return first + "".join(part.capitalize() for part in rest)


class _OpenScadModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=_camel,
        populate_by_name=True,
        serialize_by_alias=True,
        extra="forbid",
        strict=True,
    )


def _absolute_path(value: str) -> str:
    if not value.startswith("/") or "\0" in value or len(value) > 4_096:
        raise ValueError("path must be absolute")
    return value


class OpenScadConfig(_OpenScadModel):
    binary_path: str

    _validate_binary_path = field_validator("binary_path")(_absolute_path)


class OpenScadRenderArguments(_OpenScadModel):
    root: Literal["session", "workspace"]
    path: str = Field(min_length=1, max_length=1_000)
    project_path: str | None = Field(default=None, min_length=1, max_length=1_000)

    @field_validator("path")
    @classmethod
    def scad_path(cls, value: str) -> str:
        normalized = _relative_path(value)
        if normalized == "." or PurePosixPath(normalized).suffix.casefold() != ".scad":
            raise ValueError("path must identify an OpenSCAD file")
        return normalized

    _validate_project_path = field_validator("project_path")(
        lambda value: _relative_path(value) if value is not None else value
    )

    @model_validator(mode="after")
    def entrypoint_is_in_project(self) -> OpenScadRenderArguments:
        project = self.project_path or PurePosixPath(self.path).parent.as_posix()
        try:
            PurePosixPath(self.path).relative_to(PurePosixPath(project))
        except ValueError as exc:
            raise ValueError("path must be inside projectPath") from exc
        return self


class ArtifactDescriptor(_OpenScadModel):
    artifact_id: str = Field(min_length=1, max_length=128)
    backend_id: str = Field(min_length=1, max_length=128)
    session_id: str = Field(min_length=1, max_length=128)
    path: str
    filename: str = Field(min_length=1, max_length=255)
    size: int = Field(ge=1, le=MAX_TRANSFER_BYTES)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    content_type: str = Field(min_length=1, max_length=255)

    _validate_path = field_validator("path")(_relative_path)

    @field_validator("filename")
    @classmethod
    def plain_filename(cls, value: str) -> str:
        if value in {".", ".."} or "/" in value or "\\" in value or "\0" in value:
            raise ValueError("invalid artifact filename")
        return value


class OpenScadPreview(_OpenScadModel):
    view: Literal["isometric", "front", "right", "top"]
    artifact: ArtifactDescriptor


class OpenScadRenderResult(ExecutionResult):
    model_config = _OpenScadModel.model_config

    model: ArtifactDescriptor
    previews: list[OpenScadPreview] = Field(min_length=4, max_length=4)
    diagnostics: str = Field(max_length=256 * 1_024)
    diagnostics_truncated: bool

    @model_validator(mode="after")
    def complete_views(self) -> OpenScadRenderResult:
        if {preview.view for preview in self.previews} != set(_VIEWS):
            raise ValueError("previews must contain every view")
        return self


class CacheExportResult(ExecutionResult):
    size: int = Field(ge=1, le=MAX_FILE_ATTACHMENT_BYTES)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class OpenScadDiscoverResult(ExecutionResult):
    available: bool
    executable_path: str | None = None
    version: str | None = Field(default=None, min_length=1, max_length=500)
    headless_preview: bool

    _validate_executable_path = field_validator("executable_path")(
        lambda value: _absolute_path(value) if value is not None else value
    )


class OpenScadTestResult(ExecutionResult):
    ok: Literal[True]
    executable_path: str
    version: str = Field(min_length=1, max_length=500)
    headless_preview: bool

    _validate_executable_path = field_validator("executable_path")(_absolute_path)


def _revision(instance: IntegrationInstance) -> str:
    return json.dumps(
        instance.model_dump(mode="json"),
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


def _project_parts(arguments: OpenScadRenderArguments) -> tuple[str, str]:
    project = arguments.project_path or PurePosixPath(arguments.path).parent.as_posix()
    entrypoint = PurePosixPath(arguments.path).relative_to(PurePosixPath(project)).as_posix()
    return project, entrypoint


def _zip_project(root_files: RootFileService, project_path: str) -> tuple[BinaryIO, int, str]:
    output = SpooledTemporaryFile(max_size=1024 * 1024, mode="w+b")
    entries = 0
    source_bytes = 0

    def add_directory(archive: zipfile.ZipFile, descriptor: int, prefix: str = "") -> None:
        nonlocal entries, source_bytes
        for name in sorted(os.listdir(descriptor)):
            relative = _relative_path(f"{prefix}/{name}" if prefix else name)
            metadata = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
            entries += 1
            if entries > RootFileService.MAX_OPERATION_ENTRIES:
                raise RootFileError("file.operationTooLarge", "Project has too many entries")
            if stat.S_ISDIR(metadata.st_mode):
                child = os.open(name, _DIRECTORY_FLAGS, dir_fd=descriptor)
                try:
                    opened = os.fstat(child)
                    if (
                        not stat.S_ISDIR(opened.st_mode)
                        or (opened.st_dev, opened.st_ino)
                        != (metadata.st_dev, metadata.st_ino)
                    ):
                        raise RootFileError("file.changed", "Project directory changed")
                    add_directory(archive, child, relative)
                finally:
                    os.close(child)
                continue
            if not stat.S_ISREG(metadata.st_mode):
                raise RootFileError("file.unsupportedType", "Project contains an unsupported entry")
            source = os.open(name, _FILE_FLAGS, dir_fd=descriptor)
            with os.fdopen(source, "rb") as handle, archive.open(relative, "w") as target:
                opened = os.fstat(handle.fileno())
                if not stat.S_ISREG(opened.st_mode):
                    raise RootFileError(
                        "file.unsupportedType", "Project contains an unsupported entry"
                    )
                if (
                    (opened.st_dev, opened.st_ino, opened.st_size)
                    != (metadata.st_dev, metadata.st_ino, metadata.st_size)
                ):
                    raise RootFileError("file.changed", "Project file changed")
                file_bytes = 0
                while chunk := handle.read(1024 * 1024):
                    file_bytes += len(chunk)
                    source_bytes += len(chunk)
                    if file_bytes > MAX_TRANSFER_BYTES or source_bytes > MAX_TRANSFER_BYTES:
                        raise RootFileError("file.operationTooLarge", "Project is too large")
                    target.write(chunk)
                if file_bytes != opened.st_size:
                    raise RootFileError("file.changed", "Project file changed")

    try:
        with root_files.open_directory_descriptor(FileRoot.SESSION, project_path) as descriptor:
            with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                add_directory(archive, descriptor)
        size = output.tell()
        if size <= 0 or size > MAX_TRANSFER_BYTES:
            raise RootFileError("file.operationTooLarge", "Project archive is too large")
        output.seek(0)
        digest = hashlib.file_digest(output, "sha256").hexdigest()
        output.seek(0)
        return output, size, digest
    except BaseException:
        output.close()
        raise


def validate_binary_stl(data: bytes) -> dict[str, Any]:
    if not 84 <= len(data) <= MAX_FILE_ATTACHMENT_BYTES:
        raise ValueError("invalid binary STL size")
    triangles = struct.unpack_from("<I", data, 80)[0]
    if triangles == 0 or len(data) != 84 + triangles * 50:
        raise ValueError("invalid binary STL structure")
    minimum = [math.inf, math.inf, math.inf]
    maximum = [-math.inf, -math.inf, -math.inf]
    for triangle in struct.iter_unpack("<12fH", memoryview(data)[84:]):
        if not all(math.isfinite(value) for value in triangle[:12]):
            raise ValueError("binary STL contains non-finite coordinates")
        for offset in (3, 6, 9):
            for axis in range(3):
                value = triangle[offset + axis]
                minimum[axis] = min(minimum[axis], value)
                maximum[axis] = max(maximum[axis], value)
    axes = ("x", "y", "z")
    return {
        "triangleCount": triangles,
        "bounds": {
            "min": dict(zip(axes, minimum, strict=True)),
            "max": dict(zip(axes, maximum, strict=True)),
        },
        "dimensions": {
            axis: maximum[index] - minimum[index]
            for index, axis in enumerate(axes)
        },
    }


def _validate_descriptor(
    artifact: ArtifactDescriptor,
    *,
    agent_id: UUID,
    session_id: UUID,
    content_type: str,
    suffix: str,
) -> None:
    if (
        artifact.backend_id != str(agent_id)
        or artifact.session_id != str(session_id)
        or artifact.size > MAX_FILE_ATTACHMENT_BYTES
        or artifact.content_type != content_type
        or PurePosixPath(artifact.filename).suffix.casefold() != suffix
    ):
        raise ExecutionClientError("executionAgent.protocolError")


async def render_project(
    client: ExecutionClient,
    *,
    executable_path: str,
    session_id: UUID,
    arguments: OpenScadRenderArguments,
    root_files: RootFileService,
) -> OpenScadRenderResult:
    project_path, entrypoint = _project_parts(arguments)
    source: dict[str, Any]
    ticket_id = None
    if arguments.root == "workspace":
        source = {"type": "workspace", "path": project_path}
    else:
        archive, size, digest = await asyncio.to_thread(
            _zip_project, root_files, project_path
        )
        descriptor = ArtifactDescriptor(
            artifactId=f"project-{digest}",
            backendId=str(client.agent_id),
            sessionId=str(session_id),
            path=f"projects/{digest}.zip",
            filename="source.zip",
            size=size,
            sha256=digest,
            contentType="application/zip",
        )
        grant, _ = transfer_tickets.create_download(
            client.agent_id, archive, size=size, sha256=digest
        )
        ticket_id = grant.ticket_id
        try:
            transfer_url = execution_agent_connections.transfer_url(
                client.agent_id, grant.ticket_id
            )
        except ExecutionAgentError as exc:
            transfer_tickets.revoke(grant.ticket_id)
            raise ExecutionClientError(exc.code) from exc
        source = {
            "type": "archive",
            "artifact": descriptor.model_dump(mode="json", by_alias=True),
            "transfer": {
                "url": transfer_url,
                "token": grant.token,
            },
        }
    try:
        result = await client.request(
            "integration.openscad.render",
            {
                "executablePath": executable_path,
                "sessionId": str(session_id),
                "entrypoint": entrypoint,
                "source": source,
            },
            OpenScadRenderResult,
            timeout=_RENDER_TIMEOUT_SECONDS,
        )
    finally:
        if ticket_id is not None:
            transfer_tickets.revoke(ticket_id)
    assert isinstance(result, OpenScadRenderResult)
    _validate_descriptor(
        result.model,
        agent_id=client.agent_id,
        session_id=session_id,
        content_type="model/stl",
        suffix=".stl",
    )
    for preview in result.previews:
        _validate_descriptor(
            preview.artifact,
            agent_id=client.agent_id,
            session_id=session_id,
            content_type="image/png",
            suffix=".png",
        )
    return result


async def export_artifact(client: ExecutionClient, artifact: ArtifactDescriptor) -> bytes:
    grant, ticket = transfer_tickets.create_upload(
        client.agent_id, size=artifact.size, sha256=artifact.sha256
    )
    try:
        try:
            transfer_url = execution_agent_connections.transfer_url(
                client.agent_id, grant.ticket_id
            )
        except ExecutionAgentError as exc:
            raise ExecutionClientError(exc.code) from exc
        result = await client.request(
            "cache.export",
            {
                "artifact": artifact.model_dump(mode="json", by_alias=True),
                "transfer": {
                    "url": transfer_url,
                    "token": grant.token,
                },
            },
            CacheExportResult,
            timeout=_EXPORT_TIMEOUT_SECONDS,
        )
        assert isinstance(result, CacheExportResult)
        if (
            result.size != artifact.size
            or result.sha256 != artifact.sha256
            or ticket.actual_size != artifact.size
            or ticket.actual_sha256 != artifact.sha256
            or ticket.content is None
        ):
            raise ExecutionClientError("executionAgent.protocolError")
        data = ticket.content.read(MAX_FILE_ATTACHMENT_BYTES + 1)
        if len(data) != artifact.size or hashlib.sha256(data).hexdigest() != artifact.sha256:
            raise ExecutionClientError("executionAgent.protocolError")
        return data
    finally:
        transfer_tickets.revoke(grant.ticket_id)


def _authorization(arguments: OpenScadRenderArguments, context: Any) -> tuple[Any, ...]:
    from mato_loop_backend.agent.permissions import integration_operation_permission
    from mato_loop_backend.agent.tools import _filesystem_permission

    project_path, _ = _project_parts(arguments)
    read = _filesystem_permission(
        context,
        root=arguments.root,
        path=project_path,
        capability="filesystem.read",
        action_kind="render_openscad_source",
        action_label="OpenSCAD-Projekt lesen",
    )
    render = integration_operation_permission(
        capability="cad.render",
        integration_id=UUID(context.bound_options["integration_id"]),
        binding_revision=context.bound_options["binding_revision"],
        operation_key=hashlib.sha256(
            json.dumps(
                arguments.model_dump(mode="json", by_alias=True),
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            ).encode()
        ).hexdigest(),
        title="OpenSCAD-Modell rendern?",
        summary="Der Agent möchte ein OpenSCAD-Projekt rendern.",
        resource_display=f"{arguments.root}:{project_path}",
        action_kind="cad_render",
    )
    return (
        replace(
            render,
            approval_details={
                "openscad": arguments.model_dump(
                    mode="json", by_alias=True, exclude_none=True
                )
            },
        ),
        replace(read, use_integration_policy=False),
    )


def _diagnostic_params(exc: ExecutionClientError) -> dict[str, Any] | None:
    if not exc.code.startswith("openscad.") or not isinstance(exc.params, dict):
        return None
    diagnostics = exc.params.get("diagnostics")
    truncated = exc.params.get("diagnosticsTruncated")
    if not isinstance(diagnostics, str) or not isinstance(truncated, bool):
        return None
    return {
        "diagnostics": diagnostics[: 64 * 1024],
        "diagnosticsTruncated": truncated or len(diagnostics) > 64 * 1024,
    }


async def render_openscad(arguments: OpenScadRenderArguments, context: Any):
    from mato_loop_backend.agent.tools import ToolAttachment, ToolResult

    integration_id = context.bound_options.get("integration_id")
    expected_revision = context.bound_options.get("binding_revision")
    settings = await SettingsStore.load(context.db, IntegrationSettings)
    instance = next(
        (
            item
            for item in settings.instances
            if str(item.id) == integration_id
            and item.integration_type == "openscad"
            and item.adapter_type == "openscad"
        ),
        None,
    )
    run = await context.db.get(AgentRun, context.run_id)
    required = {"integration.openscad.render", "cache.export"}
    if (
        instance is None
        or not instance.enabled
        or _revision(instance) != expected_revision
        or instance.execution_agent_id is None
        or run is None
        or run.execution_agent_id != instance.execution_agent_id
        or not required <= set(run.execution_operations or [])
        or context.execution_client is None
        or context.execution_client.agent_id != instance.execution_agent_id
        or not required <= context.execution_client.operations
    ):
        return ToolResult.error("tool.unavailable")
    try:
        config = OpenScadConfig.model_validate(instance.config)
        rendered = await render_project(
            context.execution_client,
            executable_path=config.binary_path,
            session_id=context.session.id,
            arguments=arguments,
            root_files=context.root_files,
        )
        if (
            rendered.model.size
            + sum(preview.artifact.size for preview in rendered.previews)
            > _MAX_RENDER_ATTACHMENTS_BYTES
        ):
            return ToolResult.error("integration.attachmentTooLarge")
        model = await export_artifact(context.execution_client, rendered.model)
        stl_metadata = await asyncio.to_thread(validate_binary_stl, model)
        previews = []
        previews_by_view = {preview.view: preview for preview in rendered.previews}
        for view in _VIEWS:
            preview = previews_by_view[view]
            data = await export_artifact(context.execution_client, preview.artifact)
            await asyncio.to_thread(validate_image, data, "image/png")
            previews.append((preview, data))
    except ExecutionClientError as exc:
        return ToolResult.error(exc.code, params=_diagnostic_params(exc))
    except RootFileError as exc:
        return ToolResult.error(exc.code)
    except (AttachmentError, ValueError, OSError, zipfile.BadZipFile):
        return ToolResult.error("openscad.invalidArtifact")

    payload = {
        "model": rendered.model.filename,
        "previews": [preview.view for preview, _ in previews],
        "diagnostics": rendered.diagnostics,
        "diagnosticsTruncated": rendered.diagnostics_truncated,
        "stlMetadata": stl_metadata,
    }
    return ToolResult(
        json.dumps(payload, ensure_ascii=False),
        {},
        attachments=(
            ToolAttachment(
                model,
                "model/stl",
                rendered.model.filename,
                kind="file",
            ),
            *(
                ToolAttachment(data, "image/png", preview.artifact.filename)
                for preview, data in previews
            ),
        ),
    )


_CAD_RENDER = ToolAction("cad.render", "CAD rendern", ToolExecutionPolicy.ASK)
_FILESYSTEM_READ = ToolAction("filesystem.read", "Lesen", ToolExecutionPolicy.AUTO)

OPENSCAD_TOOL = ToolContribution(
    name="render_openscad",
    description=(
        "Render one .scad entrypoint from an explicit session or workspace project. "
        "Returns a downloadable binary STL and four PNG views."
    ),
    arguments_model=OpenScadRenderArguments,
    handler=render_openscad,
    label="OpenSCAD-Modell rendern",
    icon="view_in_ar",
    actions=(_CAD_RENDER, _FILESYSTEM_READ),
    authorization_factory=_authorization,
    execution_operation="integration.openscad.render",
    execution_config_arguments=("binaryPath",),
    required_execution_operations=("cache.export",),
    result_model=OpenScadRenderResult,
)


def _contributions(_instance: IntegrationInstance) -> IntegrationContributions:
    if _instance._execution_operations is not None and not {
        "integration.openscad.render",
        "cache.export",
    } <= _instance._execution_operations:
        return IntegrationContributions()
    return IntegrationContributions(
        prompt_fragments=(
            "Use render_openscad, not shell_run or process_exec, for every OpenSCAD render. A "
            "render is complete only when render_openscad returns its validated binary STL and "
            "preview attachments. Create or edit the .scad source yourself with file tools before "
            "rendering. Use the "
            "user's explicit safe filename and path. Append .scad only when the user omitted an "
            "extension; otherwise choose a concise descriptive filename. Put new work in session "
            "files by default, and use the workspace only when requested or when clearly editing an "
            "existing workspace project. Preserve stated dimensions. If required dimensions are "
            "missing, ask one grouped question; otherwise state reasonable assumptions. Read files "
            "before modifying them and do not overwrite unrelated content. Render after every "
            "material change. Inspect controlled diagnostics, STL triangle count, bounds and "
            "dimensions, and all four views. Correct problems and rerender. Never claim success "
            "before a successful render. In the final response, name the source file and STL file.",
        )
    )


OPENSCAD_ADAPTER = IntegrationAdapterDefinition(
    integration_type="openscad",
    adapter_type="openscad",
    config_model=OpenScadConfig,
    contribution_factory=_contributions,
    resource_actions=(_CAD_RENDER,),
    execution_location="agent",
    execution_kind="openscad",
    execution_tools=(OPENSCAD_TOOL,),
)


__all__ = [
    "ArtifactDescriptor",
    "CacheExportResult",
    "OPENSCAD_ADAPTER",
    "OPENSCAD_TOOL",
    "OpenScadConfig",
    "OpenScadDiscoverResult",
    "OpenScadRenderArguments",
    "OpenScadRenderResult",
    "OpenScadTestResult",
    "export_artifact",
    "render_openscad",
    "render_project",
    "validate_binary_stl",
]
