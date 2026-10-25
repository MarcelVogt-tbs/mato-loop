import json

import pytest

from mato_loop_backend.api import request_body_limit
from mato_loop_backend.api.request_body_limit import MediaRequestBodyLimitMiddleware
from mato_loop_backend.execution.transfers import MAX_TRANSFER_BYTES


def scope(path: str, headers=(), *, method: str = "POST"):
    return {
        "type": "http",
        "method": method,
        "path": path,
        "headers": list(headers),
    }


@pytest.mark.parametrize("path", [
    "/api/workspace/files",
    "/api/sessions/with-attachments",
    "/api/sessions/session/messages/with-attachments",
    "/api/sessions/session/runs/run/messages/with-attachments",
])
def test_file_upload_body_limit_allows_one_gib_and_multipart_overhead(path):
    assert request_body_limit._request_limit(scope(path)) == MAX_TRANSFER_BYTES + 1024 * 1024


async def test_content_length_is_rejected_before_body_read(monkeypatch) -> None:
    monkeypatch.setattr(request_body_limit, "TRANSCRIPTION_REQUEST_LIMIT", 4)
    app_called = False
    received = False
    messages = []

    async def app(_scope, _receive, _send):
        nonlocal app_called
        app_called = True

    async def receive():
        nonlocal received
        received = True
        return {"type": "http.request", "body": b""}

    async def send(message):
        messages.append(message)

    middleware = MediaRequestBodyLimitMiddleware(app)
    await middleware(
        scope("/api/media/transcriptions", [(b"content-length", b"5")]),
        receive,
        send,
    )

    assert app_called is False
    assert received is False
    assert messages[0]["status"] == 413
    assert json.loads(messages[1]["body"]) == {
        "error": {
            "code": "attachment.tooLarge",
            "message": "The attachment is too large.",
        }
    }


async def test_chunked_session_body_is_counted_across_all_chunks(monkeypatch) -> None:
    monkeypatch.setattr(request_body_limit, "SESSION_ATTACHMENTS_REQUEST_LIMIT", 4)
    app_completed = False
    messages = []
    chunks = iter(
        [
            {"type": "http.request", "body": b"12", "more_body": True},
            {"type": "http.request", "body": b"345", "more_body": False},
        ]
    )

    async def app(_scope, receive, _send):
        nonlocal app_completed
        while True:
            message = await receive()
            if not message.get("more_body", False):
                break
        app_completed = True

    async def receive():
        return next(chunks)

    async def send(message):
        messages.append(message)

    middleware = MediaRequestBodyLimitMiddleware(app)
    await middleware(
        scope(f"/api/sessions/{'a' * 36}/messages/with-attachments"), receive, send
    )

    assert app_completed is False
    assert messages[0]["status"] == 413
    assert json.loads(messages[1]["body"])["error"]["code"] == "attachment.tooLarge"


async def test_workspace_upload_uses_file_error(monkeypatch) -> None:
    monkeypatch.setattr(request_body_limit, "WORKSPACE_UPLOAD_REQUEST_LIMIT", 4)
    messages = []

    async def app(_scope, _receive, _send):
        raise AssertionError("Oversized request reached the application")

    async def receive():
        return {"type": "http.request", "body": b""}

    async def send(message):
        messages.append(message)

    middleware = MediaRequestBodyLimitMiddleware(app)
    await middleware(
        scope("/api/workspace/files", [(b"content-length", b"5")]),
        receive,
        send,
    )

    assert messages[0]["status"] == 413
    assert json.loads(messages[1]["body"])["error"]["code"] == "file.tooLarge"


async def test_workspace_file_edit_has_json_body_limit(monkeypatch) -> None:
    monkeypatch.setattr(request_body_limit, "WORKSPACE_FILE_EDIT_REQUEST_LIMIT", 4)
    messages = []

    async def app(_scope, _receive, _send):
        raise AssertionError("Oversized request reached the application")

    async def receive():
        return {"type": "http.request", "body": b""}

    async def send(message):
        messages.append(message)

    middleware = MediaRequestBodyLimitMiddleware(app)
    await middleware(
        scope(
            "/api/workspace/files",
            [(b"content-length", b"5")],
            method="PUT",
        ),
        receive,
        send,
    )

    assert messages[0]["status"] == 413
    assert json.loads(messages[1]["body"])["error"]["code"] == "file.tooLarge"


async def test_question_answers_have_json_body_limit(monkeypatch) -> None:
    monkeypatch.setattr(request_body_limit, "QUESTION_ANSWERS_REQUEST_LIMIT", 4)
    messages = []

    async def app(_scope, _receive, _send):
        raise AssertionError("Oversized request reached the application")

    async def receive():
        return {"type": "http.request", "body": b""}

    async def send(message):
        messages.append(message)

    middleware = MediaRequestBodyLimitMiddleware(app)
    await middleware(
        scope(
            f"/api/questions/{'a' * 36}/answers",
            [(b"content-length", b"5")],
        ),
        receive,
        send,
    )

    assert messages[0]["status"] == 413
    assert json.loads(messages[1]["body"])["error"]["code"] == "question.answerTooLarge"


async def test_agent_profiles_have_json_body_limit(monkeypatch) -> None:
    monkeypatch.setattr(request_body_limit, "AGENT_PROFILE_REQUEST_LIMIT", 4)
    messages = []

    async def app(_scope, _receive, _send):
        raise AssertionError("Oversized request reached the application")

    async def receive():
        return {"type": "http.request", "body": b""}

    async def send(message):
        messages.append(message)

    middleware = MediaRequestBodyLimitMiddleware(app)
    await middleware(
        scope(
            f"/api/agent-profiles/{'a' * 36}",
            [(b"content-length", b"5")],
            method="PUT",
        ),
        receive,
        send,
    )

    assert messages[0]["status"] == 413
    assert json.loads(messages[1]["body"])["error"]["code"] == "validation.tooLong"
