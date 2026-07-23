from __future__ import annotations

from dataclasses import dataclass

from mato_loop_backend.models import ChatSession, MemoryMode
from mato_loop_backend.settings.models import (
    LlmProviderConfig,
    MemoryPolicy,
    MemorySettings,
    ModelConfig,
)


@dataclass(frozen=True, slots=True)
class EffectiveMemory:
    read_enabled: bool
    write_enabled: bool
    tools_enabled: bool
    unavailable_reason: str | None = None

    @property
    def enabled(self) -> bool:
        return self.read_enabled

    @property
    def mode(self) -> MemoryMode:
        if not self.read_enabled:
            return MemoryMode.DISABLED
        return MemoryMode.FULL if self.write_enabled else MemoryMode.READ_ONLY


def effective_memory(
    session: ChatSession,
    settings: MemorySettings,
    provider: LlmProviderConfig | None,
    model: ModelConfig | None,
) -> EffectiveMemory:
    if not settings.enabled:
        return EffectiveMemory(False, False, False, "memory.disabled")
    session_mode = MemoryMode(session.memory_mode)
    if session_mode == MemoryMode.DISABLED:
        return EffectiveMemory(False, False, False, "memory.sessionDisabled")
    if provider is None or model is None:
        return EffectiveMemory(False, False, False, "session.modelUnavailable")
    if provider.memory_policy == MemoryPolicy.DENY:
        return EffectiveMemory(False, False, False, "memory.providerDenied")
    if model.memory_policy == MemoryPolicy.DENY:
        return EffectiveMemory(False, False, False, "memory.modelDenied")
    can_write = session_mode == MemoryMode.FULL
    return EffectiveMemory(
        True,
        can_write,
        model.supports_tools,
        None if model.supports_tools else "memory.modelToolsUnavailable",
    )
