from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.models import ExecutionAgent
from mato_loop_backend.settings.models import (
    AiSettings,
    IntegrationAdapterAvailability,
    IntegrationSettings,
    ModelAvailability,
    ToolSettings,
    UserAiSettings,
    UserIntegrationSettings,
    UserToolSettings,
)
from mato_loop_backend.settings.store import SettingsStore

_AI_DEFAULT_FIELDS = (
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
)


async def load_effective_ai(db: AsyncSession, user_id: UUID) -> AiSettings:
    instance = (await SettingsStore.load(db, AiSettings)).model_copy(deep=True)
    personal = await SettingsStore.load_optional(db, UserAiSettings, user_id=user_id)
    for provider in instance.providers:
        provider.owner_id = None
    if not instance.personal_providers_enabled or personal is None or not personal.enabled:
        return instance

    personal = personal.model_copy(deep=True)
    personal_provider_ids = {provider.id for provider in personal.providers}
    for provider in personal.providers:
        provider.owner_id = user_id
    instance.providers.extend(personal.providers)
    instance.models.extend(
        model for model in personal.models if model.provider_id in personal_provider_ids
    )

    providers = {provider.id: provider for provider in instance.providers}
    effective_model_ids = {
        model.id
        for model in instance.models
        if model.enabled
        and model.availability == ModelAvailability.AVAILABLE
        and (provider := providers.get(model.provider_id)) is not None
        and provider.enabled
        and not (
            provider.adapter_type == "openai-codex" and provider.credential_id is None
        )
    }
    for field in _AI_DEFAULT_FIELDS:
        personal_id = getattr(personal, field)
        if personal_id in effective_model_ids:
            setattr(instance, field, personal_id)
    return instance


async def load_effective_integrations(
    db: AsyncSession, user_id: UUID
) -> IntegrationSettings:
    instance = (await SettingsStore.load(db, IntegrationSettings)).model_copy(deep=True)
    for item in instance.instances:
        item.owner_id = None
    instance.instances = [
        item
        for item in instance.instances
        if instance.availability_for(item.adapter_type)
        != IntegrationAdapterAvailability.DISABLED
    ]

    personal = await SettingsStore.load_optional(
        db, UserIntegrationSettings, user_id=user_id
    )
    if personal is None:
        personal_instances = []
    else:
        personal_instances = personal.model_copy(deep=True).instances
    for item in personal_instances:
        if (
            instance.availability_for(item.adapter_type)
            != IntegrationAdapterAvailability.FULL
        ):
            continue
        item.owner_id = user_id
        instance.instances.append(item)

    agent_ids = {
        item.execution_agent_id
        for item in instance.instances
        if item.execution_agent_id is not None
    }
    if agent_ids:
        owned_agent_ids = set(
            (
                await db.scalars(
                    select(ExecutionAgent.id).where(
                        ExecutionAgent.id.in_(agent_ids),
                        ExecutionAgent.user_id == user_id,
                        ExecutionAgent.revoked_at.is_(None),
                    )
                )
            ).all()
        )
        instance.instances = [
            item
            for item in instance.instances
            if item.execution_agent_id is None
            or item.execution_agent_id in owned_agent_ids
        ]
    return instance


async def load_effective_tools(db: AsyncSession, user_id: UUID) -> ToolSettings:
    instance = (await SettingsStore.load(db, ToolSettings)).model_copy(deep=True)
    personal = await SettingsStore.load_optional(db, UserToolSettings, user_id=user_id)
    if not instance.personal_settings_enabled or personal is None:
        return instance
    effective = ToolSettings.model_validate(personal.model_dump())
    effective.personal_settings_enabled = instance.personal_settings_enabled
    effective.web_fetch.user_agent = instance.web_fetch.user_agent
    effective.web_fetch.max_response_mib = instance.web_fetch.max_response_mib
    effective.web_fetch.allowed_private_origins = list(
        instance.web_fetch.allowed_private_origins
    )
    return effective
