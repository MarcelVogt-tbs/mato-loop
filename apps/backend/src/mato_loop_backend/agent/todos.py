from __future__ import annotations

from collections.abc import Iterable
from typing import Any
from uuid import UUID

from pydantic import ValidationError

from mato_loop_backend.models import Message
from mato_loop_backend.tools.arguments import TodoWriteArguments


def session_todos(
    messages: Iterable[Message], *, latest_run_id: UUID | None = None,
) -> list[dict[str, Any]]:
    for message in reversed(list(messages)):
        todos = todo_snapshot(message, latest_run_id=latest_run_id)
        if todos is not None:
            return todos
    return []


def todo_snapshot(
    message: Message, *, latest_run_id: UUID | None = None,
) -> list[dict[str, Any]] | None:
    runtime = (
        message.provider_data.get("_matoloop")
        if isinstance(message.provider_data, dict)
        else None
    )
    if (
        message.role != "tool"
        or message.error_code is not None
        or not isinstance(runtime, dict)
        or runtime.get("toolName") != "todo_write"
        or runtime.get("errorCode") is not None
    ):
        return None
    try:
        arguments = TodoWriteArguments.model_validate_json(message.content)
    except (ValidationError, ValueError):
        return None
    todos = arguments.model_dump(mode="json")["todos"]
    if (
        latest_run_id is not None
        and runtime.get("runId") != str(latest_run_id)
        and all(todo["status"] in {"completed", "cancelled"} for todo in todos)
    ):
        return []
    return todos


def todo_context(todos: list[dict[str, Any]]) -> str:
    return TodoWriteArguments(todos=todos).model_dump_json()
