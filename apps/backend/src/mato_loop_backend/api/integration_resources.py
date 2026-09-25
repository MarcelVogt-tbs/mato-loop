from __future__ import annotations

import logging
import re

from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.api.errors import ApiError
from mato_loop_backend.integrations.bookstack.adapter import BOOKSTACK_CREDENTIAL_KIND
from mato_loop_backend.integrations.caldav.adapter import CALDAV_CREDENTIAL_KIND
from mato_loop_backend.integrations.contracts import IntegrationResource
from mato_loop_backend.integrations.mcp.adapter import MCP_CREDENTIAL_KIND
from mato_loop_backend.integrations.registry import integration_adapter
from mato_loop_backend.permissions.resolution import resolve_tool_execution_policy
from mato_loop_backend.security import load_credential, read_credential
from mato_loop_backend.settings import (
    IntegrationInstance,
    ToolExecutionPolicy,
    ToolSettings,
    UserToolSettings,
)
from mato_loop_backend.tools.catalog import tool_registry

logger = logging.getLogger("mato_loop_backend.api.routes.integrations")


_RESOURCE_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,499}$")


ToolSettingsDocument = ToolSettings | UserToolSettings


async def _integration_resources(
    db: AsyncSession, instance: IntegrationInstance
) -> tuple:
    adapter = integration_adapter(instance.integration_type, instance.adapter_type)
    if adapter is None or not adapter.resource_actions:
        raise ApiError(409, "integration.resourcePoliciesUnsupported")
    if not tool_registry.integration_resource_actions(instance):
        raise ApiError(409, "integration.resourcePoliciesUnsupported")
    credential = None
    try:
        if adapter.resource_loader is not None and instance.credential_id is not None:
            record = await load_credential(
                db,
                instance.credential_id,
                owner_id=instance.owner_id,
                populate_existing=True,
            )
            if record is None:
                raise ValueError("missing integration credential")
            if (
                instance.integration_type == "calendar"
                and instance.adapter_type == "caldav"
                and record.kind != CALDAV_CREDENTIAL_KIND
            ):
                raise ValueError("invalid integration credential kind")
            if (
                instance.integration_type == "knowledge"
                and instance.adapter_type == "bookstack"
                and record.kind != BOOKSTACK_CREDENTIAL_KIND
            ):
                raise ValueError("invalid integration credential kind")
            if instance.adapter_type == "sonobin" and record.kind != MCP_CREDENTIAL_KIND:
                raise ValueError("invalid integration credential kind")
            credential = read_credential(record)
        await db.rollback()
        resources = await adapter.resources(instance, credential)
    except Exception as exc:
        await db.rollback()
        logger.warning(
            "Resource discovery for integration %s failed with %s",
            instance.id,
            type(exc).__name__,
        )
        raise ApiError(502, "integration.resourceDiscoveryFailed") from exc
    keys = set()
    for resource in resources:
        if (
            not isinstance(resource, IntegrationResource)
            or not isinstance(resource.key, str)
            or _RESOURCE_KEY.fullmatch(resource.key) is None
            or not isinstance(resource.label, str)
            or not 1 <= len(resource.label) <= 160
            or resource.label != resource.label.strip()
            or (
                resource.group_key is not None
                and (
                    not isinstance(resource.group_key, str)
                    or _RESOURCE_KEY.fullmatch(resource.group_key) is None
                )
            )
            or resource.key in keys
        ):
            raise ApiError(502, "integration.resourceDiscoveryFailed")
        keys.add(resource.key)
    return resources


def _access_payload(
    instance: IntegrationInstance,
    resources: tuple,
    tools: ToolSettingsDocument,
) -> dict:
    adapter = integration_adapter(instance.integration_type, instance.adapter_type)
    if adapter is None:
        raise ApiError(409, "integration.resourcePoliciesUnsupported")
    integration_policies = tools.permissions.integration_policies.get(
        str(instance.id), {}
    )
    resource_policies = tools.permissions.resource_policies.get(str(instance.id), {})
    resource_actions = tool_registry.integration_resource_actions(instance)
    if not resource_actions:
        raise ApiError(409, "integration.resourcePoliciesUnsupported")
    capabilities = {action.capability for action in resource_actions}
    actions_by_capability = {
        action.capability: action for action in resource_actions
    }

    def supported_policy(action, policy: ToolExecutionPolicy) -> ToolExecutionPolicy:
        if policy == ToolExecutionPolicy.AUTO and policy not in action.allowed_policies:
            return ToolExecutionPolicy.ASK
        return policy

    def applicable_capabilities(resource: IntegrationResource) -> set[str]:
        return (
            set(resource.applicable_capabilities)
            if resource.applicable_capabilities is not None
            else capabilities
        )

    actions = [
        {
            "capability": action.capability,
            "label": action.label,
            "allowedPolicies": [policy.value for policy in action.allowed_policies],
            "globalPolicy": supported_policy(
                action,
                resolve_tool_execution_policy(
                    tools,
                    action.capability,
                    action.default_policy_for(tools.trust_level),
                ).policy,
            ).value,
            "integrationPolicy": (
                supported_policy(
                    action, integration_policies[action.capability]
                ).value
                if action.capability in integration_policies
                else None
            ),
            "effectiveIntegrationPolicy": supported_policy(
                action,
                resolve_tool_execution_policy(
                    tools,
                    action.capability,
                    action.default_policy_for(tools.trust_level),
                    integration_id=instance.id,
                ).policy,
            ).value,
        }
        for action in resource_actions
    ]
    payload = {
        "integrationId": str(instance.id),
        "actions": actions,
        "resources": [
            {
                "key": resource.key,
                "groupKey": resource.group_key,
                "label": resource.label.strip(),
                "hardUnavailableCapabilities": sorted(
                    resource.hard_unavailable_capabilities
                ),
                "applicableCapabilities": sorted(applicable_capabilities(resource)),
                "policies": {
                    capability: supported_policy(
                        actions_by_capability[capability],
                        policy,
                    ).value
                    for capability, policy in resource_policies.get(
                        resource.key, {}
                    ).items()
                    if capability in applicable_capabilities(resource)
                },
                "effectivePolicies": {
                    action.capability: supported_policy(
                        action,
                        resolve_tool_execution_policy(
                            tools,
                            action.capability,
                            action.default_policy_for(tools.trust_level),
                            integration_id=instance.id,
                            resource_key=resource.key,
                            hard_unavailable_capabilities=(
                                resource.hard_unavailable_capabilities
                            ),
                        ).policy,
                    ).value
                    for action in resource_actions
                    if action.capability in applicable_capabilities(resource)
                },
            }
            for resource in resources
        ],
    }
    if instance.integration_type == "calendar" and instance.adapter_type == "caldav":
        default_calendar = tools.default_calendar
        payload["defaultResourceKey"] = (
            default_calendar.calendar_key
            if default_calendar is not None
            and default_calendar.integration_id == instance.id
            and any(resource.key == default_calendar.calendar_key for resource in resources)
            else None
        )
        default_task_list = tools.default_task_list
        payload["defaultTaskListKey"] = (
            default_task_list.task_list_key
            if default_task_list is not None
            and default_task_list.integration_id == instance.id
            and any(
                resource.key == default_task_list.task_list_key
                for resource in resources
            )
            else None
        )
    return payload
