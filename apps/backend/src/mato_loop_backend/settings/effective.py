from collections.abc import Collection
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.models import ExecutionAgent
from mato_loop_backend.settings.models import (
    DEFAULT_AGENT_PROFILE_ID,
    AgentIdentitySettings,
    AgentProfile,
    AgentProfilePermissions,
    AgentProfilesSettings,
    AiSettings,
    EffectiveAgentIdentity,
    IntegrationAdapterAvailability,
    IntegrationSettings,
    ModelAvailability,
    PersonaProfile,
    ToolSettings,
    TrustLevel,
    UserAgentIdentitySettings,
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


def resolve_agent_persona(
    instance: AgentIdentitySettings,
    personal: UserAgentIdentitySettings | None,
    agent_profile: AgentProfile | None,
) -> PersonaProfile:
    instance_personas = {persona.id: persona for persona in instance.personas}
    fallback = instance_personas[instance.default_persona_id]
    selected_id = agent_profile.persona_id if agent_profile is not None else None

    if agent_profile is not None and agent_profile.owner_id is None:
        return instance_personas.get(selected_id, fallback)

    if selected_id in instance_personas:
        return instance_personas[selected_id]
    if not instance.personal_personas_enabled or personal is None:
        return fallback

    personal_personas = {persona.id: persona for persona in personal.personas}
    if selected_id in personal_personas:
        return personal_personas[selected_id]
    if personal.default_persona_id in personal_personas:
        return personal_personas[personal.default_persona_id]
    return instance_personas.get(personal.default_persona_id, fallback)


async def load_effective_agent_identity(
    db: AsyncSession,
    user_id: UUID,
    agent_profile: AgentProfile | None = None,
    *,
    for_update: bool = False,
) -> EffectiveAgentIdentity:
    instance = await SettingsStore.load(
        db, AgentIdentitySettings, for_update=for_update
    )
    personal = await SettingsStore.load_optional(
        db, UserAgentIdentitySettings, user_id=user_id, for_update=for_update
    )
    display_name = instance.display_name
    if (
        instance.personal_display_names_enabled
        and personal is not None
        and personal.display_name is not None
    ):
        display_name = personal.display_name

    persona = resolve_agent_persona(instance, personal, agent_profile).model_copy(deep=True)
    persona.owner_id = (
        user_id
        if personal is not None and any(item.id == persona.id for item in personal.personas)
        else None
    )
    return EffectiveAgentIdentity(
        display_name=display_name,
        persona=persona,
        personal_display_name_enabled=instance.personal_display_names_enabled,
        persona_editable=persona.owner_id == user_id,
    )


async def load_effective_personas(
    db: AsyncSession, user_id: UUID
) -> list[PersonaProfile]:
    instance = (await SettingsStore.load(db, AgentIdentitySettings)).model_copy(deep=True)
    for persona in instance.personas:
        persona.owner_id = None
    if not instance.personal_personas_enabled:
        return instance.personas
    personal = await SettingsStore.load_optional(
        db, UserAgentIdentitySettings, user_id=user_id
    )
    if personal is not None:
        for persona in personal.model_copy(deep=True).personas:
            persona.owner_id = user_id
            instance.personas.append(persona)
    return instance.personas


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
    from mato_loop_backend.integrations.registry import integration_adapter

    instance = (await SettingsStore.load(db, IntegrationSettings)).model_copy(deep=True)
    for item in instance.instances:
        item.owner_id = None
    instance.instances = [
        item
        for item in instance.instances
        if instance.availability_for(item.adapter_type)
        != IntegrationAdapterAvailability.DISABLED
        and not (
            (adapter := integration_adapter(item.integration_type, item.adapter_type))
            and adapter.personal_only
        )
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


def filter_agent_profile_integrations(
    settings: IntegrationSettings,
    profile: AgentProfile | None,
    *,
    allowed_ids: Collection[UUID | str] | None = None,
) -> IntegrationSettings:
    """Filter a runtime copy without changing integration enablement or policies."""
    effective = settings.model_copy(deep=True)
    ceiling = {str(value) for value in allowed_ids} if allowed_ids is not None else None
    effective.instances = [
        item for item in effective.instances
        if (ceiling is None or str(item.id) in ceiling)
        and (
            profile is None
            or profile.integration_availability.get(
                str(item.id), profile.integrations_enabled_by_default
            )
        )
    ]
    selected_ids = {item.id for item in effective.instances}
    effective._agent_profile_excluded_ids |= frozenset(
        str(item.id) for item in settings.instances
        if item.id not in selected_ids
    )
    return effective


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
    if not instance.tool_availability.get("browser", True):
        from mato_loop_backend.integrations.browser import BROWSER_TOOL_ACTIONS

        instance.tool_availability.update(dict.fromkeys(BROWSER_TOOL_ACTIONS, False))
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
    effective.web_fetch.user_agent = instance.web_fetch.user_agent
    effective.web_fetch.max_response_mib = instance.web_fetch.max_response_mib
    effective.web_fetch.public_addresses_only = (
        instance.web_fetch.public_addresses_only
    )
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
    tools._agent_profile = profile
    tools._agent_permissions = (
        profile.private_permissions
        if trust_level == TrustLevel.PRIVATE
        else profile.public_permissions
    )
    return tools, profile
