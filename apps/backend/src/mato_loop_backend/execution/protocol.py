from __future__ import annotations

from pathlib import PurePosixPath
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

PROTOCOL_VERSION = 1
MAX_MESSAGE_BYTES = 1_048_576
MAX_REQUEST_BYTES = 262_144
MAX_EVENT_BYTES = 65_536
MAX_EVENTS_PER_REQUEST = 1_024
MAX_OUTPUT_BYTES = 1_048_576
SUPPORTED_OPERATIONS = frozenset(
    {
        "cache.clear",
        "cache.ensure",
        "cache.export",
        "cache.store",
        "filesystem.resolve",
        "integration.git.add",
        "integration.git.clone",
        "integration.git.commit",
        "integration.git.diff",
        "integration.git.log",
        "integration.git.status",
        "integration.openscad.discover",
        "integration.openscad.render",
        "integration.openscad.render6",
        "integration.openscad.test",
        "shell.run",
        "shell.inspect",
        "system.info",
        "terminal.close",
        "terminal.open",
        "terminal.read",
        "terminal.resize",
        "terminal.run",
        "terminal.write",
        "terminal.shared.open",
        "terminal.shared.status",
        "terminal.shared.stage",
        "terminal.shared.inspect",
        "terminal.shared.execute",
        "terminal.shared.cancel",
        "filesystem.copy",
        "filesystem.delete",
        "filesystem.edit",
        "filesystem.export",
        "filesystem.glob",
        "filesystem.grep",
        "filesystem.import",
        "filesystem.list",
        "filesystem.mkdir",
        "filesystem.move",
        "filesystem.patch",
        "filesystem.read",
        "filesystem.rename",
        "filesystem.stat",
        "filesystem.upsert",
        "filesystem.write",
    }
)


def _camel(value: str) -> str:
    first, *rest = value.split("_")
    return first + "".join(part.capitalize() for part in rest)


def _absolute_path(value: str) -> str:
    parsed = PurePosixPath(value)
    if (
        not value.startswith("/")
        or "\\" in value
        or parsed.as_posix() != value
        or (
            value != "/"
            and any(part in {"", ".", ".."} for part in value[1:].split("/"))
        )
        or any(ord(character) < 32 or 127 <= ord(character) <= 159 for character in value)
    ):
        raise ValueError("path must be a normalized absolute POSIX path")
    return value


class WireModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=_camel,
        populate_by_name=True,
        serialize_by_alias=True,
        extra="forbid",
        strict=True,
    )


class HelloMessage(WireModel):
    type: Literal["hello"]
    protocol_version: Literal[1]
    agent_id: UUID
    agent_version: str = Field(min_length=1, max_length=120)
    computer_name: str = Field(min_length=1, max_length=255)
    platform: str = Field(min_length=1, max_length=120)
    operations: Annotated[list[Annotated[str, Field(pattern=r"^[a-z][a-z0-9]*(?:\.[a-z][a-z0-9]*)+$", max_length=80)]], Field(max_length=64)]
    home_directory: str = Field(min_length=1, max_length=4096)

    @model_validator(mode="after")
    def validate_hello(self) -> HelloMessage:
        _absolute_path(self.home_directory)
        if len(self.operations) != len(set(self.operations)):
            raise ValueError("operations must be unique")
        return self


class WelcomeMessage(WireModel):
    type: Literal["welcome"] = "welcome"
    protocol_version: Literal[1] = PROTOCOL_VERSION
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
