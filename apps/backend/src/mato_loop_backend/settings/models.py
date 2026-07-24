from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, ClassVar, Self
from unicodedata import category as unicode_category
from uuid import UUID, uuid4

from pydantic import (
    AliasChoices,
    BaseModel,
    Field,
    HttpUrl,
    TypeAdapter,
    field_validator,
    model_validator,
)

_MODEL_RUNTIME_KEYS = {
    "context_window_tokens": "contextWindowTokens",
    "max_output_tokens": "maxOutputTokens",
    "characters_per_token": "charactersPerToken",
    "supports_tools": "supportsTools",
    "supports_parallel_tool_calls": "supportsParallelToolCalls",
    "supports_streaming": "supportsStreaming",
    "supports_image_input": "supportsImageInput",
    "reasoning_summary": "reasoningSummary",
    "trust_level": "trustLevel",
    "pseudonymization_enabled": "pseudonymizationEnabled",
}


class SettingsScope(StrEnum):
    GLOBAL = "global"
    USER = "user"


class SettingsDocument(BaseModel):
    settings_key: ClassVar[str]
    settings_scope: ClassVar[SettingsScope] = SettingsScope.GLOBAL


def normalize_agent_display_name(value: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise ValueError("display_name must not be blank")
    if any(unicode_category(character) in {"Cc", "Cf"} for character in cleaned):
        raise ValueError("display_name must not contain control characters")
    return cleaned


class MemoryPolicy(StrEnum):
    INHERIT = "inherit"
    ALLOW = "allow"
    DENY = "deny"


class TrustLevel(StrEnum):
    PUBLIC = "public"
    PRIVATE = "private"


class WebFetchPolicy(StrEnum):
    DISABLED = "disabled"
    ASK = "ask"
    AUTO_PUBLIC = "auto_public"
    AUTO_ALLOWED = "auto_allowed"


class ToolExecutionPolicy(StrEnum):
    DISABLED = "disabled"
    ASK = "ask"
    AUTO = "auto"


class ModelWebFetchPolicy(StrEnum):
    INHERIT = "inherit"
    DISABLED = "disabled"
    ASK = "ask"
    AUTO_PUBLIC = "auto_public"
    AUTO_ALLOWED = "auto_allowed"


class ModelOrigin(StrEnum):
    MANUAL = "manual"
    DISCOVERED = "discovered"


class ModelAvailability(StrEnum):
    AVAILABLE = "available"
    MISSING = "missing"


class ModelCapability(StrEnum):
    CHAT = "chat"
    IMAGE_GENERATION = "image_generation"
    SPEECH_GENERATION = "speech_generation"
    AUDIO_GENERATION = "audio_generation"
    AUDIO_TRANSCRIPTION = "audio_transcription"


class LlmProviderConfig(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    name: str
    adapter_type: str
    enabled: bool = True
    trust_level: TrustLevel = Field(
        default=TrustLevel.PUBLIC,
        validation_alias=AliasChoices("trust_level", "trustLevel"),
    )
    pseudonymization_enabled: bool = Field(
        default=True,
        validation_alias=AliasChoices(
            "pseudonymization_enabled", "pseudonymizationEnabled"
        ),
    )
    memory_policy: MemoryPolicy = MemoryPolicy.INHERIT
    credential_id: UUID | None = None
    config: dict[str, Any] = Field(default_factory=dict)
    models_last_synced_at: datetime | None = None


class ModelConfig(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    provider_id: UUID
    model_id: str
    name: str | None = None
    enabled: bool = True
    origin: ModelOrigin = ModelOrigin.MANUAL
    availability: ModelAvailability = ModelAvailability.AVAILABLE
    last_seen_at: datetime | None = None
    discovery_metadata: dict[str, Any] = Field(default_factory=dict)
    capabilities: set[ModelCapability] = Field(
        default_factory=lambda: {ModelCapability.CHAT}
    )
    memory_policy: MemoryPolicy = MemoryPolicy.INHERIT
    trust_level: TrustLevel | None = Field(
        default=None,
        validation_alias=AliasChoices("trust_level", "trustLevel"),
    )
    pseudonymization_enabled: bool | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "pseudonymization_enabled", "pseudonymizationEnabled"
        ),
    )
    webfetch_policy: ModelWebFetchPolicy = Field(
        default=ModelWebFetchPolicy.INHERIT,
        validation_alias=AliasChoices("webfetch_policy", "webfetchPolicy"),
    )
    config: dict[str, Any] = Field(default_factory=dict)
    context_window_tokens: int = Field(
        default=128_000,
        ge=1,
        le=10_000_000,
        validation_alias=AliasChoices("context_window_tokens", "contextWindowTokens"),
    )
    max_output_tokens: int = Field(
        default=16_384,
        ge=1,
        le=1_000_000,
        validation_alias=AliasChoices("max_output_tokens", "maxOutputTokens"),
    )
    characters_per_token: float = Field(
        default=4.0,
        gt=0,
        le=100,
        validation_alias=AliasChoices("characters_per_token", "charactersPerToken"),
    )
    supports_tools: bool = Field(
        default=True,
        validation_alias=AliasChoices("supports_tools", "supportsTools"),
    )
    supports_parallel_tool_calls: bool = Field(
        default=True,
        validation_alias=AliasChoices(
            "supports_parallel_tool_calls", "supportsParallelToolCalls"
        ),
    )
    supports_streaming: bool = Field(
        default=True,
        validation_alias=AliasChoices("supports_streaming", "supportsStreaming"),
    )
    supports_image_input: bool = Field(
        default=False,
        validation_alias=AliasChoices("supports_image_input", "supportsImageInput"),
    )
    reasoning_summary: bool = Field(
        default=False,
        validation_alias=AliasChoices("reasoning_summary", "reasoningSummary"),
    )
    @model_validator(mode="before")
    @classmethod
    def load_legacy_runtime_config(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        values = dict(value)
        config = value.get("config") if isinstance(value.get("config"), dict) else {}
        for snake_key, camel_key in _MODEL_RUNTIME_KEYS.items():
            if snake_key in values or camel_key in values:
                continue
            if snake_key in config:
                values[snake_key] = config[snake_key]
            elif camel_key in config:
                values[snake_key] = config[camel_key]
        context_window = values.get("context_window_tokens", values.get("contextWindowTokens"))
        has_output_limit = "max_output_tokens" in values or "maxOutputTokens" in values
        if context_window is not None and not has_output_limit:
            if isinstance(context_window, int) and not isinstance(context_window, bool) and context_window > 1:
                values["max_output_tokens"] = min(16_384, max(1, context_window // 4))
        return values

    @model_validator(mode="after")
    def validate_runtime_limits(self) -> Self:
        if self.max_output_tokens >= self.context_window_tokens:
            raise ValueError("max_output_tokens must be less than context_window_tokens")
        if not self.supports_tools:
            self.supports_parallel_tool_calls = False
        return self


def effective_model_trust(
    provider: LlmProviderConfig, model: ModelConfig
) -> TrustLevel:
    return model.trust_level or provider.trust_level


def effective_model_pseudonymization(
    provider: LlmProviderConfig, model: ModelConfig
) -> bool:
    if effective_model_trust(provider, model) != TrustLevel.PUBLIC:
        return False
    if model.pseudonymization_enabled is not None:
        return model.pseudonymization_enabled
    return provider.pseudonymization_enabled


class IntegrationInstance(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    name: str
    integration_type: str
    adapter_type: str
    enabled: bool = True
    credential_id: UUID | None = None
    config: dict[str, Any] = Field(default_factory=dict)


_HTTP_URL_ADAPTER = TypeAdapter(HttpUrl)


def canonicalize_http_origin(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("origin must be a string")
    url = _HTTP_URL_ADAPTER.validate_python(value)
    if url.username is not None or url.password is not None:
        raise ValueError("origin must not contain credentials")
    if url.query is not None or url.fragment is not None:
        raise ValueError("origin must not contain query or fragment")
    if url.path != "/":
        raise ValueError("origin must not contain a path")

    host = url.host
    if host is None:
        raise ValueError("origin must contain a host")
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    default_port = 80 if url.scheme == "http" else 443
    port = f":{url.port}" if url.port is not None and url.port != default_port else ""
    return f"{url.scheme}://{host}{port}"


class WebFetchSettings(BaseModel):
    enabled: bool = True
    default_policy: WebFetchPolicy = Field(
        default=WebFetchPolicy.ASK,
        validation_alias=AliasChoices("default_policy", "defaultPolicy"),
    )
    allowed_private_origins: list[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices("allowed_private_origins", "allowedPrivateOrigins"),
    )

    @field_validator("allowed_private_origins", mode="before")
    @classmethod
    def validate_allowed_private_origins(cls, value: Any) -> Any:
        if not isinstance(value, (list, tuple, set)):
            return value
        origins: list[str] = []
        for item in value:
            origin = canonicalize_http_origin(item)
            if origin not in origins:
                origins.append(origin)
        return origins


class WebSearchToolSettings(BaseModel):
    primary_provider: str = Field(
        default="no_key",
        min_length=1,
        max_length=100,
        validation_alias=AliasChoices("primary_provider", "primaryProvider"),
    )
    fallback_provider: str | None = Field(
        default=None,
        min_length=1,
        max_length=100,
        validation_alias=AliasChoices("fallback_provider", "fallbackProvider"),
    )

    @field_validator("primary_provider", "fallback_provider", mode="before")
    @classmethod
    def migrate_duckduckgo_provider(cls, value: Any) -> Any:
        return "no_key" if value == "duckduckgo" else value

    @model_validator(mode="after")
    def providers_must_differ(self) -> WebSearchToolSettings:
        if self.fallback_provider == self.primary_provider:
            raise ValueError("fallback_provider must differ from primary_provider")
        return self


class MediaToolSettings(BaseModel):
    policy: ToolExecutionPolicy = ToolExecutionPolicy.DISABLED
    timeout_seconds: int = Field(
        default=300,
        ge=10,
        le=3_600,
        validation_alias=AliasChoices("timeout_seconds", "timeoutSeconds"),
    )


class PolicyToolSettings(BaseModel):
    policy: ToolExecutionPolicy = ToolExecutionPolicy.ASK


class ToolSettings(SettingsDocument):
    settings_key = "tools"

    web_search: WebSearchToolSettings = Field(
        default_factory=WebSearchToolSettings,
        validation_alias=AliasChoices("web_search", "webSearch"),
    )
    web_fetch: WebFetchSettings = Field(
        default_factory=WebFetchSettings,
        validation_alias=AliasChoices("web_fetch", "webFetch"),
    )
    generate_image: MediaToolSettings = Field(
        default_factory=MediaToolSettings,
        validation_alias=AliasChoices("generate_image", "generateImage"),
    )
    generate_speech: MediaToolSettings = Field(
        default_factory=MediaToolSettings,
        validation_alias=AliasChoices("generate_speech", "generateSpeech"),
    )
    generate_audio: MediaToolSettings = Field(
        default_factory=MediaToolSettings,
        validation_alias=AliasChoices("generate_audio", "generateAudio"),
    )
    transcribe_audio: MediaToolSettings = Field(
        default_factory=MediaToolSettings,
        validation_alias=AliasChoices("transcribe_audio", "transcribeAudio"),
    )
    update_agent_identity: PolicyToolSettings = Field(
        default_factory=PolicyToolSettings,
        validation_alias=AliasChoices("update_agent_identity", "updateAgentIdentity"),
    )


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
    compaction_model_id: UUID | None = None
    title_generation_model_id: UUID | None = None
    dreaming_model_id: UUID | None = None
    image_generation_model_id: UUID | None = None
    speech_generation_model_id: UUID | None = None
    audio_generation_model_id: UUID | None = None
    audio_transcription_model_id: UUID | None = None


class IntegrationSettings(SettingsDocument):
    settings_key = "integrations"

    instances: list[IntegrationInstance] = Field(default_factory=list)


class AgentRuntimeSettings(SettingsDocument):
    settings_key = "agent-runtime"

    max_steps: int = Field(default=20, ge=1, le=100)
    max_model_requests: int = Field(default=30, ge=1, le=200)
    max_tool_calls: int = Field(default=20, ge=1, le=100)
    request_timeout_seconds: int = Field(default=300, ge=10, le=3600)
    compaction_trigger_ratio: float = Field(default=0.8, ge=0.5, le=0.95)
    compaction_target_ratio: float = Field(default=0.6, ge=0.1, le=0.9)
    safety_reserve_tokens: int = Field(default=4_096, ge=0, le=131_072)
    protected_recent_turns: int = Field(default=2, ge=1, le=100)
    approval_timeout_seconds: int = Field(default=600, ge=10, le=86_400)
    max_compaction_requests: int = Field(default=3, ge=1, le=20)

    @model_validator(mode="after")
    def validate_compaction_ratios(self) -> Self:
        if self.compaction_target_ratio >= self.compaction_trigger_ratio:
            raise ValueError("compaction_target_ratio must be less than compaction_trigger_ratio")
        return self


class MemorySettings(SettingsDocument):
    settings_key = "memory"
    settings_scope = SettingsScope.USER

    enabled: bool = True
    max_context_items: int = Field(default=10, ge=0, le=100)
    max_context_characters: int = Field(default=20_000, ge=0, le=200_000)
    automatic_dreaming_enabled: bool = False
    dreaming_instructions: str = Field(default="", max_length=8_000)
    dreaming_interval_minutes: int = Field(default=60, ge=1, le=10_080)
    dreaming_idle_minutes: int = Field(default=15, ge=1, le=10_080)
    dreaming_max_batch_messages: int = Field(default=50, ge=2, le=500)
    dreaming_max_candidates: int = Field(default=10, ge=1, le=100)


class AgentIdentitySettings(SettingsDocument):
    settings_key = "agent-identity"
    settings_scope = SettingsScope.USER

    display_name: str = Field(default="MatoLoop", min_length=1, max_length=80)
    instructions: str = Field(default="", max_length=16_000)

    @field_validator("display_name")
    @classmethod
    def validate_display_name(cls, value: str) -> str:
        return normalize_agent_display_name(value)

    @field_validator("instructions")
    @classmethod
    def normalize_instructions(cls, value: str) -> str:
        return value.strip()


class UserPreferences(SettingsDocument):
    settings_key = "user-preferences"
    settings_scope = SettingsScope.USER

    dark_mode: bool = False
    session_drawer_open: bool = True
    thinking_expanded: bool = False
