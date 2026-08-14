from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

PROTOCOL_VERSION = 2
MAX_MESSAGE_BYTES = 1_048_576
MAX_REQUEST_BYTES = 262_144
MAX_EVENT_BYTES = 65_536
MAX_EVENTS_PER_REQUEST = 1_024
MAX_OUTPUT_BYTES = 1_048_576
SUPPORTED_OPERATIONS = frozenset(
    {
        "cache.clear",
        "cache.ensure",
        "cache.store",
        "filesystem.resolve",
        "integration.git.add",
        "integration.git.clone",
        "integration.git.commit",
        "integration.git.diff",
        "integration.git.log",
        "integration.git.status",
        "process.exec",
        "shell.run",
        "shell.inspect",
        "system.info",
        "workspace.copy",
        "workspace.delete",
        "workspace.export",
        "workspace.glob",
        "workspace.grep",
        "workspace.import",
        "workspace.list",
        "workspace.mkdir",
        "workspace.move",
        "workspace.read",
        "workspace.rename",
        "workspace.stat",
        "workspace.write",
    }
)


def _camel(value: str) -> str:
    first, *rest = value.split("_")
    return first + "".join(part.capitalize() for part in rest)


class WireModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=_camel,
        populate_by_name=True,
        serialize_by_alias=True,
        extra="forbid",
        strict=True,
    )


class RootSnapshot(WireModel):
    id: UUID
    label: str = Field(min_length=1, max_length=160)
    revision: str = Field(pattern=r"^[0-9a-f]{64}$")


class HelloMessage(WireModel):
    type: Literal["hello"]
    protocol_version: Literal[2]
    agent_id: UUID
    name: str = Field(min_length=1, max_length=160)
    platform: str = Field(min_length=1, max_length=120)
    operations: Annotated[list[Annotated[str, Field(pattern=r"^[a-z][a-z0-9]*(?:\.[a-z][a-z0-9]*)+$", max_length=80)]], Field(max_length=64)]
    roots: Annotated[list[RootSnapshot], Field(max_length=64)]
    home_directory: str | None = Field(default=None, max_length=4096)
    home_root_id: UUID | None = None
    home_root_revision: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    home_relative_directory: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def unique_snapshots(self) -> HelloMessage:
        if len(self.operations) != len(set(self.operations)):
            raise ValueError("operations must be unique")
        if len(self.roots) != len({root.id for root in self.roots}):
            raise ValueError("roots must be unique")
        if len(
            {
                self.home_directory is None,
                self.home_root_id is None,
                self.home_root_revision is None,
                self.home_relative_directory is None,
            }
        ) != 1:
            raise ValueError("home metadata must be provided together")
        if self.home_root_id is not None and not any(
            root.id == self.home_root_id and root.revision == self.home_root_revision
            for root in self.roots
        ):
            raise ValueError("home root must match an advertised root")
        if self.home_directory is not None and not self.home_directory.startswith("/"):
            raise ValueError("home directory must be absolute")
        return self


class WelcomeMessage(WireModel):
    type: Literal["welcome"] = "welcome"
    protocol_version: Literal[2] = PROTOCOL_VERSION
    agent_id: UUID


class RequestMessage(WireModel):
    type: Literal["request"] = "request"
    id: UUID
    operation: str = Field(pattern=r"^[a-z][a-z0-9]*(?:\.[a-z][a-z0-9]*)+$", max_length=64)
    arguments: dict[str, JsonValue] = Field(default_factory=dict, max_length=100)


class EventMessage(WireModel):
    type: Literal["event"]
    id: UUID
    event: Literal["stdout", "stderr"]
    data: str = Field(max_length=MAX_EVENT_BYTES)


class CancelMessage(WireModel):
    type: Literal["cancel"] = "cancel"
    id: UUID


class ResponseError(WireModel):
    code: str = Field(pattern=r"^[a-z][A-Za-z]*(?:\.[a-z][A-Za-z]*)+$", max_length=120)
    params: dict[str, JsonValue] | None = Field(default=None, max_length=20)


class ResponseMessage(WireModel):
    type: Literal["response"]
    id: UUID
    ok: bool
    result: dict[str, JsonValue] | None = None
    error: ResponseError | None = None

    @model_validator(mode="after")
    def exactly_one_outcome(self) -> ResponseMessage:
        if self.ok and (self.error is not None or self.result is None):
            raise ValueError("successful responses require only a result")
        if not self.ok and (self.error is None or self.result is not None):
            raise ValueError("failed responses require only an error")
        return self
