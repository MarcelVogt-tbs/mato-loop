import hashlib
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.agent import AgentRuntime
from mato_loop_backend.api.errors import ApiError
from mato_loop_backend.api.routes.sessions import (
    ActiveFileRequest,
    SendMessageRequest,
    SessionCreateRequest,
    SessionReadRequest,
    SessionUpdateRequest,
    cancel_run,
    create_session,
    delete_session,
    download_attachment,
    get_session,
    list_sessions,
    mark_session_read,
    message_payload,
    send_message,
    session_defaults,
    session_payload,
    stream_run_events,
    update_session,
)
from mato_loop_backend.attachments import (
    AttachmentInput,
    AttachmentService,
)
from mato_loop_backend.db.session import get_db_session
from mato_loop_backend.models import (
    AgentRun,
    ChatSession,
    ContextSummary,
    Message,
    MessageAttachment,
    TitleGenerationStatus,
    UserAccount,
)
from mato_loop_backend.security import require_user
from mato_loop_backend.session_storage import SessionStorage
from mato_loop_backend.sessions.service import (
    SessionServiceError,
    _latest_model_context,
    active_file_payload,
    session_message_page,
)
from mato_loop_backend.settings import (
    AiSettings,
    InstanceLimits,
    MemorySettings,
    SettingsStore,
    TrustLevel,
    UserPreferences,
)
from mato_loop_backend.settings.models import (
    LlmProviderConfig,
    ModelAvailability,
    ModelConfig,
)
from mato_loop_backend.workspace import FileRoot, RootFileService


def _user() -> UserAccount:
    return UserAccount(id=uuid4(), username="test", password_hash="hash")


def test_workspace_active_file_requires_a_normalized_absolute_posix_path() -> None:
    agent_id = uuid4()
    session = ChatSession(
        id=uuid4(),
        user_id=uuid4(),
        preferred_execution_agent_id=agent_id,
    )
    payload = {
        "root": "workspace",
        "executionAgentId": agent_id,
        "path": "/",
        "sha256": "a" * 64,
        "dirty": True,
    }
    active_file = active_file_payload(ActiveFileRequest.model_validate(payload), session)
    assert active_file is not None
    assert active_file["path"] == "/"

    for path in (
        "home/test",
        "/home\\test",
        "/home//test",
        "/home/./test",
        "/home/../test",
        "/home/\x1ftest",
        "/home/\x80test",
    ):
        payload["path"] = path
        with pytest.raises(SessionServiceError):
            active_file_payload(ActiveFileRequest.model_validate(payload), session)


def test_send_message_http_response_accepts_nested_message(monkeypatch) -> None:
    from mato_loop_backend.api.routes import sessions as sessions_route

    user = _user()
    session = ChatSession(id=uuid4(), user_id=user.id, title="Test")
    run = AgentRun(
        id=uuid4(),
        session_id=session.id,
        status="queued",
        model_name=None,
    )
    message = Message(
        id=uuid4(),
        session_id=session.id,
        role="user",
        content="Nachricht",
    )
    db = AsyncMock(spec=AsyncSession)
    db.scalar.side_effect = [session, None, message]
    monkeypatch.setattr(AgentRuntime, "start", AsyncMock(return_value=run))
    provider = LlmProviderConfig(name="Local", adapter_type="openai-compatible")
    model = ModelConfig(provider_id=provider.id, model_id="agent")
    session.model_config_id = model.id
    monkeypatch.setattr(
        SettingsStore,
        "load",
        AsyncMock(
            return_value=AiSettings(
                providers=[provider], models=[model], default_model_id=model.id
            )
        ),
    )

    async def override_db():
        yield db

    app = FastAPI()
    app.include_router(sessions_route.router, prefix="/api")
    app.dependency_overrides[get_db_session] = override_db
    app.dependency_overrides[require_user] = lambda: user

    with TestClient(app) as client:
        response = client.post(
            f"/api/sessions/{session.id}/messages",
            json={"content": "Nachricht"},
        )

    assert response.status_code == 202
    assert response.json() == {
        "runId": str(run.id),
        "modelName": None,
        "message": message_payload(message),
    }


async def test_session_list_first_page_has_cursor_and_payload_metadata() -> None:
    user = _user()
    session = ChatSession(
        id=uuid4(),
        user_id=user.id,
        title="Fortgesetzte Session",
        memory_mode="disabled",
        created_at=datetime(2026, 7, 1, 8, 0, tzinfo=UTC),
        updated_at=datetime(2026, 7, 2, 8, 0, tzinfo=UTC),
        last_read_at=datetime(2026, 7, 23, 8, 0, tzinfo=UTC),
    )
    next_session = ChatSession(
        id=uuid4(),
        user_id=user.id,
        title="Nächste Session",
        memory_mode="disabled",
        created_at=datetime(2026, 6, 1, 8, 0, tzinfo=UTC),
        updated_at=datetime(2026, 6, 2, 8, 0, tzinfo=UTC),
    )
    last_message_at = datetime(2026, 7, 24, 12, 0, tzinfo=UTC)
    execution_result = MagicMock()
    execution_result.all.return_value = [
        (session, last_message_at, last_message_at, "running", last_message_at),
        (next_session, None, None, None, next_session.created_at),
    ]
    db = AsyncMock(spec=AsyncSession)
    db.execute.return_value = execution_result

    result = await list_sessions(user, db, page_size=1)

    assert set(result) == {"items", "nextCursor"}
    assert len(result["items"]) == 1
    assert result["nextCursor"] is not None
    assert result["items"][0]["createdAt"] == session.created_at.isoformat()
    assert result["items"][0]["updatedAt"] == session.updated_at.isoformat()
    assert result["items"][0]["lastMessageAt"] == last_message_at.isoformat()
    assert result["items"][0]["hasUnreadMessages"] is True
    assert result["items"][0]["activeRunStatus"] == "running"
    statement = db.execute.await_args.args[0]
    sql = str(statement)
    assert "max(messages.created_at)" in sql
    assert "FILTER (WHERE messages.role" in sql
    assert "messages.role IN" in sql
    assert "agent_runs.status IN" in sql
    assert "agent_runs.created_at DESC, agent_runs.id DESC" in sql
    assert "LIMIT" in sql
    assert "coalesce(anon_1.last_message_at, chat_sessions.created_at) DESC" in sql
    assert "chat_sessions.updated_at DESC" not in sql
    assert 2 in statement.compile().params.values()


async def test_session_list_next_page_uses_full_keyset_and_preserves_order() -> None:
    user = _user()
    pinned = ChatSession(
        id=uuid4(),
        user_id=user.id,
        title="Angeheftet",
        is_pinned=True,
        memory_mode="disabled",
        created_at=datetime(2026, 7, 20, 8, 0, tzinfo=UTC),
    )
    overflow = ChatSession(
        id=uuid4(),
        user_id=user.id,
        title="Weitere Session",
        memory_mode="disabled",
        created_at=datetime(2026, 7, 19, 8, 0, tzinfo=UTC),
    )
    first_result = MagicMock()
    first_result.all.return_value = [
        (pinned, None, None, None, pinned.created_at),
        (overflow, None, None, None, overflow.created_at),
    ]
    first_db = AsyncMock(spec=AsyncSession)
    first_db.execute.return_value = first_result
    first_page = await list_sessions(user, first_db, page_size=1)

    second_result = MagicMock()
    second_result.all.return_value = [
        (overflow, None, None, "waiting_approval", overflow.created_at)
    ]
    second_db = AsyncMock(spec=AsyncSession)
    second_db.execute.return_value = second_result
    second_page = await list_sessions(
        user, second_db, page_size=2, cursor=first_page["nextCursor"]
    )

    assert [item["id"] for item in second_page["items"]] == [str(overflow.id)]
    assert second_page["nextCursor"] is None
    statement = second_db.execute.await_args.args[0]
    sql = str(statement)
    assert "chat_sessions.is_pinned IS false OR chat_sessions.is_pinned IS true" in sql
    assert "coalesce(anon_1.last_message_at, chat_sessions.created_at) <" in sql
    assert "coalesce(anon_1.last_message_at, chat_sessions.created_at) =" in sql
    assert "chat_sessions.id <" in sql
    assert sql.index("chat_sessions.is_pinned DESC") < sql.index(
        "coalesce(anon_1.last_message_at, chat_sessions.created_at) DESC"
    ) < sql.index("chat_sessions.id DESC")
    assert user.id in statement.compile().params.values()
    assert pinned.created_at in statement.compile().params.values()
    assert pinned.id in statement.compile().params.values()


async def test_session_list_rejects_malformed_cursor() -> None:
    db = AsyncMock(spec=AsyncSession)

    with pytest.raises(ApiError) as exc_info:
        await list_sessions(_user(), db, cursor="not-a-cursor")

    assert exc_info.value.status_code == 400
    assert exc_info.value.code == "validation.invalid"
    assert exc_info.value.field == "cursor"
    db.execute.assert_not_awaited()


async def test_session_list_scopes_projects_and_standalone_sessions() -> None:
    user = _user()
    project_id = uuid4()
    result = MagicMock()
    result.all.return_value = []
    project_db = AsyncMock(spec=AsyncSession)
    project_db.scalar.return_value = project_id
    project_db.execute.return_value = result

    await list_sessions(user, project_db, project_id=project_id)

    project_statement = project_db.execute.await_args.args[0]
    assert project_id in project_statement.compile().params.values()
    owner_statement = project_db.scalar.await_args.args[0]
    assert user.id in owner_statement.compile().params.values()

    standalone_db = AsyncMock(spec=AsyncSession)
    standalone_db.execute.return_value = result
    await list_sessions(user, standalone_db, standalone=True)
    assert "chat_sessions.project_id IS NULL" in str(
        standalone_db.execute.await_args.args[0]
    )


async def test_failed_project_move_restores_the_session_tree(monkeypatch) -> None:
    from mato_loop_backend.api.routes import sessions as sessions_route

    user = _user()
    source_project_id = uuid4()
    session = ChatSession(
        id=uuid4(), user_id=user.id, title="Move", project_id=source_project_id
    )
    db = AsyncMock(spec=AsyncSession)
    db.scalar.side_effect = [session.id, None]
    db.scalars.return_value = MagicMock(all=MagicMock(return_value=[]))
    db.commit.side_effect = RuntimeError("commit failed")
    persisted_location = MagicMock()
    persisted_location.one_or_none.return_value = (source_project_id,)
    db.execute.side_effect = [persisted_location]
    storage = MagicMock()
    receipt = MagicMock(
        journal=None,
        source_project_id=source_project_id,
        target_project_id=None,
    )
    storage.stage_session_move.return_value = receipt
    monkeypatch.setattr(sessions_route, "SessionStorage", lambda: storage)
    monkeypatch.setattr(
        sessions_route, "_owned_session", AsyncMock(return_value=session)
    )

    with pytest.raises(RuntimeError, match="commit failed"):
        await update_session(
            session.id,
            SessionUpdateRequest(project_id=None),
            user,
            db,
        )

    storage.stage_session_move.assert_called_once_with(
        session.id, source_project_id, None, quota_bytes=None
    )
    storage.rollback_session_move.assert_called_once_with(receipt)
    storage.finalize_session_move.assert_not_called()
    assert len(db.execute.await_args_list) == 1
    assert db.rollback.await_count == 2


async def test_standalone_project_move_rebinds_managed_file_references(monkeypatch) -> None:
    from mato_loop_backend.api.routes import sessions as sessions_route

    user = _user()
    project_id = uuid4()
    session = ChatSession(id=uuid4(), user_id=user.id, title="Move")
    db = AsyncMock(spec=AsyncSession)
    db.scalar.side_effect = [session.id, None, project_id]
    db.scalars.side_effect = [
        MagicMock(all=MagicMock(return_value=[])),
        MagicMock(all=MagicMock(return_value=[])),
    ]
    storage = MagicMock()
    receipt = storage.stage_session_move.return_value
    monkeypatch.setattr(sessions_route, "SessionStorage", lambda: storage)
    monkeypatch.setattr(
        sessions_route, "_owned_session", AsyncMock(return_value=session)
    )
    monkeypatch.setattr(
        sessions_route.SettingsStore,
        "load",
        AsyncMock(return_value=InstanceLimits(session_quota_mib=512)),
    )
    monkeypatch.setattr(sessions_route, "_session_payload", AsyncMock(return_value={}))

    await update_session(
        session.id,
        SessionUpdateRequest(project_id=project_id),
        user,
        db,
    )

    storage.stage_session_move.assert_called_once_with(
        session.id,
        None,
        project_id,
        quota_bytes=512 * 1024 * 1024,
    )
    storage.finalize_session_move.assert_called_once_with(receipt)
    storage.rollback_session_move.assert_not_called()
    artifact_update = db.execute.await_args.args[0]
    assert artifact_update.is_update
    assert "message_attachments.file_root_id" in str(artifact_update)
    params = artifact_update.compile().params.values()
    assert session.id in params
    assert project_id in params
    assert {"companionFileRootId": str(project_id)} in params
    assert session.project_id == project_id


def test_standalone_project_move_rebinds_file_action_and_control_center_metadata() -> None:
    from mato_loop_backend.api.routes import sessions as sessions_route

    session_id = uuid4()
    project_id = uuid4()
    session = ChatSession(
        id=session_id,
        user_id=uuid4(),
        title="Move",
        control_center={
            "tabs": [{"app_id": "files", "pinned": False}],
            "active_app_id": "files",
            "open": True,
            "file_view": {
                "scope": "session",
                "selected_path": "note.txt",
                "selected_kind": "file",
                "viewed_file_path": "note.txt",
                "root_identity": str(session_id),
            },
        },
    )
    message = Message(
        session_id=session_id,
        role="tool",
        content="saved",
        provider_data={
            "_matoloop": {
                "activeFile": {
                    "root": "session",
                    "rootIdentity": str(session_id),
                    "path": "note.txt",
                },
                "fileActions": [
                    {
                        "root": "session",
                        "rootIdentity": str(session_id),
                        "path": "note.txt",
                    }
                ]
            }
        },
    )

    sessions_route._rebind_session_file_metadata(
        session, [message], session_id, project_id
    )

    assert session.control_center["file_view"]["root_identity"] == str(project_id)
    assert (
        message.provider_data["_matoloop"]["fileActions"][0]["rootIdentity"]
        == str(project_id)
    )
    assert message.provider_data["_matoloop"]["activeFile"]["rootIdentity"] == str(
        project_id
    )

    sessions_route._rebind_session_file_metadata(
        session, [message], project_id, None
    )

    assert session.control_center["file_view"] is None
    assert message.provider_data["_matoloop"]["fileActions"] == []
    assert "activeFile" not in message.provider_data["_matoloop"]


async def test_mark_session_read_uses_owned_assistant_message_monotonically() -> None:
    user = _user()
    session = ChatSession(
        id=uuid4(),
        user_id=user.id,
        title="Gelesene Session",
        last_read_at=datetime(2026, 7, 23, 8, 0, tzinfo=UTC),
    )
    message_id = uuid4()
    read_at = datetime(2026, 7, 24, 12, 0, tzinfo=UTC)
    db = AsyncMock(spec=AsyncSession)
    db.scalar.side_effect = [session, read_at]

    result = await mark_session_read(
        session.id,
        SessionReadRequest(throughMessageId=message_id),
        user,
        db,
    )

    assert result == {"read": True}
    statements = [call.args[0] for call in db.scalar.await_args_list]
    assert "FOR UPDATE" in str(statements[0])
    assert message_id in statements[1].compile().params.values()
    update_statement = db.execute.await_args.args[0]
    sql = str(update_statement)
    assert "chat_sessions.last_read_at <" in sql
    assert "updated_at=chat_sessions.updated_at" in sql.replace(" ", "")
    assert read_at in update_statement.compile().params.values()
    db.commit.assert_awaited_once()


async def test_session_is_created_with_first_message_and_run(monkeypatch) -> None:
    from mato_loop_backend.api.routes import sessions as sessions_route

    user = _user()
    provider_id = uuid4()
    provider = LlmProviderConfig(
        id=provider_id,
        name="Test",
        adapter_type="openai-compatible",
    )
    model = ModelConfig(
        provider_id=provider_id,
        model_id="test-model",
        name="Test",
        trust_level=TrustLevel.PRIVATE,
    )
    ai_settings = AiSettings(providers=[provider], models=[model], default_model_id=model.id)
    memory_settings = MemorySettings()

    async def load_settings(_db, settings_type, **_kwargs):
        if settings_type is AiSettings:
            return ai_settings
        if settings_type is UserPreferences:
            return UserPreferences()
        return memory_settings

    run = AgentRun(
        id=uuid4(),
        session_id=uuid4(),
        status="queued",
        model_name="Test",
    )
    start = AsyncMock(return_value=run)
    monkeypatch.setattr(SettingsStore, "load", load_settings)
    monkeypatch.setattr(AgentRuntime, "start", start)
    storage = MagicMock()
    monkeypatch.setattr(sessions_route, "SessionStorage", lambda: storage)
    db = AsyncMock(spec=AsyncSession)
    db.add.side_effect = lambda session: setattr(session, "id", uuid4())
    persisted_message = Message(
        id=uuid4(),
        session_id=run.session_id,
        role="user",
        content="Erste Nachricht",
    )
    db.scalar.return_value = persisted_message

    result = await create_session(
        SessionCreateRequest(
            content="  Erste Nachricht  ",
            reasoning_effort="low",
            control_center={
                "tabs": [
                    {"app_id": "files", "pinned": False},
                    {"app_id": "notifications", "pinned": True},
                ],
                "active_app_id": "notifications",
            },
        ),
        user,
        db,
    )

    session = db.add.call_args.args[0]
    assert session.model_config_id == model.id
    assert session.reasoning_effort == "low"
    assert session.user_id == user.id
    assert session.memory_mode == "full"
    assert session.web_search_policy == "available"
    assert session.current_directory is None
    assert session.control_center == {
        "tabs": [
            {"app_id": "files", "pinned": False},
            {"app_id": "notifications", "pinned": True},
        ],
        "active_app_id": "notifications",
        "open": False,
        "file_view": None,
    }
    assert session.title_generation_status == TitleGenerationStatus.PENDING.value
    start.assert_awaited_once_with(
        db,
        session,
        "Erste Nachricht",
        run_id=None,
        attachments=None,
        active_file=None,
    )
    assert result["session"]["id"] == str(session.id)
    assert result["session"]["titleGenerationStatus"] == "pending"
    assert result["session"]["webSearchPolicy"] == "available"
    assert result["session"]["reasoningEffort"] == "low"
    assert "trustLevel" not in result["session"]
    assert result["runId"] == str(run.id)
    assert result["modelName"] == "Test"
    assert result["message"]["id"] == str(persisted_message.id)
    storage.provision.assert_called_once_with(session.id, None)


async def test_first_session_retry_returns_canonical_session_and_attachment() -> None:
    user = _user()
    run_id = uuid4()
    session = ChatSession(
        id=uuid4(),
        user_id=user.id,
        title="Erste Session",
        memory_mode="disabled",
    )
    message = Message(
        id=uuid4(),
        session_id=session.id,
        role="user",
        content="Erste Nachricht",
    )
    message.attachments = [
        MessageAttachment(
            id=uuid4(),
            message_id=message.id,
            position=0,
            kind="text",
            mime_type="text/plain",
            size_bytes=7,
            sha256="a" * 64,
            file_root_id=session.id,
            file_id=uuid4(),
            file_revision=1,
            file_path="attachments/note/note.txt",
            filename="note.txt",
        )
    ]
    run = AgentRun(
        id=run_id,
        session_id=session.id,
        input_message_id=message.id,
        status="completed",
        model_name="Test",
    )
    db = AsyncMock(spec=AsyncSession)
    db.scalar.side_effect = [run, message]
    db.get.return_value = session

    result = await create_session(
        SessionCreateRequest(content="ignored retry content", run_id=run_id), user, db
    )

    assert result["session"]["id"] == str(session.id)
    assert result["runId"] == str(run_id)
    assert result["message"]["id"] == str(message.id)
    assert result["message"]["attachments"][0]["filename"] == "note.txt"
    db.add.assert_not_called()


async def test_concurrent_first_session_retry_recovers_winning_run(monkeypatch) -> None:
    from mato_loop_backend.api.routes import sessions as sessions_route

    user = _user()
    run_id = uuid4()
    provider = LlmProviderConfig(name="Local", adapter_type="openai-compatible")
    model = ModelConfig(provider_id=provider.id, model_id="agent")
    winner = ChatSession(
        id=uuid4(),
        user_id=user.id,
        title="Gewinner",
        memory_mode="disabled",
        model_config_id=model.id,
    )
    message = Message(
        id=uuid4(), session_id=winner.id, role="user", content="Nachricht"
    )
    run = AgentRun(
        id=run_id,
        session_id=winner.id,
        input_message_id=message.id,
        status="queued",
    )
    db = AsyncMock(spec=AsyncSession)
    db.scalar.side_effect = [None, run, message]
    db.get.return_value = winner
    monkeypatch.setattr(
        SettingsStore,
        "load",
        AsyncMock(
            return_value=AiSettings(
                providers=[provider], models=[model], default_model_id=model.id
            )
        ),
    )
    monkeypatch.setattr(
        AgentRuntime,
        "start",
        AsyncMock(side_effect=IntegrityError("insert", {}, Exception("duplicate"))),
    )
    storage = MagicMock()
    monkeypatch.setattr(sessions_route, "SessionStorage", lambda: storage)

    result = await create_session(
        SessionCreateRequest(content="Nachricht", run_id=run_id), user, db
    )

    assert result["session"]["id"] == str(winner.id)
    assert result["runId"] == str(run_id)
    db.rollback.assert_awaited()
    storage.remove_session.assert_called_once()


async def test_session_defaults_report_effective_memory_state(monkeypatch) -> None:
    user = _user()
    provider = LlmProviderConfig(name="Test", adapter_type="openai-compatible")
    model = ModelConfig(
        provider_id=provider.id,
        model_id="test-model",
        supports_tools=False,
    )
    ai_settings = AiSettings(
        providers=[provider], models=[model], default_model_id=model.id
    )
    memory_settings = MemorySettings(enabled=False)

    async def load_settings(_db, settings_type, **_kwargs):
        if settings_type is AiSettings:
            return ai_settings
        if settings_type is UserPreferences:
            return UserPreferences()
        return memory_settings

    monkeypatch.setattr(SettingsStore, "load", load_settings)

    result = await session_defaults(user, AsyncMock(spec=AsyncSession))

    assert result["memoryMode"] == "full"
    assert result["effectiveMemoryMode"] == "disabled"
    assert result["memoryToolsAvailable"] is False
    assert result["memoryUnavailableReason"] == "memory.disabled"
    assert result["preferredExecutionAgentId"] is None
    assert "trustLevel" not in result


async def test_session_is_not_created_for_blank_first_message() -> None:
    user = _user()
    db = AsyncMock(spec=AsyncSession)

    with pytest.raises(ApiError) as exc_info:
        await create_session(SessionCreateRequest(content="   "), user, db)

    assert exc_info.value.status_code == 422
    assert exc_info.value.code == "validation.required"
    assert exc_info.value.field == "content"
    db.add.assert_not_called()


async def test_uncertain_first_commit_preserves_provisioned_session_tree(
    tmp_path: Path, monkeypatch
) -> None:
    from mato_loop_backend.api.routes import sessions as sessions_route

    user = _user()
    provider = LlmProviderConfig(name="Test", adapter_type="openai-compatible")
    model = ModelConfig(provider_id=provider.id, model_id="test-model")
    ai_settings = AiSettings(
        providers=[provider], models=[model], default_model_id=model.id
    )

    async def load_settings(_db, settings_type, **_kwargs):
        return ai_settings if settings_type is AiSettings else MemorySettings()

    sessions = SessionStorage(tmp_path / "sessions")
    monkeypatch.setattr(SettingsStore, "load", load_settings)
    monkeypatch.setattr(
        AgentRuntime, "start", AsyncMock(side_effect=RuntimeError("commit failed"))
    )
    monkeypatch.setattr(sessions_route, "SessionStorage", lambda: sessions)
    db = AsyncMock(spec=AsyncSession)

    with pytest.raises(RuntimeError, match="commit failed"):
        await create_session(SessionCreateRequest(content="Erste Nachricht"), user, db)

    assert len(
        [item for item in sessions.sessions_dir.iterdir() if not item.name.startswith(".")]
    ) == 1
    db.rollback.assert_awaited()


async def test_session_detail_contains_active_run_and_aggregate_metrics() -> None:
    user = _user()
    session_id = uuid4()
    session = ChatSession(
        id=session_id,
        user_id=user.id,
        title="Test",
        is_pinned=True,
        memory_mode="disabled",
        web_search_policy="available",
    )
    created_at = datetime(2026, 7, 19, 12, 0, tzinfo=UTC)
    run = AgentRun(
        id=uuid4(),
        session_id=session_id,
        status="waiting_approval",
        model_name="Aktives Modell",
        created_at=created_at,
    )
    db = AsyncMock(spec=AsyncSession)
    db.get.return_value = session
    anchors = MagicMock()
    anchors.all.return_value = []
    aggregate = MagicMock()
    aggregate.one.return_value = (
        None,
        None,
        47,
        {
            "privateProviderValue": "must-not-leak",
            "_matoloop": {
                "performance": {
                    "estimatedInputTokens": 12_345,
                    "inputBudgetTokens": 32_000,
                }
            },
        },
    )
    db.execute.side_effect = [anchors, aggregate]
    db.scalar.return_value = run

    result = await get_session(session_id, user, db)

    assert result["pinned"] is True
    assert result["controlCenter"] == {
        "tabs": [{"appId": "files", "pinned": False}],
        "activeAppId": "files",
        "open": False,
        "fileView": None,
    }
    assert result["activeRun"] == {
        "id": str(run.id),
        "status": "waiting_approval",
        "modelName": "Aktives Modell",
        "createdAt": created_at.isoformat(),
    }
    assert result["userRequestCount"] == 47
    assert result["latestModelContext"] == {
        "estimatedInputTokens": 12_345,
        "inputBudgetTokens": 32_000,
    }
    assert "must-not-leak" not in str(result)
    assert db.execute.await_count == 2
    aggregate_statement = db.execute.await_args_list[1].args[0]
    aggregate_sql = str(aggregate_statement)
    assert "count(messages.id) FILTER (WHERE messages.role" in aggregate_sql
    aggregate_params = list(aggregate_statement.compile().params.values())
    assert "_matoloop" in aggregate_params
    assert "performance" in aggregate_params
    assert "messages_1.created_at DESC, messages_1.id DESC" in aggregate_sql


@pytest.mark.parametrize(
    "performance",
    [
        {"estimatedInputTokens": True, "inputBudgetTokens": 32_000},
        {"estimatedInputTokens": 1, "inputBudgetTokens": False},
        {"estimatedInputTokens": -1, "inputBudgetTokens": 32_000},
        {"estimatedInputTokens": 1, "inputBudgetTokens": 0},
        {"estimatedInputTokens": "1", "inputBudgetTokens": 32_000},
        {},
    ],
)
def test_latest_model_context_rejects_malformed_latest_performance(
    performance: dict,
) -> None:
    provider_data = {
        "privateProviderValue": "must-not-leak",
        "_matoloop": {"performance": performance},
    }

    assert _latest_model_context(provider_data) is None


def test_session_payload_ignores_invalid_persisted_file_view() -> None:
    session = ChatSession(
        id=uuid4(),
        user_id=uuid4(),
        title="Test",
        memory_mode="disabled",
        control_center={
            "tabs": [{"app_id": "files", "pinned": False}],
            "active_app_id": "files",
            "open": True,
            "file_view": {
                "scope": "session",
                "selected_path": "drafts/draft-1/plan.md",
                "viewed_file_path": "drafts/draft-1/plan.md",
            },
        },
    )

    result = session_payload(session)

    assert result["controlCenter"] == {
        "tabs": [{"appId": "files", "pinned": False}],
        "activeAppId": "files",
        "open": False,
        "fileView": None,
    }


async def test_session_payload_marks_latest_restart_error_retryable() -> None:
    user = _user()
    session = ChatSession(
        id=uuid4(),
        user_id=user.id,
        title="Test",
        memory_mode="disabled",
    )
    user_message = Message(
        id=uuid4(),
        session_id=session.id,
        role="user",
        content="Fortsetzen",
    )
    failed_run = AgentRun(id=uuid4(), session_id=session.id, status="failed")
    error_message = Message(
        id=uuid4(),
        session_id=session.id,
        role="assistant",
        content="",
        error_code="agent.backendRestarted",
        error_run_id=failed_run.id,
    )
    user_message.attachments = []
    error_message.attachments = []
    created_at = datetime(2026, 7, 19, 12, 0, tzinfo=UTC)
    user_message.created_at = created_at
    error_message.created_at = created_at
    messages = MagicMock()
    messages.all.return_value = [user_message, error_message]
    summaries = MagicMock()
    summaries.all.return_value = []
    anchors = MagicMock()
    anchors.all.return_value = [SimpleNamespace(created_at=created_at, id=user_message.id)]
    aggregate = MagicMock()
    aggregate.one.return_value = (created_at, created_at, 1, None)
    db = AsyncMock(spec=AsyncSession)
    db.get.side_effect = [session, failed_run]
    db.scalars.side_effect = [messages, summaries]
    db.scalar.return_value = None
    db.execute.side_effect = [anchors, aggregate]

    result = await get_session(session.id, user, db)

    assert [message["retryAvailable"] for message in result["messages"]] == [False, True]


async def test_session_message_page_returns_complete_turns_and_cursor() -> None:
    session_id = uuid4()
    created_at = datetime(2026, 7, 19, 12, 0, tzinfo=UTC)
    oldest = Message(
        id=uuid4(), session_id=session_id, role="user", content="Älter", created_at=created_at
    )
    newest = Message(
        id=uuid4(), session_id=session_id, role="user", content="Neu", created_at=created_at
    )
    assistant = Message(
        id=uuid4(), session_id=session_id, role="assistant", content="Antwort", created_at=created_at
    )
    for message in (oldest, newest, assistant):
        message.attachments = []
    anchors = MagicMock()
    anchors.all.return_value = [
        SimpleNamespace(created_at=created_at, id=newest.id),
        SimpleNamespace(created_at=created_at, id=oldest.id),
    ]
    messages = MagicMock()
    messages.all.return_value = [newest, assistant]
    summary = ContextSummary(
        id=uuid4(),
        session_id=session_id,
        covered_through_message_id=newest.id,
        content="Verdichteter Verlauf",
        estimated_tokens=20,
        is_fallback=True,
        created_at=created_at,
    )
    summaries = MagicMock()
    summaries.all.return_value = [summary]
    db = AsyncMock(spec=AsyncSession)
    db.execute.return_value = anchors
    db.scalars.side_effect = [messages, summaries]

    items, context_summaries, next_cursor = await session_message_page(
        db,
        session_id,
        page_size=1,
        cursor=None,
    )

    assert [message.id for message in items] == [newest.id, assistant.id]
    assert context_summaries == [summary]
    assert next_cursor == str(newest.id)


async def test_session_owner_cannot_read_another_users_session() -> None:
    owner = _user()
    other = _user()
    session = ChatSession(id=uuid4(), user_id=owner.id, title="Privat")
    db = AsyncMock(spec=AsyncSession)
    db.get.return_value = session

    with pytest.raises(ApiError) as exc_info:
        await get_session(session.id, other, db)

    assert exc_info.value.code == "session.notFound"
    db.scalars.assert_not_awaited()


async def test_active_run_cancellation_is_requested_without_waiting(monkeypatch) -> None:
    user = _user()
    session_id = uuid4()
    session = ChatSession(id=session_id, user_id=user.id, title="Test")
    run = AgentRun(id=uuid4(), session_id=session_id, status="running")
    cancel = MagicMock(return_value=True)
    db = AsyncMock(spec=AsyncSession)
    db.get.side_effect = [session, run]
    monkeypatch.setattr(AgentRuntime, "cancel", cancel)

    result = await cancel_run(session_id, run.id, user, db)

    cancel.assert_called_once_with(run.id)
    db.refresh.assert_not_awaited()
    assert result["status"] == "running"


async def test_terminal_run_cancel_is_idempotent(monkeypatch) -> None:
    user = _user()
    session_id = uuid4()
    session = ChatSession(id=session_id, user_id=user.id, title="Test")
    run = AgentRun(id=uuid4(), session_id=session_id, status="completed")
    cancel = MagicMock()
    db = AsyncMock(spec=AsyncSession)
    db.get.side_effect = [session, run]
    monkeypatch.setattr(AgentRuntime, "cancel", cancel)

    result = await cancel_run(session_id, run.id, user, db)

    cancel.assert_not_called()
    assert result["status"] == "completed"


async def test_run_cancel_checks_session_hierarchy() -> None:
    user = _user()
    run = AgentRun(id=uuid4(), session_id=uuid4(), status="running")
    session_id = uuid4()
    session = ChatSession(id=session_id, user_id=user.id, title="Test")
    db = AsyncMock(spec=AsyncSession)
    db.get.side_effect = [session, run]

    with pytest.raises(ApiError) as exc_info:
        await cancel_run(session_id, run.id, user, db)

    assert exc_info.value.status_code == 404
    assert exc_info.value.code == "session.runNotFound"


def test_message_payload_exposes_only_public_reasoning() -> None:
    message = Message(
        id=uuid4(),
        session_id=uuid4(),
        role="assistant",
        content="Antwort",
        model_name="Antwortmodell",
        public_reasoning={"kind": "summary", "text": "Freigegebene Begründung"},
        provider_data={"reasoning_encrypted_content": "private-state"},
    )

    payload = message_payload(message)

    assert payload["reasoning"]["text"] == "Freigegebene Begründung"
    assert payload["modelName"] == "Antwortmodell"
    assert payload["generationStatus"] == "completed"
    assert "private-state" not in str(payload)
    assert payload["attachments"] == []


def test_message_payload_canonicalizes_historical_assistant_markdown() -> None:
    message = Message(
        id=uuid4(),
        session_id=uuid4(),
        role="assistant",
        content="````markdown\n# Document\n\n```mermaid\ngraph TD\nA --> B\n```\n````",
    )

    assert message_payload(message)["content"] == (
        "# Document\n\n```mermaid\ngraph TD\nA --> B\n```\n"
    )


def test_message_payload_exposes_only_validated_research_snapshot() -> None:
    message = Message(
        id=uuid4(),
        session_id=uuid4(),
        role="assistant",
        content="Antwort",
        provider_data={
            "secret": "private-state",
            "_matoloop": {
                "research": {
                    "policy": "available",
                    "used": True,
                    "linkedSources": [
                        {"title": "Quelle", "url": "HTTPS://EXAMPLE.TEST/source#part"},
                        {"title": "Unsicher", "url": "javascript:alert(1)"},
                    ],
                    "internal": "not-public",
                }
            },
        },
    )

    payload = message_payload(message)

    assert payload["research"] == {
        "policy": "available",
        "used": True,
        "linkedSources": [
            {"title": "Quelle", "url": "https://example.test/source"}
        ],
    }
    assert "private-state" not in str(payload)
    assert "not-public" not in str(payload)


def test_message_payload_exposes_only_validated_automation_source() -> None:
    message = Message(
        id=uuid4(),
        session_id=uuid4(),
        role="assistant",
        content="Erinnerung",
        provider_data={
            "secret": "private-state",
            "_matoloop": {
                "source": {
                    "type": "automationRun",
                    "automationId": str(uuid4()),
                    "runId": str(uuid4()),
                }
            },
        },
    )

    payload = message_payload(message)

    assert payload["sourceType"] == "automationRun"
    assert "private-state" not in str(payload)
    assert "automationId" not in str(payload)


def test_message_payload_validates_file_actions_and_derives_download_url() -> None:
    session_id = uuid4()
    execution_agent_id = str(uuid4())
    stored_action = {
        "operation": "copy",
        "root": "workspace",
        "path": "/home/user/project/reports/result.txt",
        "filename": "result.txt",
        "sizeBytes": 7,
        "executionAgentId": execution_agent_id,
        "currentDirectory": "/home/user/project",
    }
    message = Message(
        id=uuid4(),
        session_id=session_id,
        role="tool",
        content="{}",
        provider_data={"_matoloop": {"fileActions": [stored_action]}},
    )

    payload = message_payload(message)

    action = payload["fileActions"][0]
    query = parse_qs(urlparse(action.pop("downloadUrl")).query)
    assert action == stored_action
    assert query == {
        "path": ["/home/user/project/reports/result.txt"],
        "scope": ["filesystem"],
        "session_id": [str(session_id)],
        "execution_agent_id": [execution_agent_id],
        "current_directory": ["/home/user/project"],
    }
    assert "downloadUrl" not in str(message.provider_data)


def test_message_payload_keeps_session_file_action_url_exact() -> None:
    session_id = uuid4()
    stored_action = {
        "operation": "write",
        "root": "session",
        "rootIdentity": str(session_id),
        "path": "result.txt",
        "filename": "result.txt",
        "sizeBytes": 7,
    }
    message = Message(
        id=uuid4(),
        session_id=session_id,
        role="tool",
        content="{}",
        provider_data={"_matoloop": {"fileActions": [stored_action]}},
    )

    action = message_payload(message)["fileActions"][0]

    query = parse_qs(urlparse(action.pop("downloadUrl")).query)
    assert action == stored_action
    assert query == {
        "path": ["result.txt"],
        "scope": ["session"],
        "session_id": [str(session_id)],
        "root_identity": [str(session_id)],
    }


def test_message_payload_rejects_workspace_snapshot_fields_on_session_actions() -> None:
    session_id = uuid4()
    action = {
        "operation": "write",
        "root": "session",
        "rootIdentity": str(session_id),
        "path": "result.txt",
        "filename": "result.txt",
        "sizeBytes": 7,
        "executionAgentId": str(uuid4()),
        "currentDirectory": "/home/user",
    }
    message = Message(
        id=uuid4(),
        session_id=session_id,
        role="tool",
        content="{}",
        provider_data={"_matoloop": {"fileActions": [action]}},
    )

    assert message_payload(message)["fileActions"] == []


@pytest.mark.parametrize(
    "file_actions",
    [
        [
            {
                "operation": "write",
                "root": "session",
                "rootIdentity": "not-a-uuid",
                "path": "result.txt",
                "filename": "result.txt",
                "sizeBytes": 7,
            }
        ],
        [
            {
                "operation": "write",
                "root": "workspace",
                "rootIdentity": "project",
                "path": "../result.txt",
                "filename": "result.txt",
                "sizeBytes": 7,
            }
        ],
        [
            {
                "operation": "write",
                "root": "workspace",
                "rootIdentity": "project",
                "path": "result.txt",
                "filename": "result.txt",
                "sizeBytes": "7",
            }
        ],
        [
            {
                "operation": "write",
                "root": "workspace",
                "rootIdentity": "project",
                "path": "result.txt",
                "filename": "result.txt",
                "sizeBytes": 7,
                "downloadUrl": "https://evil.test/file",
            }
        ],
        [{}, {}],
    ],
)
def test_message_payload_rejects_untrusted_file_action_metadata(file_actions) -> None:
    message = Message(
        id=uuid4(),
        session_id=uuid4(),
        role="tool",
        content="{}",
        provider_data={"_matoloop": {"fileActions": file_actions}},
    )

    assert message_payload(message)["fileActions"] == []


def test_message_payload_recreates_safe_english_runtime_error() -> None:
    secret = "secret upstream response"
    message = Message(
        id=uuid4(),
        session_id=uuid4(),
        role="assistant",
        content=secret,
        error_code="provider.requestFailed",
        error_params={"retryable": True},
        error_run_id=uuid4(),
    )

    payload = message_payload(message)

    assert payload["kind"] == "runtime_error"
    assert payload["content"] == "The model provider request failed."
    assert payload["error"] == {
        "code": "provider.requestFailed",
        "message": "The model provider request failed.",
        "params": {"retryable": True},
    }
    assert payload["retryAvailable"] is False
    assert secret not in str(payload)


async def test_terminal_failure_is_serialized_before_sse_transaction_ends(
    monkeypatch,
) -> None:
    from mato_loop_backend.api.routes import sessions as sessions_route

    user = _user()
    session = ChatSession(id=uuid4(), user_id=user.id, title="Test")
    run = AgentRun(
        id=uuid4(),
        session_id=session.id,
        status="failed",
        error="provider.requestFailed",
    )
    error_message = Message(
        id=uuid4(),
        session_id=session.id,
        role="assistant",
        content="secret upstream response",
        error_code="provider.requestFailed",
        error_params={"retryable": True},
        error_run_id=run.id,
    )
    error_message.attachments = []
    db = AsyncMock(spec=AsyncSession)
    db.get.side_effect = [session, run]
    db.scalar.return_value = error_message
    original_message_payload = message_payload

    def serialize_before_rollback(message):
        assert db.rollback.await_count == 0
        return original_message_payload(message)

    monkeypatch.setattr(sessions_route, "message_payload", serialize_before_rollback)

    response = await stream_run_events(session.id, run.id, user, db)
    chunks = [chunk async for chunk in response.body_iterator]
    body = "".join(
        chunk.decode() if isinstance(chunk, bytes) else chunk for chunk in chunks
    )

    assert db.rollback.await_count == 1
    assert body.index("event: message.completed") < body.index("event: run.failed")
    assert '"retryable": true' in body
    assert "secret upstream response" not in body


def test_message_payload_contains_public_attachment_metadata_only() -> None:
    session_id = uuid4()
    message_id = uuid4()
    attachment_id = uuid4()
    file_id = uuid4()
    message = Message(
        id=message_id,
        session_id=session_id,
        role="assistant",
        content="Bild",
    )
    message.attachments = [
        MessageAttachment(
            id=attachment_id,
            message_id=message_id,
            position=0,
            kind="image",
            category="generated",
            purpose="artifact",
            processing_status="ready",
            mime_type="image/png",
            size_bytes=123,
            sha256="a" * 64,
            file_root_id=session_id,
            file_id=file_id,
            file_revision=3,
            file_path="bild.png",
            filename="bild.png",
            width=10,
            height=20,
        )
    ]

    payload = message_payload(message)

    assert payload["attachments"] == [
        {
            "id": str(attachment_id),
            "kind": "image",
            "category": "generated",
            "purpose": "artifact",
            "processingStatus": "ready",
            "mimeType": "image/png",
            "sizeBytes": 123,
            "filename": "bild.png",
            "width": 10,
            "height": 20,
            "durationMs": None,
            "metadata": {},
            "fileRootId": str(session_id),
            "fileId": str(file_id),
            "fileRevision": 3,
            "filePath": "bild.png",
            "url": (
                f"/api/sessions/{session_id}/messages/{message_id}"
                f"/attachments/{attachment_id}"
            ),
        }
    ]
    assert "storage" not in str(payload).lower()
    assert "sha256" not in str(payload)


async def test_attachment_download_has_secure_headers_and_checks_hierarchy(
    tmp_path: Path, monkeypatch
) -> None:
    from mato_loop_backend.api.routes import sessions as sessions_route

    session_id = uuid4()
    user = _user()
    message_id = uuid4()
    attachment_id = uuid4()
    sessions = SessionStorage(tmp_path / "sessions")
    sessions.provision(session_id)
    data = b"image-data"
    root_files = RootFileService(session_id, session_storage=sessions)
    root_files.create_directory(FileRoot.SESSION, "attachments")
    root_files.create_directory(FileRoot.SESSION, f"attachments/{attachment_id}")
    file_path = f"attachments/{attachment_id}/bild.png"
    root_files.create_file(
        FileRoot.SESSION, file_path, BytesIO(data), max_bytes=100
    )
    revision = root_files.list_revisions(file_path)[0]
    attachment = MessageAttachment(
        id=attachment_id,
        message_id=message_id,
        position=0,
        kind="image",
        category="user_upload",
        mime_type="image/png",
        size_bytes=10,
        sha256=hashlib.sha256(data).hexdigest(),
        file_root_id=session_id,
        file_id=revision.file_id,
        file_revision=revision.revision,
        file_path=file_path,
        filename="bild.png",
    )
    db = AsyncMock(spec=AsyncSession)
    db.scalar.return_value = attachment
    monkeypatch.setattr(
        sessions_route, "AttachmentService", lambda: AttachmentService(sessions)
    )

    response = await download_attachment(
        session_id, message_id, attachment_id, user, db
    )

    statement = db.scalar.await_args.args[0]
    assert {session_id, message_id, attachment_id}.issubset(set(statement.compile().params.values()))
    assert Path(response.path).read_bytes() == b"image-data"
    assert response.media_type == "image/png"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["cross-origin-resource-policy"] == "same-origin"
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["content-disposition"].startswith("inline;")


async def test_session_delete_trashes_commits_and_purges_tree(
    tmp_path: Path, monkeypatch
) -> None:
    from mato_loop_backend.api.routes import sessions as sessions_route

    session_id = uuid4()
    user = _user()
    session = ChatSession(id=session_id, user_id=user.id, title="Test")
    sessions = SessionStorage(tmp_path / "sessions")
    session_path = sessions.provision(session_id)
    (session_path / "files" / "notiz.txt").write_text("Inhalt", encoding="utf-8")
    active_runs_result = MagicMock()
    active_runs_result.all.return_value = []
    db = AsyncMock(spec=AsyncSession)
    db.scalar.return_value = session
    db.scalars.return_value = active_runs_result
    monkeypatch.setattr(sessions_route, "SessionStorage", lambda: sessions)

    await delete_session(session_id, user, db)

    db.delete.assert_awaited_once_with(session)
    db.commit.assert_awaited_once()
    assert not session_path.exists()
    assert list(sessions.trash_dir.iterdir()) == []


async def test_session_delete_restores_tree_when_database_commit_fails(
    tmp_path: Path, monkeypatch
) -> None:
    from mato_loop_backend.api.routes import sessions as sessions_route

    session_id = uuid4()
    user = _user()
    session = ChatSession(id=session_id, user_id=user.id, title="Test")
    sessions = SessionStorage(tmp_path / "sessions")
    session_path = sessions.provision(session_id)
    stored_file = session_path / "files" / "notiz.txt"
    stored_file.write_text("Inhalt", encoding="utf-8")
    active_runs_result = MagicMock()
    active_runs_result.all.return_value = []
    db = AsyncMock(spec=AsyncSession)
    db.scalar.return_value = session
    db.scalars.return_value = active_runs_result
    db.commit.side_effect = RuntimeError("commit failed")
    monkeypatch.setattr(sessions_route, "SessionStorage", lambda: sessions)

    with pytest.raises(RuntimeError, match="commit failed"):
        await delete_session(session_id, user, db)

    assert stored_file.read_text(encoding="utf-8") == "Inhalt"
    assert list(sessions.trash_dir.iterdir()) == []
    db.rollback.assert_awaited_once()


async def test_attachment_download_rejects_incomplete_hierarchy() -> None:
    user = _user()
    db = AsyncMock(spec=AsyncSession)
    db.scalar.return_value = None

    with pytest.raises(ApiError) as exc_info:
        await download_attachment(uuid4(), uuid4(), uuid4(), user, db)

    assert exc_info.value.status_code == 404
    assert exc_info.value.code == "attachment.notFound"


async def test_session_rejects_unavailable_model_before_creation(monkeypatch) -> None:
    user = _user()
    provider = LlmProviderConfig(name="Local", adapter_type="openai-compatible")
    model = ModelConfig(
        provider_id=provider.id,
        model_id="missing",
        availability=ModelAvailability.MISSING,
    )
    settings = AiSettings(providers=[provider], models=[model], default_model_id=model.id)

    async def load_settings(_db, settings_type, **_kwargs):
        return settings if settings_type is AiSettings else MemorySettings()

    monkeypatch.setattr(SettingsStore, "load", load_settings)
    run = AgentRun(
        id=uuid4(),
        session_id=uuid4(),
        status="queued",
        model_config_id=model.id,
    )
    start = AsyncMock(return_value=run)
    monkeypatch.setattr(AgentRuntime, "start", start)
    from mato_loop_backend.api.routes import sessions as sessions_route

    storage = MagicMock()
    monkeypatch.setattr(sessions_route, "SessionStorage", lambda: storage)
    persisted_message = Message(
        id=uuid4(),
        session_id=run.session_id,
        role="user",
        content="Test",
    )
    db = AsyncMock(spec=AsyncSession)
    db.scalar.return_value = persisted_message

    with pytest.raises(ApiError) as exc_info:
        await create_session(SessionCreateRequest(content="Test"), user, db)

    assert exc_info.value.code == "session.modelUnavailable"
    start.assert_not_awaited()


async def test_required_search_unavailability_does_not_reject_user_message(monkeypatch) -> None:
    user = _user()
    session = ChatSession(
        id=uuid4(),
        user_id=user.id,
        title="Test",
        web_search_policy="required",
    )
    run = AgentRun(id=uuid4(), session_id=session.id, status="queued")
    user_message = Message(
        id=uuid4(), session_id=session.id, role="user", content="Aktuelle Frage"
    )
    db = AsyncMock(spec=AsyncSession)
    db.scalar.side_effect = [session, None, user_message]
    start = AsyncMock(return_value=run)
    monkeypatch.setattr(AgentRuntime, "start", start)
    provider = LlmProviderConfig(name="Local", adapter_type="openai-compatible")
    model = ModelConfig(provider_id=provider.id, model_id="agent")
    session.model_config_id = model.id
    load = AsyncMock(
        return_value=AiSettings(
            providers=[provider], models=[model], default_model_id=model.id
        )
    )
    monkeypatch.setattr(SettingsStore, "load", load)

    request = SendMessageRequest.model_validate(
        {
            "content": "Aktuelle Frage",
            "activeFile": {
                "root": "session",
                "rootIdentity": str(session.id),
                "path": "notes.txt",
                "sha256": "a" * 64,
                "dirty": True,
            },
        }
    )
    result = await send_message(session.id, request, user, db)

    start.assert_awaited_once_with(
        db,
        session,
        "Aktuelle Frage",
        run_id=None,
        attachments=None,
        active_file={
            "root": "session",
            "rootIdentity": str(session.id),
            "path": "notes.txt",
            "sha256": "a" * 64,
            "dirty": True,
        },
    )
    load.assert_awaited_once()
    assert result["runId"] == str(run.id)


async def test_existing_session_retry_reschedules_queued_run(monkeypatch) -> None:
    user = _user()
    session = ChatSession(id=uuid4(), user_id=user.id, title="Test")
    message = Message(
        id=uuid4(), session_id=session.id, role="user", content="Original"
    )
    run = AgentRun(
        id=uuid4(),
        session_id=session.id,
        input_message_id=message.id,
        status="queued",
        model_name="Test",
    )
    db = AsyncMock(spec=AsyncSession)
    db.scalar.side_effect = [session, run, message]
    db.get.return_value = session
    schedule = MagicMock()
    monkeypatch.setattr(AgentRuntime, "_schedule_run", schedule)

    result = await send_message(
        session.id,
        SendMessageRequest(content="retry", run_id=run.id),
        user,
        db,
    )

    assert result["runId"] == str(run.id)
    assert result["message"]["content"] == "Original"
    assert len(db.scalar.await_args_list) == 3
    schedule.assert_called_once_with(run)


async def test_existing_session_retry_rejects_owned_run_from_another_session() -> None:
    user = _user()
    requested = ChatSession(id=uuid4(), user_id=user.id, title="Requested")
    run = AgentRun(id=uuid4(), session_id=uuid4(), status="completed")
    db = AsyncMock(spec=AsyncSession)
    db.scalar.side_effect = [requested, run]

    with pytest.raises(ApiError) as exc_info:
        await send_message(
            requested.id,
            SendMessageRequest(content="retry", run_id=run.id),
            user,
            db,
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.code == "http.conflict"
    ownership_statement = db.scalar.await_args_list[1].args[0]
    assert user.id in ownership_statement.compile().params.values()


async def test_run_id_collision_with_another_owner_is_a_controlled_conflict(monkeypatch) -> None:
    user = _user()
    session = ChatSession(id=uuid4(), user_id=user.id, title="Test")
    run_id = uuid4()
    provider = LlmProviderConfig(name="Local", adapter_type="openai-compatible")
    model = ModelConfig(provider_id=provider.id, model_id="agent")
    session.model_config_id = model.id
    db = AsyncMock(spec=AsyncSession)
    db.scalar.side_effect = [session, None, None, None]
    monkeypatch.setattr(
        SettingsStore,
        "load",
        AsyncMock(
            return_value=AiSettings(
                providers=[provider], models=[model], default_model_id=model.id
            )
        ),
    )
    monkeypatch.setattr(
        AgentRuntime,
        "start",
        AsyncMock(side_effect=IntegrityError("insert", {}, Exception("duplicate"))),
    )

    with pytest.raises(ApiError) as exc_info:
        await send_message(
            session.id,
            SendMessageRequest(content="Nachricht", run_id=run_id),
            user,
            db,
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.code == "http.conflict"
    db.rollback.assert_awaited_once()


async def test_attachment_only_message_starts_run(monkeypatch) -> None:
    from mato_loop_backend.api.routes import sessions as sessions_route

    user = _user()
    session = ChatSession(id=uuid4(), user_id=user.id, title="Test")
    run = AgentRun(id=uuid4(), session_id=session.id, status="queued")
    user_message = Message(id=uuid4(), session_id=session.id, role="user", content="")
    db = AsyncMock(spec=AsyncSession)
    db.scalar.side_effect = [session, None, user_message]
    start = AsyncMock(return_value=run)
    monkeypatch.setattr(AgentRuntime, "start", start)
    provider = LlmProviderConfig(name="Local", adapter_type="openai-compatible")
    model = ModelConfig(provider_id=provider.id, model_id="agent")
    session.model_config_id = model.id
    monkeypatch.setattr(
        SettingsStore,
        "load",
        AsyncMock(
            return_value=AiSettings(
                providers=[provider], models=[model], default_model_id=model.id
            )
        ),
    )
    attachment = AttachmentInput(b"Bericht", "text/plain", "bericht.txt")

    result = await sessions_route._send_message(
        session.id,
        SendMessageRequest(content="   "),
        user,
        db,
        attachments=[attachment],
    )

    start.assert_awaited_once_with(
        db,
        session,
        "",
        run_id=None,
        attachments=[attachment],
        active_file=None,
    )
    assert result["message"]["content"] == ""


async def test_session_can_switch_to_public_model(monkeypatch) -> None:
    user = _user()
    provider = LlmProviderConfig(name="Cloud", adapter_type="openai-compatible")
    public_model = ModelConfig(provider_id=provider.id, model_id="public")
    settings = AiSettings(providers=[provider], models=[public_model])
    session = ChatSession(
        id=uuid4(),
        user_id=user.id,
        title="Privat gestartet",
        model_config_id=None,
        memory_mode="disabled",
    )
    monkeypatch.setattr(
        SettingsStore,
        "load",
        AsyncMock(return_value=settings),
    )
    db = AsyncMock(spec=AsyncSession)
    db.get.return_value = session
    db.scalar.return_value = None

    await update_session(
        session.id,
        SessionUpdateRequest(model_id=public_model.id),
        user,
        db,
    )

    assert session.model_config_id == public_model.id


async def test_session_can_override_reasoning_effort() -> None:
    user = _user()
    session = ChatSession(
        id=uuid4(),
        user_id=user.id,
        title="Test",
        memory_mode="disabled",
    )
    db = AsyncMock(spec=AsyncSession)
    db.get.return_value = session
    db.scalar.return_value = None

    result = await update_session(
        session.id,
        SessionUpdateRequest(reasoning_effort="ultra"),
        user,
        db,
    )

    assert session.reasoning_effort == "ultra"
    assert result["reasoningEffort"] == "ultra"


async def test_manual_title_change_skips_automatic_generation(monkeypatch) -> None:
    user = _user()
    session = ChatSession(
        id=uuid4(),
        user_id=user.id,
        title="Automatischer Entwurf",
        title_generation_status=TitleGenerationStatus.RUNNING.value,
        memory_mode="full",
        web_search_policy="available",
    )
    db = AsyncMock(spec=AsyncSession)
    db.get.return_value = session
    monkeypatch.setattr(
        SettingsStore,
        "load",
        AsyncMock(
            side_effect=lambda _db, settings_type, **_kwargs: settings_type()
        ),
    )

    await update_session(
        session.id,
        SessionUpdateRequest(title="Manueller Titel"),
        user,
        db,
    )

    assert session.title == "Manueller Titel"
    assert session.title_generation_status == TitleGenerationStatus.SKIPPED.value
    db.commit.assert_awaited_once()


async def test_session_can_be_pinned_during_active_run() -> None:
    user = _user()
    session_id = uuid4()
    session = ChatSession(
        id=session_id,
        user_id=user.id,
        title="Test",
        is_pinned=False,
        memory_mode="disabled",
        web_search_policy="available",
    )
    db = AsyncMock(spec=AsyncSession)
    db.get.return_value = session

    result = await update_session(
        session_id,
        SessionUpdateRequest(pinned=True),
        user,
        db,
    )

    assert session.is_pinned is True
    assert result["pinned"] is True
    db.scalar.assert_not_awaited()


async def test_control_center_state_can_be_replaced() -> None:
    user = _user()
    session = ChatSession(
        id=uuid4(),
        user_id=user.id,
        title="Test",
        memory_mode="disabled",
        control_center={
            "tabs": [{"app_id": "files", "pinned": False}],
            "active_app_id": "files",
        },
    )
    db = AsyncMock(spec=AsyncSession)
    db.get.return_value = session
    state = {
        "tabs": [
            {"app_id": "files", "pinned": True},
            {"app_id": "terminal_2", "pinned": False},
        ],
        "active_app_id": "terminal_2",
        "open": True,
        "file_view": {
            "scope": "session",
            "selected_path": "drafts/draft-id/plan.md",
            "selected_kind": "file",
            "viewed_file_path": "drafts/draft-id/plan.md",
            "root_identity": None,
            "execution_agent_id": None,
            "current_directory": None,
        },
    }

    result = await update_session(
        session.id, SessionUpdateRequest(control_center=state), user, db
    )

    assert session.control_center == state
    assert result["controlCenter"] == {
        "tabs": [
            {"appId": "files", "pinned": True},
            {"appId": "terminal_2", "pinned": False},
        ],
        "activeAppId": "terminal_2",
        "open": True,
        "fileView": {
            "scope": "session",
            "selectedPath": "drafts/draft-id/plan.md",
            "selectedKind": "file",
            "viewedFilePath": "drafts/draft-id/plan.md",
            "rootIdentity": None,
            "executionAgentId": None,
            "currentDirectory": None,
        },
    }
    db.commit.assert_awaited_once()


@pytest.mark.parametrize(
    "state",
    [
        {
            "tabs": [
                {"app_id": "files", "pinned": False},
                {"app_id": "files", "pinned": True},
            ],
            "active_app_id": "files",
        },
        {
            "tabs": [{"app_id": "files", "pinned": False}],
            "active_app_id": "files",
            "file_view": {
                "scope": "session",
                "selected_path": "drafts/../secret.txt",
                "selected_kind": "file",
            },
        },
        {
            "tabs": [{"app_id": "files", "pinned": False}],
            "active_app_id": "files",
            "file_view": {
                "scope": "filesystem",
                "selected_path": "report.txt",
                "selected_kind": "file",
                "viewed_file_path": "other.txt",
            },
        },
        {
            "tabs": [{"app_id": "../files", "pinned": False}],
            "active_app_id": "../files",
        },
        {
            "tabs": [{"app_id": "files", "pinned": False}],
            "active_app_id": None,
        },
        {"tabs": [], "active_app_id": "files"},
        {
            "tabs": [
                {"app_id": f"app_{index}", "pinned": False}
                for index in range(21)
            ],
            "active_app_id": "app_0",
        },
        None,
    ],
)
def test_control_center_state_rejects_invalid_full_state(state) -> None:
    with pytest.raises(ValidationError):
        SessionUpdateRequest(control_center=state)


async def test_control_center_state_can_be_empty() -> None:
    user = _user()
    session = ChatSession(
        id=uuid4(),
        user_id=user.id,
        title="Test",
        memory_mode="disabled",
    )
    db = AsyncMock(spec=AsyncSession)
    db.get.return_value = session

    result = await update_session(
        session.id,
        SessionUpdateRequest(control_center={"tabs": [], "active_app_id": None}),
        user,
        db,
    )

    assert session.control_center == {
        "tabs": [],
        "active_app_id": None,
        "open": False,
        "file_view": None,
    }
    assert result["controlCenter"] == {
        "tabs": [],
        "activeAppId": None,
        "open": False,
        "fileView": None,
    }


async def test_control_center_state_can_change_during_active_run() -> None:
    user = _user()
    session = ChatSession(
        id=uuid4(),
        user_id=user.id,
        title="Test",
        memory_mode="disabled",
    )
    db = AsyncMock(spec=AsyncSession)
    db.get.return_value = session

    await update_session(
        session.id,
        SessionUpdateRequest(
            control_center={
                "tabs": [{"app_id": "files", "pinned": True}],
                "active_app_id": "files",
            }
        ),
        user,
        db,
    )

    assert session.control_center["tabs"][0]["pinned"] is True
    db.scalar.assert_not_awaited()


async def test_control_center_update_is_owner_scoped() -> None:
    owner = _user()
    other = _user()
    session = ChatSession(id=uuid4(), user_id=owner.id, title="Privat")
    db = AsyncMock(spec=AsyncSession)
    db.get.return_value = session

    with pytest.raises(ApiError) as exc_info:
        await update_session(
            session.id,
            SessionUpdateRequest(control_center={"tabs": [], "active_app_id": None}),
            other,
            db,
        )

    assert exc_info.value.code == "session.notFound"
    db.flush.assert_not_awaited()


async def test_runtime_policy_cannot_change_during_active_run() -> None:
    user = _user()
    session_id = uuid4()
    session = ChatSession(
        id=session_id,
        user_id=user.id,
        title="Test",
        memory_mode="disabled",
        web_search_policy="available",
    )
    db = AsyncMock(spec=AsyncSession)
    db.get.return_value = session
    db.scalar.return_value = uuid4()

    with pytest.raises(ApiError) as exc_info:
        await update_session(
            session_id,
            SessionUpdateRequest(model_id=uuid4()),
            user,
            db,
        )

    assert exc_info.value.code == "session.runAlreadyActive"


async def test_model_cannot_change_during_active_run() -> None:
    user = _user()
    session_id = uuid4()
    session = ChatSession(
        id=session_id,
        user_id=user.id,
        title="Test",
        memory_mode="disabled",
        web_search_policy="available",
    )
    db = AsyncMock(spec=AsyncSession)
    db.get.return_value = session
    db.scalar.return_value = uuid4()

    with pytest.raises(ApiError) as exc_info:
        await update_session(
            session_id,
            SessionUpdateRequest(model_id=uuid4()),
            user,
            db,
        )

    assert exc_info.value.code == "session.runAlreadyActive"
    assert session.model_config_id is None
