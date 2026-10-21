"""Host-registered command contributions shared by chat and messaging."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.models import ChatSession


@dataclass(frozen=True)
class Command:
    name: str
    label: str
    description: str
    instructions: str
    icon: str = "terminal"
    mutates_workspace: bool = False
    initial_workspace_mode: Literal["local", "worktree"] | None = None


@dataclass(frozen=True)
class CommandProvider:
    source: str
    commands: tuple[Command, ...]
    availability: Callable[[AsyncSession, ChatSession], Awaitable[dict[str, str | None]]]
    prepare: Callable[[AsyncSession, ChatSession, Command, str | None], Awaitable[dict]]


def command_arguments(content: str | None) -> str:
    parts = (content or "").split(maxsplit=1)
    return parts[1] if len(parts) > 1 else ""


def command_context(command: dict) -> dict:
    value = command.get("context")
    return value if isinstance(value, dict) else {}
