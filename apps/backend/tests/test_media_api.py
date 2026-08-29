import asyncio
import json
import logging
import wave
from io import BytesIO
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.datastructures import Headers, UploadFile

from mato_loop_backend.agent import AgentRuntime
from mato_loop_backend.api.errors import ApiError
from mato_loop_backend.api.routes import sessions as sessions_route
from mato_loop_backend.api.routes.media import create_transcription
from mato_loop_backend.attachments import AttachmentError, AttachmentInput
from mato_loop_backend.models import ChatSession, UserAccount
from mato_loop_backend.providers import TranscriptionResult
from mato_loop_backend.providers.base import ProviderError
from mato_loop_backend.providers.media import MediaService
from mato_loop_backend.settings import InstanceLimits, SettingsStore, ToolSettings


def wav_bytes() -> bytes:
    buffer = BytesIO()
    with wave.open(buffer, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(8_000)
        audio.writeframes(b"\x00\x00" * 80)
    return buffer.getvalue()


def upload() -> UploadFile:
    return UploadFile(
        BytesIO(wav_bytes()),
        filename="aufnahme.wav",
        headers=Headers({"content-type": "audio/wav"}),
    )


async def test_direct_transcription_obeys_policy_and_returns_text(monkeypatch) -> None:
    settings = ToolSettings.model_validate(
        {"transcribeAudio": {"policy": "ask", "timeoutSeconds": 175}}
    )
    monkeypatch.setattr(
        SettingsStore,
        "load",
        AsyncMock(side_effect=[settings, InstanceLimits()]),
    )
    transcribe = AsyncMock(return_value=TranscriptionResult("Gesprochener Text"))
    monkeypatch.setattr(MediaService, "transcribe_audio", transcribe)
    db = AsyncMock(spec=AsyncSession)
    user = UserAccount(id=uuid4(), username="test", password_hash="hash")

    result = await create_transcription(upload(), db, user, "de")

    assert result == {"text": "Gesprochener Text"}
    assert transcribe.await_args.kwargs["audio"] == wav_bytes()
    assert transcribe.await_args.kwargs["language"] == "de"
    assert transcribe.await_args.kwargs["timeout"] == 175


async def test_direct_transcription_returns_structured_timeout(monkeypatch) -> None:
    settings = ToolSettings.model_validate(
        {"transcribeAudio": {"policy": "auto", "timeoutSeconds": 480}}
    )
    monkeypatch.setattr(
        SettingsStore,
        "load",
        AsyncMock(side_effect=[settings, InstanceLimits()]),
    )
    monkeypatch.setattr(
        MediaService,
        "transcribe_audio",
        AsyncMock(
            side_effect=ProviderError(
                "timed out",
                code="provider.timeout",
                params={"timeoutSeconds": 480, "retryable": True},
            )
        ),
    )

    with pytest.raises(ApiError) as exc_info:
        await create_transcription(
            upload(),
            AsyncMock(spec=AsyncSession),
            UserAccount(id=uuid4(), username="test", password_hash="hash"),
        )

    assert exc_info.value.status_code == 504
    assert exc_info.value.code == "provider.timeout"
    assert exc_info.value.params == {"timeoutSeconds": 480, "retryable": True}


async def test_direct_transcription_rejects_unavailable_tool(monkeypatch) -> None:
    monkeypatch.setattr(
        SettingsStore,
        "load",
        AsyncMock(return_value=ToolSettings(tool_availability={"transcribe_audio": False})),
    )
    transcribe = AsyncMock()
    monkeypatch.setattr(MediaService, "transcribe_audio", transcribe)

    with pytest.raises(ApiError) as exc_info:
        await create_transcription(
            upload(),
            AsyncMock(spec=AsyncSession),
            UserAccount(id=uuid4(), username="test", password_hash="hash"),
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.code == "tool.unavailable"
    transcribe.assert_not_awaited()


async def test_session_multipart_routes_forward_attachments_without_text(monkeypatch) -> None:
    user = UserAccount(id=uuid4(), username="test", password_hash="hash")
    db = AsyncMock(spec=AsyncSession)
    create = AsyncMock(return_value={"created": True})
    send = AsyncMock(return_value={"runId": str(uuid4()), "modelName": "Test"})
    monkeypatch.setattr(sessions_route, "_create_session", create)
    monkeypatch.setattr(sessions_route, "_send_message", send)
    monkeypatch.setattr(
        SettingsStore, "load", AsyncMock(return_value=InstanceLimits())
    )
    create_payload = json.dumps({"content": ""})
    send_payload = json.dumps({"content": ""})

    result = await sessions_route.create_session_with_attachments(
        create_payload, [upload()], user, db
    )
    session_id = uuid4()
    sent = await sessions_route.send_message_with_attachments(
        session_id, send_payload, [upload()], user, db
    )

    assert result == {"created": True}
    assert sent["modelName"] == "Test"
    assert create.await_args.args[0].content == ""
    assert send.await_args.args[1].content == ""
    created_audio = create.await_args.args[3][0]
    sent_audio = send.await_args.args[4][0]
    assert isinstance(created_audio, AttachmentInput)
    assert created_audio.kind == "audio"
    assert created_audio.mime_type == "audio/wav"
    assert sent_audio.filename == "aufnahme.wav"


async def test_session_multipart_retries_skip_attachment_classification(monkeypatch) -> None:
    user = UserAccount(id=uuid4(), username="test", password_hash="hash")
    db = AsyncMock(spec=AsyncSession)
    run_id = uuid4()
    duplicate_response = {"runId": str(run_id), "message": {"attachments": [{"id": "old"}]}}
    duplicate = AsyncMock(return_value=duplicate_response)
    load = AsyncMock()
    uploads = AsyncMock()
    monkeypatch.setattr(sessions_route, "_duplicate_submission_response", duplicate)
    monkeypatch.setattr(SettingsStore, "load", load)
    monkeypatch.setattr(sessions_route, "_attachment_uploads", uploads)
    payload = json.dumps({"content": "retry", "run_id": str(run_id)})

    created = await sessions_route.create_session_with_attachments(
        payload, [upload()], user, db
    )
    session_id = uuid4()
    sent = await sessions_route.send_message_with_attachments(
        session_id, payload, [upload()], user, db
    )

    assert created is duplicate_response
    assert sent is duplicate_response
    load.assert_not_awaited()
    uploads.assert_not_awaited()


async def test_session_attachment_processing_limit_returns_413(monkeypatch) -> None:
    monkeypatch.setattr(
        SettingsStore, "load", AsyncMock(return_value=InstanceLimits())
    )
    monkeypatch.setattr(
        sessions_route,
        "_send_message",
        AsyncMock(side_effect=AttachmentError("attachment.textTooLarge")),
    )

    with pytest.raises(ApiError) as exc_info:
        await sessions_route.send_message_with_attachments(
            uuid4(),
            json.dumps({"content": "Analysiere das Log"}),
            [upload()],
            UserAccount(id=uuid4(), username="test", password_hash="hash"),
            AsyncMock(spec=AsyncSession),
        )

    assert exc_info.value.status_code == 413
    assert exc_info.value.code == "attachment.textTooLarge"


async def test_attachment_upload_count_is_bounded() -> None:
    files = [upload() for _ in range(9)]

    with pytest.raises(ApiError) as exc_info:
        await sessions_route._attachment_uploads(files)

    assert exc_info.value.code == "validation.tooManyItems"
    assert exc_info.value.params == {"maxItems": 8}


async def test_invalid_image_upload_logs_safe_decoder_context(caplog) -> None:
    data = b"\xff\xd8\xffprivate-image-bytes"
    image = UploadFile(
        BytesIO(data),
        filename="private-location-name.jpeg",
        headers=Headers({"content-type": "image/jpeg"}),
    )
    caplog.set_level(logging.WARNING, logger=sessions_route.__name__)

    with pytest.raises(ApiError) as exc_info:
        await sessions_route._attachment_uploads([image])

    assert exc_info.value.code == "attachment.invalidImage"
    assert f"size_bytes={len(data)}" in caplog.text
    assert "content_type='image/jpeg'" in caplog.text
    assert "detected_type='image/jpeg'" in caplog.text
    assert "suffix='.jpeg'" in caplog.text
    assert "diagnostic='verify:" in caplog.text
    assert "private-location-name" not in caplog.text
    assert "private-image-bytes" not in caplog.text


async def test_runtime_preserves_stored_audio_when_commit_outcome_is_uncertain(
    monkeypatch,
) -> None:
    from mato_loop_backend.agent import runtime as runtime_module
    from mato_loop_backend.models import MessageAttachment
    from mato_loop_backend.settings import AiSettings
    from mato_loop_backend.settings.models import LlmProviderConfig, ModelConfig

    provider = LlmProviderConfig(name="Test", adapter_type="openai-compatible")
    model = ModelConfig(provider_id=provider.id, model_id="test")
    settings = AiSettings(
        providers=[provider], models=[model], default_model_id=model.id
    )
    monkeypatch.setattr(
        SettingsStore,
        "load",
        AsyncMock(
            side_effect=lambda _db, settings_type, **_kwargs: (
                settings if settings_type is AiSettings else InstanceLimits()
            )
        ),
    )
    monkeypatch.setattr(SettingsStore, "load_optional", AsyncMock(return_value=None))
    attachment = MessageAttachment(
        id=uuid4(),
        message_id=uuid4(),
        position=0,
        kind="audio",
        mime_type="audio/wav",
        size_bytes=100,
        sha256="a" * 64,
        file_path=(
            "attachments/00000000-0000-4000-8000-000000000000/audio.wav"
        ),
    )
    service = AsyncMock()
    service.create_attachment.return_value = attachment
    monkeypatch.setattr(runtime_module, "AttachmentService", lambda **_kwargs: service)
    db = AsyncMock(spec=AsyncSession)
    db.commit.side_effect = RuntimeError("commit failed")
    session = ChatSession(
        id=uuid4(),
        user_id=uuid4(),
        title="Test",
        model_config_id=model.id,
    )
    db.scalar.side_effect = [session, None, True, True]

    with pytest.raises(RuntimeError, match="commit failed"):
        await AgentRuntime.start(
            db,
            session,
            "Nachricht",
            attachments=[AttachmentInput(wav_bytes(), "audio/wav", "audio.wav", "audio")],
        )

    service.create_attachment.assert_awaited_once()
    service.discard.assert_not_awaited()
    db.rollback.assert_awaited_once()


async def test_runtime_waits_for_commit_before_propagating_cancellation(
    monkeypatch,
) -> None:
    from mato_loop_backend.agent import runtime as runtime_module
    from mato_loop_backend.models import MessageAttachment
    from mato_loop_backend.settings import AiSettings
    from mato_loop_backend.settings.models import LlmProviderConfig, ModelConfig

    provider = LlmProviderConfig(name="Test", adapter_type="openai-compatible")
    model = ModelConfig(provider_id=provider.id, model_id="test")
    settings = AiSettings(
        providers=[provider], models=[model], default_model_id=model.id
    )
    monkeypatch.setattr(
        SettingsStore,
        "load",
        AsyncMock(
            side_effect=lambda _db, settings_type, **_kwargs: (
                settings if settings_type is AiSettings else InstanceLimits()
            )
        ),
    )
    monkeypatch.setattr(SettingsStore, "load_optional", AsyncMock(return_value=None))
    attachment = MessageAttachment(
        id=uuid4(),
        message_id=uuid4(),
        position=0,
        kind="audio",
        mime_type="audio/wav",
        size_bytes=100,
        sha256="a" * 64,
        file_path=(
            "attachments/00000000-0000-4000-8000-000000000000/audio.wav"
        ),
    )
    service = AsyncMock()
    service.create_attachment.return_value = attachment
    monkeypatch.setattr(runtime_module, "AttachmentService", lambda **_kwargs: service)
    commit_started = asyncio.Event()
    finish_commit = asyncio.Event()
    committed = False

    async def commit() -> None:
        nonlocal committed
        commit_started.set()
        await finish_commit.wait()
        committed = True

    db = AsyncMock(spec=AsyncSession)
    db.commit.side_effect = commit
    session = ChatSession(
        id=uuid4(),
        user_id=uuid4(),
        title="Test",
        model_config_id=model.id,
    )
    db.scalar.side_effect = [session, None, True, True]
    schedule = MagicMock()
    monkeypatch.setattr(AgentRuntime, "_schedule_run", schedule)
    task = asyncio.create_task(
        AgentRuntime.start(
            db,
            session,
            "Nachricht",
            attachments=[AttachmentInput(wav_bytes(), "audio/wav", "audio.wav", "audio")],
        )
    )
    await commit_started.wait()

    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    finish_commit.set()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert committed is True
    schedule.assert_called_once()
    service.discard.assert_not_awaited()
    db.rollback.assert_not_awaited()
