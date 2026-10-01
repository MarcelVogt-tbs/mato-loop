from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class _VisibleResponse:
    content_parts: list[str] = field(default_factory=list)
    reasoning: dict[str, Any] | None = None
    warnings: list[str] = field(default_factory=list)
    generation_diagnostics: dict[str, Any] | None = None

    @property
    def content(self) -> str:
        return "".join(self.content_parts)

    def reset(self) -> None:
        self.content_parts.clear()
        self.reasoning = None
        self.warnings.clear()
        self.generation_diagnostics = None


class AgentRunError(RuntimeError):
    def __init__(self, code: str, *, params: dict[str, Any] | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.params = params


@dataclass(frozen=True, slots=True)
class ApprovalResolution:
    decision: str
    expected_revision: str | None = None
    argument_edits: tuple[tuple[tuple[str, ...], str], ...] = ()
    sudo_password: str | None = field(default=None, repr=False)
