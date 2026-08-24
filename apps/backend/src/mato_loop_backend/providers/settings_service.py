from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.models import ChatSession, Credential, UserAccount
from mato_loop_backend.providers.base import ProviderError
from mato_loop_backend.providers.media import media_model_available
from mato_loop_backend.providers.openai_compatible import DiscoveredModel
from mato_loop_backend.security import scoped_credentials
from mato_loop_backend.settings import AiSettings, SettingsStore, UserAiSettings
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

SettingsApiScope = Literal["instance", "personal"]
AiSettingsDocument = AiSettings | UserAiSettings
CredentialLoader = Callable[..., Awaitable[Credential | None]]
CredentialStore = Callable[..., Awaitable[Credential]]
CredentialDelete = Callable[..., Awaitable[bool]]
ScopedSettingsLoader = Callable[
    [bool], Awaitable[tuple[AiSettingsDocument, UUID | None]]
]
ModelDiscovery = Callable[..., Awaitable[list[DiscoveredModel]]]

DEFAULT_MODEL_FIELDS: dict[str, tuple[str, ModelCapability]] = {
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


class ProviderSettingsError(Exception):
    def __init__(self, code: str, *, field: str | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.field = field


class ProviderDiscoveryError(Exception):
    pass


def provider(settings: AiSettingsBase, provider_id: UUID) -> LlmProviderConfig:
    value = next((item for item in settings.providers if item.id == provider_id), None)
    if value is None:
        raise ProviderSettingsError("provider.notFound")
    return value


def model(settings: AiSettingsBase, model_id: UUID) -> ModelConfig:
    value = next((item for item in settings.models if item.id == model_id), None)
    if value is None:
        raise ProviderSettingsError("provider.modelMissing")
    return value


def available_model(
    settings: AiSettingsBase,
    model_id: UUID | None,
    capability: ModelCapability | None = None,
) -> ModelConfig | None:
    value = next((item for item in settings.models if item.id == model_id), None)
    owner = (
        next((item for item in settings.providers if item.id == value.provider_id), None)
        if value
        else None
    )
    if (
        value is None
        or owner is None
        or not value.enabled
        or not owner.enabled
        or (owner.adapter_type == "openai-codex" and owner.credential_id is None)
        or value.availability == ModelAvailability.MISSING
        or (capability is not None and capability not in value.capabilities)
    ):
        return None
    return value


def video_analysis_model_available(settings: AiSettingsBase, model_id: UUID) -> bool:
    value = available_model(settings, model_id, ModelCapability.CHAT)
    if value is None:
        return False
    if value.supports_image_input:
        return True
    owner = provider(settings, value.provider_id)
    return (
        ModelInputModality.VIDEO in value.input_modalities
        and owner.adapter_type == "openai-compatible"
        and owner.config.get("api_type", "chat_completions") == "chat_completions"
    )


def clear_invalid_defaults(settings: AiSettingsBase) -> None:
    for field, (_api_field, capability) in DEFAULT_MODEL_FIELDS.items():
        model_id = getattr(settings, field)
        if (
            isinstance(settings, UserAiSettings)
            and model_id is not None
            and not any(item.id == model_id for item in settings.models)
        ):
            continue
        media_capability = capability != ModelCapability.CHAT
        if model_id is not None and (
            available_model(settings, model_id, capability) is None
            or (
                field in {"default_model_id", "skill_creation_model_id"}
                and not model(settings, model_id).supports_tools
            )
            or (
                field == "vision_model_id"
                and not model(settings, model_id).supports_image_input
            )
            or (
                field == "video_model_id"
                and not video_analysis_model_available(settings, model_id)
            )
            or (
                media_capability
                and not media_model_available(settings, capability, model_id)
            )
        ):
            setattr(settings, field, None)


def clear_removed_defaults(settings: AiSettingsBase, model_ids: set[UUID]) -> None:
    for field in DEFAULT_MODEL_FIELDS:
        if getattr(settings, field) in model_ids:
            setattr(settings, field, None)


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
        raise ProviderSettingsError("provider.modelIdConflict", field="modelId")


def _discovered_context_window(metadata: dict[str, Any]) -> int | None:
    for key in ("context_length", "max_context_window", "max_model_len"):
        value = metadata.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and value > 1:
            return value
    return None


def _apply_discovered_context_window(value: ModelConfig, discovered: int | None) -> None:
    value.discovered_context_window_tokens = discovered
    if not value.use_discovered_context_window or discovered is None:
        return
    value.context_window_tokens = discovered
    value.max_output_tokens = min(
        value.max_output_tokens,
        max(1, discovered // 4),
        discovered - 1,
    )


async def _clear_sessions(
    db: AsyncSession, model_ids: set[UUID], owner_id: UUID | None
) -> None:
    if not model_ids:
        return
    statement = update(ChatSession).where(ChatSession.model_config_id.in_(model_ids))
    if owner_id is not None:
        statement = statement.where(ChatSession.user_id == owner_id)
    await db.execute(statement.values(model_config_id=None))


class ProviderSettingsService:
    @staticmethod
    async def load_scoped(
        db: AsyncSession,
        scope: SettingsApiScope,
        user: UserAccount | None,
        *,
        for_update: bool = False,
    ) -> tuple[AiSettingsDocument, UUID | None]:
        if scope == "instance":
            if user is not None and not getattr(user, "is_admin", False):
                raise ProviderSettingsError("auth.adminRequired")
            return await SettingsStore.load(db, AiSettings, for_update=for_update), None
        if user is None:
            raise ProviderSettingsError("auth.notAuthenticated")
        instance = await SettingsStore.load(db, AiSettings)
        if not instance.personal_providers_enabled:
            raise ProviderSettingsError("provider.personalDisabled")
        settings = await SettingsStore.load(
            db, UserAiSettings, user_id=user.id, for_update=for_update
        )
        for item in settings.providers:
            item.owner_id = user.id
        return settings, user.id

    @staticmethod
    async def save(
        db: AsyncSession, settings: AiSettingsDocument, owner_id: UUID | None
    ) -> None:
        await SettingsStore.save(db, settings, user_id=owner_id)

    @classmethod
    async def save_configured_provider(
        cls,
        db: AsyncSession,
        settings: AiSettingsDocument,
        *,
        adapter_type: str,
        credential_kind: str,
        name: str,
        config: dict[str, Any],
        api_key: str | None,
        remove_api_key: bool,
        trust_level: TrustLevel,
        pseudonymization_enabled: bool,
        memory_policy: MemoryPolicy,
        provider_config: LlmProviderConfig | None = None,
        owner_id: UUID | None = None,
        load_credential: CredentialLoader,
        store_credential: CredentialStore,
        delete_credential: CredentialDelete,
    ) -> LlmProviderConfig:
        credential_id = provider_config.credential_id if provider_config else None
        previous_config = dict(provider_config.config) if provider_config else None
        stripped_key = api_key.strip() if api_key else ""
        if remove_api_key and credential_id:
            await delete_credential(
                db, credential_id, owner_id=provider_config.owner_id
            )
            credential_id = None
        elif stripped_key:
            existing = (
                await load_credential(
                    db, credential_id, owner_id=provider_config.owner_id
                )
                if credential_id
                else None
            )
            credential = await store_credential(
                db,
                owner_id=provider_config.owner_id if provider_config else owner_id,
                kind=credential_kind,
                label=name,
                payload={"api_key": stripped_key},
                credential=existing,
            )
            credential_id = credential.id
        if provider_config is None:
            provider_config = LlmProviderConfig(name=name, adapter_type=adapter_type)
            provider_config.owner_id = owner_id
            settings.providers.append(provider_config)
        provider_config.name = name
        provider_config.trust_level = trust_level
        provider_config.pseudonymization_enabled = pseudonymization_enabled
        provider_config.memory_policy = memory_policy
        provider_config.credential_id = credential_id
        provider_config.config = config
        if previous_config is not None and (
            previous_config != config or bool(stripped_key) or remove_api_key
        ):
            provider_config.models_last_synced_at = None
            for item in settings.models:
                if (
                    item.provider_id == provider_config.id
                    and item.origin == ModelOrigin.DISCOVERED
                ):
                    item.availability = ModelAvailability.MISSING
            clear_invalid_defaults(settings)
        await cls.save(db, settings, owner_id)
        return provider_config

    @classmethod
    async def set_provider_enabled(
        cls,
        db: AsyncSession,
        settings: AiSettingsDocument,
        owner_id: UUID | None,
        provider_id: UUID,
        enabled: bool,
    ) -> None:
        provider(settings, provider_id).enabled = enabled
        clear_invalid_defaults(settings)
        await cls.save(db, settings, owner_id)
        await db.commit()

    @classmethod
    async def set_provider_memory_policy(
        cls,
        db: AsyncSession,
        settings: AiSettingsDocument,
        owner_id: UUID | None,
        provider_id: UUID,
        memory_policy: MemoryPolicy,
    ) -> None:
        provider(settings, provider_id).memory_policy = memory_policy
        await cls.save(db, settings, owner_id)
        await db.commit()

    @classmethod
    async def set_provider_policy(
        cls,
        db: AsyncSession,
        settings: AiSettingsDocument,
        owner_id: UUID | None,
        provider_id: UUID,
        *,
        trust_level: TrustLevel,
        pseudonymization_enabled: bool,
        memory_policy: MemoryPolicy,
    ) -> None:
        value = provider(settings, provider_id)
        value.trust_level = trust_level
        value.pseudonymization_enabled = pseudonymization_enabled
        value.memory_policy = memory_policy
        await cls.save(db, settings, owner_id)
        await db.commit()

    @classmethod
    async def set_ai_enabled(
        cls,
        db: AsyncSession,
        settings: AiSettingsDocument,
        owner_id: UUID | None,
        enabled: bool,
    ) -> None:
        settings.enabled = enabled
        await cls.save(db, settings, owner_id)
        await db.commit()

    @staticmethod
    async def set_personal_providers_enabled(
        db: AsyncSession, settings: AiSettings, enabled: bool
    ) -> None:
        settings.personal_providers_enabled = enabled
        await SettingsStore.save(db, settings)
        await db.commit()

    @staticmethod
    async def provider_sync_fingerprint(
        db: AsyncSession,
        value: LlmProviderConfig,
        *,
        load_credential: CredentialLoader,
    ) -> tuple[str, UUID | None, dict[str, Any], bytes | str | None]:
        credential = (
            await load_credential(
                db,
                value.credential_id,
                owner_id=value.owner_id,
                populate_existing=True,
            )
            if value.credential_id
            else None
        )
        credential_fingerprint = credential.encrypted_payload if credential else None
        if credential and value.adapter_type == "openai-codex":
            credential_fingerprint = str(credential.metadata_json.get("accountId") or "")
        return (
            value.adapter_type,
            value.credential_id,
            dict(value.config),
            credential_fingerprint,
        )

    @classmethod
    async def synchronize_models(
        cls,
        db: AsyncSession,
        provider_id: UUID,
        *,
        load_settings: ScopedSettingsLoader,
        discover_models: ModelDiscovery,
        load_credential: CredentialLoader,
    ) -> tuple[AiSettingsDocument, UUID | None]:
        settings, owner_id = await load_settings(False)
        snapshot_provider = provider(settings, provider_id).model_copy(deep=True)
        snapshot = await cls.provider_sync_fingerprint(
            db, snapshot_provider, load_credential=load_credential
        )
        try:
            discovered = await discover_models(db, provider=snapshot_provider)
        except ProviderError:
            raise
        except Exception as exc:
            raise ProviderDiscoveryError from exc

        settings, current_owner_id = await load_settings(True)
        if current_owner_id != owner_id:
            raise ProviderSettingsError("provider.configurationChanged")
        current_provider = provider(settings, provider_id)
        if snapshot != await cls.provider_sync_fingerprint(
            db, current_provider, load_credential=load_credential
        ):
            raise ProviderSettingsError("provider.configurationChanged")
        cls._reconcile_discovered_models(settings, current_provider, discovered)
        await cls.save(db, settings, owner_id)
        await db.commit()
        return settings, owner_id

    @staticmethod
    def _reconcile_discovered_models(
        settings: AiSettingsDocument,
        provider_config: LlmProviderConfig,
        discovered: list[DiscoveredModel],
    ) -> None:
        synced_at = datetime.now(UTC)
        discovered_by_id = {item.id: item for item in discovered}
        existing_by_id = {
            item.model_id: item
            for item in settings.models
            if item.provider_id == provider_config.id
        }
        for external_id, discovered_model in discovered_by_id.items():
            value = existing_by_id.get(external_id)
            if value is None:
                name = discovered_model.metadata.get("name")
                discovered_context = _discovered_context_window(
                    discovered_model.metadata
                )
                context_window = discovered_context or 128_000
                max_output = min(16_384, max(1, context_window // 4))
                supports_tools = discovered_model.metadata.get("supports_tools")
                supports_parallel = discovered_model.metadata.get(
                    "supports_parallel_tool_calls"
                )
                input_modalities = discovered_model.metadata.get("input_modalities")
                supports_image = discovered_model.metadata.get("supports_image_input")
                is_lemonade = provider_config.adapter_type == "lemonade"
                settings.models.append(
                    ModelConfig(
                        provider_id=provider_config.id,
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
                        supports_streaming=(
                            ModelCapability.CHAT in discovered_model.capabilities
                        ),
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
            elif value.origin == ModelOrigin.DISCOVERED:
                value.availability = ModelAvailability.AVAILABLE
                value.last_seen_at = synced_at
                value.discovery_metadata = discovered_model.metadata
                _apply_discovered_context_window(
                    value, _discovered_context_window(discovered_model.metadata)
                )
                input_modalities = discovered_model.metadata.get("input_modalities")
                supports_image = discovered_model.metadata.get("supports_image_input")
                if isinstance(input_modalities, list):
                    value.input_modalities = {
                        ModelInputModality(item) for item in input_modalities
                    }
                elif isinstance(supports_image, bool):
                    value.input_modalities = {ModelInputModality.TEXT}
                    if supports_image:
                        value.input_modalities.add(ModelInputModality.IMAGE)
                if (
                    provider_config.adapter_type == "lemonade"
                    or discovered_model.metadata.get("capabilities_authoritative") is True
                ):
                    value.capabilities = discovered_model.capabilities
                    supports_parallel = discovered_model.metadata.get(
                        "supports_parallel_tool_calls"
                    )
                    supports_tools = discovered_model.metadata.get("supports_tools")
                    value.supports_tools = (
                        supports_tools if isinstance(supports_tools, bool) else False
                    )
                    value.supports_parallel_tool_calls = (
                        supports_parallel
                        if value.supports_tools
                        and isinstance(supports_parallel, bool)
                        else False
                    )
                    value.supports_streaming = (
                        ModelCapability.CHAT in discovered_model.capabilities
                    )
        for value in settings.models:
            if (
                value.provider_id == provider_config.id
                and value.origin == ModelOrigin.DISCOVERED
                and value.model_id not in discovered_by_id
            ):
                value.availability = ModelAvailability.MISSING
        provider_config.models_last_synced_at = synced_at
        clear_invalid_defaults(settings)

    @classmethod
    async def create_manual_model(
        cls,
        db: AsyncSession,
        settings: AiSettingsDocument,
        owner_id: UUID | None,
        provider_id: UUID,
        values: dict[str, Any],
        enabled: bool | None,
    ) -> None:
        provider(settings, provider_id)
        _ensure_unique_model_id(
            settings, provider_id=provider_id, model_id=values["model_id"]
        )
        settings.models.append(
            ModelConfig(
                provider_id=provider_id,
                origin=ModelOrigin.MANUAL,
                enabled=enabled if enabled is not None else True,
                **values,
            )
        )
        await cls.save(db, settings, owner_id)
        await db.commit()

    @classmethod
    async def update_model(
        cls,
        db: AsyncSession,
        settings: AiSettingsDocument,
        owner_id: UUID | None,
        model_id: UUID,
        values: dict[str, Any],
        enabled: bool | None,
    ) -> None:
        current = model(settings, model_id)
        if (
            current.origin == ModelOrigin.DISCOVERED
            and values["model_id"] != current.model_id
        ):
            raise ProviderSettingsError(
                "provider.discoveredModelIdImmutable", field="modelId"
            )
        _ensure_unique_model_id(
            settings,
            provider_id=current.provider_id,
            model_id=values["model_id"],
            exclude_id=current.id,
        )
        candidate = current.model_dump()
        candidate.update(values)
        if enabled is not None:
            if enabled and current.availability == ModelAvailability.MISSING:
                raise ProviderSettingsError("provider.modelUnavailable")
            candidate["enabled"] = enabled
        try:
            validated = ModelConfig.model_validate(candidate)
        except ValidationError as exc:
            raise ProviderSettingsError("provider.configurationInvalid") from exc
        settings.models[settings.models.index(current)] = validated
        clear_invalid_defaults(settings)
        await cls.save(db, settings, owner_id)
        await db.commit()

    @classmethod
    async def set_model_enabled(
        cls,
        db: AsyncSession,
        settings: AiSettingsDocument,
        owner_id: UUID | None,
        model_id: UUID,
        enabled: bool,
    ) -> None:
        value = model(settings, model_id)
        if enabled and value.availability == ModelAvailability.MISSING:
            raise ProviderSettingsError("provider.modelUnavailable")
        value.enabled = enabled
        clear_invalid_defaults(settings)
        await cls.save(db, settings, owner_id)
        await db.commit()

    @classmethod
    async def delete_model(
        cls,
        db: AsyncSession,
        settings: AiSettingsDocument,
        owner_id: UUID | None,
        model_id: UUID,
    ) -> None:
        value = model(settings, model_id)
        settings.models.remove(value)
        clear_removed_defaults(settings, {value.id})
        statement = update(ChatSession).where(
            ChatSession.model_config_id == value.id
        )
        if owner_id is not None:
            statement = statement.where(ChatSession.user_id == owner_id)
        await db.execute(statement.values(model_config_id=None))
        clear_invalid_defaults(settings)
        await cls.save(db, settings, owner_id)
        await db.commit()

    @classmethod
    async def set_default_model(
        cls,
        db: AsyncSession,
        settings: AiSettingsDocument,
        validation_settings: AiSettingsBase,
        owner_id: UUID | None,
        model_id: UUID | None,
    ) -> None:
        if model_id is not None:
            value = available_model(validation_settings, model_id)
            if value is None:
                raise ProviderSettingsError("provider.modelUnavailable")
            if ModelCapability.CHAT not in value.capabilities:
                raise ProviderSettingsError("provider.modelCapabilityUnavailable")
            if not value.supports_tools:
                raise ProviderSettingsError("session.modelToolsUnavailable")
        settings.default_model_id = model_id
        await cls.save(db, settings, owner_id)
        await db.commit()

    @classmethod
    async def set_default_models(
        cls,
        db: AsyncSession,
        settings: AiSettingsDocument,
        validation_settings: AiSettingsBase,
        owner_id: UUID | None,
        values: dict[str, UUID | None],
        fields_set: set[str],
    ) -> None:
        for field, (api_field, capability) in DEFAULT_MODEL_FIELDS.items():
            model_id = values[field]
            if model_id is None:
                continue
            value = available_model(validation_settings, model_id)
            if value is None:
                raise ProviderSettingsError(
                    "provider.modelUnavailable", field=api_field
                )
            if capability not in value.capabilities:
                raise ProviderSettingsError(
                    "provider.modelCapabilityUnavailable", field=api_field
                )
            if (
                field in {"default_model_id", "skill_creation_model_id"}
                and not value.supports_tools
            ):
                raise ProviderSettingsError(
                    "session.modelToolsUnavailable", field=api_field
                )
            if field == "vision_model_id" and not value.supports_image_input:
                raise ProviderSettingsError(
                    "provider.modelCapabilityUnavailable", field=api_field
                )
            if field == "video_model_id" and not video_analysis_model_available(
                validation_settings, model_id
            ):
                raise ProviderSettingsError(
                    "provider.modelCapabilityUnavailable", field=api_field
                )
            if capability != ModelCapability.CHAT and not media_model_available(
                validation_settings, capability, model_id
            ):
                raise ProviderSettingsError(
                    "provider.modelCapabilityUnavailable", field=api_field
                )
            if field == "dreaming_model_id" and not value.supports_tools:
                raise ProviderSettingsError(
                    "memory.dreamingModelToolsUnavailable", field=api_field
                )
        additive_fields = {
            "vision_model_id",
            "skill_creation_model_id",
            "video_model_id",
            "image_generation_model_id",
            "speech_generation_model_id",
            "audio_generation_model_id",
            "audio_transcription_model_id",
        }
        for field in DEFAULT_MODEL_FIELDS:
            if field in additive_fields and field not in fields_set:
                continue
            setattr(settings, field, values[field])
        await cls.save(db, settings, owner_id)
        await db.commit()

    @classmethod
    async def delete_provider(
        cls,
        db: AsyncSession,
        settings: AiSettingsDocument,
        owner_id: UUID | None,
        provider_id: UUID,
        *,
        delete_credential: CredentialDelete,
    ) -> None:
        value = provider(settings, provider_id)
        removed_model_ids = {
            item.id for item in settings.models if item.provider_id == provider_id
        }
        settings.providers = [
            item for item in settings.providers if item.id != provider_id
        ]
        settings.models = [
            item for item in settings.models if item.provider_id != provider_id
        ]
        clear_removed_defaults(settings, removed_model_ids)
        await _clear_sessions(db, removed_model_ids, owner_id)
        clear_invalid_defaults(settings)
        if value.credential_id:
            await delete_credential(
                db, value.credential_id, owner_id=value.owner_id
            )
        await cls.save(db, settings, owner_id)
        await db.commit()

    @classmethod
    async def connect_codex(
        cls,
        db: AsyncSession,
        token: dict[str, Any],
        owner_id: UUID | None,
        *,
        load_settings: ScopedSettingsLoader,
        store_credential: CredentialStore,
    ) -> LlmProviderConfig:
        existing = await db.scalar(
            scoped_credentials(owner_id).where(Credential.kind == "codex-oauth")
        )
        credential = await store_credential(
            db,
            owner_id=owner_id,
            kind="codex-oauth",
            label="ChatGPT Codex",
            payload=token,
            metadata={"accountId": token.get("account_id")},
            credential=existing,
        )
        settings, current_owner_id = await load_settings(True)
        if current_owner_id != owner_id:
            raise RuntimeError("Codex OAuth scope changed")
        value = next(
            (
                item
                for item in settings.providers
                if item.adapter_type == "openai-codex"
            ),
            None,
        )
        if value is None:
            value = LlmProviderConfig(
                name="ChatGPT Codex", adapter_type="openai-codex"
            )
            value.owner_id = owner_id
            settings.providers.append(value)
        value.credential_id = credential.id
        await cls.save(db, settings, owner_id)
        await db.commit()
        return value

    @classmethod
    async def disconnect_codex(
        cls,
        db: AsyncSession,
        settings: AiSettingsDocument,
        owner_id: UUID | None,
        credentials: list[Credential],
    ) -> None:
        credential_ids = {item.id for item in credentials}
        for value in settings.providers:
            if value.credential_id in credential_ids:
                value.credential_id = None
        clear_invalid_defaults(settings)
        for credential in credentials:
            await db.delete(credential)
        await cls.save(db, settings, owner_id)
        await db.commit()
