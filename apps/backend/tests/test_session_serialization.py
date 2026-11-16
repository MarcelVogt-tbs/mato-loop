import json
from uuid import uuid4

import pytest

from mato_loop_backend.models import Message
from mato_loop_backend.sessions.serialization import message_payload


@pytest.mark.parametrize("result", [
    "😀" * 10_000,
    {"output": "x" * 20_000, "error": {
        "code": "permission.denied", "field": "path" * 10_000,
        "params": {"reason": "x" * 20_000},
    }},
    {"output": "x" * 20_000, "isError": True},
    {"session": {"title": "Child session"}, "messages": ["x" * 20_000]},
])
def test_compact_tool_preview_is_bounded_and_preserves_display_status(result) -> None:
    content = result if isinstance(result, str) else json.dumps(result)
    message = Message(
        id=uuid4(), session_id=uuid4(), role="tool", content=content, attachments=[],
    )
    full = message_payload(message)
    preview = message_payload(message, compact=True)
    summary = preview["resultSummary"]
    expected_code = (
        "permission.denied" if isinstance(result, dict) and "error" in result
        else "mcp.callFailed" if isinstance(result, dict) and result.get("isError")
        else None
    )
    assert (
        preview["contentTruncated"], preview["sessionId"],
        len(preview["content"].encode("utf-8")) <= 2_000,
        len(json.dumps(summary)) < 2_000,
        (summary["error"] or {}).get("code"), summary.get("summary"),
        {key: value for key, value in preview.items()
         if key not in {"content", "contentTruncated", "sessionId", "resultSummary"}},
        full["content"],
    ) == (
        True, str(message.session_id), True, True, expected_code,
        "Child session" if isinstance(result, dict) and "session" in result else None,
        {key: value for key, value in full.items() if key != "content"}, content,
    )


def test_compact_tool_arguments_keep_display_and_full_message_unchanged() -> None:
    message = Message(
        id=uuid4(), session_id=uuid4(), role="assistant", content="", attachments=[],
        tool_calls=[
            {"id": "write", "name": "write_file", "arguments": {
                "path": "output.txt", "content": "😀" * 2_000,
            }},
            {"id": "read", "name": "read", "arguments": {"path": "output.txt"}},
        ],
    )
    full = message_payload(message)
    preview = message_payload(message, compact=True)
    assert (
        preview["argumentsTruncated"], preview["sessionId"],
        preview["toolCalls"][0], preview["toolCalls"][1],
        len(json.dumps(preview)) < 3_000, message_payload(message) == full,
    ) == (
        True, str(message.session_id),
        {**full["toolCalls"][0], "arguments": {}, "argumentsTruncated": True,
         "sessionId": str(message.session_id)},
        full["toolCalls"][1], True, True,
    )
