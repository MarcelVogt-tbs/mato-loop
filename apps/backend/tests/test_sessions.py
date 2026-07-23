from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.agent import AgentRuntime
from mato_loop_backend.api.errors import ApiError
from mato_loop_backend.api.routes.sessions import (
    SessionCreateRequest,
    SessionUpdateRequest,
    cancel_run,
    create_session,
    delete_session,
    download_attachment,
    get_session,
    message_payload,
    session_defaults,
    update_session,
)
from mato_loop_backend.attachments import AttachmentStorage
from mato_loop_backend.models import (
    AgentRun,
    ChatSession,
    Message,
    MessageAttachment,
    TitleGenerationStatus,
    UserAccount,
)
from mato_loop_backend.settings import AiSettings, MemorySettings, SettingsStore, TrustLevel
from mato_loop_backend.settings.models import (
    LlmProviderConfig,
    ModelAvailability,
    ModelConfig,
)


def _user() -> UserAccount:
    return UserAccount(id=uuid4(), username="test", password_hash="hash")


async def test_session_is_created_with_first_message_and_run(monkeypatch) -> None:
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
    db = AsyncMock(spec=AsyncSession)
    db.add.side_effect = lambda session: setattr(session, "id", uuid4())

    result = await create_session(
        SessionCreateRequest(content="  Erste Nachricht  ", workspace_access="read-write"),
        user,
        db,
    )

    session = db.add.call_args.args[0]
    assert session.model_config_id == model.id
    assert session.user_id == user.id
    assert session.trust_level == "private"
    assert session.memory_mode == "full"
    assert session.web_search_policy == "available"
    assert session.working_directory is None
    assert session.workspace_access == "read-write"
    assert session.title_generation_status == TitleGenerationStatus.PENDING.value
    start.assert_awaited_once_with(db, session, "Erste Nachricht")
    assert result["session"]["id"] == str(session.id)
    assert result["session"]["webSearchPolicy"] == "available"
    assert result["runId"] == str(run.id)
    assert result["modelName"] == "Test"


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


async def test_session_is_not_created_for_blank_first_message() -> None:
    user = _user()
    db = AsyncMock(spec=AsyncSession)

    with pytest.raises(ApiError) as exc_info:
        await create_session(SessionCreateRequest(content="   "), user, db)

    assert exc_info.value.status_code == 422
    assert exc_info.value.code == "validation.required"
    assert exc_info.value.field == "content"
    db.add.assert_not_called()


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
            mime_type="image/png",
            size_bytes=123,
            sha256="a" * 64,
            storage_key="ab/abcdefabcdefabcdefabcdefabcdefab",
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
            "mimeType": "image/png",
            "sizeBytes": 123,
            "filename": "bild.png",
            "width": 10,
            "height": 20,
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
    storage = AttachmentStorage(tmp_path / "attachments")
    storage_key = storage.write(b"image-data")
    attachment = MessageAttachment(
        id=attachment_id,
        message_id=message_id,
        position=0,
        kind="image",
        mime_type="image/png",
        size_bytes=10,
        sha256="a" * 64,
        storage_key=storage_key,
    )
    db = AsyncMock(spec=AsyncSession)
    db.scalar.return_value = attachment
    monkeypatch.setattr(sessions_route, "AttachmentStorage", lambda: storage)

    response = await download_attachment(
        session_id, message_id, attachment_id, user, db
    )

    statement = db.scalar.await_args.args[0]
    assert {session_id, message_id, attachment_id}.issubset(set(statement.compile().params.values()))
    assert Path(response.path).read_bytes() == b"image-data"
    assert response.media_type == "image/png"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["cross-origin-resource-policy"] == "same-origin"
    assert response.headers["cache-control"] == "private"


async def test_session_delete_removes_attachment_files_after_database_commit(monkeypatch) -> None:
    from mato_loop_backend.api.routes import sessions as sessions_route

    session_id = uuid4()
    user = _user()
    session = ChatSession(id=session_id, user_id=user.id, title="Test")
    active_runs_result = MagicMock()
    active_runs_result.all.return_value = []
    storage_keys_result = MagicMock()
    storage_keys_result.all.return_value = ["ab/abcdefabcdefabcdefabcdefabcdefab"]
    db = AsyncMock(spec=AsyncSession)
    db.get.return_value = session
    db.scalars.side_effect = [active_runs_result, storage_keys_result]
    delete_files = AsyncMock()
    service = MagicMock()
    service.delete_files = delete_files
    monkeypatch.setattr(sessions_route, "AttachmentService", lambda: service)

    await delete_session(session_id, user, db)

    db.delete.assert_awaited_once_with(session)
    db.commit.assert_awaited_once()
    delete_files.assert_awaited_once_with(["ab/abcdefabcdefabcdefabcdefabcdefab"])


async def test_attachment_download_rejects_incomplete_hierarchy() -> None:
    user = _user()
    db = AsyncMock(spec=AsyncSession)
    db.scalar.return_value = None

    with pytest.raises(ApiError) as exc_info:
        await download_attachment(uuid4(), uuid4(), uuid4(), user, db)

    assert exc_info.value.status_code == 404
    assert exc_info.value.code == "attachment.notFound"


async def test_session_rejects_model_missing_from_provider(monkeypatch) -> None:
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

    with pytest.raises(ApiError) as exc_info:
        await create_session(
            SessionCreateRequest(content="Test"), user, AsyncMock(spec=AsyncSession)
        )

    assert exc_info.value.code == "session.modelUnavailable"


async def test_private_session_can_switch_to_public_model(monkeypatch) -> None:
    user = _user()
    provider = LlmProviderConfig(name="Cloud", adapter_type="openai-compatible")
    public_model = ModelConfig(provider_id=provider.id, model_id="public")
    settings = AiSettings(providers=[provider], models=[public_model])
    session = ChatSession(
        id=uuid4(),
        user_id=user.id,
        title="Privat gestartet",
        model_config_id=None,
        trust_level="private",
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
    assert session.trust_level == "private"


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
