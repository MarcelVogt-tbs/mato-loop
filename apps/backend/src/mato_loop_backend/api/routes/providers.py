from __future__ import annotations

import logging
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
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.api.errors import ApiError
from mato_loop_backend.db.session import get_db_session
from mato_loop_backend.error_contract import describe_error
from mato_loop_backend.models import Credential, UserAccount
from mato_loop_backend.providers.azure_speech import validate_endpoint
from mato_loop_backend.providers.base import ProviderError
from mato_loop_backend.providers.codex_oauth import (
    CodexOAuthError,
    exchange_code,
    oauth_manager,
    poll_device_code,
)
from mato_loop_backend.providers.media import media_model_available
from mato_loop_backend.providers.reasoning import ReasoningProfile, reasoning_profile
from mato_loop_backend.providers.service import ProviderService
from mato_loop_backend.providers.settings_service import (
    AiSettingsDocument,
    ProviderDiscoveryError,
    ProviderSettingsError,
    ProviderSettingsService,
    SettingsApiScope,
    available_model,
    model,
    provider,
)
from mato_loop_backend.security import (
    delete_credential,
    load_credential,
    require_user,
    scoped_credentials,
    store_credential,
)
from mato_loop_backend.settings import (
    AiSettings,
    SettingsStore,
    effective_model_pseudonymization,
    effective_model_trust,
)
from mato_loop_backend.settings.effective import load_effective_ai
from mato_loop_backend.settings.models import (
    AiSettingsBase,
    LlmProviderConfig,
    MemoryPolicy,
    ModelAvailability,
    ModelCapability,
    ModelConfig,
    ModelInputModality,
    ReasoningEffort,
    TrustLevel,
)

router = APIRouter(prefix="/providers", dependencies=[Depends(require_user)])
logger = logging.getLogger(__name__)
_available_model = available_model
_find_model = model
_find_provider = provider

_PROVIDER_SETTINGS_ERROR_STATUS = {
    "auth.notAuthenticated": 401,
    "auth.adminRequired": 403,
    "provider.notFound": 404,
    "provider.modelMissing": 404,
    "provider.configurationInvalid": 400,
}


def _provider_settings_api_error(exc: ProviderSettingsError) -> ApiError:
    return ApiError(
        _PROVIDER_SETTINGS_ERROR_STATUS.get(exc.code, 409),
        exc.code,
        field=exc.field,
    )


async def _apply_settings(operation: Any) -> Any:
    try:
        return await operation
    except ProviderSettingsError as exc:
        raise _provider_settings_api_error(exc) from exc


def _model_reasoning_profile(
    provider: LlmProviderConfig, model: ModelConfig
) -> ReasoningProfile | None:
    api_type = (
        "chat_completions"
        if provider.adapter_type == "lemonade"
        else provider.config.get("api_type", "chat_completions")
    )
    return (
        reasoning_profile(model.model_id, model.discovery_metadata)
        if provider.adapter_type == "openai-codex" or api_type == "chat_completions"
        else None
    )


def _model_reasoning_default(
    provider: LlmProviderConfig, model: ModelConfig
) -> str | None:
    profile = _model_reasoning_profile(provider, model)
    effort = profile.default_effort if profile else model.reasoning_effort
    return effort.value if effort else None


def _model_reasoning_options(
    provider: LlmProviderConfig, model: ModelConfig
) -> list[str]:
    profile = _model_reasoning_profile(provider, model)
    return [effort.value for effort in profile.options] if profile else []


async def _load_scoped_ai(
    db: AsyncSession,
    scope: SettingsApiScope,
    user: UserAccount | None,
    *,
    for_update: bool = False,
    mutation: bool = False,
) -> tuple[AiSettingsDocument, UUID | None]:
    return await _apply_settings(
        ProviderSettingsService.load_scoped(
            db, scope, user, for_update=for_update
        )
    )


def _to_camel(value: str) -> str:
    first, *rest = value.split("_")
    return first + "".join(part.capitalize() for part in rest)


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
    context_window_tokens: int = Field(default=128_000, ge=2, le=10_000_000)
    use_discovered_context_window: bool = False
    max_output_tokens: int | None = Field(default=None, strict=True, gt=0)
    characters_per_token: float = Field(default=4.0, gt=0, le=100)
    supports_tools: bool = True
    supports_parallel_tool_calls: bool = True
    supports_streaming: bool = True
    input_modalities: set[ModelInputModality] = Field(
        default_factory=lambda: {ModelInputModality.TEXT}
    )
    supports_image_input: bool = False
    reasoning_summary: bool = False
    reasoning_effort: ReasoningEffort | None = None
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


class AzureSpeechProviderRequest(LemonadeProviderRequest):
    trust_level: TrustLevel = TrustLevel.PUBLIC
    pseudonymization_enabled: bool = True

    @model_validator(mode="after")
    def validate_azure_endpoint(self) -> Self:
        validate_endpoint(self.base_url)
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
    title_vision_fallback_enabled: bool = True
    skill_creation_model_id: UUID | None = None
    dreaming_model_id: UUID | None = None
    vision_model_id: UUID | None = None
    video_model_id: UUID | None = None
    image_generation_model_id: UUID | None = None
    speech_generation_model_id: UUID | None = None
    audio_generation_model_id: UUID | None = None
    audio_transcription_model_id: UUID | None = None


def _provider(settings: AiSettingsBase, provider_id: UUID) -> LlmProviderConfig:
    try:
        return _find_provider(settings, provider_id)
    except ProviderSettingsError as exc:
        raise _provider_settings_api_error(exc) from exc


def _model(settings: AiSettingsBase, model_id: UUID) -> ModelConfig:
    try:
        return _find_model(settings, model_id)
    except ProviderSettingsError as exc:
        raise _provider_settings_api_error(exc) from exc


def _provider_config_payload(config: dict[str, Any]) -> dict[str, Any]:
    payload = {key: value for key, value in config.items() if key not in {"base_url", "api_type"}}
    if "base_url" in config:
        payload["baseUrl"] = config["base_url"]
    if "api_type" in config:
        payload["apiType"] = config["api_type"]
    return payload


def _settings_payload(
    settings: AiSettingsBase,
    *,
    include_configuration: bool = True,
) -> dict[str, Any]:
    providers_by_id = {provider.id: provider for provider in settings.providers}
    return {
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
        "titleVisionFallbackEnabled": settings.title_vision_fallback_enabled,
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
                "discoveredMaxOutputTokens": model.discovered_max_output_tokens,
                "charactersPerToken": model.characters_per_token,
                "supportsTools": model.supports_tools,
                "supportsParallelToolCalls": model.supports_parallel_tool_calls,
                "supportsStreaming": model.supports_streaming,
                "inputModalities": sorted(item.value for item in model.input_modalities),
                "supportsImageInput": model.supports_image_input,
                "reasoningSummary": model.reasoning_summary,
                "reasoningEffort": _model_reasoning_default(
                    providers_by_id[model.provider_id], model
                ),
                "reasoningEfforts": _model_reasoning_options(
                    providers_by_id[model.provider_id], model
                ),
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
    return _settings_payload(settings, include_configuration=include_configuration)


async def _save_openai_provider(
    db: AsyncSession,
    settings: AiSettingsDocument,
    payload: OpenAIProviderRequest,
    provider: LlmProviderConfig | None = None,
    owner_id: UUID | None = None,
) -> dict[str, Any]:
    await _apply_settings(
        ProviderSettingsService.save_configured_provider(
            db,
            settings,
            adapter_type="openai-compatible",
            credential_kind="openai-compatible-api-key",
            name=payload.name,
            config={
                "base_url": str(payload.base_url).rstrip("/"),
                "api_type": payload.api_type,
            },
            api_key=payload.api_key,
            remove_api_key=payload.remove_api_key,
            trust_level=payload.trust_level,
            pseudonymization_enabled=payload.pseudonymization_enabled,
            memory_policy=payload.memory_policy,
            provider_config=provider,
            owner_id=owner_id,
            load_credential=load_credential,
            store_credential=store_credential,
            delete_credential=delete_credential,
        )
    )
    return await _settings_response(db, settings, owner_id)


async def _save_media_provider(
    db: AsyncSession,
    settings: AiSettingsDocument,
    payload: LemonadeProviderRequest | AzureSpeechProviderRequest,
    provider: LlmProviderConfig | None = None,
    owner_id: UUID | None = None,
) -> dict[str, Any]:
    adapter_type = "azure-speech" if isinstance(payload, AzureSpeechProviderRequest) else "lemonade"
    await _apply_settings(
        ProviderSettingsService.save_configured_provider(
            db,
            settings,
            adapter_type=adapter_type,
            credential_kind=f"{adapter_type}-api-key",
            name=payload.name,
            config={"base_url": str(payload.base_url).rstrip("/")},
            api_key=payload.api_key,
            remove_api_key=payload.remove_api_key,
            trust_level=payload.trust_level,
            pseudonymization_enabled=payload.pseudonymization_enabled,
            memory_policy=payload.memory_policy,
            provider_config=provider,
            owner_id=owner_id,
            load_credential=load_credential,
            store_credential=store_credential,
            delete_credential=delete_credential,
        )
    )
    return await _settings_response(db, settings, owner_id)


@router.get("")
async def list_providers(
    db: Annotated[AsyncSession, Depends(get_db_session)],
    scope: SettingsApiScope | None = None,
    user: Annotated[UserAccount | None, Depends(require_user)] = None,
) -> dict:
    if scope is None and user is not None:
        return await _settings_response(
            db,
            await load_effective_ai(db, user.id),
            user.id,
            include_configuration=False,
        )
    settings, owner_id = await _load_scoped_ai(db, scope or "instance", user)
    response = await _settings_response(
        db,
        settings,
        owner_id,
        include_configuration=user is None or user.is_admin or scope == "personal",
    )
    if scope == "personal" and user is not None:
        available = await _settings_response(
            db,
            await load_effective_ai(db, user.id),
            user.id,
            include_configuration=False,
        )
        response["availableProviders"] = available["providers"]
        response["availableModels"] = available["models"]
        response["effectiveTitleGenerationModelId"] = available[
            "titleGenerationModelId"
        ]
    return response


@router.get("/personal-policy")
async def get_personal_provider_policy(
    _user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict[str, bool]:
    settings = await SettingsStore.load(db, AiSettings)
    return {"personalProvidersEnabled": settings.personal_providers_enabled}


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
    result = await _save_media_provider(db, settings, payload, owner_id=owner_id)
    result["providerId"] = str(settings.providers[-1].id)
    await db.commit()
    return result


@router.post("/azure-speech", status_code=status.HTTP_201_CREATED)
async def save_azure_speech_provider(
    payload: AzureSpeechProviderRequest,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    scope: SettingsApiScope = "instance",
    user: Annotated[UserAccount | None, Depends(require_user)] = None,
) -> dict:
    settings, owner_id = await _load_scoped_ai(
        db, scope, user, for_update=True, mutation=True
    )
    result = await _save_media_provider(db, settings, payload, owner_id=owner_id)
    result["providerId"] = str(settings.providers[-1].id)
    await db.commit()
    return result


@router.put("/configuration/{provider_id}")
async def update_openai_provider(
    provider_id: UUID,
    payload: OpenAIProviderRequest | LemonadeProviderRequest | AzureSpeechProviderRequest,
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
        if type(payload) is not LemonadeProviderRequest:
            try:
                payload = LemonadeProviderRequest.model_validate(
                    payload.model_dump(exclude_unset=True)
                )
            except ValidationError as exc:
                raise ApiError(400, "provider.configurationInvalid") from exc
        result = await _save_media_provider(
            db, settings, payload, provider, owner_id=owner_id
        )
    elif provider.adapter_type == "azure-speech":
        try:
            payload = AzureSpeechProviderRequest.model_validate(
                payload.model_dump(exclude_unset=True)
            )
        except ValidationError as exc:
            raise ApiError(400, "provider.configurationInvalid") from exc
        result = await _save_media_provider(
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
    await _apply_settings(
        ProviderSettingsService.set_provider_enabled(
            db, settings, owner_id, provider_id, payload.enabled
        )
    )
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
    await _apply_settings(
        ProviderSettingsService.set_provider_memory_policy(
            db, settings, owner_id, provider_id, payload.memory_policy
        )
    )
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
    await _apply_settings(
        ProviderSettingsService.set_provider_policy(
            db,
            settings,
            owner_id,
            provider_id,
            trust_level=payload.trust_level,
            pseudonymization_enabled=payload.pseudonymization_enabled,
            memory_policy=payload.memory_policy,
        )
    )
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
    await ProviderSettingsService.set_personal_providers_enabled(
        db, settings, payload.enabled
    )
    return await _settings_response(db, settings)


@router.post("/configuration/{provider_id}/models/sync")
async def sync_provider_models(
    provider_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    scope: SettingsApiScope = "instance",
    user: Annotated[UserAccount | None, Depends(require_user)] = None,
) -> dict:
    async def load_settings(
        for_update: bool,
    ) -> tuple[AiSettingsDocument, UUID | None]:
        return await _load_scoped_ai(
            db,
            scope,
            user,
            for_update=for_update,
            mutation=True,
        )

    try:
        settings, owner_id = await ProviderSettingsService.synchronize_models(
            db,
            provider_id,
            load_settings=load_settings,
            discover_models=ProviderService.discover_models,
            load_credential=load_credential,
        )
    except ProviderSettingsError as exc:
        raise _provider_settings_api_error(exc) from exc
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
    except ProviderDiscoveryError as exc:
        logger.exception("Unexpected provider model discovery failure")
        raise ApiError(502, "provider.requestFailed") from exc
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
    await _apply_settings(
        ProviderSettingsService.create_manual_model(
            db,
            settings,
            owner_id,
            provider_id,
            payload.model_dump(exclude={"enabled"}),
            payload.enabled,
        )
    )
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
    await _apply_settings(
        ProviderSettingsService.update_model(
            db,
            settings,
            owner_id,
            model_id,
            payload.model_dump(exclude={"enabled"}, exclude_unset=True),
            payload.enabled,
        )
    )
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
    await _apply_settings(
        ProviderSettingsService.set_model_enabled(
            db, settings, owner_id, model_id, payload.enabled
        )
    )
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
    await _apply_settings(
        ProviderSettingsService.delete_model(
            db, settings, owner_id, model_id
        )
    )


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
            max_output_tokens=1_024,
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
    await _apply_settings(
        ProviderSettingsService.set_default_model(
            db, settings, validation_settings, owner_id, model_id
        )
    )
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
    await _apply_settings(
        ProviderSettingsService.set_default_models(
            db,
            settings,
            validation_settings,
            owner_id,
            payload.model_dump(),
            payload.model_fields_set,
        )
    )
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
    await _apply_settings(
        ProviderSettingsService.delete_provider(
            db,
            settings,
            owner_id,
            provider_id,
            delete_credential=delete_credential,
        )
    )


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
        async def load_settings(
            for_update: bool,
        ) -> tuple[AiSettingsDocument, UUID | None]:
            return await _load_scoped_ai(
                db,
                scope,
                user,
                for_update=for_update,
                mutation=True,
            )

        provider = await ProviderSettingsService.connect_codex(
            db,
            token,
            owner_id,
            load_settings=load_settings,
            store_credential=store_credential,
        )
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
    await ProviderSettingsService.disconnect_codex(
        db, settings, owner_id, credentials
    )
