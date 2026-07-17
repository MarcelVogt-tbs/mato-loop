from __future__ import annotations

from enum import StrEnum
from typing import Any, ClassVar
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class SettingsDocument(BaseModel):
    settings_key: ClassVar[str]


class MemoryPolicy(StrEnum):
    INHERIT = "inherit"
    ALLOW = "allow"
    DENY = "deny"


class LlmProviderConfig(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    name: str
    adapter_type: str
    enabled: bool = True
    memory_policy: MemoryPolicy = MemoryPolicy.INHERIT
    credential_id: UUID | None = None
    config: dict[str, Any] = Field(default_factory=dict)


class ModelConfig(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    provider_id: UUID
    model_id: str
    name: str | None = None
    enabled: bool = True
    memory_policy: MemoryPolicy = MemoryPolicy.INHERIT
    config: dict[str, Any] = Field(default_factory=dict)


class SearchProviderConfig(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    name: str
    adapter_type: str
    enabled: bool = True
    credential_id: UUID | None = None
    config: dict[str, Any] = Field(default_factory=dict)


class GeneralSettings(SettingsDocument):
    settings_key = "general"

    default_locale: str = "de-DE"
    default_timezone: str = "Europe/Berlin"


class AiSettings(SettingsDocument):
    settings_key = "ai"

    enabled: bool = True
    providers: list[LlmProviderConfig] = Field(default_factory=list)
    models: list[ModelConfig] = Field(default_factory=list)
    default_model_id: UUID | None = None


class SearchSettings(SettingsDocument):
    settings_key = "search"

    enabled: bool = True
    providers: list[SearchProviderConfig] = Field(default_factory=list)
    default_provider_id: UUID | None = None


class AgentRuntimeSettings(SettingsDocument):
    settings_key = "agent-runtime"

    max_steps: int = Field(default=20, ge=1, le=100)
    max_model_requests: int = Field(default=30, ge=1, le=200)
    max_tool_calls: int = Field(default=20, ge=1, le=100)
    request_timeout_seconds: int = Field(default=300, ge=10, le=3600)


class MemorySettings(SettingsDocument):
    settings_key = "memory"

    enabled: bool = True
    default_session_enabled: bool = False
    max_context_items: int = Field(default=10, ge=0, le=100)
    max_context_characters: int = Field(default=20_000, ge=0, le=200_000)


class UserPreferences(SettingsDocument):
    settings_key = "user-preferences"

    dark_mode: bool = False
    session_drawer_open: bool = True
