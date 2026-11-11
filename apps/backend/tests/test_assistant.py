import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from mato_loop_backend import assistant
from mato_loop_backend.assistant import ASSISTANT_SKILL_ID, AssistantService, AssistantSettings
from mato_loop_backend.automations import AutomationError
from mato_loop_backend.models import ChatSession
from mato_loop_backend.settings.models import GENERAL_PROFILE_ID


@pytest.mark.asyncio
@pytest.mark.parametrize("preferences,valid", [
    ({"version": 1, "sources": [{"tool": "custom_source", "resource": "chosen"}]}, True),
    ({"version": True, "sources": [{}]}, False),
    ({"version": 1, "sources": []}, False),
    ({"version": 1, "sources": ["source"]}, False),
    ({"version": 1, "sources": [{}], "instructions": None}, False),
    ([], False),
    ("invalid json", False),
])
async def test_preferences_require_minimal_generic_schema(monkeypatch, preferences, valid):
    user_id = uuid4()
    skill = SimpleNamespace(id=ASSISTANT_SKILL_ID, owner_id=user_id, enabled=True)
    monkeypatch.setattr(assistant.SkillService, "list", AsyncMock(return_value=[skill]))
    content = json.dumps(preferences).encode() if not isinstance(preferences, str) else preferences.encode()
    monkeypatch.setattr(assistant.SkillService, "package", AsyncMock(return_value=SimpleNamespace(
        files={"references/preferences.json": SimpleNamespace(content=content)},
    )))
    assert await assistant.assistant_initialized(AsyncMock(), user_id) is valid


@pytest.mark.asyncio
@pytest.mark.parametrize("personal,enabled", [(False, True), (True, False)])
async def test_initialization_requires_personally_owned_enabled_skill(monkeypatch, personal, enabled):
    user_id = uuid4()
    monkeypatch.setattr(assistant.SkillService, "list", AsyncMock(return_value=[SimpleNamespace(
        id=ASSISTANT_SKILL_ID, owner_id=user_id if personal else None, enabled=enabled,
    )]))
    assert not await assistant.assistant_initialized(AsyncMock(), user_id)


@pytest.mark.asyncio
async def test_initialization_guard_only_blocks_assigned_automation(monkeypatch):
    automation = SimpleNamespace(id=uuid4(), user_id=uuid4())
    settings = AssistantSettings()
    monkeypatch.setattr(assistant.SettingsStore, "load_optional", AsyncMock(return_value=settings))
    monkeypatch.setattr(assistant, "assistant_initialized", AsyncMock(return_value=False))
    await assistant.require_assistant_initialized(AsyncMock(), automation)
    settings.automation_id = automation.id
    with pytest.raises(AutomationError) as error:
        await assistant.require_assistant_initialized(AsyncMock(), automation)
    assert (error.value.code, error.value.field) == ("validation.required", "assistantConfiguration")


@pytest.mark.asyncio
async def test_uninitialized_assistant_fails_before_model_or_runtime(monkeypatch):
    from mato_loop_backend.agent.runtime import AgentRuntime

    automation = SimpleNamespace(id=uuid4(), user_id=uuid4(), execution_session_id=uuid4())
    run = SimpleNamespace(id=uuid4(), automation_id=automation.id, definition_snapshot={"task": "Review"})
    db = AsyncMock()
    db.scalar.return_value = run
    db.get.side_effect = [automation, ChatSession(id=automation.execution_session_id)]
    monkeypatch.setattr(assistant.SettingsStore, "load_optional", AsyncMock(
        return_value=AssistantSettings(automation_id=automation.id),
    ))
    monkeypatch.setattr(assistant.SkillService, "list", AsyncMock(return_value=[]))
    model, start, fail = AsyncMock(), AsyncMock(), AsyncMock()
    monkeypatch.setattr(assistant.AutomationService, "validate_model", model)
    monkeypatch.setattr(AgentRuntime, "start", start)
    monkeypatch.setattr(assistant.AutomationService, "_fail_start", fail)
    await assistant.AutomationService.process_next(db)
    assert (model.await_count, start.await_count, fail.await_args.args) == (
        0, 0, (db, run.id, "validation.required"),
    )


@pytest.mark.asyncio
async def test_setup_creates_stable_normal_session_and_disabled_automation(monkeypatch):
    import mato_loop_backend.sessions.service as sessions

    user = SimpleNamespace(id=uuid4(), is_active=True)
    item = ChatSession(id=uuid4(), user_id=user.id, parent_session_id=None)
    settings = AssistantSettings()
    db = AsyncMock()
    db.get.side_effect = [user, item]
    create = AsyncMock(return_value={"session": {"id": str(item.id)}})
    automation = SimpleNamespace(id=uuid4(), execution_session_id=item.id)
    monkeypatch.setattr(sessions, "create_session", create)
    monkeypatch.setattr(assistant.SettingsStore, "load", AsyncMock(return_value=settings))
    monkeypatch.setattr(assistant.SettingsStore, "save", AsyncMock())
    monkeypatch.setattr(AssistantService, "assigned_session", AsyncMock(return_value=item))
    monkeypatch.setattr(AssistantService, "get", AsyncMock(return_value={}))
    monkeypatch.setattr(assistant.AutomationService, "create", AsyncMock(return_value=automation))
    monkeypatch.setattr(assistant.AutomationService, "owned", AsyncMock(return_value=automation))
    monkeypatch.setattr(assistant.SkillService, "set_enabled", AsyncMock())
    await AssistantService.setup(db, user)
    await AssistantService.setup(db, user)
    request = create.call_args.args[0]
    assert "agent_profile_id" not in request.model_fields_set
    assert request.project_id is None and request.current_directory is None
    assert "model_id" not in request.model_fields_set
    create.assert_awaited_once()
    assert create.call_args.kwargs["commit"] is False
    assistant.AutomationService.create.assert_awaited_once()
    assert assistant.AutomationService.create.call_args.kwargs["enabled"] is False
    assert assistant.AutomationService.create.call_args.kwargs["schedules"] == [
        {"cron": "*/15 * * * *", "timezone": "UTC", "enabled": True},
    ]
    assert (settings.session_id, settings.automation_id) == (item.id, automation.id)
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_personal_chat_does_not_enable_skill_or_create_automation(monkeypatch):
    import mato_loop_backend.sessions.service as sessions

    user = SimpleNamespace(id=uuid4(), is_active=True)
    item = ChatSession(id=uuid4(), user_id=user.id, parent_session_id=None)
    settings = AssistantSettings()
    db = AsyncMock()
    db.get.side_effect = [user, item]
    db.scalar.return_value = item
    create = AsyncMock(return_value={"session": {"id": str(item.id)}})
    automation, skill = AsyncMock(), AsyncMock()
    monkeypatch.setattr(sessions, "create_session", create)
    monkeypatch.setattr(assistant.SettingsStore, "load", AsyncMock(return_value=settings))
    monkeypatch.setattr(assistant.SettingsStore, "save", AsyncMock())
    monkeypatch.setattr(assistant, "is_session_archived", AsyncMock(return_value=False))
    monkeypatch.setattr(assistant.AutomationService, "create", automation)
    monkeypatch.setattr(assistant.SkillService, "set_enabled", skill)

    assert await assistant.ensure_personal_chat(db, user.id) is item
    assert await assistant.ensure_personal_chat(db, user.id) is item

    assert (item.title, settings.session_id, settings.automation_id) == ("Chat", item.id, None)
    create.assert_awaited_once()
    automation.assert_not_awaited()
    skill.assert_not_awaited()
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("archived", [True, False])
async def test_unavailable_assignment_is_not_replaced(monkeypatch, archived):
    import mato_loop_backend.sessions.service as sessions

    user_id = uuid4()
    settings = AssistantSettings(session_id=uuid4(), automation_id=uuid4())
    db = AsyncMock()
    db.scalar.return_value = (
        ChatSession(id=settings.session_id, user_id=user_id, parent_session_id=None) if archived else None
    )
    create = AsyncMock()
    monkeypatch.setattr(sessions, "create_session", create)
    monkeypatch.setattr(assistant.SettingsStore, "load", AsyncMock(return_value=settings))
    monkeypatch.setattr(assistant, "is_session_archived", AsyncMock(return_value=archived))
    code = "session.archived" if archived else "automation.executionSessionMissing"
    with pytest.raises(AutomationError, match=code):
        await assistant.ensure_personal_chat(db, user_id)
    create.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("requested_plan", [False, True])
async def test_init_command_prepares_binding_and_directs_other_chat(monkeypatch, requested_plan):
    from mato_loop_backend.sessions.assistant_commands import ASSISTANT_INIT_COMMAND
    from mato_loop_backend.sessions.commands import prepare_command
    from mato_loop_backend.settings import PLAN_PROFILE_ID

    binding = {"sessionId": str(uuid4()), "automationId": str(uuid4())}
    setup = AsyncMock(return_value=binding)
    monkeypatch.setattr(AssistantService, "setup", setup)
    monkeypatch.setattr(assistant.SettingsStore, "load", AsyncMock(return_value=AssistantSettings()))
    profile = SimpleNamespace(id=PLAN_PROFILE_ID if requested_plan else GENERAL_PROFILE_ID, base_profile_id=None)
    monkeypatch.setattr("mato_loop_backend.settings.load_agent_profile", AsyncMock(
        side_effect=lambda _db, _user, profile_id: SimpleNamespace(id=profile_id, base_profile_id=profile.base_profile_id),
    ))
    session = SimpleNamespace(id=uuid4(), user_id=uuid4(), parent_session_id=None,
                              agent_profile_id=GENERAL_PROFILE_ID if requested_plan else PLAN_PROFILE_ID)
    result = await prepare_command(AsyncMock(), session, ASSISTANT_INIT_COMMAND, agent_profile_id=profile.id)
    if requested_plan:
        setup.assert_not_awaited()
    else:
        assert f"/chat?session={binding['sessionId']}" in result["prompt"]
        setup.assert_awaited_once()
        setup.reset_mock()
    profile.base_profile_id = PLAN_PROFILE_ID
    await prepare_command(AsyncMock(), session, ASSISTANT_INIT_COMMAND, agent_profile_id=profile.id)
    setup.assert_not_awaited()


@pytest.mark.asyncio
async def test_existing_assignment_redirect_does_not_run_setup(monkeypatch):
    from mato_loop_backend.sessions.assistant_commands import ASSISTANT_INIT_COMMAND, _prepare

    settings = AssistantSettings(session_id=uuid4())
    monkeypatch.setattr(assistant.SettingsStore, "load", AsyncMock(return_value=settings))
    monkeypatch.setattr("mato_loop_backend.settings.load_agent_profile", AsyncMock(
        return_value=SimpleNamespace(id=GENERAL_PROFILE_ID, base_profile_id=None),
    ))
    setup = AsyncMock(side_effect=AssertionError("Redirect must not lock another session"))
    monkeypatch.setattr(AssistantService, "setup", setup)
    session = SimpleNamespace(id=uuid4(), user_id=uuid4(), agent_profile_id=GENERAL_PROFILE_ID)
    result = await _prepare(AsyncMock(), session, ASSISTANT_INIT_COMMAND, None)
    assert f"/chat?session={settings.session_id}" in result["prompt"]


@pytest.mark.asyncio
async def test_personal_chat_locks_session_before_settings_and_revalidates(monkeypatch):
    settings = AssistantSettings(session_id=uuid4())
    session_locked = False

    async def assigned(*_args):
        nonlocal session_locked
        session_locked = True
        return SimpleNamespace(id=settings.session_id)

    async def load(*_args, for_update=False, **_kwargs):
        if for_update:
            assert session_locked
            return AssistantSettings(session_id=uuid4())
        return settings

    monkeypatch.setattr(assistant.SettingsStore, "load", load)
    monkeypatch.setattr(AssistantService, "assigned_session", assigned)
    with pytest.raises(AutomationError, match="http.conflict"):
        await assistant.ensure_personal_chat(AsyncMock(), uuid4())


@pytest.mark.asyncio
async def test_setup_api_commits_the_prepared_binding(monkeypatch):
    from mato_loop_backend.api.routes.assistant import setup_assistant

    result = {"sessionId": str(uuid4())}
    monkeypatch.setattr(AssistantService, "setup", AsyncMock(return_value=result))
    db = AsyncMock()
    assert await setup_assistant(SimpleNamespace(id=uuid4()), db) == result
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("deleted", ["session", "automation", "unrelated"])
async def test_binding_cleanup_only_clears_deleted_assignments(monkeypatch, deleted):
    settings = AssistantSettings(session_id=uuid4(), automation_id=uuid4())
    monkeypatch.setattr(assistant.SettingsStore, "load_optional", AsyncMock(return_value=settings))
    save = AsyncMock()
    monkeypatch.setattr(assistant.SettingsStore, "save", save)
    await assistant.clear_assistant_binding(AsyncMock(), uuid4(),
        session_ids=[settings.session_id] if deleted == "session" else [uuid4()],
        automation_ids=[settings.automation_id] if deleted == "automation" else [])
    assert (settings.session_id is None, settings.automation_id is None, save.await_count) == {
        "session": (True, True, 1), "automation": (False, True, 1), "unrelated": (False, False, 0),
    }[deleted]


def test_assistant_api_only_exposes_read_setup_and_chat():
    from mato_loop_backend.api.routes.assistant import router

    assert {(route.path, method) for route in router.routes for method in route.methods} == {
        ("/assistant", "GET"), ("/assistant/setup", "POST"), ("/assistant/chat", "POST"),
    }
