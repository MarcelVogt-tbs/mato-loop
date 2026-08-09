from __future__ import annotations

import json
from collections.abc import Awaitable, Callable

from starlette.types import Message, Receive, Scope, Send

from mato_loop_backend.attachments import (
    MAX_ATTACHMENTS_PER_MESSAGE,
    MAX_FILE_ATTACHMENT_BYTES,
)
from mato_loop_backend.error_contract import error_payload
from mato_loop_backend.workspace import MAX_EDITABLE_TEXT_BYTES

_MULTIPART_OVERHEAD_BYTES = 1024 * 1024
TRANSCRIPTION_REQUEST_LIMIT = MAX_FILE_ATTACHMENT_BYTES + _MULTIPART_OVERHEAD_BYTES
SESSION_ATTACHMENTS_REQUEST_LIMIT = (
    MAX_ATTACHMENTS_PER_MESSAGE * MAX_FILE_ATTACHMENT_BYTES + _MULTIPART_OVERHEAD_BYTES
)
WORKSPACE_UPLOAD_REQUEST_LIMIT = MAX_FILE_ATTACHMENT_BYTES + _MULTIPART_OVERHEAD_BYTES
# JSON escapes each ASCII control byte as up to six bytes.
WORKSPACE_FILE_EDIT_REQUEST_LIMIT = 6 * MAX_EDITABLE_TEXT_BYTES + 64 * 1024
QUESTION_ANSWERS_REQUEST_LIMIT = 8 * 1024 * 1024
_TOO_LARGE_BODY = json.dumps(
    error_payload("attachment.tooLarge"), separators=(",", ":")
).encode("utf-8")
_FILE_TOO_LARGE_BODY = json.dumps(
    error_payload("file.tooLarge"), separators=(",", ":")
).encode("utf-8")
_QUESTION_ANSWER_TOO_LARGE_BODY = json.dumps(
    error_payload("question.answerTooLarge"), separators=(",", ":")
).encode("utf-8")


class _RequestBodyTooLarge(Exception):
    pass


def _is_question_answer_path(path: object) -> bool:
    parts = str(path).split("/")
    return (
        len(parts) == 5
        and parts[1:3] == ["api", "questions"]
        and parts[3]
        and parts[4] == "answers"
    )


def _request_limit(scope: Scope) -> int | None:
    if scope.get("type") != "http":
        return None
    path = scope.get("path")
    method = scope.get("method")
    if path == "/api/workspace/files" and method == "PUT":
        return WORKSPACE_FILE_EDIT_REQUEST_LIMIT
    if method != "POST":
        return None
    if _is_question_answer_path(path):
        return QUESTION_ANSWERS_REQUEST_LIMIT
    if path == "/api/media/transcriptions":
        return TRANSCRIPTION_REQUEST_LIMIT
    if path == "/api/sessions/with-attachments":
        return SESSION_ATTACHMENTS_REQUEST_LIMIT
    if path == "/api/workspace/files":
        return WORKSPACE_UPLOAD_REQUEST_LIMIT
    parts = str(path).split("/")
    if (
        len(parts) == 6
        and parts[1:3] == ["api", "sessions"]
        and parts[4:] == ["messages", "with-attachments"]
        and parts[3]
    ):
        return SESSION_ATTACHMENTS_REQUEST_LIMIT
    return None


async def _send_too_large(send: Send, body: bytes) -> None:
    await send(
        {
            "type": "http.response.start",
            "status": 413,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode("ascii")),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


class MediaRequestBodyLimitMiddleware:
    def __init__(
        self,
        app: Callable[[Scope, Receive, Send], Awaitable[None]],
    ) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        limit = _request_limit(scope)
        if limit is None:
            await self.app(scope, receive, send)
            return
        path = scope.get("path")
        if path == "/api/workspace/files":
            error_body = _FILE_TOO_LARGE_BODY
        elif _is_question_answer_path(path):
            error_body = _QUESTION_ANSWER_TOO_LARGE_BODY
        else:
            error_body = _TOO_LARGE_BODY

        for name, value in scope.get("headers", []):
            if name.lower() != b"content-length":
                continue
            try:
                if int(value) > limit:
                    await _send_too_large(send, error_body)
                    return
            except ValueError:
                pass

        total = 0

        async def limited_receive() -> Message:
            nonlocal total
            message = await receive()
            if message["type"] == "http.request":
                total += len(message.get("body", b""))
                if total > limit:
                    raise _RequestBodyTooLarge
            return message

        try:
            await self.app(scope, limited_receive, send)
        except _RequestBodyTooLarge:
            await _send_too_large(send, error_body)
