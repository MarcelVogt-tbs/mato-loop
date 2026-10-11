from typing import Literal

from mato_loop_backend.permissions.resolution import resolve_tool_execution_policy
from mato_loop_backend.settings.models import ToolExecutionPolicy, ToolSettings, TrustLevel
from mato_loop_backend.tools.catalog import tool_registry

ToolPermissionPreset = Literal["ask_all", "ask_changes", "ask_delete_sensitive", "ask_sensitive", "auto_all"]
DELETE_CAPABILITIES = frozenset({"filesystem.delete", "automation.delete"})
SENSITIVE_CAPABILITIES = frozenset({"sensitive_data.access", "secrets.access", "secrets.write"})


def tool_preset_policies(preset: ToolPermissionPreset) -> dict[str, ToolExecutionPolicy]:
    policies = {}
    for action in tool_registry.policy_actions():
        if not action.configurable:
            continue
        if action.capability in SENSITIVE_CAPABILITIES:
            ask = preset != "auto_all"
        elif action.capability in DELETE_CAPABILITIES:
            ask = preset not in {"ask_sensitive", "auto_all"}
        else:
            ask = preset == "ask_all" or (preset == "ask_changes" and action.mutates)
        policy = ToolExecutionPolicy.ASK if ask else ToolExecutionPolicy.AUTO
        if policy not in action.allowed_policies:
            policy = ToolExecutionPolicy.ASK
        if policy in action.allowed_policies:
            policies[action.capability] = policy
    return policies


def tool_permission_presets(settings: ToolSettings) -> dict[str, ToolPermissionPreset | None]:
    result = {}
    for trust in TrustLevel:
        tools = settings.model_copy(deep=True).select_trust(trust)
        current = {
            action.capability: resolve_tool_execution_policy(
                tools, action.capability, action.default_policy_for(trust)
            ).policy
            for action in tool_registry.policy_actions()
            if action.configurable
        }
        result[trust.value] = next(
            (preset for preset in ("ask_all", "ask_changes", "ask_delete_sensitive", "ask_sensitive", "auto_all")
             if all(current[key] == value for key, value in tool_preset_policies(preset).items())),
            None,
        )
    return result
