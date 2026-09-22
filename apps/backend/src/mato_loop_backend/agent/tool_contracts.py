from dataclasses import dataclass
from typing import Literal

from mato_loop_backend.settings.models import ToolExecutionPolicy, TrustLevel


@dataclass(frozen=True, slots=True)
class ToolAction:
    capability: str
    label: str
    private_default_policy: ToolExecutionPolicy
    public_default_policy: ToolExecutionPolicy
    configurable: bool = True
    mutates: bool = False
    allowed_policies: tuple[ToolExecutionPolicy, ...] = tuple(ToolExecutionPolicy)
    gates_tool_availability: bool = True

    def default_policy_for(self, trust_level: TrustLevel) -> ToolExecutionPolicy:
        return (
            self.private_default_policy
            if trust_level == TrustLevel.PRIVATE
            else self.public_default_policy
        )


@dataclass(frozen=True, slots=True)
class ApprovalEditField:
    path: tuple[str, ...]
    label: str
    input_type: Literal["text", "textarea", "temporal"] = "text"
    required: bool = False
    max_length: int = 10_000


@dataclass(frozen=True, slots=True)
class ToolPolicyResolution:
    policy: ToolExecutionPolicy
    source: str
    reason: str
