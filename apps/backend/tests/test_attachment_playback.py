import json
import subprocess
import wave
from io import BytesIO
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

import mato_loop_backend.media.processing as media_processing
from mato_loop_backend.agent.projection import message_dict
from mato_loop_backend.api.errors import ApiError
from mato_loop_backend.api.routes import sessions as routes
from mato_loop_backend.files.service import RootFileService
from mato_loop_backend.files.storage import SessionStorage
from mato_loop_backend.media.attachments import AttachmentService
from mato_loop_backend.models import Message, MessageAttachment, UserAccount
from mato_loop_backend.sessions.serialization import message_payload


@pytest.mark.parametrize("mime_type", [
    "audio/ogg", "audio/mpeg", "audio/webm", "audio/mp4", "audio/aac", "audio/flac",
])
async def test_persisted_audio_playback(tmp_path, monkeypatch, mime_type):
    session_id, message_id, attachment_id = uuid4(), uuid4(), uuid4()
    storage = SessionStorage(tmp_path / "sessions")
    storage.provision(session_id)
    files = RootFileService(session_id, session_storage=storage)
    original = b"compressed audio"
    source = files.create_output_file("original.audio", original)
    buffer = BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(b"\0\0" * 160)
    pcm = buffer.getvalue()

    def run_media(command, data, _timeout):
        assert data == original
        output = json.dumps({
            "streams": [{"codec_type": "audio", "channels": 1, "sample_rate": "16000"}],
            "format": {"duration": "0.01"},
        }).encode() if command[0] == "ffprobe" else b"\0\0" * 160
        return subprocess.CompletedProcess(command, 0, output, b"")

    monkeypatch.setattr(media_processing, "_run_media", run_media)
    companion = files.create_output_file("playback.wav", pcm)
    service = AttachmentService(storage)
    attachment = MessageAttachment(
        id=attachment_id, message_id=message_id, kind="audio",
        processing_status="ready", mime_type=mime_type, filename="original.audio",
        size_bytes=source.size_bytes, sha256=source.sha256,
        file_root_id=session_id, file_id=source.file_id,
        file_revision=source.revision, file_path=source.path, duration_ms=10,
        metadata_json=service._companion_metadata(session_id, companion),
    )
    message = Message(id=message_id, session_id=session_id, role="user", content="")
    message.attachments = [attachment]
    assert "companion" not in message_dict(message)["content"]
    payload = message_payload(message)["attachments"][0]
    url = f"/api/sessions/{session_id}/messages/{message_id}/attachments/{attachment_id}"
    assert payload["url"] == url
    assert payload["playbackUrl"] == url + "?playback=true"
    assert payload["playbackMimeType"] == "audio/wav"
    for field, value in [("kind", "image"), ("processing_status", "failed"),
                         ("mime_type", "audio/wav"), ("mime_type", "audio/unknown")]:
        previous = getattr(attachment, field)
        setattr(attachment, field, value)
        assert "playbackUrl" not in message_payload(message)["attachments"][0]
        setattr(attachment, field, previous)

    monkeypatch.setattr(routes, "AttachmentService", lambda: service)
    db = AsyncMock(spec=AsyncSession)
    db.scalar.return_value = attachment
    user = UserAccount(id=uuid4())
    response = await routes.download_attachment(
        session_id, message_id, attachment_id, user, db, playback=True
    )
    params = db.scalar.await_args.args[0].compile().params.values()
    assert {session_id, message_id, attachment_id, user.id, "chat"}.issubset(set(params))
    assert response.body == pcm
    assert response.media_type == "audio/wav"
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["cross-origin-resource-policy"] == "same-origin"
    original_response = await routes.download_attachment(
        session_id, message_id, attachment_id, user, db
    )
    assert Path(original_response.path).read_bytes() == original
    assert original_response.media_type == mime_type

    metadata = attachment.metadata_json
    for value in [{}, {**metadata, "companionFileRootId": str(uuid4())},
                  {**metadata, "companionFileRevision": 99},
                  {**metadata, "companionSha256": "0" * 64}]:
        attachment.metadata_json = value
        assert message_payload(message)["attachments"][0]["playbackUrl"] == url + "?playback=true"
        assert await service.read_audio(attachment, session_id) == pcm
    attachment.metadata_json = metadata
    companion_path = files.revision_path(companion.file_id, companion.revision)
    companion_path.write_bytes(b"corrupt")
    assert await service.read_audio(attachment, session_id) == pcm
    assert companion_path.read_bytes() == b"corrupt"
    companion_path.unlink()
    assert await service.read_audio(attachment, session_id) == pcm
    assert not companion_path.exists()

    monkeypatch.setattr(media_processing, "_run_media", lambda command, *_: subprocess.CompletedProcess(command, 1, b"", b"private error"))
    with pytest.raises(ApiError, match="attachment.notFound"):
        await routes.download_attachment(
            session_id, message_id, attachment_id, user, db, playback=True
        )
    monkeypatch.setattr(media_processing, "_run_media", run_media)
    files.revision_path(source.file_id, source.revision).write_bytes(b"corrupt")
    with pytest.raises(ApiError, match="attachment.notFound"):
        await routes.download_attachment(
            session_id, message_id, attachment_id, user, db, playback=True
        )
    db.scalar.return_value = None
    with pytest.raises(ApiError) as error:
        await routes.download_attachment(
            session_id, message_id, attachment_id, UserAccount(id=uuid4()), db, playback=True
        )
    assert (error.value.status_code, error.value.code) == (404, "attachment.notFound")
