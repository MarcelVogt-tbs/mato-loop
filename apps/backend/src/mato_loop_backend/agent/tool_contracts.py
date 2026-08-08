from dataclasses import dataclass

from mato_loop_backend.settings.models import ToolExecutionPolicy


@dataclass(frozen=True, slots=True)
class ToolAction:
    capability: str
    label: str
    default_policy: ToolExecutionPolicy
    configurable: bool = True
    allowed_policies: tuple[ToolExecutionPolicy, ...] | None = None
    mutates: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "allowed_policies",
            tuple(ToolExecutionPolicy(policy) for policy in self.allowed_policies)
            if self.allowed_policies is not None
            else tuple(ToolExecutionPolicy),
        )
        if not self.allowed_policies or self.default_policy not in self.allowed_policies:
            raise ValueError("default_policy must be allowed")


@dataclass(frozen=True, slots=True)
class ToolPolicyResolution:
    policy: ToolExecutionPolicy
    source: str
    reason: str
