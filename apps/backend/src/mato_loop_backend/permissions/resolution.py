from __future__ import annotations

from typing import Any
from uuid import UUID

from mato_loop_backend.settings import ToolExecutionPolicy, ToolSettings, cap_agent_profile_policy
from mato_loop_backend.tools.actions import ToolPolicyResolution


def resolve_tool_execution_policy(
    settings: ToolSettings,
    capability: str,
    default: ToolExecutionPolicy = ToolExecutionPolicy.ASK,
    *,
    integration_id: UUID | None = None,
    resource_key: str | None = None,
    hard_unavailable_capabilities: frozenset[str] = frozenset(),
    include_scoped: bool = False,
) -> ToolPolicyResolution:
    def resolve_value(value: Any, source: str) -> ToolPolicyResolution:
        try:
            policy = ToolExecutionPolicy(value)
        except (TypeError, ValueError):
            return ToolPolicyResolution(
                ToolExecutionPolicy.DISABLED, source, "invalid_policy"
            )
        return ToolPolicyResolution(policy, source, "configured")

    permissions = settings.permissions
    if integration_id is not None and resource_key is not None and permissions.resource_availability.get(str(integration_id), {}).get(resource_key) is False:
        return ToolPolicyResolution(ToolExecutionPolicy.DISABLED, "resource", "resource_disabled")
    configured = permissions.policies.get(capability) if integration_id is None or settings._legacy_integration_permissions else None
    # Preserve saved read rules until a session-specific rule replaces them.
    if integration_id is None and configured is None and capability == "session.files.read":
        configured = permissions.policies.get("filesystem.read")
    if configured is not None:
        resolution = resolve_value(configured, "global")
    else:
        resolution = ToolPolicyResolution(default, "default", "trust_default")
    if integration_id is not None:
        integration_policy = permissions.integration_policies.get(
            str(integration_id), {}
        ).get(capability)
        if integration_policy is not None:
            resolution = resolve_value(integration_policy, "integration")
            if resolution.reason != "configured":
                return resolution
        if resource_key is not None:
            resource_policy = (
                permissions.resource_policies.get(str(integration_id), {})
                .get(resource_key, {})
                .get(capability)
            )
            if resource_policy is not None:
                resolution = resolve_value(resource_policy, "resource")
                if resolution.reason != "configured":
                    return resolution
    elif integration_id is None and resource_key is not None:
        return ToolPolicyResolution(
            ToolExecutionPolicy.DISABLED, "resource", "missing_integration"
        )
    if capability in hard_unavailable_capabilities:
        return ToolPolicyResolution(
            ToolExecutionPolicy.DISABLED,
            "hard_unavailable",
            "hard_unavailable",
        )
    profile_policy = cap_agent_profile_policy(
        settings,
        capability,
        resolution.policy,
        integration_id=integration_id,
        resource_key=resource_key,
        include_scoped=include_scoped,
    )
    if profile_policy != resolution.policy:
        resolution = ToolPolicyResolution(profile_policy, "agent_profile", "configured")
    if (snapshot := settings._delegation_ceiling) is not None:
        from mato_loop_backend.agent.subagents import ceiling_settings

        defaults = (
            snapshot["integrationDefaults"].get(str(integration_id), {})
            if integration_id is not None else snapshot["actionDefaults"]
        )
        ceiling = (
            resolve_tool_execution_policy(
                ceiling_settings(snapshot, settings.trust_level),
                capability,
                ToolExecutionPolicy(defaults[capability]),
                integration_id=integration_id,
                resource_key=resource_key,
                hard_unavailable_capabilities=hard_unavailable_capabilities,
                include_scoped=include_scoped,
            ).policy
            if capability in defaults else ToolExecutionPolicy.DISABLED
        )
        policies = (ToolExecutionPolicy.AUTO, ToolExecutionPolicy.ASK, ToolExecutionPolicy.DISABLED)
        if policies.index(ceiling) > policies.index(resolution.policy):
            return ToolPolicyResolution(ceiling, "delegation", "ceiling")
    return resolution


def tool_execution_policy(
    settings: ToolSettings,
    capability: str,
    default: ToolExecutionPolicy = ToolExecutionPolicy.ASK,
    *,
    integration_id: UUID | None = None,
    resource_key: str | None = None,
    hard_unavailable_capabilities: frozenset[str] = frozenset(),
    include_scoped: bool = False,
) -> ToolExecutionPolicy:
    return resolve_tool_execution_policy(
        settings,
        capability,
        default,
        integration_id=integration_id,
        resource_key=resource_key,
        hard_unavailable_capabilities=hard_unavailable_capabilities,
        include_scoped=include_scoped,
    ).policy
