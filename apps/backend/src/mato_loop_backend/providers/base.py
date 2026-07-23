from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal

DeltaCallback = Callable[[str], Awaitable[None]]
ReasoningDeltaCallback = Callable[[str, str], Awaitable[None]]


@dataclass(slots=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(slots=True)
class PublicReasoning:
    text: str
    kind: str = "content"
    status: str = "completed"
    reasoning_tokens: int | None = None
    observed_duration_ms: int | None = None


@dataclass(slots=True)
class ProviderMedia:
    message_index: int
    mime_type: str
    data_url: str | None = field(default=None, repr=False)
    status: str = "unavailable"


@dataclass(slots=True)
class ProviderRequest:
    model: str
    messages: list[dict[str, Any]]
    tools: list[dict[str, Any]] = field(default_factory=list)
    tool_choice: Literal["auto", "required", "none"] = "auto"
    stream: bool = True
    supports_parallel_tool_calls: bool = False
    max_output_tokens: int | None = None
    temperature: float | None = None
    timeout: float = 300
    reasoning_summary: bool = False
    continuation_scope: dict[str, str | None] = field(default_factory=dict)
    media: list[ProviderMedia] = field(default_factory=list)


@dataclass(slots=True)
class ProviderResult:
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    provider_data: dict[str, Any] = field(default_factory=dict)
    usage: dict[str, int] = field(default_factory=dict)
    finish_reason: str | None = None
    reasoning: PublicReasoning | None = None


class ProviderError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        code: str | None = None,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.retry_after = retry_after

    @property
    def public_code(self) -> str:
        if self.code:
            return self.code
        if self.status_code in {401, 403}:
            return "provider.authenticationFailed"
        if self.status_code == 404:
            return "provider.endpointNotFound"
        if self.status_code == 429:
            return "provider.rateLimited"
        if self.status_code is not None and self.status_code >= 500:
            return "provider.unavailable"
        return "provider.requestFailed"
