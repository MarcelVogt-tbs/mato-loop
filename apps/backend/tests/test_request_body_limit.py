import json

from mato_loop_backend.api import request_body_limit
from mato_loop_backend.api.request_body_limit import MediaRequestBodyLimitMiddleware


def scope(path: str, headers=()):
    return {
        "type": "http",
        "method": "POST",
        "path": path,
        "headers": list(headers),
    }


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
