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


class AgentConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    backend_url: str = Field(alias="backendUrl", min_length=1)
    token: SecretStr
    agent_id: str = Field(
        alias="agentId",
        pattern=UUID_PATTERN,
    )
    name: str = Field(min_length=1, max_length=128)
    roots: list[RootConfig] = Field(min_length=1, max_length=32)
    data_dir: Path = Field(alias="dataDir")
    allow_insecure: bool = Field(default=False, alias="allowInsecure")
    process_executables: dict[str, Path] = Field(default_factory=dict, alias="processExecutables")
    shell_executable: Path | None = Field(default=None, alias="shellExecutable")
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

    @field_validator("data_dir")
    @classmethod
    def absolute_data_dir(cls, value: Path) -> Path:
        if not value.is_absolute():
            raise ValueError("dataDir must be absolute")
        return value

    @field_validator("process_executables")
    @classmethod
    def absolute_executables(cls, value: dict[str, Path]) -> dict[str, Path]:
        if any(not alias or not path.is_absolute() for alias, path in value.items()):
            raise ValueError("process executable aliases must be non-empty and paths absolute")
        return value

    @field_validator("shell_executable")
    @classmethod
    def absolute_shell(cls, value: Path | None) -> Path | None:
        if value is not None and not value.is_absolute():
            raise ValueError("shellExecutable must be absolute")
        return value

    @model_validator(mode="after")
    def unique_roots(self) -> Self:
        if len({root.id for root in self.roots}) != len(self.roots):
            raise ValueError("root IDs must be unique")
        if not self.allow_insecure and self.backend_url.startswith("ws://"):
            raise ValueError("ws:// requires allowInsecure")
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
        return self

    @classmethod
    def load(cls, path: Path) -> Self:
        data = json.loads(path.read_text(encoding="utf-8"))
        overrides = {
            "backendUrl": os.getenv("MATO_EXECUTION_AGENT_BACKEND_URL"),
            "token": os.getenv("MATO_EXECUTION_AGENT_TOKEN"),
            "agentId": os.getenv("MATO_EXECUTION_AGENT_ID"),
            "name": os.getenv("MATO_EXECUTION_AGENT_NAME"),
        }
        data.update({key: value for key, value in overrides.items() if value is not None})
        # JSON mode accepts native JSON path strings while strict mode still rejects coercion.
        return cls.model_validate_json(json.dumps(data))
