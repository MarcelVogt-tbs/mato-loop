from uuid import UUID

from mato_loop_backend.providers.settings_service import available_model
from mato_loop_backend.sessions.service import SessionServiceError
from mato_loop_backend.settings import (
    SettingsStore,
    load_effective_agent_profiles,
    load_effective_ai,
)
from mato_loop_backend.settings.effective import personal_settings_allowed
from mato_loop_backend.settings.models import ModelCapability
from mato_loop_backend.settings.session_profiles import (
    DEFAULT_TEMPLATE_ID,
    ChatModelOverride,
    SessionProfileConfig,
    SessionTemplatesSettings,
    UserSessionTemplatesSettings,
)


def validate_chat_model(ai, choice: ChatModelOverride, inherited_model_id=None, *, defer_unavailable=False):
    model_id = choice.model_id or inherited_model_id or ai.default_model_id
    model = available_model(ai, model_id, ModelCapability.CHAT) if model_id else None
    if not defer_unavailable and choice.model_id and (model is None or not model.supports_tools):
        raise SessionServiceError(422, "session.modelUnavailable", field="modelId")
    if choice.reasoning_effort is not None and model is not None:
        from mato_loop_backend.providers.reasoning import reasoning_profile

        reasoning = reasoning_profile(model.model_id, model.discovery_metadata)
        if reasoning is None or choice.reasoning_effort not in reasoning.options:
            raise SessionServiceError(422, "validation.invalidChoice", field="reasoningEffort")


async def validate_session_profile(db, user_id, config: SessionProfileConfig, *, instance=False, model_id=None, defer_unavailable=False, previous: SessionProfileConfig | None = None):
    profiles = await load_effective_agent_profiles(db, user_id, include_personal=not instance, include_integrations=False)
    requested = set(config.agent_ids or []) | set(config.agent_models)
    if not instance and requested - {profile.id for profile in profiles.profiles}:
        profiles = await load_effective_agent_profiles(db, user_id)
    available = {profile.id for profile in profiles.profiles if profile.enabled}
    if set(config.agent_models) - available or config.agent_ids is not None and set(config.agent_ids) - available:
        raise SessionServiceError(422, "agentProfile.selectedUnavailable", field="agentIds")
    ai = await load_effective_ai(db, user_id, include_personal=not instance)
    for agent_id, choice in config.agent_models.items():
        unchanged_model = previous is not None and agent_id in previous.agent_models and choice.model_id == previous.agent_models[agent_id].model_id
        profile_choice = ai.profile_models.get(agent_id, ChatModelOverride())
        validate_chat_model(ai, choice, model_id or profile_choice.model_id, defer_unavailable=defer_unavailable or unchanged_model)
    return ai


async def available_templates(db, user_id):
    instance = await SettingsStore.load_optional(db, SessionTemplatesSettings) or SessionTemplatesSettings()
    personal = await SettingsStore.load_optional(db, UserSessionTemplatesSettings, user_id=user_id) or UserSessionTemplatesSettings()
    allowed = await personal_settings_allowed(db, user_id, instance.personal_settings_enabled)
    return [*( (item, "instance") for item in instance.templates), *((item, "personal") for item in personal.templates if allowed)]


async def load_template(db, user_id, template_id: UUID | None):
    template_id = template_id or DEFAULT_TEMPLATE_ID
    template = next((item for item, _ in await available_templates(db, user_id) if item.id == template_id), None)
    if template is None:
        raise SessionServiceError(404, "sessionTemplate.notFound")
    return template
