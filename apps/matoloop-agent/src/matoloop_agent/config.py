from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from typing import Self
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, field_validator, model_validator

UUID_PATTERN = (
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-8][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$"
)


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
    agent_id: str | None = Field(default=None, alias="agentId", pattern=UUID_PATTERN)
    home_directory: Path = Field(alias="homeDirectory")
    allow_insecure: bool = Field(default=False, alias="allowInsecure")
    shell_executable: Path | None = Field(default=None, alias="shellExecutable")
    openscad_executable: Path | None = Field(default=None, alias="openscadExecutable")
    max_message_bytes: int = Field(
        default=1024 * 1024, alias="maxMessageBytes", ge=1024, le=1024 * 1024
    )
    max_file_bytes: int = Field(default=16 * 1024 * 1024, alias="maxFileBytes", ge=1)
    max_transfer_bytes: int = Field(default=1024 * 1024 * 1024, alias="maxTransferBytes", ge=1)
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
    max_timeout_seconds: float = Field(default=3600.0, alias="maxTimeoutSeconds", gt=0, le=3600)
    _data_directory: Path = PrivateAttr(default_factory=Path.cwd)

    @property
    def data_dir(self) -> Path:
        return self._data_directory

    @field_validator("home_directory")
    @classmethod
    def canonical_home(cls, value: Path) -> Path:
        if not value.is_absolute():
            raise ValueError("homeDirectory must be absolute")
        try:
            canonical = value.resolve(strict=True)
            metadata = canonical.stat(follow_symlinks=False)
        except OSError as exc:
            raise ValueError("homeDirectory is unavailable") from exc
        if not stat.S_ISDIR(metadata.st_mode) or not os.access(canonical, os.R_OK | os.X_OK):
            raise ValueError("homeDirectory must be an accessible directory")
        return canonical

    @field_validator("shell_executable", "openscad_executable")
    @classmethod
    def absolute_optional_executable(cls, value: Path | None) -> Path | None:
        if value is not None and not value.is_absolute():
            raise ValueError("executable paths must be absolute")
        return value

    @model_validator(mode="after")
    def valid_connection(self) -> Self:
        if (self.backend_url is None) != (self.agent_id is None):
            raise ValueError("backendUrl and agentId must be configured together")
        if self.backend_url is not None and self.agent_id is not None:
            ConnectionConfig(
                backendUrl=self.backend_url,
                agentId=self.agent_id,
                allowInsecure=self.allow_insecure,
            )
        return self

    @classmethod
    def load(cls, path: Path) -> Self:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            data.pop("processExecutables", None)
        config = cls.model_validate_json(json.dumps(data))
        config._data_directory = path.resolve().parent
        return config
