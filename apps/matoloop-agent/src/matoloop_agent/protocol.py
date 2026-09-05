from __future__ import annotations

import base64
import binascii
from pathlib import PurePosixPath
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator, model_validator

PROTOCOL_VERSION = 1
UUID_PATTERN = (
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-8][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$"
)


def _absolute_path(value: str) -> str:
    parsed = PurePosixPath(value)
    if (
        not value.startswith("/")
        or "\\" in value
        or parsed.as_posix() != value
        or (value != "/" and any(part in {"", ".", ".."} for part in value[1:].split("/")))
        or any(ord(character) < 32 or 127 <= ord(character) <= 159 for character in value)
    ):
        raise ValueError("path must be a normalized absolute POSIX path")
    return value


def _relative_path(value: str, *, allow_dot: bool = False) -> str:
    if allow_dot and value == ".":
        return value
    parsed = PurePosixPath(value)
    if (
        parsed.is_absolute()
        or "\\" in value
        or parsed.as_posix() != value
        or any(part in {"", ".", ".."} for part in value.split("/"))
        or any(ord(character) < 32 or 127 <= ord(character) <= 159 for character in value)
    ):
        raise ValueError("path must be a normalized relative POSIX path")
    return value


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, populate_by_name=True)


class Hello(StrictModel):
    type: Literal["hello"] = "hello"
    protocol_version: Literal[1] = Field(default=1, alias="protocolVersion")
    agent_id: str = Field(alias="agentId", pattern=UUID_PATTERN)
    agent_version: str = Field(alias="agentVersion", min_length=1, max_length=80)
    computer_name: str = Field(alias="computerName", min_length=1, max_length=255)
    platform: str = Field(min_length=1, max_length=80)
    home_directory: str = Field(alias="homeDirectory", min_length=1, max_length=4096)
    operations: list[str] = Field(max_length=64)

    _home_is_absolute = field_validator("home_directory")(_absolute_path)


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
    params: dict[str, str | bool | int | float] | None = Field(default=None, max_length=20)


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


class PathArguments(StrictModel):
    path: str = Field(min_length=1, max_length=4096)

    _path_is_absolute = field_validator("path")(_absolute_path)


class StatArguments(PathArguments):
    pass


class ListArguments(PathArguments):
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=100, ge=1, le=1000)


class ReadArguments(PathArguments):
    offset: int = Field(default=1, ge=1)
    limit: int = Field(default=2000, ge=1, le=2000)
    max_line_length: int = Field(default=2000, alias="maxLineLength", ge=1, le=10_000)


class GlobArguments(PathArguments):
    pattern: str = Field(min_length=1, max_length=1000, pattern=r"^[^\x00]+$")


class GrepArguments(PathArguments):
    pattern: str = Field(min_length=1, max_length=4096, pattern=r"^[^\x00]+$")
    include: str | None = Field(default=None, min_length=1, max_length=1000, pattern=r"^[^\x00]+$")


class WriteArguments(PathArguments):
    content: str
    expected_sha256: str | None = Field(
        default=None, alias="expectedSha256", pattern=r"^[0-9a-f]{64}$"
    )


class UpsertArguments(PathArguments):
    content: str


class EditArguments(PathArguments):
    old_text: str = Field(alias="oldText", min_length=1)
    new_text: str = Field(alias="newText")
    replace_all: bool = Field(default=False, alias="replaceAll")
    expected_sha256: str | None = Field(
        default=None, alias="expectedSha256", pattern=r"^[0-9a-f]{64}$"
    )

    @model_validator(mode="after")
    def different_text(self):
        if self.old_text == self.new_text:
            raise ValueError("oldText and newText must differ")
        return self


class PatchArguments(StrictModel):
    patch: str = Field(min_length=1, max_length=512 * 1024)
    expected_sha256: dict[str, Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]] = Field(
        alias="expectedSha256", max_length=1000
    )

    @field_validator("expected_sha256")
    @classmethod
    def absolute_revision_paths(cls, value: dict[str, str]) -> dict[str, str]:
        for path in value:
            if len(path) > 4096:
                raise ValueError("revision path is too long")
            _absolute_path(path)
        return value


class MkdirArguments(PathArguments):
    parents: bool = False


class RenameArguments(PathArguments):
    destination: str = Field(min_length=1, max_length=4096)

    _destination_is_absolute = field_validator("destination")(_absolute_path)


class DeleteArguments(PathArguments):
    recursive: bool = False


class CopyMoveArguments(PathArguments):
    destination: str = Field(min_length=1, max_length=4096)

    _destination_is_absolute = field_validator("destination")(_absolute_path)


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


class HostDirectoryArguments(StrictModel):
    current_directory: str = Field(alias="currentDirectory", min_length=1, max_length=4096)

    _cwd_is_absolute = field_validator("current_directory")(_absolute_path)


class ShellArguments(HostDirectoryArguments):
    command: str = Field(min_length=1, max_length=256 * 1024)
    timeout_seconds: float | None = Field(default=None, alias="timeoutSeconds", gt=0, le=3600)
    expected_inspection_revision: str | None = Field(
        default=None, alias="expectedInspectionRevision", pattern=r"^[0-9a-f]{64}$"
    )


class ResolveDirectoryArguments(PathArguments):
    pass


class SystemInfoArguments(StrictModel):
    current_directory: str | None = Field(
        default=None, alias="currentDirectory", min_length=1, max_length=4096
    )

    @field_validator("current_directory")
    @classmethod
    def absolute_directory(cls, value: str | None) -> str | None:
        return _absolute_path(value) if value is not None else None


class ShellInspectArguments(HostDirectoryArguments):
    command: str = Field(min_length=1, max_length=256 * 1024)


class TerminalOpenArguments(StrictModel):
    rows: int = Field(ge=1, le=1000)
    columns: int = Field(ge=1, le=1000)


class TerminalIdArguments(StrictModel):
    terminal_id: str = Field(alias="terminalId", pattern=UUID_PATTERN)


class TerminalReadArguments(TerminalIdArguments):
    max_bytes: int = Field(default=65_536, alias="maxBytes", ge=1, le=65_536)
    wait_seconds: float = Field(default=0, alias="waitSeconds", ge=0, le=30)


class TerminalWriteArguments(TerminalIdArguments):
    data: str = Field(max_length=87_384)

    @field_validator("data")
    @classmethod
    def bounded_base64(cls, value: str) -> str:
        try:
            decoded = base64.b64decode(value, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("data must be valid base64") from exc
        if len(decoded) > 65_536:
            raise ValueError("decoded data exceeds 65536 bytes")
        return value


class TerminalResizeArguments(TerminalIdArguments):
    rows: int = Field(ge=1, le=1000)
    columns: int = Field(ge=1, le=1000)


class TerminalCloseArguments(TerminalIdArguments):
    pass


class TerminalRunArguments(HostDirectoryArguments, TerminalIdArguments):
    rows: int = Field(ge=1, le=1000)
    columns: int = Field(ge=1, le=1000)
    command: str = Field(min_length=1, max_length=256 * 1024)
    timeout_seconds: float | None = Field(default=None, alias="timeoutSeconds", gt=0, le=3600)
    expected_inspection_revision: str | None = Field(
        default=None, alias="expectedInspectionRevision", pattern=r"^[0-9a-f]{64}$"
    )


GitPath = Annotated[str, Field(min_length=1, max_length=1000, pattern=r"^[^\x00]+$")]


class GitCloneArguments(HostDirectoryArguments):
    source: str = Field(min_length=1, max_length=4096, pattern=r"^[^\x00]+$")
    destination: str = Field(min_length=1, max_length=1000, pattern=r"^[^\x00]+$")
    allow_network: bool = Field(default=False, alias="allowNetwork")

    @field_validator("destination")
    @classmethod
    def relative_destination(cls, value: str) -> str:
        return _relative_path(value)


class GitStatusArguments(HostDirectoryArguments):
    pass


class GitDiffArguments(HostDirectoryArguments):
    staged: bool = False
    paths: list[GitPath] = Field(default_factory=list, max_length=100)

    @field_validator("paths")
    @classmethod
    def relative_paths(cls, values: list[str]) -> list[str]:
        return [_relative_path(value) for value in values]


class GitLogArguments(HostDirectoryArguments):
    limit: int = Field(default=20, ge=1, le=100)
    paths: list[GitPath] = Field(default_factory=list, max_length=100)

    @field_validator("paths")
    @classmethod
    def relative_paths(cls, values: list[str]) -> list[str]:
        return [_relative_path(value) for value in values]


class GitAddArguments(HostDirectoryArguments):
    paths: list[GitPath] = Field(min_length=1, max_length=100)

    @field_validator("paths")
    @classmethod
    def relative_paths(cls, values: list[str]) -> list[str]:
        return [_relative_path(value) for value in values]


class GitCommitArguments(HostDirectoryArguments):
    message: str = Field(min_length=1, max_length=10_000, pattern=r"^[^\x00]+$")
    expected_index_tree: str = Field(alias="expectedIndexTree", pattern=r"^[0-9a-f]{64}$")


class ArtifactDescriptor(StrictModel):
    artifact_id: str = Field(alias="artifactId", min_length=1, max_length=128)
    backend_id: str = Field(alias="backendId", min_length=1, max_length=128)
    session_id: str = Field(alias="sessionId", min_length=1, max_length=128)
    path: str = Field(min_length=1, max_length=1000, pattern=r"^[^\x00\\]+$")
    filename: str = Field(min_length=1, max_length=255)
    size: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    content_type: str = Field(alias="contentType", min_length=1, max_length=255)

    @field_validator("path")
    @classmethod
    def relative_path(cls, value: str) -> str:
        return _relative_path(value)

    @field_validator("filename")
    @classmethod
    def plain_filename(cls, value: str) -> str:
        if value in {".", ".."} or "/" in value or "\\" in value or "\x00" in value:
            raise ValueError("artifact filename must be plain")
        return value


class CacheEnsureArguments(StrictModel):
    artifact: ArtifactDescriptor
    transfer: TransferSpec


class CacheStoreArguments(PathArguments):
    artifact: ArtifactDescriptor


class CacheClearArguments(StrictModel):
    pass


class CacheExportArguments(StrictModel):
    artifact: ArtifactDescriptor
    transfer: TransferSpec


class OpenScadDiscoverArguments(StrictModel):
    pass


class OpenScadTestArguments(StrictModel):
    executable_path: str = Field(alias="executablePath", min_length=1, max_length=4096)

    _executable_is_absolute = field_validator("executable_path")(_absolute_path)


class OpenScadWorkspaceSource(StrictModel):
    type: Literal["workspace"]
    path: str = Field(min_length=1, max_length=4096)

    _path_is_absolute = field_validator("path")(_absolute_path)


class OpenScadArchiveSource(StrictModel):
    type: Literal["archive"]
    artifact: ArtifactDescriptor
    transfer: TransferSpec


OpenScadSource = Annotated[
    OpenScadWorkspaceSource | OpenScadArchiveSource, Field(discriminator="type")
]


class OpenScadRenderArguments(StrictModel):
    executable_path: str = Field(alias="executablePath", min_length=1, max_length=4096)
    session_id: str = Field(
        alias="sessionId", min_length=1, max_length=128, pattern=r"^[^/\\\x00]+$"
    )
    entrypoint: str = Field(min_length=1, max_length=1000, pattern=r"^[^\x00\\]+$")
    source: OpenScadSource
    output: Literal["preview", "stl"] = "preview"
    preview_images: bool = Field(default=False, alias="previewImages")

    _executable_is_absolute = field_validator("executable_path")(_absolute_path)

    @field_validator("entrypoint")
    @classmethod
    def relative_entrypoint(cls, value: str) -> str:
        return _relative_path(value)


ARGUMENT_MODELS: dict[str, type[StrictModel]] = {
    "system.info": SystemInfoArguments,
    "filesystem.resolve": ResolveDirectoryArguments,
    "filesystem.stat": StatArguments,
    "filesystem.list": ListArguments,
    "filesystem.read": ReadArguments,
    "filesystem.glob": GlobArguments,
    "filesystem.grep": GrepArguments,
    "filesystem.write": WriteArguments,
    "filesystem.upsert": UpsertArguments,
    "filesystem.edit": EditArguments,
    "filesystem.patch": PatchArguments,
    "filesystem.mkdir": MkdirArguments,
    "filesystem.rename": RenameArguments,
    "filesystem.delete": DeleteArguments,
    "filesystem.copy": CopyMoveArguments,
    "filesystem.move": CopyMoveArguments,
    "filesystem.import": ImportArguments,
    "filesystem.export": ExportArguments,
    "shell.run": ShellArguments,
    "shell.inspect": ShellInspectArguments,
    "terminal.open": TerminalOpenArguments,
    "terminal.read": TerminalReadArguments,
    "terminal.write": TerminalWriteArguments,
    "terminal.resize": TerminalResizeArguments,
    "terminal.close": TerminalCloseArguments,
    "terminal.run": TerminalRunArguments,
    "integration.git.clone": GitCloneArguments,
    "integration.git.status": GitStatusArguments,
    "integration.git.diff": GitDiffArguments,
    "integration.git.log": GitLogArguments,
    "integration.git.add": GitAddArguments,
    "integration.git.commit": GitCommitArguments,
    "cache.ensure": CacheEnsureArguments,
    "cache.store": CacheStoreArguments,
    "cache.export": CacheExportArguments,
    "cache.clear": CacheClearArguments,
    "integration.openscad.discover": OpenScadDiscoverArguments,
    "integration.openscad.test": OpenScadTestArguments,
    "integration.openscad.render": OpenScadRenderArguments,
    "integration.openscad.render6": OpenScadRenderArguments,
}

INBOUND_ADAPTER = TypeAdapter(Welcome | Request | Cancel)


def parse_inbound(data: str | bytes) -> Welcome | Request | Cancel:
    return INBOUND_ADAPTER.validate_json(data)


def validate_arguments(request: Request) -> StrictModel:
    model = ARGUMENT_MODELS.get(request.operation)
    if model is None:
        raise ValueError("unsupported operation")
    return model.model_validate(request.arguments)
