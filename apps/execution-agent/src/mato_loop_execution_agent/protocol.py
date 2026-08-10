from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator

PROTOCOL_VERSION = 1
UUID_PATTERN = (
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-8][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$"
)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, populate_by_name=True)


class RootDescriptor(StrictModel):
    id: str = Field(pattern=UUID_PATTERN)
    label: str = Field(min_length=1, max_length=160)
    revision: str = Field(pattern=r"^[0-9a-f]{64}$")


class Hello(StrictModel):
    type: Literal["hello"] = "hello"
    protocol_version: Literal[1] = Field(default=1, alias="protocolVersion")
    agent_id: str = Field(alias="agentId", pattern=UUID_PATTERN)
    name: str = Field(min_length=1, max_length=160)
    platform: str = Field(min_length=1, max_length=80)
    operations: list[str] = Field(max_length=64)
    roots: list[RootDescriptor] = Field(max_length=64)


class Welcome(StrictModel):
    type: Literal["welcome"]
    protocol_version: Literal[1] = Field(alias="protocolVersion")
    agent_id: str = Field(alias="agentId", pattern=UUID_PATTERN)


class Request(StrictModel):
    type: Literal["request"]
    id: str = Field(pattern=UUID_PATTERN)
    operation: str = Field(min_length=1, max_length=64)
    arguments: dict[str, Any]


class Cancel(StrictModel):
    type: Literal["cancel"]
    id: str = Field(pattern=UUID_PATTERN)


class Error(StrictModel):
    code: str = Field(pattern=r"^[a-z][A-Za-z]*(?:\.[a-z][A-Za-z]*)+$", max_length=120)
    params: dict[str, Any] | None = Field(default=None, max_length=20)


class Response(StrictModel):
    type: Literal["response"] = "response"
    id: str = Field(pattern=UUID_PATTERN)
    ok: bool
    result: dict[str, Any] | None = None
    error: Error | None = None

    @model_validator(mode="after")
    def consistent_result(self):
        if (
            self.ok == (self.error is not None)
            or (self.ok and self.result is None)
            or (not self.ok and self.result is not None)
        ):
            raise ValueError("response success and payload are inconsistent")
        return self


class Event(StrictModel):
    type: Literal["event"] = "event"
    id: str = Field(pattern=UUID_PATTERN)
    event: Literal["stdout", "stderr"]
    data: str = Field(max_length=65_536)


class RootArguments(StrictModel):
    root_id: str = Field(alias="rootId", pattern=UUID_PATTERN)
    root_revision: str = Field(alias="rootRevision", pattern=r"^[0-9a-f]{64}$")
    working_directory: str = Field(default=".", alias="workingDirectory")


class PathArguments(RootArguments):
    path: str


class StatArguments(PathArguments):
    pass


class ListArguments(PathArguments):
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=100, ge=1, le=1000)


class ReadArguments(PathArguments):
    offset: int = Field(default=1, ge=1)
    limit: int = Field(default=2000, ge=1, le=2000)
    max_line_length: int = Field(default=2000, alias="maxLineLength", ge=1, le=10_000)


class WriteArguments(PathArguments):
    content: str
    expected_sha256: str | None = Field(
        default=None, alias="expectedSha256", pattern=r"^[0-9a-f]{64}$"
    )


class MkdirArguments(PathArguments):
    parents: bool = False


class RenameArguments(PathArguments):
    destination: str


class DeleteArguments(PathArguments):
    recursive: bool = False


class CopyMoveArguments(PathArguments):
    destination: str
    destination_root_id: str | None = Field(
        default=None, alias="destinationRootId", pattern=UUID_PATTERN
    )
    destination_root_revision: str | None = Field(
        default=None, alias="destinationRootRevision", pattern=r"^[0-9a-f]{64}$"
    )
    destination_working_directory: str = Field(default=".", alias="destinationWorkingDirectory")


class TransferSpec(StrictModel):
    url: str = Field(min_length=1, max_length=4096)
    token: str = Field(min_length=1, max_length=8192)


class ImportArguments(PathArguments):
    transfer: TransferSpec
    size: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    overwrite: bool = False


class ExportArguments(PathArguments):
    transfer: TransferSpec
    expected_sha256: str | None = Field(
        default=None, alias="expectedSha256", pattern=r"^[0-9a-f]{64}$"
    )


class ProcessArguments(RootArguments):
    program: str = Field(min_length=1, max_length=128)
    args: list[str] = Field(default_factory=list, max_length=256)
    timeout_seconds: float | None = Field(default=None, alias="timeoutSeconds", gt=0, le=3600)


class ShellArguments(RootArguments):
    command: str = Field(min_length=1, max_length=256 * 1024)
    timeout_seconds: float | None = Field(default=None, alias="timeoutSeconds", gt=0, le=3600)


class ArtifactDescriptor(StrictModel):
    artifact_id: str = Field(alias="artifactId", min_length=1, max_length=128)
    backend_id: str = Field(alias="backendId", min_length=1, max_length=128)
    session_id: str = Field(alias="sessionId", min_length=1, max_length=128)
    path: str
    filename: str = Field(min_length=1, max_length=255)
    size: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    content_type: str = Field(alias="contentType", min_length=1, max_length=255)


class CacheEnsureArguments(StrictModel):
    artifact: ArtifactDescriptor
    transfer: TransferSpec


class CacheStoreArguments(PathArguments):
    artifact: ArtifactDescriptor


class CacheClearArguments(StrictModel):
    pass


ARGUMENT_MODELS: dict[str, type[StrictModel]] = {
    "workspace.stat": StatArguments,
    "workspace.list": ListArguments,
    "workspace.read": ReadArguments,
    "workspace.write": WriteArguments,
    "workspace.mkdir": MkdirArguments,
    "workspace.rename": RenameArguments,
    "workspace.delete": DeleteArguments,
    "workspace.copy": CopyMoveArguments,
    "workspace.move": CopyMoveArguments,
    "workspace.import": ImportArguments,
    "workspace.export": ExportArguments,
    "process.exec": ProcessArguments,
    "shell.run": ShellArguments,
    "cache.ensure": CacheEnsureArguments,
    "cache.store": CacheStoreArguments,
    "cache.clear": CacheClearArguments,
}

INBOUND_ADAPTER = TypeAdapter(Welcome | Request | Cancel)


def parse_inbound(data: str | bytes) -> Welcome | Request | Cancel:
    return INBOUND_ADAPTER.validate_json(data)


def validate_arguments(request: Request) -> StrictModel:
    model = ARGUMENT_MODELS.get(request.operation)
    if model is None:
        raise ValueError("unsupported operation")
    return model.model_validate(request.arguments)
