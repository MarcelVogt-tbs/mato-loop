from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, ClassVar, Self
from unicodedata import category as unicode_category
from uuid import UUID, uuid4

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    PrivateAttr,
    computed_field,
    field_validator,
    model_validator,
)

_MODEL_RUNTIME_KEYS = {
    "context_window_tokens": "contextWindowTokens",
    "discovered_context_window_tokens": "discoveredContextWindowTokens",
    "use_discovered_context_window": "useDiscoveredContextWindow",
    "max_output_tokens": "maxOutputTokens",
    "characters_per_token": "charactersPerToken",
    "supports_tools": "supportsTools",
    "supports_parallel_tool_calls": "supportsParallelToolCalls",
    "supports_streaming": "supportsStreaming",
    "input_modalities": "inputModalities",
    "supports_image_input": "supportsImageInput",
    "reasoning_summary": "reasoningSummary",
    "reasoning_effort": "reasoningEffort",
    "trust_level": "trustLevel",
    "pseudonymization_enabled": "pseudonymizationEnabled",
}

DEFAULT_WEBFETCH_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36"
)


def _camel_case(value: str) -> str:
    first, *rest = value.split("_")
    return first + "".join(part.capitalize() for part in rest)


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


def normalize_skill_name(value: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise ValueError("name must not be blank")
    if any(unicode_category(character) in {"Cc", "Cf"} for character in cleaned):
        raise ValueError("name must not contain control characters")
    return cleaned


def normalize_skill_text(value: str, field: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise ValueError(f"{field} must not be blank")
    return cleaned


class MemoryPolicy(StrEnum):
    INHERIT = "inherit"
    ALLOW = "allow"
    DENY = "deny"


class TrustLevel(StrEnum):
    PUBLIC = "public"
    PRIVATE = "private"


class ToolExecutionPolicy(StrEnum):
    DISABLED = "disabled"
    ASK = "ask"
    AUTO = "auto"


DEFAULT_AGENT_PROFILE_ID = UUID("00000000-0000-4000-8000-000000000001")
DEFAULT_PERSONA_ID = UUID("00000000-0000-4000-8000-000000000002")
PLAN_AGENT_PROFILE_ID = UUID("00000000-0000-4000-8000-000000000003")
BUILD_AGENT_PROFILE_ID = UUID("00000000-0000-4000-8000-000000000004")
BUILTIN_AGENT_PROFILE_NAMES = {
    DEFAULT_AGENT_PROFILE_ID: "Allgemein",
    PLAN_AGENT_PROFILE_ID: "Plan",
    BUILD_AGENT_PROFILE_ID: "Build",
}
BUILTIN_AGENT_PROFILE_IDS = frozenset(BUILTIN_AGENT_PROFILE_NAMES)


class PersonaProfile(BaseModel):
    model_config = ConfigDict(
        alias_generator=lambda value: _camel_case(value),
        populate_by_name=True,
        extra="forbid",
    )

    id: UUID = Field(default_factory=uuid4)
    name: str = Field(min_length=1, max_length=80)
    instructions: str = Field(default="", max_length=32_002)
    owner_id: UUID | None = Field(default=None, exclude=True)

    @field_validator("name", "instructions", mode="before")
    @classmethod
    def normalize_text(cls, value: Any, info: Any) -> Any:
        if not isinstance(value, str):
            return value
        cleaned = value.strip()
        if info.field_name == "name" and not cleaned:
            raise ValueError("name must not be blank")
        return cleaned


def default_persona_profile() -> PersonaProfile:
    return PersonaProfile(id=DEFAULT_PERSONA_ID, name="Standard")


class AgentProfilePermissions(BaseModel):
    model_config = ConfigDict(
        alias_generator=lambda value: _camel_case(value),
        populate_by_name=True,
        extra="forbid",
    )

    policies: dict[str, ToolExecutionPolicy] = Field(default_factory=dict)
    integration_policies: dict[str, dict[str, ToolExecutionPolicy]] = Field(
        default_factory=dict
    )
    resource_policies: dict[
        str, dict[str, dict[str, ToolExecutionPolicy]]
    ] = Field(default_factory=dict)


class AgentProfile(BaseModel):
    model_config = ConfigDict(
        alias_generator=lambda value: _camel_case(value),
        populate_by_name=True,
        extra="forbid",
    )

    id: UUID = Field(default_factory=uuid4)
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=500)
    persona_id: UUID | None = None
    instructions: str = Field(min_length=1, max_length=16_000)
    private_permissions: AgentProfilePermissions = Field(
        default_factory=AgentProfilePermissions
    )
    public_permissions: AgentProfilePermissions = Field(
        default_factory=AgentProfilePermissions
    )
    owner_id: UUID | None = Field(default=None, exclude=True)

    @field_validator("name", "description", "instructions", mode="before")
    @classmethod
    def normalize_text(cls, value: Any, info: Any) -> Any:
        if not isinstance(value, str):
            return value
        cleaned = value.strip()
        if info.field_name in {"name", "instructions"} and not cleaned:
            raise ValueError(f"{info.field_name} must not be blank")
        return cleaned


def default_agent_profile() -> AgentProfile:
    return AgentProfile(
        id=DEFAULT_AGENT_PROFILE_ID,
        name=BUILTIN_AGENT_PROFILE_NAMES[DEFAULT_AGENT_PROFILE_ID],
        description="Standardagent für Sessions ohne ausgewähltes Profil.",
        instructions="Follow the user's request and the selected persona.",
    )


def plan_agent_profile() -> AgentProfile:
    policies = {
        "filesystem.write": ToolExecutionPolicy.DISABLED,
        "filesystem.delete": ToolExecutionPolicy.DISABLED,
        "session.files.write": ToolExecutionPolicy.AUTO,
        "shell.write": ToolExecutionPolicy.DISABLED,
        "git.stage": ToolExecutionPolicy.DISABLED,
        "git.clone": ToolExecutionPolicy.DISABLED,
        "git.commit": ToolExecutionPolicy.DISABLED,
    }
    return AgentProfile(
        id=PLAN_AGENT_PROFILE_ID,
        name=BUILTIN_AGENT_PROFILE_NAMES[PLAN_AGENT_PROFILE_ID],
        description="Erstellt klare, umsetzbare Pläne, ohne Änderungen ungefragt auszuführen.",
        instructions=(
            "Create a clear, actionable plan. Inspect the relevant context, identify risks "
            "and verification steps. Do not modify computer files or run commands classified "
            "as writes."
        ),
        private_permissions=AgentProfilePermissions(policies=policies),
        public_permissions=AgentProfilePermissions(policies=policies),
    )


def build_agent_profile() -> AgentProfile:
    return AgentProfile(
        id=BUILD_AGENT_PROFILE_ID,
        name=BUILTIN_AGENT_PROFILE_NAMES[BUILD_AGENT_PROFILE_ID],
        description="Setzt Aufgaben um und führt notwendige Änderungen selbstständig aus.",
        instructions=(
            "Implement the user's request. Inspect the relevant context, make the smallest "
            "correct changes, and verify the result."
        ),
    )


def default_agent_profiles() -> list[AgentProfile]:
    return [default_agent_profile(), plan_agent_profile(), build_agent_profile()]


def cap_agent_profile_policy(
    settings: ToolSettingsBase,
    capability: str,
    baseline: ToolExecutionPolicy,
    *,
    integration_id: UUID | None = None,
    resource_key: str | None = None,
    include_scoped: bool = False,
) -> ToolExecutionPolicy:
    permissions = settings._agent_permissions
    if settings._agent_profile_unavailable:
        return ToolExecutionPolicy.DISABLED
    if permissions is None:
        return baseline
    profile_policy = permissions.policies.get(capability)
    if integration_id is not None:
        profile_policy = permissions.integration_policies.get(
            str(integration_id), {}
        ).get(capability, profile_policy)
        if resource_key is not None:
            profile_policy = (
                permissions.resource_policies.get(str(integration_id), {})
                .get(resource_key, {})
                .get(capability, profile_policy)
            )
    elif include_scoped and profile_policy in {None, ToolExecutionPolicy.DISABLED}:
        scoped_policies = (
            policies.get(capability)
            for policies in permissions.integration_policies.values()
        )
        resource_policies = (
            policies.get(capability)
            for resources in permissions.resource_policies.values()
            for policies in resources.values()
        )
        profile_policy = next(
            (
                policy
                for policy in (*scoped_policies, *resource_policies)
                if policy is not None and policy != ToolExecutionPolicy.DISABLED
            ),
            profile_policy,
        )
    if profile_policy is None:
        return baseline
    return profile_policy


class IntegrationAdapterAvailability(StrEnum):
    FULL = "full"
    INSTANCE_ONLY = "instance_only"
    DISABLED = "disabled"


class ModelOrigin(StrEnum):
    MANUAL = "manual"
    DISCOVERED = "discovered"


class ModelAvailability(StrEnum):
    AVAILABLE = "available"
    MISSING = "missing"


class ModelCapability(StrEnum):
    CHAT = "chat"
    IMAGE_GENERATION = "image_generation"
    IMAGE_EDITING = "image_editing"
    SPEECH_GENERATION = "speech_generation"
    AUDIO_GENERATION = "audio_generation"
    AUDIO_TRANSCRIPTION = "audio_transcription"


class ModelInputModality(StrEnum):
    TEXT = "text"
    IMAGE = "image"
    AUDIO = "audio"
    VIDEO = "video"


class ReasoningEffort(StrEnum):
    NONE = "none"
    MINIMAL = "minimal"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    XHIGH = "xhigh"
    MAX = "max"
    ULTRA = "ultra"


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
    owner_id: UUID | None = Field(default=None, exclude=True)
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
    config: dict[str, Any] = Field(default_factory=dict)
    context_window_tokens: int = Field(
        default=128_000,
        ge=1,
        le=10_000_000,
        validation_alias=AliasChoices("context_window_tokens", "contextWindowTokens"),
    )
    discovered_context_window_tokens: int | None = Field(
        default=None,
        ge=2,
        le=10_000_000,
        validation_alias=AliasChoices(
            "discovered_context_window_tokens", "discoveredContextWindowTokens"
        ),
    )
    use_discovered_context_window: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "use_discovered_context_window", "useDiscoveredContextWindow"
        ),
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
    input_modalities: set[ModelInputModality] = Field(
        default_factory=lambda: {ModelInputModality.TEXT},
        validation_alias=AliasChoices("input_modalities", "inputModalities"),
    )
    reasoning_summary: bool = Field(
        default=False,
        validation_alias=AliasChoices("reasoning_summary", "reasoningSummary"),
    )
    reasoning_effort: ReasoningEffort | None = Field(
        default=None,
        validation_alias=AliasChoices("reasoning_effort", "reasoningEffort"),
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
        if "input_modalities" not in values and "inputModalities" not in values:
            supports_image = values.get(
                "supports_image_input", values.get("supportsImageInput", False)
            )
            values["input_modalities"] = (
                [ModelInputModality.TEXT, ModelInputModality.IMAGE]
                if supports_image is True
                else [ModelInputModality.TEXT]
            )
        values.pop("supports_image_input", None)
        values.pop("supportsImageInput", None)
        context_window = values.get("context_window_tokens", values.get("contextWindowTokens"))
        has_output_limit = "max_output_tokens" in values or "maxOutputTokens" in values
        if context_window is not None and not has_output_limit:
            if isinstance(context_window, int) and not isinstance(context_window, bool) and context_window > 1:
                values["max_output_tokens"] = min(16_384, max(1, context_window // 4))
        return values

    @model_validator(mode="after")
    def validate_runtime_limits(self) -> Self:
        if (
            self.use_discovered_context_window
            and self.discovered_context_window_tokens is not None
        ):
            self.context_window_tokens = self.discovered_context_window_tokens
            self.max_output_tokens = min(
                self.max_output_tokens,
                max(1, self.context_window_tokens // 4),
                self.context_window_tokens - 1,
            )
        if self.max_output_tokens >= self.context_window_tokens:
            raise ValueError("max_output_tokens must be less than context_window_tokens")
        if not self.supports_tools:
            self.supports_parallel_tool_calls = False
        return self

    @computed_field(return_type=bool)
    @property
    def supports_image_input(self) -> bool:
        return ModelInputModality.IMAGE in self.input_modalities

    @property
    def context_window_source(self) -> str:
        if self.use_discovered_context_window:
            return (
                "provider"
                if self.discovered_context_window_tokens is not None
                else "fallback"
            )
        return "override"


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
    execution_agent_id: UUID | None = Field(
        default=None,
        validation_alias=AliasChoices("execution_agent_id", "executionAgentId"),
    )
    credential_id: UUID | None = None
    owner_id: UUID | None = Field(default=None, exclude=True)
    config: dict[str, Any] = Field(default_factory=dict)
    _execution_operations: frozenset[str] | None = PrivateAttr(default=None)


class WebFetchSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_agent: str = Field(
        default=DEFAULT_WEBFETCH_USER_AGENT,
        min_length=1,
        max_length=512,
        validation_alias=AliasChoices("user_agent", "userAgent"),
    )
    max_response_mib: int = Field(
        default=64,
        ge=1,
        le=256,
        validation_alias=AliasChoices("max_response_mib", "maxResponseMiB"),
    )
    public_addresses_only: bool = Field(
        default=True,
        validation_alias=AliasChoices(
            "public_addresses_only", "publicAddressesOnly"
        ),
    )

    @field_validator("user_agent", mode="before")
    @classmethod
    def validate_user_agent(cls, value: Any) -> Any:
        if not isinstance(value, str):
            return value
        if any(not 32 <= ord(character) <= 126 for character in value):
            raise ValueError("user_agent must contain visible ASCII characters only")
        return value.strip()

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
    policy: ToolExecutionPolicy = ToolExecutionPolicy.ASK
    timeout_seconds: int = Field(
        default=300,
        ge=10,
        le=3_600,
        validation_alias=AliasChoices("timeout_seconds", "timeoutSeconds"),
    )


class PolicyToolSettings(BaseModel):
    policy: ToolExecutionPolicy = ToolExecutionPolicy.ASK


class ShellPrefixSettings(BaseModel):
    read: list[list[str]] = Field(default_factory=list, max_length=100)
    write: list[list[str]] = Field(default_factory=list, max_length=100)

    @field_validator("read", "write")
    @classmethod
    def validate_prefixes(cls, value: list[list[str]]) -> list[list[str]]:
        normalized: list[list[str]] = []
        for prefix in value:
            if not 1 <= len(prefix) <= 8 or any(
                not token
                or len(token) > 256
                or any(
                    not 33 <= ord(character) <= 126
                    for character in token
                )
                for token in prefix
            ):
                raise ValueError("invalid shell prefix")
            if prefix not in normalized:
                normalized.append(prefix)
        return normalized


class DefaultCalendarSettings(BaseModel):
    integration_id: UUID = Field(
        validation_alias=AliasChoices("integration_id", "integrationId")
    )
    calendar_key: str = Field(
        pattern=r"^calendar:[a-f0-9]{32}$",
        validation_alias=AliasChoices("calendar_key", "calendarKey"),
    )


class DefaultTaskListSettings(BaseModel):
    integration_id: UUID = Field(
        validation_alias=AliasChoices("integration_id", "integrationId")
    )
    task_list_key: str = Field(
        pattern=r"^tasklist:[a-f0-9]{32}$",
        validation_alias=AliasChoices("task_list_key", "taskListKey"),
    )


class ToolSettingsBase(SettingsDocument):
    _agent_permissions: AgentProfilePermissions | None = PrivateAttr(default=None)
    _agent_profile_unavailable: bool = PrivateAttr(default=False)
    _trust_level: TrustLevel = PrivateAttr(default=TrustLevel.PRIVATE)

    private_permissions: AgentProfilePermissions = Field(
        default_factory=AgentProfilePermissions,
        validation_alias=AliasChoices("private_permissions", "privatePermissions"),
    )
    public_permissions: AgentProfilePermissions = Field(
        default_factory=AgentProfilePermissions,
        validation_alias=AliasChoices("public_permissions", "publicPermissions"),
    )
    tool_availability: dict[str, bool] = Field(
        default_factory=dict,
        validation_alias=AliasChoices("tool_availability", "toolAvailability"),
    )
    resource_labels: dict[str, dict[str, str]] = Field(
        default_factory=dict,
        validation_alias=AliasChoices("resource_labels", "resourceLabels"),
    )
    shell_prefixes: ShellPrefixSettings = Field(
        default_factory=ShellPrefixSettings,
        validation_alias=AliasChoices("shell_prefixes", "shellPrefixes"),
    )
    default_calendar: DefaultCalendarSettings | None = Field(
        default=None,
        validation_alias=AliasChoices("default_calendar", "defaultCalendar"),
    )
    default_task_list: DefaultTaskListSettings | None = Field(
        default=None,
        validation_alias=AliasChoices("default_task_list", "defaultTaskList"),
    )
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
    edit_image: MediaToolSettings = Field(
        default_factory=lambda: MediaToolSettings(policy=ToolExecutionPolicy.ASK),
        validation_alias=AliasChoices("edit_image", "editImage"),
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
    analyze_video: MediaToolSettings = Field(
        default_factory=lambda: MediaToolSettings(
            policy=ToolExecutionPolicy.ASK, timeout_seconds=1_800
        ),
        validation_alias=AliasChoices("analyze_video", "analyzeVideo"),
    )
    update_agent_identity: PolicyToolSettings = Field(
        default_factory=PolicyToolSettings,
        validation_alias=AliasChoices("update_agent_identity", "updateAgentIdentity"),
    )

    @property
    def permissions(self) -> AgentProfilePermissions:
        return (
            self.private_permissions
            if self._trust_level == TrustLevel.PRIVATE
            else self.public_permissions
        )

    @property
    def trust_level(self) -> TrustLevel:
        return self._trust_level

    def select_trust(self, trust_level: TrustLevel) -> ToolSettingsBase:
        self._trust_level = trust_level
        return self

class ToolSettings(ToolSettingsBase):
    settings_key = "tools"

    personal_settings_enabled: bool = True


class UserToolSettings(ToolSettingsBase):
    settings_key = "tools"
    settings_scope = SettingsScope.USER

    # Existing documents are personal Tool snapshots; Integration-only documents opt out.
    personal_tool_defaults_enabled: bool = True


class AgentProfilesSettings(SettingsDocument):
    settings_key = "agent-profiles"

    personal_agents_enabled: bool = True
    profiles: list[AgentProfile] = Field(
        default_factory=default_agent_profiles, max_length=102
    )


class UserAgentProfilesSettings(SettingsDocument):
    settings_key = "agent-profiles"
    settings_scope = SettingsScope.USER

    profiles: list[AgentProfile] = Field(default_factory=list, max_length=20)


class GeneralSettings(SettingsDocument):
    settings_key = "general"

    default_locale: str = "de-DE"
    default_timezone: str = "Europe/Berlin"


class ChannelSettings(SettingsDocument):
    settings_key = "channels"

    allowed_providers: set[str] = Field(default_factory=lambda: {"telegram"})


class InstanceLimits(SettingsDocument):
    settings_key = "instance-limits"
    model_config = ConfigDict(
        alias_generator=_camel_case,
        populate_by_name=True,
        extra="forbid",
    )

    max_upload_mib: int = Field(default=64, ge=1, le=256)
    max_attachments_per_message: int = Field(default=8, ge=1, le=16)
    max_message_upload_mib: int = Field(default=512, ge=1, le=512)
    session_quota_mib: int = Field(default=500, ge=1, le=102_400)
    max_image_preview_mib: int = Field(default=5, ge=1, le=64)
    max_editable_text_mib: int = Field(default=10, ge=1, le=16)
    max_audio_duration_seconds: int = Field(default=3_600, ge=1, le=3_600)
    max_video_duration_seconds: int = Field(default=900, ge=1, le=900)
    max_extracted_characters: int = Field(default=5_000_000, ge=1_000, le=10_000_000)
    max_pdf_pages: int = Field(default=1_000, ge=1, le=5_000)
    max_workbook_sheets: int = Field(default=100, ge=1, le=500)
    max_workbook_cells: int = Field(default=1_000_000, ge=1_000, le=5_000_000)
    max_presentation_slides: int = Field(default=500, ge=1, le=2_000)
    max_archive_entries: int = Field(default=5_000, ge=1, le=20_000)
    max_archive_uncompressed_mib: int = Field(default=256, ge=1, le=1_024)
    max_archive_entry_mib: int = Field(default=64, ge=1, le=256)
    max_archive_compression_ratio: int = Field(default=200, ge=1, le=500)

    @model_validator(mode="after")
    def validate_related_limits(self) -> Self:
        if self.max_upload_mib > self.max_message_upload_mib:
            raise ValueError("max_upload_mib must not exceed max_message_upload_mib")
        if self.max_upload_mib > self.session_quota_mib:
            raise ValueError("max_upload_mib must not exceed session_quota_mib")
        if self.max_archive_entry_mib > self.max_archive_uncompressed_mib:
            raise ValueError(
                "max_archive_entry_mib must not exceed max_archive_uncompressed_mib"
            )
        return self

    @staticmethod
    def _mib(value: int) -> int:
        return value * 1024 * 1024

    @property
    def max_upload_bytes(self) -> int:
        return self._mib(self.max_upload_mib)

    @property
    def max_message_upload_bytes(self) -> int:
        return self._mib(self.max_message_upload_mib)

    @property
    def session_quota_bytes(self) -> int:
        return self._mib(self.session_quota_mib)

    @property
    def max_image_preview_bytes(self) -> int:
        return self._mib(self.max_image_preview_mib)

    @property
    def max_editable_text_bytes(self) -> int:
        return self._mib(self.max_editable_text_mib)

    @property
    def max_archive_uncompressed_bytes(self) -> int:
        return self._mib(self.max_archive_uncompressed_mib)

    @property
    def max_archive_entry_bytes(self) -> int:
        return self._mib(self.max_archive_entry_mib)


class AiSettingsBase(SettingsDocument):
    providers: list[LlmProviderConfig] = Field(default_factory=list)
    models: list[ModelConfig] = Field(default_factory=list)
    default_model_id: UUID | None = None
    compaction_model_id: UUID | None = None
    title_generation_model_id: UUID | None = None
    title_vision_fallback_enabled: bool = True
    skill_creation_model_id: UUID | None = None
    dreaming_model_id: UUID | None = None
    vision_model_id: UUID | None = None
    video_model_id: UUID | None = None
    image_generation_model_id: UUID | None = None
    speech_generation_model_id: UUID | None = None
    audio_generation_model_id: UUID | None = None
    audio_transcription_model_id: UUID | None = None


class AiSettings(AiSettingsBase):
    settings_key = "ai"

    personal_providers_enabled: bool = True


class UserAiSettings(AiSettingsBase):
    settings_key = "ai"
    settings_scope = SettingsScope.USER


class IntegrationSettingsBase(SettingsDocument):
    instances: list[IntegrationInstance] = Field(default_factory=list)

    @model_validator(mode="after")
    def unique_agent_integration_kinds(self) -> Self:
        keys = [
            (instance.execution_agent_id, instance.adapter_type)
            for instance in self.instances
            if instance.execution_agent_id is not None
        ]
        if len(keys) != len(set(keys)):
            raise ValueError("only one integration instance per kind and MatoLoop Agent is allowed")
        return self


class IntegrationSettings(IntegrationSettingsBase):
    settings_key = "integrations"

    adapter_availability: dict[str, IntegrationAdapterAvailability] = Field(
        default_factory=dict
    )

    @model_validator(mode="before")
    @classmethod
    def normalize_legacy_availability(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        data = dict(value)
        availability = dict(data.get("adapter_availability", {}))
        backend_adapters = {"searxng", "caldav", "bookstack", "mealie", "imap_smtp"}
        agent_adapters = {"git", "openscad"}
        legacy_adapters = backend_adapters | agent_adapters
        if (
            "personal_integrations_enabled" in data
            or "allowed_personal_adapter_types" in data
        ):
            enabled = data.pop("personal_integrations_enabled", True)
            allowed = set(
                data.pop("allowed_personal_adapter_types", legacy_adapters)
            )
            for adapter_type in legacy_adapters:
                if enabled and adapter_type in allowed:
                    continue
                availability.setdefault(
                    adapter_type,
                    IntegrationAdapterAvailability.DISABLED
                    if adapter_type in agent_adapters
                    else IntegrationAdapterAvailability.INSTANCE_ONLY,
                )
        for adapter_type in data.pop("disabled_integration_adapter_types", set()):
            availability[adapter_type] = IntegrationAdapterAvailability.DISABLED
        data["adapter_availability"] = availability
        return data

    @model_validator(mode="after")
    def remove_default_availability(self) -> Self:
        self.adapter_availability = {
            adapter_type: availability
            for adapter_type, availability in self.adapter_availability.items()
            if availability != IntegrationAdapterAvailability.FULL
        }
        return self

    def availability_for(self, adapter_type: str) -> IntegrationAdapterAvailability:
        return self.adapter_availability.get(
            adapter_type, IntegrationAdapterAvailability.FULL
        )


class UserIntegrationSettings(IntegrationSettingsBase):
    settings_key = "integrations"
    settings_scope = SettingsScope.USER


class AgentRuntimeSettings(SettingsDocument):
    settings_key = "agent-runtime"

    max_steps: int = Field(default=20, ge=1, le=100)
    max_model_requests: int = Field(default=30, ge=1, le=200)
    max_tool_calls: int = Field(default=50, ge=1, le=100)
    max_research_tool_calls: int = Field(default=8, ge=1, le=100)
    request_timeout_seconds: int = Field(default=300, ge=10, le=3600)
    compaction_trigger_ratio: float = Field(default=0.8, ge=0.5, le=0.95)
    compaction_target_ratio: float = Field(default=0.6, ge=0.1, le=0.9)
    safety_reserve_tokens: int = Field(default=4_096, ge=0, le=131_072)
    protected_recent_turns: int = Field(default=2, ge=1, le=100)
    max_compaction_requests: int = Field(default=3, ge=1, le=20)
    historical_tool_output_tokens: int = Field(default=2_000, ge=0, le=100_000)

    @model_validator(mode="after")
    def validate_compaction_ratios(self) -> Self:
        if self.compaction_target_ratio >= self.compaction_trigger_ratio:
            raise ValueError("compaction_target_ratio must be less than compaction_trigger_ratio")
        return self


class MemorySettings(SettingsDocument):
    settings_key = "memory"
    settings_scope = SettingsScope.USER

    enabled: bool = True
    automatic_dreaming_enabled: bool = False
    dreaming_instructions: str = Field(default="", max_length=8_000)
    dreaming_interval_minutes: int = Field(default=60, ge=1, le=10_080)
    dreaming_idle_minutes: int = Field(default=15, ge=1, le=10_080)
    dreaming_max_batch_messages: int = Field(default=50, ge=2, le=500)
    dreaming_max_candidates: int = Field(default=10, ge=1, le=100)
    dreaming_update_policy: ToolExecutionPolicy = ToolExecutionPolicy.AUTO
    dreaming_remove_policy: ToolExecutionPolicy = ToolExecutionPolicy.ASK
    automatic_memory_snippet_limit: int = Field(default=2, ge=0, le=5)
    automatic_session_snippet_limit: int = Field(default=3, ge=0, le=5)
    recent_session_snippet_limit: int = Field(default=2, ge=0, le=5)
    automatic_context_max_tokens: int = Field(default=1_024, ge=128, le=2_048)


class UserProfileFact(BaseModel):
    model_config = ConfigDict(
        alias_generator=_camel_case,
        populate_by_name=True,
        extra="forbid",
    )

    both: str | None = Field(default=None, min_length=1, max_length=20_000)
    private_only: str | None = Field(default=None, min_length=1, max_length=20_000)
    public_only: str | None = Field(default=None, min_length=1, max_length=20_000)

    @field_validator("both", "private_only", "public_only", mode="before")
    @classmethod
    def normalize_content(cls, value: Any) -> Any:
        return value.strip() if isinstance(value, str) else value


class UserProfileSettings(SettingsDocument):
    settings_key = "user-profile"
    settings_scope = SettingsScope.USER
    model_config = ConfigDict(
        alias_generator=_camel_case,
        populate_by_name=True,
        extra="forbid",
    )

    full_name: UserProfileFact = Field(default_factory=UserProfileFact)
    preferred_name: UserProfileFact = Field(default_factory=UserProfileFact)
    form_of_address: UserProfileFact = Field(default_factory=UserProfileFact)
    pronouns: UserProfileFact = Field(default_factory=UserProfileFact)
    primary_language: UserProfileFact = Field(default_factory=UserProfileFact)
    primary_residence: UserProfileFact = Field(default_factory=UserProfileFact)


class AgentIdentitySettings(SettingsDocument):
    settings_key = "agent-identity"

    display_name: str = Field(default="MatoLoop", min_length=1, max_length=80)
    personal_display_names_enabled: bool = True
    personal_personas_enabled: bool = True
    default_persona_id: UUID = DEFAULT_PERSONA_ID
    personas: list[PersonaProfile] = Field(
        default_factory=lambda: [default_persona_profile()], max_length=101
    )

    @field_validator("display_name")
    @classmethod
    def validate_display_name(cls, value: str) -> str:
        return normalize_agent_display_name(value)

    @model_validator(mode="after")
    def validate_personas(self) -> Self:
        ids = [persona.id for persona in self.personas]
        names = [persona.name.casefold() for persona in self.personas]
        if len(ids) != len(set(ids)) or len(names) != len(set(names)):
            raise ValueError("persona ids and names must be unique")
        if self.default_persona_id not in ids:
            raise ValueError("default_persona_id must reference an instance persona")
        builtin = next(
            (persona for persona in self.personas if persona.id == DEFAULT_PERSONA_ID),
            None,
        )
        if builtin is None or builtin.instructions:
            raise ValueError("the built-in empty persona must remain available")
        return self


class UserAgentIdentitySettings(SettingsDocument):
    settings_key = "agent-identity"
    settings_scope = SettingsScope.USER

    display_name: str | None = Field(default=None, min_length=1, max_length=80)
    default_persona_id: UUID | None = None
    personas: list[PersonaProfile] = Field(default_factory=list, max_length=21)

    @field_validator("display_name")
    @classmethod
    def validate_display_name(cls, value: str | None) -> str | None:
        return normalize_agent_display_name(value) if value is not None else None

    @model_validator(mode="after")
    def validate_personas(self) -> Self:
        ids = [persona.id for persona in self.personas]
        names = [persona.name.casefold() for persona in self.personas]
        if len(ids) != len(set(ids)) or len(names) != len(set(names)):
            raise ValueError("persona ids and names must be unique")
        return self


class EffectiveAgentIdentity(BaseModel):
    display_name: str
    persona: PersonaProfile
    personal_display_name_enabled: bool = True
    persona_editable: bool = False


class SkillDefinition(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    name: str = Field(min_length=1, max_length=64)
    description: str = Field(min_length=1, max_length=240)
    instructions: str = Field(min_length=1, max_length=8_000)

    @field_validator("name", mode="before")
    @classmethod
    def validate_name(cls, value: Any) -> Any:
        return normalize_skill_name(value) if isinstance(value, str) else value

    @field_validator("description", "instructions", mode="before")
    @classmethod
    def validate_text(cls, value: Any, info: Any) -> Any:
        return normalize_skill_text(value, info.field_name) if isinstance(value, str) else value


class SkillsSettings(SettingsDocument):
    settings_key = "skills"
    settings_scope = SettingsScope.USER

    skills: list[SkillDefinition] = Field(default_factory=list, max_length=20)


class UserPreferences(SettingsDocument):
    settings_key = "user-preferences"
    settings_scope = SettingsScope.USER

    dark_mode: bool = False
    session_drawer_open: bool = True
    default_execution_agent_id: UUID | None = None
