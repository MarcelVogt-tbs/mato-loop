from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from pydantic import ValidationError

from mato_loop_backend.models import Message
from mato_loop_backend.tools.arguments import TodoWriteArguments


def session_todos(messages: Iterable[Message]) -> list[dict[str, Any]]:
    for message in reversed(list(messages)):
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
            continue
        try:
            arguments = TodoWriteArguments.model_validate_json(message.content)
        except (ValidationError, ValueError):
            continue
        return arguments.model_dump(mode="json")["todos"]
    return []


def todo_context(todos: list[dict[str, Any]]) -> str:
    return TodoWriteArguments(todos=todos).model_dump_json()
