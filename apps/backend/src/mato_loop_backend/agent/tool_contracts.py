from dataclasses import dataclass

from mato_loop_backend.settings.models import ToolExecutionPolicy


@dataclass(frozen=True, slots=True)
class ToolAction:
    capability: str
    label: str
    default_policy: ToolExecutionPolicy
    configurable: bool = True
    mutates: bool = False


@dataclass(frozen=True, slots=True)
class ToolPolicyResolution:
    policy: ToolExecutionPolicy
    source: str
    reason: str
