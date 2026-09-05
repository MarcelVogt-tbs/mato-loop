from __future__ import annotations

import base64
import binascii
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, BinaryIO, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from mato_loop_backend.execution.manager import (
    ExecutionAgentError,
    execution_agent_connections,
)
from mato_loop_backend.execution.protocol import EventMessage
from mato_loop_backend.execution.transfers import TransferError, stage_file, transfer_tickets


class ExecutionResult(BaseModel):
    model_config = ConfigDict(
        alias_generator=lambda value: value.split("_")[0]
        + "".join(part.capitalize() for part in value.split("_")[1:]),
        populate_by_name=True,
        extra="ignore",
        strict=True,
    )


def _absolute_path(value: str) -> str:
    parts = value.split("/")[1:]
    if (
        not value
        or "\\" in value
        or not PurePosixPath(value).is_absolute()
        or PurePosixPath(value).as_posix() != value
        or (value != "/" and any(part in {"", ".", ".."} for part in parts))
        or any(ord(character) < 32 or 127 <= ord(character) <= 159 for character in value)
    ):
        raise ValueError("invalid absolute path")
    return value


class WorkspaceEntry(ExecutionResult):
    name: str
    path: str
    kind: Literal["file", "directory", "symlink", "other"]
    size: int | None = Field(ge=0)
    modified_at: float

    _validate_path = field_validator("path")(_absolute_path)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        if not value or "/" in value or value in {".", ".."}:
            raise ValueError("invalid entry name")
        return value


class WorkspaceListResult(ExecutionResult):
    entries: list[WorkspaceEntry]
    has_more: bool
    next_offset: int | None = Field(default=None, ge=0)


class WorkspaceReadResult(ExecutionResult):
    content: str
    lines_read: int = Field(ge=0)
    has_more: bool
    next_offset: int | None = Field(default=None, ge=1)
    truncated_lines: list[int]
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class WorkspaceGlobResult(ExecutionResult):
    paths: list[str] = Field(max_length=100)
    truncated: bool

    _validate_paths = field_validator("paths")(
        lambda values: [_absolute_path(value) for value in values]
    )


class WorkspaceGrepMatch(ExecutionResult):
    path: str
    line: int = Field(ge=1)
    text: str = Field(max_length=2_000)

    _validate_path = field_validator("path")(_absolute_path)


class WorkspaceGrepResult(ExecutionResult):
    matches: list[WorkspaceGrepMatch] = Field(max_length=100)
    truncated: bool


class WorkspacePathResult(ExecutionResult):
    path: str

    _validate_path = field_validator("path")(_absolute_path)


class WorkspaceWriteResult(WorkspacePathResult):
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size: int = Field(ge=0)


class WorkspacePatchFile(WorkspaceWriteResult):
    operation: Literal["add", "update", "move", "delete"]
    destination: str | None = None

    @field_validator("destination")
    @classmethod
    def validate_destination(cls, value: str | None) -> str | None:
        return _absolute_path(value) if value is not None else None


class WorkspacePatchResult(ExecutionResult):
    files: list[WorkspacePatchFile] = Field(min_length=1, max_length=1_000)


class WorkspaceExportResult(ExecutionResult):
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size: int = Field(ge=0)


class WorkspaceStatResult(WorkspacePathResult):
    kind: Literal["file", "directory", "symlink", "other"]
    size: int | None = Field(ge=0)
    modified_at: float


class WorkspaceDeleteResult(ExecutionResult):
    pass


class ProcessResult(ExecutionResult):
    exit_code: int
    timed_out: bool
    duration_ms: int = Field(ge=0)
    stdout_bytes: int = Field(ge=0)
    stderr_bytes: int = Field(ge=0)
    stdout_truncated: bool
    stderr_truncated: bool


class TerminalOpenResult(ExecutionResult):
    terminal_id: UUID = Field(strict=False)


class TerminalReadResult(ExecutionResult):
    data: str = Field(max_length=87_384)
    eof: bool
    exit_code: int | None = None

    @field_validator("data")
    @classmethod
    def validate_data(cls, value: str) -> str:
        try:
            decoded = base64.b64decode(value, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("invalid terminal data") from exc
        if len(decoded) > 65_536:
            raise ValueError("terminal data exceeds requested maximum")
        return value


class TerminalControlResult(ExecutionResult):
    pass


class DirectoryResult(ExecutionResult):
    path: str

    _validate_path = field_validator("path")(_absolute_path)


class SystemInfoResult(ExecutionResult):
    agent_version: str
    platform: str
    operating_system: str
    kernel: str
    architecture: str
    logical_cpu_count: int | None
    memory: dict[str, int] | None = None
    disk: dict[str, Any] | None = None


class ShellTarget(ExecutionResult):
    path: str
    kind: str

    _validate_path = field_validator("path")(_absolute_path)


class ShellCommand(ExecutionResult):
    tokens: list[str] = Field(min_length=1, max_length=256)
    prefix: list[str] | None = Field(default=None, min_length=1, max_length=8)
    classification: Literal["read", "write"]

    @field_validator("tokens", "prefix")
    @classmethod
    def validate_tokens(cls, value: list[str] | None) -> list[str] | None:
        if value is not None and any(
            not token
            or len(token) > 1_024
            or any(ord(character) < 32 or 127 <= ord(character) <= 159 for character in token)
            for token in value
        ):
            raise ValueError("invalid shell token")
        return value


class ShellInspectResult(ExecutionResult):
    targets: list[ShellTarget]
    analysis_complete: bool
    classification: Literal["read", "write"]
    read_only: bool = False
    commands: list[ShellCommand] = Field(max_length=64)
    inspection_revision: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("read_only")
    @classmethod
    def validate_read_only(cls, value: bool, info) -> bool:
        classification = info.data.get("classification")
        if classification is not None and value != (classification == "read"):
            raise ValueError("inconsistent shell classification")
        return value


class ExecutionClientError(Exception):
    def __init__(self, code: str, params: dict[str, Any] | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.params = dict(params) if params is not None else None


_REMOTE_ERROR_CODES = {
    "file.changed": "file.changed",
    "file.editTextNotFound": "file.editTextNotFound",
    "file.editTextNotUnique": "file.editTextNotUnique",
    "file.invalidEncoding": "file.invalidEncoding",
    "file.patchContextMismatch": "file.patchContextMismatch",
    "file.patchIncomplete": "file.patchIncomplete",
    "file.tooLarge": "file.tooLarge",
    "git.addFailed": "git.operationFailed",
    "git.cloneFailed": "git.cloneFailed",
    "git.commitFailed": "git.operationFailed",
    "git.diffFailed": "git.operationFailed",
    "git.failed": "git.operationFailed",
    "git.indexChanged": "git.conflict",
    "git.indexInvalid": "git.invalidRepository",
    "git.invalidDestination": "file.invalidDestination",
    "git.invalidOutput": "executionAgent.protocolError",
    "git.invalidSource": "git.invalidRepository",
    "git.logFailed": "git.operationFailed",
    "git.networkAddressDenied": "git.networkDisabled",
    "git.networkDenied": "git.networkDisabled",
    "git.notRepository": "git.notRepository",
    "git.outcomeUnknown": "executionAgent.outcomeUnknown",
    "git.startFailed": "git.operationFailed",
    "git.statusFailed": "git.operationFailed",
    "git.timedOut": "git.operationFailed",
    "git.unsafeRepository": "git.invalidRepository",
    "cache.exportFailed": "cache.exportFailed",
    "cache.invalidContent": "cache.invalidContent",
    "cache.invalidDescriptor": "cache.invalidDescriptor",
    "cache.invalidPath": "cache.invalidPath",
    "cache.notFound": "cache.notFound",
    "openscad.headlessPreviewUnavailable": "openscad.previewUnavailable",
    "openscad.invalidExecutable": "openscad.invalidExecutable",
    "openscad.invalidArchive": "openscad.invalidArchive",
    "openscad.invalidOutput": "openscad.invalidArtifact",
    "openscad.notFound": "openscad.notFound",
    "openscad.previewFailed": "openscad.previewFailed",
    "openscad.projectTooLarge": "openscad.projectTooLarge",
    "openscad.renderFailed": "openscad.renderFailed",
    "openscad.startFailed": "openscad.renderFailed",
    "openscad.testFailed": "openscad.testFailed",
    "openscad.timedOut": "openscad.renderFailed",
    "openscad.unavailable": "openscad.unavailable",
    "openscad.unsafeProject": "openscad.unsafeProject",
    "openscad.versionFailed": "openscad.testFailed",
    "operation.failed": "tool.executionFailed",
    "operation.tooLarge": "file.operationTooLarge",
    "path.exists": "file.pathAlreadyExists",
    "path.invalid": "file.invalidPath",
    "path.invalidDestination": "file.invalidDestination",
    "path.notDirectory": "file.directoryNotFound",
    "path.notEmpty": "file.directoryNotEmpty",
    "path.notFile": "file.notAFile",
    "path.notFound": "file.pathNotFound",
    "path.outsideRoots": "file.invalidPath",
    "path.rootMutation": "file.rootMutationForbidden",
    "path.unsupportedType": "file.unsupportedType",
    "process.notAllowed": "process.notAllowed",
    "process.inspectionChanged": "process.inspectionChanged",
    "process.outputTooLarge": "process.outputTooLarge",
    "process.startFailed": "process.startFailed",
    "process.timedOut": "process.timedOut",
    "process.unavailable": "process.unavailable",
    "request.invalid": "tool.invalidArguments",
    "root.stale": "executionAgent.unavailable",
    "root.unavailable": "executionAgent.unavailable",
    "root.unknown": "executionAgent.unavailable",
    "terminal.closed": "terminal.disconnected",
    "terminal.exists": "terminal.commandActive",
    "terminal.limitReached": "terminal.unavailable",
    "terminal.notFound": "terminal.disconnected",
}

_MANAGER_ERROR_CODES = frozenset(
    {
        "executionAgent.operationUnavailable",
        "executionAgent.outcomeUnknown",
        "executionAgent.protocolError",
        "executionAgent.replaced",
        "executionAgent.requestTooLarge",
        "executionAgent.responseTooLarge",
        "executionAgent.revoked",
        "executionAgent.timeout",
        "executionAgent.unavailable",
        "process.outcomeUnknown",
        "process.outputTooLarge",
    }
)


@dataclass(slots=True)
class ExecutionClient:
    agent_id: UUID
    operations: frozenset[str]
    current_directory: str | None = None

    def supports(self, operation: str) -> bool:
        return operation in self.operations

    async def read_bytes(self, path: str, *, limit: int) -> bytes:
        stat_result = await self.request(
            "filesystem.stat", {"path": path}, WorkspaceStatResult
        )
        assert isinstance(stat_result, WorkspaceStatResult)
        if stat_result.kind != "file":
            raise ExecutionClientError("file.notAFile")
        if stat_result.size is None or stat_result.size > limit:
            raise ExecutionClientError("file.tooLarge")
        grant, ticket = transfer_tickets.create_upload(
            self.agent_id, size=stat_result.size
        )
        try:
            result = await self.request(
                "filesystem.export",
                {
                    "path": path,
                    "transfer": {
                        "url": execution_agent_connections.transfer_url(
                            self.agent_id, grant.ticket_id
                        ),
                        "token": grant.token,
                    },
                },
                WorkspaceExportResult,
            )
            assert isinstance(result, WorkspaceExportResult)
            if (
                result.size != ticket.actual_size
                or result.sha256 != ticket.actual_sha256
                or ticket.content is None
            ):
                raise ExecutionClientError("executionAgent.protocolError")
            return ticket.content.read(limit + 1)
        finally:
            transfer_tickets.revoke(grant.ticket_id)

    async def write_bytes(
        self, path: str, source: BinaryIO, *, limit: int
    ) -> WorkspaceWriteResult:
        try:
            staged, size, digest = stage_file(source, max_bytes=limit)
        except TransferError as exc:
            raise ExecutionClientError(
                "file.tooLarge"
                if exc.code == "transfer.tooLarge"
                else "executionAgent.protocolError"
            ) from exc
        grant, _ = transfer_tickets.create_download(
            self.agent_id, staged, size=size, sha256=digest
        )
        try:
            result = await self.request(
                "filesystem.import",
                {
                    "path": path,
                    "transfer": {
                        "url": execution_agent_connections.transfer_url(
                            self.agent_id, grant.ticket_id
                        ),
                        "token": grant.token,
                    },
                    "size": size,
                    "sha256": digest,
                    "overwrite": False,
                },
                WorkspaceWriteResult,
            )
            assert isinstance(result, WorkspaceWriteResult)
            if result.path != path or result.size != size or result.sha256 != digest:
                raise ExecutionClientError("executionAgent.protocolError")
            return result
        finally:
            transfer_tickets.revoke(grant.ticket_id)

    async def request(
        self,
        operation: str,
        arguments: dict[str, Any],
        result_model: type[ExecutionResult],
        *,
        timeout: float | None = None,
        on_event: Callable[[EventMessage], Awaitable[None] | None] | None = None,
    ) -> ExecutionResult:
        if not self.supports(operation):
            raise ExecutionClientError("executionAgent.operationUnavailable")
        try:
            response = await execution_agent_connections.request(
                self.agent_id,
                operation,
                arguments,
                timeout=timeout,
                on_event=on_event,
            )
        except ExecutionAgentError as exc:
            raise ExecutionClientError(
                exc.code if exc.code in _MANAGER_ERROR_CODES else "executionAgent.unavailable"
            ) from exc
        if not response.ok:
            code = response.error.code if response.error is not None else None
            if code == "operation.cancelled":
                mapped_code = (
                    "process.interrupted"
                    if operation.startswith("shell.") or operation == "terminal.run"
                    else "tool.executionInterrupted"
                )
            elif code == "operation.failed" and (
                operation.startswith("shell.") or operation == "terminal.run"
            ):
                mapped_code = "process.failed"
            else:
                mapped_code = _REMOTE_ERROR_CODES.get(
                    code or "", "executionAgent.protocolError"
                )
            raise ExecutionClientError(
                mapped_code,
                response.error.params if response.error is not None else None,
            )
        try:
            return result_model.model_validate(response.result)
        except ValidationError as exc:
            raise ExecutionClientError("executionAgent.protocolError") from exc


__all__ = [
    "DirectoryResult",
    "ExecutionClient",
    "ExecutionClientError",
    "ProcessResult",
    "ShellInspectResult",
    "SystemInfoResult",
    "TerminalControlResult",
    "TerminalOpenResult",
    "TerminalReadResult",
    "WorkspaceDeleteResult",
    "WorkspaceExportResult",
    "WorkspaceGlobResult",
    "WorkspaceGrepResult",
    "WorkspaceListResult",
    "WorkspacePatchResult",
    "WorkspacePathResult",
    "WorkspaceReadResult",
    "WorkspaceStatResult",
    "WorkspaceWriteResult",
]
