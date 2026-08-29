from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.models import ExecutionAgent
from mato_loop_backend.settings.models import (
    DEFAULT_AGENT_PROFILE_ID,
    AgentProfile,
    AgentProfilePermissions,
    AgentProfilesSettings,
    AiSettings,
    IntegrationAdapterAvailability,
    IntegrationSettings,
    ModelAvailability,
    ToolSettings,
    TrustLevel,
    UserAgentProfilesSettings,
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

_BUILTIN_TOOL_FIELDS = (
    "shell_prefixes",
    "generate_image",
    "edit_image",
    "generate_speech",
    "generate_audio",
    "transcribe_audio",
    "analyze_video",
    "update_agent_identity",
)
_INTEGRATION_TOOL_FIELDS = (
    "resource_labels",
    "default_calendar",
    "default_task_list",
    "web_search",
)


async def load_effective_ai(db: AsyncSession, user_id: UUID) -> AiSettings:
    instance = (await SettingsStore.load(db, AiSettings)).model_copy(deep=True)
    personal = await SettingsStore.load_optional(db, UserAiSettings, user_id=user_id)
    for provider in instance.providers:
        provider.owner_id = None
    if not instance.personal_providers_enabled or personal is None:
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
    instance.title_vision_fallback_enabled = personal.title_vision_fallback_enabled
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


def _merge_permissions(
    baseline: AgentProfilePermissions,
    override: AgentProfilePermissions,
    *,
    include_policies: bool = True,
) -> AgentProfilePermissions:
    data = baseline.model_dump()
    if include_policies:
        data["policies"].update(override.policies)
    for integration_id, policies in override.integration_policies.items():
        data["integration_policies"].setdefault(integration_id, {}).update(policies)
    for integration_id, resources in override.resource_policies.items():
        target = data["resource_policies"].setdefault(integration_id, {})
        for resource_key, policies in resources.items():
            target.setdefault(resource_key, {}).update(policies)
    return AgentProfilePermissions.model_validate(data)


async def load_effective_tools(
    db: AsyncSession, user_id: UUID, trust_level: TrustLevel = TrustLevel.PUBLIC
) -> ToolSettings:
    instance = (await SettingsStore.load(db, ToolSettings)).model_copy(deep=True)
    personal = await SettingsStore.load_optional(db, UserToolSettings, user_id=user_id)
    if personal is None:
        return instance.select_trust(trust_level)

    data = instance.model_dump()
    personal_data = personal.model_dump()
    for field in _INTEGRATION_TOOL_FIELDS:
        data[field] = personal_data[field]
    if instance.personal_settings_enabled and personal.personal_tool_defaults_enabled:
        for field in _BUILTIN_TOOL_FIELDS:
            data[field] = personal_data[field]
    for field in ("private_permissions", "public_permissions"):
        data[field] = _merge_permissions(
            getattr(instance, field),
            getattr(personal, field),
            include_policies=(
                instance.personal_settings_enabled
                and personal.personal_tool_defaults_enabled
            ),
        ).model_dump()
    effective = ToolSettings.model_validate(data)
    effective.tool_availability = dict(instance.tool_availability)
    effective.resource_availability = {
        key: dict(value) for key, value in instance.resource_availability.items()
    }
    effective.web_fetch.user_agent = instance.web_fetch.user_agent
    effective.web_fetch.max_response_mib = instance.web_fetch.max_response_mib
    return effective.select_trust(trust_level)


def personal_tool_settings_snapshot(
    instance: ToolSettings,
    personal: UserToolSettings | None,
) -> UserToolSettings:
    data = (
        personal.model_dump()
        if personal is not None
        else instance.model_dump(exclude={"personal_settings_enabled"})
    )
    instance_data = instance.model_dump()
    for field in _BUILTIN_TOOL_FIELDS:
        data[field] = instance_data[field]
    if personal is None:
        data["private_permissions"] = AgentProfilePermissions().model_dump()
        data["public_permissions"] = AgentProfilePermissions().model_dump()
    data["personal_tool_defaults_enabled"] = True
    return UserToolSettings.model_validate(data)


def personal_integration_tool_settings(instance: ToolSettings) -> UserToolSettings:
    return UserToolSettings.model_validate(
        {
            **instance.model_dump(include=set(_INTEGRATION_TOOL_FIELDS)),
            "personal_tool_defaults_enabled": False,
        }
    )


class AgentProfileUnavailable(Exception):
    pass


async def load_effective_agent_profiles(
    db: AsyncSession, user_id: UUID, *, for_update: bool = False
) -> AgentProfilesSettings:
    instance = (
        await SettingsStore.load(db, AgentProfilesSettings, for_update=for_update)
    ).model_copy(deep=True)
    for profile in instance.profiles:
        profile.owner_id = None
    if not instance.personal_agents_enabled:
        return instance
    personal = await SettingsStore.load_optional(
        db, UserAgentProfilesSettings, user_id=user_id, for_update=for_update
    )
    if personal is not None:
        for profile in personal.model_copy(deep=True).profiles:
            profile.owner_id = user_id
            instance.profiles.append(profile)
    return instance


async def load_agent_profile(
    db: AsyncSession,
    user_id: UUID,
    profile_id: UUID | None,
    *,
    for_update: bool = False,
) -> AgentProfile:
    profile_id = profile_id or DEFAULT_AGENT_PROFILE_ID
    settings = await load_effective_agent_profiles(db, user_id, for_update=for_update)
    profile = next((item for item in settings.profiles if item.id == profile_id), None)
    if profile is None:
        raise AgentProfileUnavailable
    return profile


async def load_agent_profile_tools(
    db: AsyncSession,
    user_id: UUID,
    profile_id: UUID | None,
    trust_level: TrustLevel,
) -> tuple[ToolSettings, AgentProfile | None]:
    tools = await load_effective_tools(db, user_id, trust_level)
    if profile_id is None:
        return tools, None
    profile = await load_agent_profile(db, user_id, profile_id)
    tools._agent_permissions = (
        profile.private_permissions
        if trust_level == TrustLevel.PRIVATE
        else profile.public_permissions
    )
    return tools, profile
