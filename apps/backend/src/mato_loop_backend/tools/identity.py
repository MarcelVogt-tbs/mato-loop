from __future__ import annotations

import mato_loop_backend.permissions.resolution as permissions_resolution
import mato_loop_backend.tools.access as tools_access
from mato_loop_backend.permissions.policy import agent_identity_permission, user_profile_permission
from mato_loop_backend.settings import (
    AgentProfileUnavailable,
    PersonaProfile,
    SettingsStore,
    ToolExecutionPolicy,
    TrustLevel,
    UserAgentIdentitySettings,
    UserProfileFact,
    UserProfileSettings,
    load_agent_profile,
    load_effective_agent_identity,
)
from mato_loop_backend.tools.actions import _USER_PROFILE_UPDATE, ToolAction
from mato_loop_backend.tools.arguments import (
    UpdateAgentIdentityArguments,
    UpdateUserProfileArguments,
)
from mato_loop_backend.tools.context import ToolContext as ToolContext
from mato_loop_backend.tools.contracts import ToolDefinition as ToolDefinition
from mato_loop_backend.tools.contracts import ToolResult as ToolResult
from mato_loop_backend.user_profile import effective_profile_fact


def _personal_settings_still_available(context: ToolContext) -> bool:
    if context.tool_call.name != "customize_matoloop":
        return True
    legacy_name = (
        "update_agent_identity"
        if context.tool_call.arguments.get("area") == "agent_identity"
        else "update_user_profile"
    )
    return (
        context.tool_settings.tool_availability.get("customize_matoloop", True)
        and context.tool_settings.tool_availability.get(legacy_name, True)
        and permissions_resolution.tool_execution_policy(
            context.tool_settings, "settings.personal.update", ToolExecutionPolicy.ASK
        ) != ToolExecutionPolicy.DISABLED
    )


async def _update_agent_identity(
    arguments: UpdateAgentIdentityArguments, context: ToolContext
) -> ToolResult:
    from mato_loop_backend.tools.catalog import tool_registry

    context.tool_settings = await tools_access.profile_tool_settings(context)
    policy = permissions_resolution.tool_execution_policy(
        context.tool_settings,
        "settings.agent_identity.update",
        tool_registry.action_default(
            "settings.agent_identity.update", context.tool_settings.trust_level
        ),
    )
    if policy == ToolExecutionPolicy.DISABLED:
        return ToolResult.error("tool.unavailable")
    try:
        profile = await load_agent_profile(
            context.db,
            context.session.user_id,
            getattr(context, "agent_profile_id", None)
            or getattr(context.session, "agent_profile_id", None),
        )
    except AgentProfileUnavailable:
        return ToolResult.error("tool.unavailable")
    effective_identity = await load_effective_agent_identity(
        context.db, context.session.user_id, profile
    )
    if (
        arguments.instructions is not None
        and context.agent_persona_id is not None
        and effective_identity.persona.id != context.agent_persona_id
    ):
        return ToolResult.error("tool.unavailable")
    approved_persona = effective_identity.persona.model_dump() if arguments.instructions is not None else None
    if (
        arguments.display_name is not None
        and not effective_identity.personal_display_name_enabled
    ) or (arguments.instructions is not None and not effective_identity.persona_editable):
        return ToolResult.error("tool.unavailable")
    approval_received = False
    if policy == ToolExecutionPolicy.ASK:
        allowed = await tools_access.allow(
            context,
            agent_identity_permission(
                context.session.user_id,
                changes_name=arguments.display_name is not None,
                changes_instructions=arguments.instructions is not None,
                persona_name=(
                    effective_identity.persona.name
                    if arguments.instructions is not None
                    else None
                ),
            ),
        )
        if not allowed:
            return ToolResult.error("permission.denied")
        approval_received = True

    context.tool_settings = await tools_access.profile_tool_settings(context)
    current_policy = permissions_resolution.tool_execution_policy(
        context.tool_settings,
        "settings.agent_identity.update",
        tool_registry.action_default(
            "settings.agent_identity.update", context.tool_settings.trust_level
        ),
    )
    if not _personal_settings_still_available(context) or current_policy == ToolExecutionPolicy.DISABLED or (
        current_policy == ToolExecutionPolicy.ASK and not approval_received
    ):
        return ToolResult.error("tool.unavailable")

    try:
        profile = await load_agent_profile(
            context.db,
            context.session.user_id,
            getattr(context, "agent_profile_id", None)
            or getattr(context.session, "agent_profile_id", None),
        )
    except AgentProfileUnavailable:
        return ToolResult.error("tool.unavailable")
    effective_identity = await load_effective_agent_identity(
        context.db,
        context.session.user_id,
        profile,
        for_update=True,
    )
    if (
        arguments.display_name is not None
        and not effective_identity.personal_display_name_enabled
    ) or (arguments.instructions is not None and not effective_identity.persona_editable):
        return ToolResult.error("tool.unavailable")
    if (
        approved_persona is not None
        and effective_identity.persona.model_dump() != approved_persona
    ):
        return ToolResult.error("tool.unavailable")

    current = await SettingsStore.load(
        context.db,
        UserAgentIdentitySettings,
        user_id=context.session.user_id,
        for_update=True,
    )
    if arguments.display_name is not None:
        current.display_name = arguments.display_name
    if arguments.instructions is not None:
        persona = next((item for item in current.personas if item.id == effective_identity.persona.id), None)
        if persona is None:
            name = f"Persona für {profile.name}"[:80]
            used = {item.name.casefold() for item in current.personas}
            candidate, suffix = name, 2
            while candidate.casefold() in used:
                candidate = f"{name[:70]} ({suffix})"
                suffix += 1
            persona = PersonaProfile(name=candidate, instructions=arguments.instructions)
            current.personas.append(persona)
        else:
            persona.instructions = arguments.instructions
        current.profile_personas[profile.id] = persona.id
        effective_identity.persona = persona
    await SettingsStore.save(context.db, current, user_id=context.session.user_id)
    display_name = arguments.display_name or effective_identity.display_name
    identity_payload = {
        "displayName": display_name,
    }
    return ToolResult.json(
        {
            "displayName": display_name,
            "personaName": effective_identity.persona.name,
            "instructionsUpdated": arguments.instructions is not None,
            "effectiveFrom": "next_run",
        },
        metadata={"agentIdentity": identity_payload},
    )


_PROFILE_FIELD_LABELS = {
    "full_name": "den vollständigen Namen",
    "preferred_name": "den bevorzugten Namen",
    "form_of_address": "die Anrede",
    "pronouns": "die Pronomen",
    "primary_language": "die bevorzugte Sprache",
    "primary_residence": "den Hauptwohnsitz",
}


def _resolved_profile_scope(
    requested_scope: str, model_trust_level: TrustLevel
) -> str:
    if requested_scope != "current_model":
        return requested_scope
    return (
        "private_models"
        if model_trust_level == TrustLevel.PRIVATE
        else "public_models"
    )


def _updated_profile_fact(
    current: UserProfileFact, *, model_scope: str, value: str | None
) -> UserProfileFact:
    if model_scope == "both_models":
        return UserProfileFact(both=value)
    private_value = effective_profile_fact(current, TrustLevel.PRIVATE)
    public_value = effective_profile_fact(current, TrustLevel.PUBLIC)
    if model_scope == "private_models":
        private_value = value
    else:
        public_value = value
    return UserProfileFact(
        private_only=private_value,
        public_only=public_value,
    )


async def _update_user_profile(
    arguments: UpdateUserProfileArguments, context: ToolContext
) -> ToolResult:
    capability = "settings.user_profile.update"
    context.tool_settings = await tools_access.profile_tool_settings(context)
    policy = permissions_resolution.tool_execution_policy(
        context.tool_settings, capability, ToolExecutionPolicy.ASK
    )
    if policy == ToolExecutionPolicy.DISABLED:
        return ToolResult.error("tool.unavailable")

    model_scope = _resolved_profile_scope(
        arguments.model_scope, context.model_trust_level
    )
    changed_fields = [
        field for field in _PROFILE_FIELD_LABELS if field in arguments.model_fields_set
    ]
    approval_received = False
    if policy == ToolExecutionPolicy.ASK:
        allowed = await tools_access.allow(
            context,
            user_profile_permission(
                context.session.user_id,
                field_labels=[_PROFILE_FIELD_LABELS[field] for field in changed_fields],
                model_scope=model_scope,
            ),
        )
        if not allowed:
            return ToolResult.error("permission.denied")
        approval_received = True

    context.tool_settings = await tools_access.profile_tool_settings(context)
    current_policy = permissions_resolution.tool_execution_policy(
        context.tool_settings, capability, ToolExecutionPolicy.ASK
    )
    if not _personal_settings_still_available(context) or current_policy == ToolExecutionPolicy.DISABLED or (
        current_policy == ToolExecutionPolicy.ASK and not approval_received
    ):
        return ToolResult.error("tool.unavailable")

    current = await SettingsStore.load(
        context.db,
        UserProfileSettings,
        user_id=context.session.user_id,
        for_update=True,
    )
    values = current.model_dump()
    for field in changed_fields:
        values[field] = _updated_profile_fact(
            getattr(current, field),
            model_scope=model_scope,
            value=getattr(arguments, field),
        ).model_dump()
    profile = UserProfileSettings.model_validate(values)
    await SettingsStore.save(context.db, profile, user_id=context.session.user_id)
    return ToolResult.json(
        {
            "updatedFields": changed_fields,
            "modelScope": model_scope,
            "effectiveFrom": "next_run",
        }
    )


UPDATE_AGENT_IDENTITY = ToolDefinition(
            "update_agent_identity",
            "Update the permitted personal agent name or the active personal persona after an explicit user request. "
            "Editing a personal persona affects all agents assigned to it. If a global persona is active, create a personal persona and assign it only to the current agent. "
            "Changes apply reliably from the next run.",
            UpdateAgentIdentityArguments,
            _update_agent_identity,
            required_capability="settings.agent_identity.update",
            group="agent",
            label="Persona ändern",
            icon="face",
            actions=(
                ToolAction(
                    "settings.agent_identity.update",
                    "Persona ändern",
                    ToolExecutionPolicy.ASK,
                    ToolExecutionPolicy.ASK,
                    category="write",
                    mutates=True,
                ),
            ),
        )

UPDATE_USER_PROFILE = ToolDefinition(
            "update_user_profile",
            "Persist or clear maintained user-profile fields only after an explicit latest-message request. "
            "Use current_model unless the user explicitly names another model scope; use both_models only "
            "when both private and public models are explicitly requested. Changes apply from the next run.",
            UpdateUserProfileArguments,
            _update_user_profile,
            group="agent",
            label="Persönliches Profil ändern",
            icon="manage_accounts",
            actions=(_USER_PROFILE_UPDATE,),
        )
