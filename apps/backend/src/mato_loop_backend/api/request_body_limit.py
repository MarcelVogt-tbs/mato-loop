from __future__ import annotations

import json
from collections.abc import Awaitable, Callable

from starlette.types import Message, Receive, Scope, Send

from mato_loop_backend.attachments import MAX_FILE_ATTACHMENT_BYTES
from mato_loop_backend.error_contract import error_payload

_MULTIPART_OVERHEAD_BYTES = 1024 * 1024
TRANSCRIPTION_REQUEST_LIMIT = MAX_FILE_ATTACHMENT_BYTES + _MULTIPART_OVERHEAD_BYTES
SESSION_ATTACHMENTS_REQUEST_LIMIT = 4 * MAX_FILE_ATTACHMENT_BYTES + _MULTIPART_OVERHEAD_BYTES
_TOO_LARGE_BODY = json.dumps(
    error_payload("attachment.tooLarge"), separators=(",", ":")
).encode("utf-8")


class _RequestBodyTooLarge(Exception):
    pass


def _request_limit(scope: Scope) -> int | None:
    if scope.get("type") != "http" or scope.get("method") != "POST":
        return None
    path = scope.get("path")
    if path == "/api/media/transcriptions":
        return TRANSCRIPTION_REQUEST_LIMIT
    if path == "/api/sessions/with-attachments":
        return SESSION_ATTACHMENTS_REQUEST_LIMIT
    parts = str(path).split("/")
    if (
        len(parts) == 6
        and parts[1:3] == ["api", "sessions"]
        and parts[4:] == ["messages", "with-attachments"]
        and parts[3]
    ):
        return SESSION_ATTACHMENTS_REQUEST_LIMIT
    return None


async def _send_too_large(send: Send) -> None:
    await send(
        {
            "type": "http.response.start",
            "status": 413,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(_TOO_LARGE_BODY)).encode("ascii")),
            ],
        }
    )
    await send({"type": "http.response.body", "body": _TOO_LARGE_BODY})


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

        for name, value in scope.get("headers", []):
            if name.lower() != b"content-length":
                continue
            try:
                if int(value) > limit:
                    await _send_too_large(send)
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
            await _send_too_large(send)
