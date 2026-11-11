"""Personal assistant bindings; preferences belong to the user's ordinary skill package."""

from __future__ import annotations

import json
from collections.abc import Collection
from typing import Any, ClassVar
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.automations import AutomationError, AutomationService
from mato_loop_backend.models import Automation, ChatSession, UserAccount
from mato_loop_backend.sessions.archive import is_session_archived
from mato_loop_backend.settings import SettingsStore
from mato_loop_backend.settings.models import SettingsDocument, SettingsScope
from mato_loop_backend.skills import SkillError, SkillService

ASSISTANT_SKILL_ID = UUID("00000000-0000-4000-8000-000000000103")
ASSISTANT_TASK = (
    "Load skill 00000000-0000-4000-8000-000000000103 and perform its scheduled review."
)


class AssistantSettings(SettingsDocument):
    settings_key: ClassVar[str] = "assistant"
    settings_scope: ClassVar[SettingsScope] = SettingsScope.USER

    session_id: UUID | None = None
    automation_id: UUID | None = None


async def assistant_initialized(db: AsyncSession, user_id: UUID) -> bool:
    try:
        skill = next((item for item in await SkillService.list(db, user_id)
                      if item.id == ASSISTANT_SKILL_ID and item.owner_id == user_id and item.enabled), None)
        if skill is None:
            return False
        package = await SkillService.package(skill)
        preferences = json.loads(package.files["references/preferences.json"].content)
    except (SkillError, KeyError, ValueError, UnicodeError):
        return False
    return (
        isinstance(preferences, dict)
        and type(preferences.get("version")) is int and preferences["version"] == 1
        and isinstance(preferences.get("sources"), list) and bool(preferences["sources"])
        and all(isinstance(source, dict) for source in preferences["sources"])
        and ("instructions" not in preferences or isinstance(preferences["instructions"], str))
    )


async def require_assistant_initialized(db: AsyncSession, automation: Automation) -> None:
    """Guard only the owner's assigned assistant automation, without granting source access."""
    settings = await SettingsStore.load_optional(db, AssistantSettings, user_id=automation.user_id)
    if settings is not None and settings.automation_id == automation.id:
        if not await assistant_initialized(db, automation.user_id):
            raise AutomationError("validation.required", field="assistantConfiguration")


class AssistantService:
    @staticmethod
    async def assigned_session(db: AsyncSession, user_id: UUID, settings: AssistantSettings) -> ChatSession | None:
        if settings.session_id is None:
            return None
        item = await db.scalar(select(ChatSession).where(
            ChatSession.id == settings.session_id, ChatSession.user_id == user_id,
            ChatSession.session_type == "chat",
        ).with_for_update().execution_options(populate_existing=True))
        if item is None:
            raise AutomationError("automation.executionSessionMissing")
        if item.parent_session_id is not None:
            raise AutomationError("session.readOnly")
        if await is_session_archived(db, item):
            raise AutomationError("session.archived")
        return item

    @staticmethod
    async def setup(
        db: AsyncSession, user: UserAccount, *, command_session_id: UUID | None = None,
    ) -> dict[str, Any]:
        """Prepare the binding in the caller's transaction without committing it."""
        item = await ensure_personal_chat(db, user.id, command_session_id=command_session_id)
        settings = await SettingsStore.load(db, AssistantSettings, user_id=user.id, for_update=True)
        if settings.automation_id is None:
            automation = await AutomationService.create(
                db, user_id=user.id, session_id=item.id, name="Persönlicher Assistent",
                task=ASSISTANT_TASK, enabled=False,
                schedules=[{"cron": "*/15 * * * *", "timezone": "UTC", "enabled": True}],
            )
            settings.automation_id = automation.id
        else:
            automation = await AutomationService.owned(db, settings.automation_id, user.id)
            if automation.execution_session_id != item.id:
                raise AutomationError("http.conflict")
        await SkillService.set_enabled(db, user.id, ASSISTANT_SKILL_ID, True)
        await SettingsStore.save(db, settings, user_id=user.id)
        return await AssistantService.get(db, user.id)

    @staticmethod
    async def get(db: AsyncSession, user_id: UUID) -> dict[str, Any]:
        settings = await SettingsStore.load(db, AssistantSettings, user_id=user_id)
        result = {
            "sessionId": str(settings.session_id) if settings.session_id else None,
            "automationId": str(settings.automation_id) if settings.automation_id else None,
            "initialized": await assistant_initialized(db, user_id), "enabled": False,
        }
        if settings.automation_id is not None:
            try:
                item = await AssistantService.assigned_session(db, user_id, settings)
                automation = await AutomationService.owned(db, settings.automation_id, user_id)
                if item is None or automation.execution_session_id != item.id:
                    raise AutomationError("automation.executionSessionMissing")
                result["enabled"] = automation.enabled
            except AutomationError as exc:
                result["errorCode"] = exc.code
        return result


async def ensure_personal_chat(
    db: AsyncSession, user_id: UUID, *, command_session_id: UUID | None = None,
) -> ChatSession:
    """Ensure an ordinary owner-bound chat without enabling skills or automation; caller commits."""
    observed = await SettingsStore.load(db, AssistantSettings, user_id=user_id)
    observed_session_id = observed.session_id
    if command_session_id is not None and observed_session_id not in {None, command_session_id}:
        raise AutomationError("http.conflict")
    # Deletion and command admission already lock sessions before assistant settings.
    # Never wait for an existing session while holding the settings scope lock.
    item = await AssistantService.assigned_session(db, user_id, observed) if observed_session_id else None
    settings = await SettingsStore.load(db, AssistantSettings, user_id=user_id, for_update=True)
    if settings.session_id != observed_session_id:
        raise AutomationError("http.conflict")
    if settings.session_id is None and settings.automation_id is not None:
        raise AutomationError("automation.executionSessionMissing")
    if item is not None:
        return item
    from mato_loop_backend.files.storage import SessionStorage
    from mato_loop_backend.sessions.contracts import SessionCreateRequest
    from mato_loop_backend.sessions.service import SessionServiceError, create_session

    user = await db.get(UserAccount, user_id)
    if user is None or not user.is_active:
        raise SessionServiceError(403, "permission.denied")

    async def serialize(_db, session, **_kwargs):
        return {"id": str(session.id)}

    async def no_duplicate(*_args):
        return None

    created = await create_session(
        SessionCreateRequest(content=None), user, db,
        attachments=None, storage_factory=SessionStorage, session_serializer=serialize,
        message_serializer=lambda message: {}, duplicate_submission=no_duplicate, commit=False,
    )
    settings.session_id = UUID(created["session"]["id"])
    item = await db.get(ChatSession, settings.session_id)
    assert item is not None
    item.title = "Chat"
    item.title_generation_status = "skipped"
    await SettingsStore.save(db, settings, user_id=user_id)
    return item


async def clear_assistant_binding(
    db: AsyncSession, user_id: UUID, *,
    session_ids: Collection[UUID] = (), automation_ids: Collection[UUID] = (),
) -> None:
    """Clear only deleted owner-bound assignments; the deletion caller commits."""
    settings = await SettingsStore.load_optional(db, AssistantSettings, user_id=user_id, for_update=True)
    if settings is None:
        return
    if settings.session_id in session_ids:
        settings.session_id = None
        settings.automation_id = None
    elif settings.automation_id in automation_ids:
        settings.automation_id = None
    else:
        return
    await SettingsStore.save(db, settings, user_id=user_id)
