"""Assistant initialization is a normal model-driven command, not a policy bypass."""

from uuid import UUID

from mato_loop_backend.assistant import AssistantService, AssistantSettings
from mato_loop_backend.automations import AutomationError
from mato_loop_backend.models import UserAccount
from mato_loop_backend.sessions.command_contracts import Command, CommandProvider, command_arguments
from mato_loop_backend.skills import SkillError

ASSISTANT_INIT_COMMAND = Command(
    "assistant-init", "Assistent einrichten",
    "Persönlichen Assistenten und seine Quellen einrichten.",
    "Set up the user's personal assistant through normal tools and current profile rules. "
    "Load skill 00000000-0000-4000-8000-000000000103 and follow its setup workflow. "
    "Save preferences in the personal skill's references/preferences.json using edit_skill "
    "or save_skill. Reuse known choices and ask only for missing required choices. "
    "Prepare required permissions only in the assigned session; never copy another chat's grants. "
    "Use edit_automation to enable scheduling only after initialization and permission preparation. "
    "This request permits setup, not source actions or automatic grants. "
    "In planning-only profiles, explain without changing preferences or permissions.",
    icon="support_agent",
)


async def _availability(_db, session) -> dict[str, str | None]:
    return {ASSISTANT_INIT_COMMAND.name: None} if session.parent_session_id is None else {}


async def _prepare(db, session, command, content, agent_profile_id: UUID | None = None) -> dict:
    from mato_loop_backend.sessions.service import SessionServiceError
    from mato_loop_backend.settings import SettingsStore, load_agent_profile
    from mato_loop_backend.settings.models import PLAN_PROFILE_ID

    profile = await load_agent_profile(db, session.user_id, agent_profile_id or session.agent_profile_id)
    if PLAN_PROFILE_ID in {profile.id, profile.base_profile_id}:
        return {"prompt": "Explain personal assistant setup without changing settings, skills or permissions."}
    user = await db.get(UserAccount, session.user_id)
    if user is None:
        raise SessionServiceError(403, "permission.denied")
    try:
        settings = await SettingsStore.load(db, AssistantSettings, user_id=session.user_id)
        if settings.session_id is not None and settings.session_id != session.id:
            # The caller holds this conversation's lock. Redirect without locking
            # or mutating another existing conversation.
            binding = {
                "sessionId": str(settings.session_id),
                "automationId": str(settings.automation_id) if settings.automation_id else None,
            }
        else:
            binding = await AssistantService.setup(db, user, command_session_id=session.id)
    except (AutomationError, SkillError) as exc:
        raise SessionServiceError(409, exc.code, params=getattr(exc, "params", None)) from exc
    context = {"assistantSessionId": binding["sessionId"], "assistantAutomationId": binding["automationId"]}
    link = f"/chat?session={binding['sessionId']}"
    if str(session.id) != binding["sessionId"]:
        prompt = (
            f"Assistant setup is assigned to [the assistant session]({link}). "
            "Give this link and ask the user to run /assistant-init there. "
            "Do not configure preferences or prepare permissions in this conversation."
        )
    else:
        prompt = (
            f"{command.instructions}\n\nAssigned session: {binding['sessionId']}. "
            f"Assigned automation: {binding['automationId']}.\n\n{command_arguments(content)}"
        )
    return {"context": context, "prompt": prompt}


ASSISTANT_COMMAND_PROVIDER = CommandProvider(
    "assistant", (ASSISTANT_INIT_COMMAND,), _availability, _prepare, prepare_with_profile=_prepare,
)
