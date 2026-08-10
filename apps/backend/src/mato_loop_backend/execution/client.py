from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from mato_loop_backend.execution.manager import (
    ExecutionAgentError,
    execution_agent_connections,
)
from mato_loop_backend.execution.protocol import EventMessage


class ExecutionResult(BaseModel):
    model_config = ConfigDict(
        alias_generator=lambda value: value.split("_")[0]
        + "".join(part.capitalize() for part in value.split("_")[1:]),
        populate_by_name=True,
        extra="ignore",
        strict=True,
    )


def _relative_path(value: str) -> str:
    if value == ".":
        return value
    parts = value.split("/")
    if (
        not value
        or "\\" in value
        or PurePosixPath(value).is_absolute()
        or any(part in {"", ".", ".."} for part in parts)
        or any(ord(character) < 32 or 127 <= ord(character) <= 159 for character in value)
    ):
        raise ValueError("invalid relative path")
    return value


class WorkspaceEntry(ExecutionResult):
    name: str
    path: str
    kind: Literal["file", "directory", "symlink", "other"]
    size: int | None = Field(ge=0)
    modified_at: float

    _validate_path = field_validator("path")(_relative_path)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        if _relative_path(value) == "." or "/" in value:
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


class WorkspacePathResult(ExecutionResult):
    path: str

    _validate_path = field_validator("path")(_relative_path)


class WorkspaceWriteResult(WorkspacePathResult):
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


class ExecutionClientError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


_REMOTE_ERROR_CODES = {
    "file.changed": "file.changed",
    "file.invalidEncoding": "file.invalidEncoding",
    "file.tooLarge": "file.tooLarge",
    "operation.failed": "tool.executionFailed",
    "operation.tooLarge": "file.operationTooLarge",
    "path.exists": "file.pathAlreadyExists",
    "path.invalid": "file.invalidPath",
    "path.invalidDestination": "file.invalidDestination",
    "path.notDirectory": "file.directoryNotFound",
    "path.notEmpty": "file.directoryNotEmpty",
    "path.notFile": "file.notAFile",
    "path.notFound": "file.pathNotFound",
    "path.rootMutation": "file.rootMutationForbidden",
    "path.unsupportedType": "file.unsupportedType",
    "process.notAllowed": "process.notAllowed",
    "process.outputTooLarge": "process.outputTooLarge",
    "process.startFailed": "process.startFailed",
    "process.timedOut": "process.timedOut",
    "process.unavailable": "process.unavailable",
    "request.invalid": "tool.invalidArguments",
    "root.stale": "executionAgent.unavailable",
    "root.unavailable": "executionAgent.unavailable",
    "root.unknown": "executionAgent.unavailable",
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
    root_id: UUID
    root_revision: str
    working_directory: str
    operations: frozenset[str]
    cancellation_deferred: bool = field(default=False, init=False)

    def supports(self, operation: str) -> bool:
        return operation in self.operations

    def _mark_cancellation_deferred(self) -> None:
        self.cancellation_deferred = True

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
                {
                    **arguments,
                    "rootId": str(self.root_id),
                    "rootRevision": self.root_revision,
                    "workingDirectory": self.working_directory,
                },
                timeout=timeout,
                on_event=on_event,
                on_cancellation_deferred=self._mark_cancellation_deferred,
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
                    if operation.startswith(("process.", "shell."))
                    else "tool.executionInterrupted"
                )
            elif code == "operation.failed" and operation.startswith(("process.", "shell.")):
                mapped_code = "process.failed"
            else:
                mapped_code = _REMOTE_ERROR_CODES.get(
                    code or "", "executionAgent.protocolError"
                )
            raise ExecutionClientError(
                mapped_code
            )
        try:
            return result_model.model_validate(response.result)
        except ValidationError as exc:
            raise ExecutionClientError("executionAgent.protocolError") from exc


__all__ = [
    "ExecutionClient",
    "ExecutionClientError",
    "ProcessResult",
    "WorkspaceDeleteResult",
    "WorkspaceListResult",
    "WorkspacePathResult",
    "WorkspaceReadResult",
    "WorkspaceStatResult",
    "WorkspaceWriteResult",
]
