from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Annotated, Any, Literal, Self
from uuid import UUID

from fastapi import APIRouter, Depends, status
from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    ValidationError,
    model_validator,
)
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.api.errors import ApiError
from mato_loop_backend.db.session import get_db_session
from mato_loop_backend.error_contract import describe_error
from mato_loop_backend.models import ChatSession, Credential, UserAccount
from mato_loop_backend.providers.base import ProviderError
from mato_loop_backend.providers.codex_oauth import (
    CodexOAuthError,
    exchange_code,
    oauth_manager,
    poll_device_code,
)
from mato_loop_backend.providers.media import media_model_available
from mato_loop_backend.providers.service import ProviderService
from mato_loop_backend.security import (
    delete_credential,
    load_credential,
    require_user,
    scoped_credentials,
    store_credential,
)
from mato_loop_backend.settings import (
    AiSettings,
    ModelWebFetchPolicy,
    SettingsStore,
    ToolSettings,
    UserAiSettings,
    effective_model_pseudonymization,
    effective_model_trust,
    resolve_web_fetch_policy,
)
from mato_loop_backend.settings.effective import load_effective_ai, load_effective_tools
from mato_loop_backend.settings.models import (
    AiSettingsBase,
    LlmProviderConfig,
    MemoryPolicy,
    ModelAvailability,
    ModelCapability,
    ModelConfig,
    ModelInputModality,
    ModelOrigin,
    TrustLevel,
)

router = APIRouter(prefix="/providers", dependencies=[Depends(require_user)])
logger = logging.getLogger(__name__)
SettingsApiScope = Literal["instance", "personal"]
AiSettingsDocument = AiSettings | UserAiSettings


async def _load_scoped_ai(
    db: AsyncSession,
    scope: SettingsApiScope,
    user: UserAccount | None,
    *,
    for_update: bool = False,
    mutation: bool = False,
) -> tuple[AiSettingsDocument, UUID | None]:
    if scope == "instance":
        if user is not None and not getattr(user, "is_admin", False):
            raise ApiError(403, "auth.adminRequired")
        return await SettingsStore.load(db, AiSettings, for_update=for_update), None
    if user is None:
        raise ApiError(401, "auth.notAuthenticated")
    instance = await SettingsStore.load(db, AiSettings)
    if not instance.personal_providers_enabled:
        raise ApiError(409, "provider.personalDisabled")
    settings = await SettingsStore.load(
        db, UserAiSettings, user_id=user.id, for_update=for_update
    )
    for provider in settings.providers:
        provider.owner_id = user.id
    return settings, user.id


async def _save_scoped_ai(
    db: AsyncSession, settings: AiSettingsDocument, owner_id: UUID | None
) -> None:
    await SettingsStore.save(db, settings, user_id=owner_id)


def _to_camel(value: str) -> str:
    first, *rest = value.split("_")
    return first + "".join(part.capitalize() for part in rest)


def _discovered_context_window(metadata: dict[str, Any]) -> int | None:
    value = metadata.get("max_context_window", metadata.get("max_model_len"))
    if isinstance(value, int) and not isinstance(value, bool) and value > 1:
        return value
    return None


def _apply_discovered_context_window(model: ModelConfig, value: int | None) -> None:
    model.discovered_context_window_tokens = value
    if not model.use_discovered_context_window or value is None:
        return
    model.context_window_tokens = value
    model.max_output_tokens = min(
        model.max_output_tokens,
        max(1, value // 4),
        value - 1,
    )


class ApiRequest(BaseModel):
    model_config = ConfigDict(
        alias_generator=_to_camel,
        populate_by_name=True,
        extra="forbid",
    )


class ModelRuntimeRequest(ApiRequest):
    capabilities: set[ModelCapability] = Field(
        default_factory=lambda: {ModelCapability.CHAT}
    )
    context_window_tokens: int = Field(default=128_000, ge=1, le=10_000_000)
    use_discovered_context_window: bool = False
    max_output_tokens: int = Field(default=16_384, ge=1, le=1_000_000)
    characters_per_token: float = Field(default=4.0, gt=0, le=100)
    supports_tools: bool = True
    supports_parallel_tool_calls: bool = True
    supports_streaming: bool = True
    input_modalities: set[ModelInputModality] = Field(
        default_factory=lambda: {ModelInputModality.TEXT}
    )
    supports_image_input: bool = False
    webfetch_policy: ModelWebFetchPolicy = ModelWebFetchPolicy.INHERIT
    reasoning_summary: bool = False
    trust_level: TrustLevel | None = None
    pseudonymization_enabled: bool | None = None
    memory_policy: MemoryPolicy = MemoryPolicy.INHERIT

    @model_validator(mode="before")
    @classmethod
    def accept_legacy_image_capability(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        values = dict(value)
        has_legacy_image = (
            "supports_image_input" in values or "supportsImageInput" in values
        )
        if (
            "input_modalities" not in values
            and "inputModalities" not in values
            and has_legacy_image
        ):
            supports_image = values.get(
                "supports_image_input", values.get("supportsImageInput", False)
            )
            values["input_modalities"] = (
                [ModelInputModality.TEXT, ModelInputModality.IMAGE]
                if supports_image is True
                else [ModelInputModality.TEXT]
            )
        return values

    @model_validator(mode="after")
    def validate_limits(self) -> Self:
        if self.max_output_tokens >= self.context_window_tokens:
            raise ValueError("max_output_tokens must be less than context_window_tokens")
        if not self.supports_tools:
            self.supports_parallel_tool_calls = False
        self.supports_image_input = (
            ModelInputModality.IMAGE in self.input_modalities
        )
        return self


class OpenAIProviderRequest(ApiRequest):
    name: str = Field(min_length=1, max_length=160)
    base_url: HttpUrl
    api_type: Literal["chat_completions", "responses"] = "chat_completions"
    api_key: str | None = None
    remove_api_key: bool = False
    trust_level: TrustLevel = TrustLevel.PUBLIC
    pseudonymization_enabled: bool = True
    memory_policy: MemoryPolicy = MemoryPolicy.INHERIT

    @model_validator(mode="after")
    def validate_base_url(self) -> Self:
        if self.base_url.username or self.base_url.password:
            raise ValueError("base_url must not contain credentials")
        if self.base_url.query or self.base_url.fragment:
            raise ValueError("base_url must not contain query or fragment")
        if self.remove_api_key and self.api_key and self.api_key.strip():
            raise ValueError("api_key and remove_api_key are mutually exclusive")
        return self


class LemonadeProviderRequest(ApiRequest):
    name: str = Field(min_length=1, max_length=160)
    base_url: HttpUrl
    api_key: str | None = None
    remove_api_key: bool = False
    trust_level: TrustLevel = TrustLevel.PRIVATE
    pseudonymization_enabled: bool = False
    memory_policy: MemoryPolicy = MemoryPolicy.INHERIT

    @model_validator(mode="after")
    def validate_base_url(self) -> Self:
        if self.base_url.username or self.base_url.password:
            raise ValueError("base_url must not contain credentials")
        if self.base_url.query or self.base_url.fragment:
            raise ValueError("base_url must not contain query or fragment")
        if self.remove_api_key and self.api_key and self.api_key.strip():
            raise ValueError("api_key and remove_api_key are mutually exclusive")
        return self


class ModelRequest(ModelRuntimeRequest):
    model_id: str = Field(min_length=1, max_length=300)
    name: str | None = Field(default=None, max_length=300)
    enabled: bool | None = None


class EnabledRequest(ApiRequest):
    enabled: bool


class MemoryPolicyRequest(ApiRequest):
    memory_policy: MemoryPolicy


class ProviderPolicyRequest(ApiRequest):
    trust_level: TrustLevel
    pseudonymization_enabled: bool
    memory_policy: MemoryPolicy


class DefaultModelRequest(ApiRequest):
    model_id: UUID | None = Field(
        validation_alias=AliasChoices("modelId", "defaultModelId", "model_id")
    )

    @model_validator(mode="before")
    @classmethod
    def accept_scalar(cls, value: Any) -> Any:
        if value is None or isinstance(value, (str, UUID)):
            return {"modelId": value}
        return value


class DefaultModelsRequest(ApiRequest):
    default_model_id: UUID | None
    compaction_model_id: UUID | None
    title_generation_model_id: UUID | None
    skill_creation_model_id: UUID | None = None
    dreaming_model_id: UUID | None = None
    vision_model_id: UUID | None = None
    video_model_id: UUID | None = None
    image_generation_model_id: UUID | None = None
    speech_generation_model_id: UUID | None = None
    audio_generation_model_id: UUID | None = None
    audio_transcription_model_id: UUID | None = None


def _provider(settings: AiSettingsBase, provider_id: UUID) -> LlmProviderConfig:
    provider = next((item for item in settings.providers if item.id == provider_id), None)
    if provider is None:
        raise ApiError(404, "provider.notFound")
    return provider


def _model(settings: AiSettingsBase, model_id: UUID) -> ModelConfig:
    model = next((item for item in settings.models if item.id == model_id), None)
    if model is None:
        raise ApiError(404, "provider.modelMissing")
    return model


def _ensure_unique_model_id(
    settings: AiSettingsBase,
    *,
    provider_id: UUID,
    model_id: str,
    exclude_id: UUID | None = None,
) -> None:
    if any(
        item.provider_id == provider_id
        and item.model_id == model_id
        and item.id != exclude_id
        for item in settings.models
    ):
        raise ApiError(409, "provider.modelIdConflict", field="modelId")


def _available_model(
    settings: AiSettingsBase,
    model_id: UUID | None,
    capability: ModelCapability | None = None,
) -> ModelConfig | None:
    model = next((item for item in settings.models if item.id == model_id), None)
    provider = (
        next((item for item in settings.providers if item.id == model.provider_id), None)
        if model
        else None
    )
    if (
        model is None
        or provider is None
        or not model.enabled
        or not provider.enabled
        or (provider.adapter_type == "openai-codex" and provider.credential_id is None)
        or model.availability == ModelAvailability.MISSING
        or (capability is not None and capability not in model.capabilities)
    ):
        return None
    return model


def _video_analysis_model_available(settings: AiSettingsBase, model_id: UUID) -> bool:
    model = _available_model(settings, model_id, ModelCapability.CHAT)
    if model is None:
        return False
    if model.supports_image_input:
        return True
    provider = _provider(settings, model.provider_id)
    return (
        ModelInputModality.VIDEO in model.input_modalities
        and provider.adapter_type == "openai-compatible"
        and provider.config.get("api_type", "chat_completions") == "chat_completions"
    )


def _clear_invalid_defaults(settings: AiSettingsBase) -> None:
    fields = {
        "default_model_id": ModelCapability.CHAT,
        "compaction_model_id": ModelCapability.CHAT,
        "title_generation_model_id": ModelCapability.CHAT,
        "skill_creation_model_id": ModelCapability.CHAT,
        "dreaming_model_id": ModelCapability.CHAT,
        "vision_model_id": ModelCapability.CHAT,
        "video_model_id": ModelCapability.CHAT,
        "image_generation_model_id": ModelCapability.IMAGE_GENERATION,
        "speech_generation_model_id": ModelCapability.SPEECH_GENERATION,
        "audio_generation_model_id": ModelCapability.AUDIO_GENERATION,
        "audio_transcription_model_id": ModelCapability.AUDIO_TRANSCRIPTION,
    }
    for field, capability in fields.items():
        model_id = getattr(settings, field)
        if (
            isinstance(settings, UserAiSettings)
            and model_id is not None
            and not any(model.id == model_id for model in settings.models)
        ):
            continue
        media_capability = capability != ModelCapability.CHAT
        if model_id is not None and (
            _available_model(settings, model_id, capability) is None
            or (
                field in {"default_model_id", "skill_creation_model_id"}
                and not _model(settings, model_id).supports_tools
            )
            or (field == "vision_model_id" and not _model(settings, model_id).supports_image_input)
            or (
                field == "video_model_id"
                and not _video_analysis_model_available(settings, model_id)
            )
            or (
                media_capability
                and not media_model_available(settings, capability, model_id)
            )
        ):
            setattr(settings, field, None)


def _clear_removed_defaults(settings: AiSettingsBase, model_ids: set[UUID]) -> None:
    for field in (
        "default_model_id",
        "compaction_model_id",
        "title_generation_model_id",
        "skill_creation_model_id",
        "dreaming_model_id",
        "vision_model_id",
        "video_model_id",
        "image_generation_model_id",
        "speech_generation_model_id",
        "audio_generation_model_id",
        "audio_transcription_model_id",
    ):
        if getattr(settings, field) in model_ids:
            setattr(settings, field, None)


def _provider_config_payload(config: dict[str, Any]) -> dict[str, Any]:
    payload = {key: value for key, value in config.items() if key not in {"base_url", "api_type"}}
    if "base_url" in config:
        payload["baseUrl"] = config["base_url"]
    if "api_type" in config:
        payload["apiType"] = config["api_type"]
    return payload


async def _provider_sync_fingerprint(
    db: AsyncSession, provider: LlmProviderConfig
) -> tuple[str, UUID | None, dict[str, Any], bytes | str | None]:
    credential = (
        await load_credential(
            db,
            provider.credential_id,
            owner_id=provider.owner_id,
            populate_existing=True,
        )
        if provider.credential_id
        else None
    )
    credential_fingerprint = credential.encrypted_payload if credential else None
    if credential and provider.adapter_type == "openai-codex":
        credential_fingerprint = str(credential.metadata_json.get("accountId") or "")
    return (
        provider.adapter_type,
        provider.credential_id,
        dict(provider.config),
        credential_fingerprint,
    )


def _settings_payload(
    settings: AiSettingsBase,
    tool_settings: ToolSettings | None = None,
    *,
    include_configuration: bool = True,
) -> dict[str, Any]:
    tool_settings = tool_settings or ToolSettings()
    providers_by_id = {provider.id: provider for provider in settings.providers}
    return {
        "enabled": settings.enabled,
        **(
            {"personalProvidersEnabled": settings.personal_providers_enabled}
            if isinstance(settings, AiSettings)
            else {}
        ),
        "defaultModelId": str(settings.default_model_id) if settings.default_model_id else None,
        "compactionModelId": (
            str(settings.compaction_model_id) if settings.compaction_model_id else None
        ),
        "titleGenerationModelId": (
            str(settings.title_generation_model_id)
            if settings.title_generation_model_id
            else None
        ),
        "skillCreationModelId": (
            str(settings.skill_creation_model_id)
            if settings.skill_creation_model_id
            else None
        ),
        "dreamingModelId": (
            str(settings.dreaming_model_id) if settings.dreaming_model_id else None
        ),
        "visionModelId": (
            str(settings.vision_model_id) if settings.vision_model_id else None
        ),
        "videoModelId": (
            str(settings.video_model_id) if settings.video_model_id else None
        ),
        "imageGenerationModelId": (
            str(settings.image_generation_model_id)
            if settings.image_generation_model_id
            else None
        ),
        "speechGenerationModelId": (
            str(settings.speech_generation_model_id)
            if settings.speech_generation_model_id
            else None
        ),
        "audioGenerationModelId": (
            str(settings.audio_generation_model_id)
            if settings.audio_generation_model_id
            else None
        ),
        "audioTranscriptionModelId": (
            str(settings.audio_transcription_model_id)
            if settings.audio_transcription_model_id
            else None
        ),
        "providers": [
            {
                "id": str(provider.id),
                "name": provider.name,
                "adapterType": provider.adapter_type,
                "enabled": provider.enabled,
                "trustLevel": provider.trust_level.value,
                "pseudonymizationEnabled": provider.pseudonymization_enabled,
                "memoryPolicy": provider.memory_policy.value,
                "hasCredential": provider.credential_id is not None,
                "config": (
                    _provider_config_payload(provider.config)
                    if include_configuration
                    else {}
                ),
                "modelsLastSyncedAt": (
                    provider.models_last_synced_at.isoformat()
                    if provider.models_last_synced_at
                    else None
                ),
            }
            for provider in settings.providers
        ],
        "models": [
            {
                "id": str(model.id),
                "providerId": str(model.provider_id),
                "modelId": model.model_id,
                "name": model.name or model.model_id,
                "enabled": model.enabled,
                "origin": model.origin.value,
                "availability": model.availability.value,
                "agentAvailable": bool(
                    _available_model(settings, model.id, ModelCapability.CHAT)
                    and model.supports_tools
                ),
                "agentUnavailableReason": (
                    None
                    if _available_model(settings, model.id, ModelCapability.CHAT)
                    and model.supports_tools
                    else (
                        "session.modelToolsUnavailable"
                        if ModelCapability.CHAT in model.capabilities
                        and not model.supports_tools
                        else "session.modelUnavailable"
                    )
                ),
                "lastSeenAt": model.last_seen_at.isoformat() if model.last_seen_at else None,
                "discoveryMetadata": model.discovery_metadata,
                "capabilities": sorted(item.value for item in model.capabilities),
                "executableCapabilities": sorted(
                    capability.value
                    for capability in model.capabilities
                    if capability == ModelCapability.CHAT
                    or media_model_available(settings, capability, model.id)
                ),
                "contextWindowTokens": model.context_window_tokens,
                "discoveredContextWindowTokens": model.discovered_context_window_tokens,
                "useDiscoveredContextWindow": model.use_discovered_context_window,
                "contextWindowSource": model.context_window_source,
                "maxOutputTokens": model.max_output_tokens,
                "charactersPerToken": model.characters_per_token,
                "supportsTools": model.supports_tools,
                "supportsParallelToolCalls": model.supports_parallel_tool_calls,
                "supportsStreaming": model.supports_streaming,
                "inputModalities": sorted(item.value for item in model.input_modalities),
                "supportsImageInput": model.supports_image_input,
                "webfetchPolicy": model.webfetch_policy.value,
                "effectiveWebfetchPolicy": resolve_web_fetch_policy(
                    tool_settings, model
                ).value,
                "reasoningSummary": model.reasoning_summary,
                "trustLevel": model.trust_level.value if model.trust_level else None,
                "effectiveTrustLevel": effective_model_trust(
                    providers_by_id[model.provider_id], model
                ).value,
                "pseudonymizationEnabled": model.pseudonymization_enabled,
                "effectivePseudonymizationEnabled": effective_model_pseudonymization(
                    providers_by_id[model.provider_id], model
                ),
                "memoryPolicy": model.memory_policy.value,
            }
            for model in settings.models
        ],
    }


async def _settings_response(
    db: AsyncSession,
    settings: AiSettingsBase,
    user_id: UUID | None = None,
    *,
    include_configuration: bool = True,
) -> dict[str, Any]:
    tool_settings = (
        await load_effective_tools(db, user_id)
        if user_id is not None
        else await SettingsStore.load(db, ToolSettings)
    )
    return _settings_payload(
        settings, tool_settings, include_configuration=include_configuration
    )


async def _save_openai_provider(
    db: AsyncSession,
    settings: AiSettingsDocument,
    payload: OpenAIProviderRequest,
    provider: LlmProviderConfig | None = None,
    owner_id: UUID | None = None,
) -> dict[str, Any]:
    credential_id = provider.credential_id if provider else None
    previous_config = dict(provider.config) if provider else None
    api_key = payload.api_key.strip() if payload.api_key else ""
    if payload.remove_api_key and credential_id:
        await delete_credential(db, credential_id, owner_id=provider.owner_id)
        credential_id = None
    elif api_key:
        existing = (
            await load_credential(db, credential_id, owner_id=provider.owner_id)
            if credential_id
            else None
        )
        credential = await store_credential(
            db,
            owner_id=provider.owner_id if provider else owner_id,
            kind="openai-compatible-api-key",
            label=payload.name,
            payload={"api_key": api_key},
            credential=existing,
        )
        credential_id = credential.id
    if provider is None:
        provider = LlmProviderConfig(name=payload.name, adapter_type="openai-compatible")
        provider.owner_id = owner_id
        settings.providers.append(provider)
    provider.name = payload.name
    provider.trust_level = payload.trust_level
    provider.pseudonymization_enabled = payload.pseudonymization_enabled
    provider.memory_policy = payload.memory_policy
    provider.credential_id = credential_id
    provider.config = {
        "base_url": str(payload.base_url).rstrip("/"),
        "api_type": payload.api_type,
    }
    if previous_config is not None and (
        previous_config != provider.config or bool(api_key) or payload.remove_api_key
    ):
        provider.models_last_synced_at = None
        for model in settings.models:
            if model.provider_id == provider.id and model.origin == ModelOrigin.DISCOVERED:
                model.availability = ModelAvailability.MISSING
        _clear_invalid_defaults(settings)
    await _save_scoped_ai(db, settings, owner_id)
    return await _settings_response(db, settings, owner_id)


async def _save_lemonade_provider(
    db: AsyncSession,
    settings: AiSettingsDocument,
    payload: LemonadeProviderRequest,
    provider: LlmProviderConfig | None = None,
    owner_id: UUID | None = None,
) -> dict[str, Any]:
    credential_id = provider.credential_id if provider else None
    previous_config = dict(provider.config) if provider else None
    api_key = payload.api_key.strip() if payload.api_key else ""
    if payload.remove_api_key and credential_id:
        await delete_credential(db, credential_id, owner_id=provider.owner_id)
        credential_id = None
    elif api_key:
        existing = (
            await load_credential(db, credential_id, owner_id=provider.owner_id)
            if credential_id
            else None
        )
        credential = await store_credential(
            db,
            owner_id=provider.owner_id if provider else owner_id,
            kind="lemonade-api-key",
            label=payload.name,
            payload={"api_key": api_key},
            credential=existing,
        )
        credential_id = credential.id
    if provider is None:
        provider = LlmProviderConfig(name=payload.name, adapter_type="lemonade")
        provider.owner_id = owner_id
        settings.providers.append(provider)
    provider.name = payload.name
    provider.trust_level = payload.trust_level
    provider.pseudonymization_enabled = payload.pseudonymization_enabled
    provider.memory_policy = payload.memory_policy
    provider.credential_id = credential_id
    provider.config = {"base_url": str(payload.base_url).rstrip("/")}
    if previous_config is not None and (
        previous_config != provider.config or bool(api_key) or payload.remove_api_key
    ):
        provider.models_last_synced_at = None
        for model in settings.models:
            if model.provider_id == provider.id and model.origin == ModelOrigin.DISCOVERED:
                model.availability = ModelAvailability.MISSING
        _clear_invalid_defaults(settings)
    await _save_scoped_ai(db, settings, owner_id)
    return await _settings_response(db, settings, owner_id)


@router.get("")
async def list_providers(
    db: Annotated[AsyncSession, Depends(get_db_session)],
    scope: SettingsApiScope | None = None,
    user: Annotated[UserAccount | None, Depends(require_user)] = None,
) -> dict:
    if scope is None and user is not None and not user.is_admin:
        return await _settings_response(
            db,
            await load_effective_ai(db, user.id),
            user.id,
            include_configuration=False,
        )
    settings, owner_id = await _load_scoped_ai(db, scope or "instance", user)
    return await _settings_response(
        db,
        settings,
        owner_id,
        include_configuration=user is None or user.is_admin or scope == "personal",
    )


@router.get("/personal-policy")
async def get_personal_provider_policy(
    _user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict[str, bool]:
    settings = await SettingsStore.load(db, AiSettings)
    return {
        "enabled": settings.enabled,
        "personalProvidersEnabled": settings.personal_providers_enabled,
    }


@router.post("/openai-compatible", status_code=status.HTTP_201_CREATED)
async def save_openai_provider(
    payload: OpenAIProviderRequest,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    scope: SettingsApiScope = "instance",
    user: Annotated[UserAccount | None, Depends(require_user)] = None,
) -> dict:
    settings, owner_id = await _load_scoped_ai(
        db, scope, user, for_update=True, mutation=True
    )
    result = await _save_openai_provider(db, settings, payload, owner_id=owner_id)
    result["providerId"] = str(settings.providers[-1].id)
    await db.commit()
    return result


@router.post("/lemonade", status_code=status.HTTP_201_CREATED)
async def save_lemonade_provider(
    payload: LemonadeProviderRequest,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    scope: SettingsApiScope = "instance",
    user: Annotated[UserAccount | None, Depends(require_user)] = None,
) -> dict:
    settings, owner_id = await _load_scoped_ai(
        db, scope, user, for_update=True, mutation=True
    )
    result = await _save_lemonade_provider(db, settings, payload, owner_id=owner_id)
    result["providerId"] = str(settings.providers[-1].id)
    await db.commit()
    return result


@router.put("/configuration/{provider_id}")
async def update_openai_provider(
    provider_id: UUID,
    payload: OpenAIProviderRequest | LemonadeProviderRequest,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    scope: SettingsApiScope = "instance",
    user: Annotated[UserAccount | None, Depends(require_user)] = None,
) -> dict:
    settings, owner_id = await _load_scoped_ai(
        db, scope, user, for_update=True, mutation=True
    )
    provider = _provider(settings, provider_id)
    if provider.adapter_type == "openai-compatible":
        if not isinstance(payload, OpenAIProviderRequest):
            payload = OpenAIProviderRequest.model_validate(payload.model_dump(exclude_unset=True))
        result = await _save_openai_provider(
            db, settings, payload, provider, owner_id=owner_id
        )
    elif provider.adapter_type == "lemonade":
        if not isinstance(payload, LemonadeProviderRequest):
            try:
                payload = LemonadeProviderRequest.model_validate(
                    payload.model_dump(exclude_unset=True)
                )
            except ValidationError as exc:
                raise ApiError(400, "provider.configurationInvalid") from exc
        result = await _save_lemonade_provider(
            db, settings, payload, provider, owner_id=owner_id
        )
    else:
        raise ApiError(404, "provider.notFound")
    await db.commit()
    return result


@router.patch("/configuration/{provider_id}/enabled")
async def set_provider_enabled(
    provider_id: UUID,
    payload: EnabledRequest,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    scope: SettingsApiScope = "instance",
    user: Annotated[UserAccount | None, Depends(require_user)] = None,
) -> dict:
    settings, owner_id = await _load_scoped_ai(
        db, scope, user, for_update=True, mutation=True
    )
    _provider(settings, provider_id).enabled = payload.enabled
    _clear_invalid_defaults(settings)
    await _save_scoped_ai(db, settings, owner_id)
    await db.commit()
    return await _settings_response(db, settings, owner_id)


@router.patch("/configuration/{provider_id}/memory-policy")
async def set_provider_memory_policy(
    provider_id: UUID,
    payload: MemoryPolicyRequest,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    scope: SettingsApiScope = "instance",
    user: Annotated[UserAccount | None, Depends(require_user)] = None,
) -> dict:
    settings, owner_id = await _load_scoped_ai(
        db, scope, user, for_update=True, mutation=True
    )
    _provider(settings, provider_id).memory_policy = payload.memory_policy
    await _save_scoped_ai(db, settings, owner_id)
    await db.commit()
    return await _settings_response(db, settings, owner_id)


@router.patch("/configuration/{provider_id}/policy")
async def set_provider_policy(
    provider_id: UUID,
    payload: ProviderPolicyRequest,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    scope: SettingsApiScope = "instance",
    user: Annotated[UserAccount | None, Depends(require_user)] = None,
) -> dict:
    settings, owner_id = await _load_scoped_ai(
        db, scope, user, for_update=True, mutation=True
    )
    provider = _provider(settings, provider_id)
    provider.trust_level = payload.trust_level
    provider.pseudonymization_enabled = payload.pseudonymization_enabled
    provider.memory_policy = payload.memory_policy
    await _save_scoped_ai(db, settings, owner_id)
    await db.commit()
    return await _settings_response(db, settings, owner_id)


@router.patch("/enabled")
async def set_ai_enabled(
    payload: EnabledRequest,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    scope: SettingsApiScope = "instance",
    user: Annotated[UserAccount | None, Depends(require_user)] = None,
) -> dict:
    settings, owner_id = await _load_scoped_ai(
        db, scope, user, for_update=True, mutation=True
    )
    settings.enabled = payload.enabled
    await _save_scoped_ai(db, settings, owner_id)
    await db.commit()
    return await _settings_response(db, settings, owner_id)


@router.patch("/personal-enabled")
async def set_personal_providers_enabled(
    payload: EnabledRequest,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    user: Annotated[UserAccount | None, Depends(require_user)] = None,
) -> dict:
    settings, _owner_id = await _load_scoped_ai(
        db, "instance", user, for_update=True, mutation=True
    )
    assert isinstance(settings, AiSettings)
    settings.personal_providers_enabled = payload.enabled
    await SettingsStore.save(db, settings)
    await db.commit()
    return await _settings_response(db, settings)


@router.post("/configuration/{provider_id}/models/sync")
async def sync_provider_models(
    provider_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    scope: SettingsApiScope = "instance",
    user: Annotated[UserAccount | None, Depends(require_user)] = None,
) -> dict:
    settings, owner_id = await _load_scoped_ai(db, scope, user, mutation=True)
    provider = _provider(settings, provider_id).model_copy(deep=True)
    provider_snapshot = await _provider_sync_fingerprint(db, provider)
    try:
        discovered = await ProviderService.discover_models(db, provider=provider)
    except ProviderError as exc:
        logger.warning("Provider model discovery failed: %s", exc.public_code)
        if exc.public_code == "provider.notConnected":
            status_code = 409
        elif exc.public_code in {
            "provider.discoveryUnsupported",
            "provider.configurationInvalid",
        }:
            status_code = 400
        else:
            status_code = 502
        raise ApiError(status_code, exc.public_code) from exc
    except Exception as exc:
        logger.exception("Unexpected provider model discovery failure")
        raise ApiError(502, "provider.requestFailed") from exc

    settings, current_owner_id = await _load_scoped_ai(
        db, scope, user, for_update=True, mutation=True
    )
    if current_owner_id != owner_id:
        raise ApiError(409, "provider.configurationChanged")
    provider = _provider(settings, provider_id)
    if provider_snapshot != await _provider_sync_fingerprint(db, provider):
        raise ApiError(409, "provider.configurationChanged")
    synced_at = datetime.now(UTC)
    discovered_by_id = {item.id: item for item in discovered}
    existing_by_id = {
        item.model_id: item for item in settings.models if item.provider_id == provider.id
    }
    for external_id, discovered_model in discovered_by_id.items():
        model = existing_by_id.get(external_id)
        if model is None:
            name = discovered_model.metadata.get("name")
            discovered_context = _discovered_context_window(discovered_model.metadata)
            context_window = discovered_context or 128_000
            max_output = min(16_384, max(1, context_window // 4))
            supports_tools = discovered_model.metadata.get("supports_tools")
            supports_parallel = discovered_model.metadata.get("supports_parallel_tool_calls")
            input_modalities = discovered_model.metadata.get("input_modalities")
            supports_image = discovered_model.metadata.get("supports_image_input")
            is_lemonade = provider.adapter_type == "lemonade"
            settings.models.append(
                ModelConfig(
                    provider_id=provider.id,
                    model_id=external_id,
                    name=name if isinstance(name, str) else None,
                    enabled=(
                        discovered_model.metadata.get("native_for_chat_provider")
                        is True
                    ),
                    origin=ModelOrigin.DISCOVERED,
                    last_seen_at=synced_at,
                    discovery_metadata=discovered_model.metadata,
                    capabilities=discovered_model.capabilities,
                    context_window_tokens=context_window,
                    discovered_context_window_tokens=discovered_context,
                    use_discovered_context_window=True,
                    max_output_tokens=max_output,
                    supports_parallel_tool_calls=(
                        supports_parallel
                        if isinstance(supports_parallel, bool)
                        else not is_lemonade
                    ),
                    supports_tools=(
                        supports_tools
                        if isinstance(supports_tools, bool)
                        else not is_lemonade
                    ),
                    supports_streaming=ModelCapability.CHAT
                    in discovered_model.capabilities,
                    input_modalities=(
                        input_modalities
                        if isinstance(input_modalities, list)
                        else (
                            [ModelInputModality.TEXT, ModelInputModality.IMAGE]
                            if supports_image is True
                            else [ModelInputModality.TEXT]
                        )
                    ),
                )
            )
        elif model.origin == ModelOrigin.DISCOVERED:
            model.availability = ModelAvailability.AVAILABLE
            model.last_seen_at = synced_at
            model.discovery_metadata = discovered_model.metadata
            _apply_discovered_context_window(
                model, _discovered_context_window(discovered_model.metadata)
            )
            input_modalities = discovered_model.metadata.get("input_modalities")
            supports_image = discovered_model.metadata.get("supports_image_input")
            if isinstance(input_modalities, list):
                model.input_modalities = {
                    ModelInputModality(item) for item in input_modalities
                }
            elif isinstance(supports_image, bool):
                model.input_modalities = {ModelInputModality.TEXT}
                if supports_image:
                    model.input_modalities.add(ModelInputModality.IMAGE)
            if (
                provider.adapter_type == "lemonade"
                or discovered_model.metadata.get("capabilities_authoritative") is True
            ):
                model.capabilities = discovered_model.capabilities
                supports_parallel = discovered_model.metadata.get(
                    "supports_parallel_tool_calls"
                )
                supports_tools = discovered_model.metadata.get("supports_tools")
                model.supports_tools = (
                    supports_tools if isinstance(supports_tools, bool) else False
                )
                model.supports_parallel_tool_calls = (
                    supports_parallel
                    if model.supports_tools and isinstance(supports_parallel, bool)
                    else False
                )
                model.supports_streaming = (
                    ModelCapability.CHAT in discovered_model.capabilities
                )
    for model in settings.models:
        if (
            model.provider_id == provider.id
            and model.origin == ModelOrigin.DISCOVERED
            and model.model_id not in discovered_by_id
        ):
            model.availability = ModelAvailability.MISSING
    provider.models_last_synced_at = synced_at
    _clear_invalid_defaults(settings)
    await _save_scoped_ai(db, settings, owner_id)
    await db.commit()
    return await _settings_response(db, settings, owner_id)


@router.post("/configuration/{provider_id}/models", status_code=status.HTTP_201_CREATED)
async def create_manual_model(
    provider_id: UUID,
    payload: ModelRequest,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    scope: SettingsApiScope = "instance",
    user: Annotated[UserAccount | None, Depends(require_user)] = None,
) -> dict:
    settings, owner_id = await _load_scoped_ai(
        db, scope, user, for_update=True, mutation=True
    )
    _provider(settings, provider_id)
    _ensure_unique_model_id(settings, provider_id=provider_id, model_id=payload.model_id)
    model_values = payload.model_dump(exclude={"enabled"})
    settings.models.append(
        ModelConfig(
            provider_id=provider_id,
            origin=ModelOrigin.MANUAL,
            enabled=payload.enabled if payload.enabled is not None else True,
            **model_values,
        )
    )
    await _save_scoped_ai(db, settings, owner_id)
    await db.commit()
    return await _settings_response(db, settings, owner_id)


@router.put("/models/{model_id}")
async def update_model(
    model_id: UUID,
    payload: ModelRequest,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    scope: SettingsApiScope = "instance",
    user: Annotated[UserAccount | None, Depends(require_user)] = None,
) -> dict:
    settings, owner_id = await _load_scoped_ai(
        db, scope, user, for_update=True, mutation=True
    )
    model = _model(settings, model_id)
    if model.origin == ModelOrigin.DISCOVERED and payload.model_id != model.model_id:
        raise ApiError(409, "provider.discoveredModelIdImmutable", field="modelId")
    _ensure_unique_model_id(
        settings,
        provider_id=model.provider_id,
        model_id=payload.model_id,
        exclude_id=model.id,
    )
    candidate = model.model_dump()
    candidate.update(payload.model_dump(exclude={"enabled"}, exclude_unset=True))
    if payload.enabled is not None:
        if payload.enabled and model.availability == ModelAvailability.MISSING:
            raise ApiError(409, "provider.modelUnavailable")
        candidate["enabled"] = payload.enabled
    try:
        validated = ModelConfig.model_validate(candidate)
    except ValidationError as exc:
        raise ApiError(400, "provider.configurationInvalid") from exc
    settings.models[settings.models.index(model)] = validated
    _clear_invalid_defaults(settings)
    await _save_scoped_ai(db, settings, owner_id)
    await db.commit()
    return await _settings_response(db, settings, owner_id)


@router.patch("/models/{model_id}/enabled")
async def set_model_enabled(
    model_id: UUID,
    payload: EnabledRequest,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    scope: SettingsApiScope = "instance",
    user: Annotated[UserAccount | None, Depends(require_user)] = None,
) -> dict:
    settings, owner_id = await _load_scoped_ai(
        db, scope, user, for_update=True, mutation=True
    )
    model = _model(settings, model_id)
    if payload.enabled and model.availability == ModelAvailability.MISSING:
        raise ApiError(409, "provider.modelUnavailable")
    model.enabled = payload.enabled
    _clear_invalid_defaults(settings)
    await _save_scoped_ai(db, settings, owner_id)
    await db.commit()
    return await _settings_response(db, settings, owner_id)


@router.delete("/models/{model_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_model(
    model_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    scope: SettingsApiScope = "instance",
    user: Annotated[UserAccount | None, Depends(require_user)] = None,
) -> None:
    settings, owner_id = await _load_scoped_ai(
        db, scope, user, for_update=True, mutation=True
    )
    model = _model(settings, model_id)
    if model.origin != ModelOrigin.MANUAL:
        raise ApiError(409, "provider.discoveredModelDeleteForbidden")
    referenced_query = select(ChatSession.id).where(
        ChatSession.model_config_id == model.id
    )
    if owner_id is not None:
        referenced_query = referenced_query.where(ChatSession.user_id == owner_id)
    referenced = await db.scalar(referenced_query.limit(1))
    if referenced is not None:
        raise ApiError(409, "provider.modelInUse")
    settings.models.remove(model)
    _clear_removed_defaults(settings, {model.id})
    _clear_invalid_defaults(settings)
    await _save_scoped_ai(db, settings, owner_id)
    await db.commit()


@router.post("/models/{model_id}/test")
async def test_model(
    model_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    scope: SettingsApiScope = "instance",
    user: Annotated[UserAccount | None, Depends(require_user)] = None,
) -> dict[str, str]:
    settings, _owner_id = await _load_scoped_ai(db, scope, user)
    model = _model(settings, model_id)
    provider = _provider(settings, model.provider_id)
    if ModelCapability.CHAT not in model.capabilities:
        raise ApiError(409, "provider.modelCapabilityUnavailable")
    if (
        not provider.enabled
        or model.availability == ModelAvailability.MISSING
    ):
        raise ApiError(409, "provider.modelUnavailable")
    try:
        await ProviderService.complete(
            db,
            provider=provider,
            model=model,
            messages=[{"role": "user", "content": "Reply with OK only."}],
            tools=[],
        )
    except ProviderError as exc:
        logger.warning("Model connection test failed: %s", exc.public_code)
        raise ApiError(502, exc.public_code) from exc
    except Exception as exc:
        logger.exception("Unexpected model connection test failure")
        raise ApiError(502, "provider.requestFailed") from exc
    return {"status": "ok"}


@router.put("/default-model")
async def set_default_model(
    payload: DefaultModelRequest | None,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    scope: SettingsApiScope = "instance",
    user: Annotated[UserAccount | None, Depends(require_user)] = None,
) -> dict:
    settings, owner_id = await _load_scoped_ai(
        db, scope, user, for_update=True, mutation=True
    )
    model_id = payload.model_id if payload else None
    validation_settings = (
        await load_effective_ai(db, owner_id) if owner_id is not None else settings
    )
    if model_id is not None:
        model = _available_model(validation_settings, model_id)
        if model is None:
            raise ApiError(409, "provider.modelUnavailable")
        if ModelCapability.CHAT not in model.capabilities:
            raise ApiError(409, "provider.modelCapabilityUnavailable")
        if not model.supports_tools:
            raise ApiError(409, "session.modelToolsUnavailable")
    settings.default_model_id = model_id
    await _save_scoped_ai(db, settings, owner_id)
    await db.commit()
    return await _settings_response(db, settings, owner_id)


@router.put("/default-models")
async def set_default_models(
    payload: DefaultModelsRequest,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    scope: SettingsApiScope = "instance",
    user: Annotated[UserAccount | None, Depends(require_user)] = None,
) -> dict:
    settings, owner_id = await _load_scoped_ai(
        db, scope, user, for_update=True, mutation=True
    )
    validation_settings = (
        await load_effective_ai(db, owner_id) if owner_id is not None else settings
    )
    fields = {
        "default_model_id": ("defaultModelId", ModelCapability.CHAT),
        "compaction_model_id": ("compactionModelId", ModelCapability.CHAT),
        "title_generation_model_id": ("titleGenerationModelId", ModelCapability.CHAT),
        "skill_creation_model_id": ("skillCreationModelId", ModelCapability.CHAT),
        "dreaming_model_id": ("dreamingModelId", ModelCapability.CHAT),
        "vision_model_id": ("visionModelId", ModelCapability.CHAT),
        "video_model_id": ("videoModelId", ModelCapability.CHAT),
        "image_generation_model_id": (
            "imageGenerationModelId",
            ModelCapability.IMAGE_GENERATION,
        ),
        "speech_generation_model_id": (
            "speechGenerationModelId",
            ModelCapability.SPEECH_GENERATION,
        ),
        "audio_generation_model_id": (
            "audioGenerationModelId",
            ModelCapability.AUDIO_GENERATION,
        ),
        "audio_transcription_model_id": (
            "audioTranscriptionModelId",
            ModelCapability.AUDIO_TRANSCRIPTION,
        ),
    }
    for field, (api_field, capability) in fields.items():
        model_id = getattr(payload, field)
        if model_id is None:
            continue
        model = _available_model(validation_settings, model_id)
        if model is None:
            raise ApiError(409, "provider.modelUnavailable", field=api_field)
        if capability not in model.capabilities:
            raise ApiError(
                409, "provider.modelCapabilityUnavailable", field=api_field
            )
        if field in {"default_model_id", "skill_creation_model_id"} and not model.supports_tools:
            raise ApiError(409, "session.modelToolsUnavailable", field=api_field)
        if field == "vision_model_id" and not model.supports_image_input:
            raise ApiError(
                409, "provider.modelCapabilityUnavailable", field=api_field
            )
        if (
            field == "video_model_id"
            and not _video_analysis_model_available(validation_settings, model_id)
        ):
            raise ApiError(
                409, "provider.modelCapabilityUnavailable", field=api_field
            )
        if capability != ModelCapability.CHAT and not media_model_available(
            validation_settings, capability, model_id
        ):
            raise ApiError(
                409, "provider.modelCapabilityUnavailable", field=api_field
            )
        if field == "dreaming_model_id" and not model.supports_tools:
            raise ApiError(409, "memory.dreamingModelToolsUnavailable", field=api_field)
    additive_fields = {
        "vision_model_id",
        "skill_creation_model_id",
        "video_model_id",
        "image_generation_model_id",
        "speech_generation_model_id",
        "audio_generation_model_id",
        "audio_transcription_model_id",
    }
    for field in fields:
        if field in additive_fields and field not in payload.model_fields_set:
            continue
        setattr(settings, field, getattr(payload, field))
    await _save_scoped_ai(db, settings, owner_id)
    await db.commit()
    return await _settings_response(db, settings, owner_id)


@router.delete("/configuration/{provider_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_provider(
    provider_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    scope: SettingsApiScope = "instance",
    user: Annotated[UserAccount | None, Depends(require_user)] = None,
) -> None:
    settings, owner_id = await _load_scoped_ai(
        db, scope, user, for_update=True, mutation=True
    )
    provider = _provider(settings, provider_id)
    removed_model_ids = {item.id for item in settings.models if item.provider_id == provider_id}
    settings.providers = [item for item in settings.providers if item.id != provider_id]
    settings.models = [item for item in settings.models if item.provider_id != provider_id]
    _clear_removed_defaults(settings, removed_model_ids)
    if removed_model_ids:
        clear_sessions = update(ChatSession).where(
            ChatSession.model_config_id.in_(removed_model_ids)
        )
        if owner_id is not None:
            clear_sessions = clear_sessions.where(ChatSession.user_id == owner_id)
        await db.execute(clear_sessions.values(model_config_id=None))
    _clear_invalid_defaults(settings)
    if provider.credential_id:
        await delete_credential(
            db, provider.credential_id, owner_id=provider.owner_id
        )
    await _save_scoped_ai(db, settings, owner_id)
    await db.commit()


@router.post("/codex/login")
async def start_codex_login(
    db: Annotated[AsyncSession, Depends(get_db_session)],
    scope: SettingsApiScope = "instance",
    user: Annotated[UserAccount | None, Depends(require_user)] = None,
) -> dict[str, Any]:
    _settings, owner_id = await _load_scoped_ai(db, scope, user, mutation=True)
    try:
        attempt = await oauth_manager.start_login(owner_id)
    except CodexOAuthError as exc:
        logger.warning("Codex device-code login could not be started: %s", exc.code)
        raise ApiError(502, exc.code) from exc
    return {
        "loginId": attempt.id,
        "verificationUrl": attempt.verification_url,
        "userCode": attempt.user_code,
        "intervalSeconds": attempt.interval,
        "expiresInSeconds": 15 * 60,
    }


@router.get("/codex/login/{login_id}")
async def codex_login_status(
    login_id: str,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    scope: SettingsApiScope = "instance",
    user: Annotated[UserAccount | None, Depends(require_user)] = None,
) -> dict:
    _settings, owner_id = await _load_scoped_ai(db, scope, user, mutation=True)
    attempt, should_poll = oauth_manager.begin_poll(login_id)
    if attempt is None or attempt.owner_id != owner_id:
        raise ApiError(404, "oauth.loginNotFound")
    if oauth_manager.expired(attempt):
        oauth_manager.finish(login_id)
        return {
            "status": "failed",
            "error": describe_error("oauth.loginExpired").payload(),
        }
    if not should_poll:
        return {"status": "pending"}
    try:
        device_token = await poll_device_code(attempt)
        if device_token is None:
            oauth_manager.end_poll(login_id)
            return {"status": "pending"}
        token = await exchange_code(device_token.authorization_code, device_token.code_verifier)
    except CodexOAuthError as exc:
        logger.warning("Codex device-code login failed: %s", exc.code)
        oauth_manager.finish(login_id)
        return {"status": "failed", "error": describe_error(exc.code).payload()}
    except Exception:
        logger.exception("Codex device-code login failed unexpectedly")
        oauth_manager.finish(login_id)
        return {
            "status": "failed",
            "error": describe_error("oauth.exchangeFailed").payload(),
        }
    try:
        credential = await db.scalar(
            scoped_credentials(owner_id).where(Credential.kind == "codex-oauth")
        )
        credential = await store_credential(
            db,
            owner_id=owner_id,
            kind="codex-oauth",
            label="ChatGPT Codex",
            payload=token,
            metadata={"accountId": token.get("account_id")},
            credential=credential,
        )
        settings, current_owner_id = await _load_scoped_ai(
            db, scope, user, for_update=True, mutation=True
        )
        if current_owner_id != owner_id:
            raise RuntimeError("Codex OAuth scope changed")
        provider = next(
            (item for item in settings.providers if item.adapter_type == "openai-codex"), None
        )
        if provider is None:
            provider = LlmProviderConfig(name="ChatGPT Codex", adapter_type="openai-codex")
            provider.owner_id = owner_id
            settings.providers.append(provider)
        provider.credential_id = credential.id
        await _save_scoped_ai(db, settings, owner_id)
        await db.commit()
    except Exception:
        logger.exception("Codex credential persistence failed")
        oauth_manager.finish(login_id)
        return {
            "status": "failed",
            "error": describe_error("oauth.exchangeFailed").payload(),
        }
    oauth_manager.finish(login_id)
    return {
        "status": "authenticated",
        "accountId": token.get("account_id"),
        "providerId": str(provider.id),
    }


@router.get("/codex/status")
async def codex_status(
    db: Annotated[AsyncSession, Depends(get_db_session)],
    scope: SettingsApiScope | None = None,
    user: Annotated[UserAccount | None, Depends(require_user)] = None,
) -> dict:
    if scope is None and user is not None and not user.is_admin:
        settings = await load_effective_ai(db, user.id)
        return {
            "authenticated": any(
                provider.adapter_type == "openai-codex"
                and provider.credential_id is not None
                for provider in settings.providers
            ),
            "accountId": None,
        }
    _settings, owner_id = await _load_scoped_ai(db, scope or "instance", user)
    credential = await db.scalar(
        scoped_credentials(owner_id).where(Credential.kind == "codex-oauth")
    )
    return {
        "authenticated": credential is not None,
        "accountId": credential.metadata_json.get("accountId") if credential else None,
    }


@router.delete("/codex/logout", status_code=status.HTTP_204_NO_CONTENT)
async def codex_logout(
    db: Annotated[AsyncSession, Depends(get_db_session)],
    scope: SettingsApiScope = "instance",
    user: Annotated[UserAccount | None, Depends(require_user)] = None,
) -> None:
    settings, owner_id = await _load_scoped_ai(
        db, scope, user, for_update=True, mutation=True
    )
    credentials = list(
        (
            await db.scalars(
                scoped_credentials(owner_id).where(Credential.kind == "codex-oauth")
            )
        ).all()
    )
    credential_ids = {item.id for item in credentials}
    for provider in settings.providers:
        if provider.credential_id in credential_ids:
            provider.credential_id = None
    _clear_invalid_defaults(settings)
    for credential in credentials:
        await db.delete(credential)
    await _save_scoped_ai(db, settings, owner_id)
    await db.commit()
