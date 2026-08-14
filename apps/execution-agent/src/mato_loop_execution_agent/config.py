from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from typing import Self
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator

UUID_PATTERN = (
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-8][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$"
)


class RootConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    id: str = Field(pattern=UUID_PATTERN)
    label: str = Field(min_length=1, max_length=128)
    path: Path

    @field_validator("path")
    @classmethod
    def absolute_path(cls, value: Path) -> Path:
        if not value.is_absolute():
            raise ValueError("root path must be absolute")
        return value


class ConnectionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, populate_by_name=True)

    backend_url: str = Field(alias="backendUrl", min_length=1)
    agent_id: str = Field(alias="agentId", pattern=UUID_PATTERN)
    allow_insecure: bool = Field(default=False, alias="allowInsecure")

    @field_validator("backend_url")
    @classmethod
    def websocket_url(cls, value: str) -> str:
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"ws", "wss"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or not parsed.path.endswith("/execution-agents/connect")
        ):
            raise ValueError("backendUrl must use ws:// or wss://")
        return value

    @model_validator(mode="after")
    def secure_transport(self) -> Self:
        if not self.allow_insecure and self.backend_url.startswith("ws://"):
            raise ValueError("ws:// requires allowInsecure")
        return self


class AgentConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    backend_url: str | None = Field(default=None, alias="backendUrl", min_length=1)
    token: SecretStr | None = Field(default=None, min_length=1, max_length=4096)
    agent_id: str | None = Field(
        default=None,
        alias="agentId",
        pattern=UUID_PATTERN,
    )
    name: str = Field(min_length=1, max_length=128)
    roots: list[RootConfig] = Field(min_length=1, max_length=32)
    home_directory: Path | None = Field(default=None, alias="homeDirectory")
    data_dir: Path = Field(alias="dataDir")
    allow_insecure: bool = Field(default=False, alias="allowInsecure")
    process_executables: dict[str, Path] = Field(default_factory=dict, alias="processExecutables")
    shell_executable: Path | None = Field(default=None, alias="shellExecutable")
    git_executable: Path | None = Field(default=None, alias="gitExecutable")
    openscad_executable: Path | None = Field(default=None, alias="openscadExecutable")
    allow_network: bool = Field(default=True, alias="allowNetwork")
    max_message_bytes: int = Field(
        default=1024 * 1024, alias="maxMessageBytes", ge=1024, le=1024 * 1024
    )
    max_file_bytes: int = Field(default=16 * 1024 * 1024, alias="maxFileBytes", ge=1)
    max_transfer_bytes: int = Field(default=256 * 1024 * 1024, alias="maxTransferBytes", ge=1)
    max_operation_entries: int = Field(default=10_000, alias="maxOperationEntries", ge=1)
    max_output_bytes: int = Field(
        default=1024 * 1024, alias="maxOutputBytes", ge=1024, le=1024 * 1024
    )
    max_session_cache_bytes: int = Field(
        default=512 * 1024 * 1024, alias="maxSessionCacheBytes", ge=0
    )
    default_timeout_seconds: float = Field(
        default=60.0, alias="defaultTimeoutSeconds", gt=0, le=3600
    )

    @field_validator("data_dir", "home_directory")
    @classmethod
    def absolute_directory(cls, value: Path | None) -> Path | None:
        if value is not None and not value.is_absolute():
            raise ValueError("directory paths must be absolute")
        return value

    @field_validator("process_executables")
    @classmethod
    def absolute_executables(cls, value: dict[str, Path]) -> dict[str, Path]:
        if any(not alias or not path.is_absolute() for alias, path in value.items()):
            raise ValueError("process executable aliases must be non-empty and paths absolute")
        return value

    @field_validator("shell_executable", "git_executable", "openscad_executable")
    @classmethod
    def absolute_optional_executable(cls, value: Path | None) -> Path | None:
        if value is not None and not value.is_absolute():
            raise ValueError("executable paths must be absolute")
        return value

    @model_validator(mode="after")
    def unique_roots(self) -> Self:
        if (self.backend_url is None) != (self.agent_id is None):
            raise ValueError("backendUrl and agentId must be configured together")
        if self.backend_url is not None and self.agent_id is not None:
            ConnectionConfig(
                backendUrl=self.backend_url,
                agentId=self.agent_id,
                allowInsecure=self.allow_insecure,
            )
        if len({root.id for root in self.roots}) != len(self.roots):
            raise ValueError("root IDs must be unique")
        physical_roots: list[tuple[Path, tuple[int, int]]] = []
        for root in self.roots:
            try:
                canonical = root.path.resolve(strict=True)
                metadata = canonical.stat(follow_symlinks=False)
            except OSError as exc:
                raise ValueError("root path is unavailable") from exc
            if not stat.S_ISDIR(metadata.st_mode):
                raise ValueError("root path must be a directory")
            root.path = canonical
            identity = (metadata.st_dev, metadata.st_ino)
            for other, other_identity in physical_roots:
                if (
                    identity == other_identity
                    or canonical in other.parents
                    or other in canonical.parents
                ):
                    raise ValueError("workspace roots must not overlap")
            physical_roots.append((canonical, identity))
        if self.home_directory is not None:
            try:
                home = self.home_directory.resolve(strict=True)
                metadata = home.stat(follow_symlinks=False)
            except OSError as exc:
                raise ValueError("homeDirectory is unavailable") from exc
            if (
                not stat.S_ISDIR(metadata.st_mode)
                or not os.access(home, os.R_OK | os.X_OK)
                or not any(home == root.path or root.path in home.parents for root in self.roots)
            ):
                raise ValueError("homeDirectory must be an accessible directory within a root")
            self.home_directory = home
        return self

    @classmethod
    def load(cls, path: Path) -> Self:
        data = json.loads(path.read_text(encoding="utf-8"))
        overrides = {
            "name": os.getenv("MATO_EXECUTION_AGENT_NAME"),
        }
        data.update({key: value for key, value in overrides.items() if value})
        # JSON mode accepts native JSON path strings while strict mode still rejects coercion.
        return cls.model_validate_json(json.dumps(data))
