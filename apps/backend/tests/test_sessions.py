import hashlib
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.agent import AgentRuntime
from mato_loop_backend.api.errors import ApiError
from mato_loop_backend.api.routes.sessions import (
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
    stream_run_events,
    update_session,
)
from mato_loop_backend.attachments import AttachmentService, AttachmentStorage
from mato_loop_backend.db.session import get_db_session
from mato_loop_backend.models import (
    AgentRun,
    ChatSession,
    Message,
    MessageAttachment,
    TitleGenerationStatus,
    UserAccount,
)
from mato_loop_backend.security import require_user
from mato_loop_backend.session_storage import SessionStorage
from mato_loop_backend.settings import AiSettings, MemorySettings, SettingsStore, TrustLevel
from mato_loop_backend.settings.models import (
    LlmProviderConfig,
    ModelAvailability,
    ModelConfig,
)


def _user() -> UserAccount:
    return UserAccount(id=uuid4(), username="test", password_hash="hash")


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
        return ai_settings if settings_type is AiSettings else memory_settings

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
        SessionCreateRequest(content="  Erste Nachricht  ", workspace_access="read-write"),
        user,
        db,
    )

    session = db.add.call_args.args[0]
    assert session.model_config_id == model.id
    assert session.user_id == user.id
    assert session.memory_mode == "full"
    assert session.web_search_policy == "available"
    assert session.working_directory is None
    assert session.workspace_access == "read-write"
    assert session.title_generation_status == TitleGenerationStatus.PENDING.value
    start.assert_awaited_once_with(db, session, "Erste Nachricht")
    assert result["session"]["id"] == str(session.id)
    assert result["session"]["titleGenerationStatus"] == "pending"
    assert result["session"]["webSearchPolicy"] == "available"
    assert "trustLevel" not in result["session"]
    assert result["runId"] == str(run.id)
    assert result["modelName"] == "Test"
    assert result["message"]["id"] == str(persisted_message.id)
    storage.provision.assert_called_once_with(session.id)


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
        return ai_settings if settings_type is AiSettings else memory_settings

    monkeypatch.setattr(SettingsStore, "load", load_settings)

    result = await session_defaults(user, AsyncMock(spec=AsyncSession))

    assert result["memoryMode"] == "full"
    assert result["effectiveMemoryMode"] == "disabled"
    assert result["memoryToolsAvailable"] is False
    assert result["memoryUnavailableReason"] == "memory.disabled"
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


async def test_failed_first_commit_removes_provisioned_session_tree(
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

    assert [item for item in sessions.sessions_dir.iterdir() if not item.name.startswith(".")] == []
    db.rollback.assert_awaited()


async def test_session_payload_contains_active_run() -> None:
    user = _user()
    session_id = uuid4()
    session = ChatSession(
        id=session_id,
        user_id=user.id,
        title="Test",
        is_pinned=True,
        memory_mode="disabled",
        web_search_policy="available",
        workspace_access="read-only",
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
    message_result = MagicMock()
    message_result.all.return_value = []
    db.scalars.return_value = message_result
    db.scalar.return_value = run

    result = await get_session(session_id, user, db)

    assert result["pinned"] is True
    assert result["activeRun"] == {
        "id": str(run.id),
        "status": "waiting_approval",
        "modelName": "Aktives Modell",
        "createdAt": created_at.isoformat(),
    }


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


async def test_active_run_can_be_cancelled(monkeypatch) -> None:
    user = _user()
    session_id = uuid4()
    session = ChatSession(id=session_id, user_id=user.id, title="Test")
    run = AgentRun(id=uuid4(), session_id=session_id, status="running")
    cancel_and_wait = AsyncMock(return_value=True)
    db = AsyncMock(spec=AsyncSession)
    db.get.side_effect = [session, run]

    async def refresh(_run) -> None:
        run.status = "cancelled"
        run.completed_at = datetime.now(UTC)

    db.refresh.side_effect = refresh
    monkeypatch.setattr(AgentRuntime, "cancel_and_wait", cancel_and_wait)

    result = await cancel_run(session_id, run.id, user, db)

    cancel_and_wait.assert_awaited_once_with(run.id)
    assert result["status"] == "cancelled"


async def test_terminal_run_cancel_is_idempotent(monkeypatch) -> None:
    user = _user()
    session_id = uuid4()
    session = ChatSession(id=session_id, user_id=user.id, title="Test")
    run = AgentRun(id=uuid4(), session_id=session_id, status="completed")
    cancel_and_wait = AsyncMock()
    db = AsyncMock(spec=AsyncSession)
    db.get.side_effect = [session, run]
    monkeypatch.setattr(AgentRuntime, "cancel_and_wait", cancel_and_wait)

    result = await cancel_run(session_id, run.id, user, db)

    cancel_and_wait.assert_not_awaited()
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


def test_message_payload_validates_file_actions_and_derives_download_url() -> None:
    session_id = uuid4()
    stored_action = {
        "operation": "copy",
        "root": "workspace",
        "rootIdentity": "original project",
        "path": "reports/result.txt",
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

    payload = message_payload(message)

    action = payload["fileActions"][0]
    query = parse_qs(urlparse(action.pop("downloadUrl")).query)
    assert action == stored_action
    assert query == {
        "path": ["reports/result.txt"],
        "scope": ["filesystem"],
        "session_id": [str(session_id)],
        "root_identity": ["original project"],
    }
    assert "downloadUrl" not in str(message.provider_data)


@pytest.mark.parametrize(
    "file_actions",
    [
        [
            {
                "operation": "write",
                "root": "session",
                "rootIdentity": str(uuid4()),
                "path": "files/result.txt",
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
            purpose="attachment",
            processing_status="ready",
            mime_type="image/png",
            size_bytes=123,
            sha256="a" * 64,
            storage_path=(
                "generated/00000000-0000-4000-8000-000000000000/bild.png"
            ),
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
            "purpose": "attachment",
            "processingStatus": "ready",
            "mimeType": "image/png",
            "sizeBytes": 123,
            "filename": "bild.png",
            "width": 10,
            "height": 20,
            "durationMs": None,
            "metadata": {},
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
    storage = AttachmentStorage(sessions)
    data = b"image-data"
    storage_path = storage.write(
        session_id, attachment_id, data, "bild.png", "user_upload"
    )
    attachment = MessageAttachment(
        id=attachment_id,
        message_id=message_id,
        position=0,
        kind="image",
        category="user_upload",
        mime_type="image/png",
        size_bytes=10,
        sha256=hashlib.sha256(data).hexdigest(),
        storage_path=storage_path,
        filename="bild.png",
    )
    db = AsyncMock(spec=AsyncSession)
    db.scalar.return_value = attachment
    monkeypatch.setattr(
        sessions_route, "AttachmentService", lambda: AttachmentService(storage)
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

    result = await send_message(
        session.id, SendMessageRequest(content="Aktuelle Frage"), user, db
    )

    start.assert_awaited_once_with(db, session, "Aktuelle Frage")
    load.assert_awaited_once()
    assert result["runId"] == str(run.id)


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


async def test_workspace_mode_is_preserved_without_workdir() -> None:
    user = _user()
    session_id = uuid4()
    session = ChatSession(
        id=session_id,
        user_id=user.id,
        title="Test",
        memory_mode="disabled",
        web_search_policy="available",
        working_directory="project",
        workspace_access="read-write",
    )
    db = AsyncMock(spec=AsyncSession)
    db.get.return_value = session
    db.scalar.return_value = None

    await update_session(
        session_id,
        SessionUpdateRequest(working_directory=None),
        user,
        db,
    )

    assert session.working_directory is None
    assert session.workspace_access == "read-write"
    assert db.scalar.await_count == 2
    assert "FOR UPDATE" in str(db.scalar.await_args_list[0].args[0])


async def test_manual_title_change_skips_automatic_generation(monkeypatch) -> None:
    user = _user()
    session = ChatSession(
        id=uuid4(),
        user_id=user.id,
        title="Automatischer Entwurf",
        title_generation_status=TitleGenerationStatus.RUNNING.value,
        memory_mode="full",
        web_search_policy="available",
        workspace_access="read-only",
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


async def test_write_mode_can_be_selected_without_workdir() -> None:
    user = _user()
    session_id = uuid4()
    session = ChatSession(
        id=session_id,
        user_id=user.id,
        title="Test",
        memory_mode="disabled",
        web_search_policy="available",
        working_directory=None,
        workspace_access="read-only",
    )
    db = AsyncMock(spec=AsyncSession)
    db.get.return_value = session
    db.scalar.return_value = None

    await update_session(
        session_id,
        SessionUpdateRequest(workspace_access="read-write"),
        user,
        db,
    )

    assert session.working_directory is None
    assert session.workspace_access == "read-write"


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
        workspace_access="read-only",
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


async def test_runtime_policy_cannot_change_during_active_run() -> None:
    user = _user()
    session_id = uuid4()
    session = ChatSession(
        id=session_id,
        user_id=user.id,
        title="Test",
        memory_mode="disabled",
        web_search_policy="available",
        working_directory=None,
        workspace_access="read-write",
    )
    db = AsyncMock(spec=AsyncSession)
    db.get.return_value = session
    db.scalar.return_value = uuid4()

    with pytest.raises(ApiError) as exc_info:
        await update_session(
            session_id,
            SessionUpdateRequest(workspace_access="read-only"),
            user,
            db,
        )

    assert exc_info.value.code == "session.runAlreadyActive"
    assert session.workspace_access == "read-write"


async def test_model_cannot_change_during_active_run() -> None:
    user = _user()
    session_id = uuid4()
    session = ChatSession(
        id=session_id,
        user_id=user.id,
        title="Test",
        memory_mode="disabled",
        web_search_policy="available",
        workspace_access="read-only",
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
