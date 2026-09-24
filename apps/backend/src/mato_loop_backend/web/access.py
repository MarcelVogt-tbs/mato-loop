from __future__ import annotations

import mato_loop_backend.permissions.resolution as permissions_resolution
import mato_loop_backend.tools.access as tools_access
from mato_loop_backend.permissions.policy import webfetch_permission
from mato_loop_backend.settings import ToolExecutionPolicy, ToolSettings
from mato_loop_backend.tools.context import ToolContext as ToolContext
from mato_loop_backend.web.fetch import WebFetchError


async def ensure_webfetch_binding_is_current(context: ToolContext) -> None:
    current_tools = await tools_access.profile_tool_settings(context)
    if not current_tools.tool_availability.get("webfetch", True):
        raise WebFetchError("tool.unavailable")
    context.tool_settings = current_tools


async def ensure_webfetch_capability_is_current(context: ToolContext) -> None:
    await ensure_webfetch_binding_is_current(context)
    if permissions_resolution.tool_execution_policy(
        context.tool_settings,
        "network.fetch",
        ToolExecutionPolicy.AUTO,
    ) == ToolExecutionPolicy.DISABLED:
        raise WebFetchError("tool.unavailable")


async def authorize_webfetch_target(
    context: ToolContext, url: str
) -> None:
    await ensure_webfetch_capability_is_current(context)
    policy = permissions_resolution.tool_execution_policy(
        context.tool_settings,
        "network.fetch",
        ToolExecutionPolicy.AUTO,
    )
    if policy == ToolExecutionPolicy.ASK and not await tools_access.allow(
        context, webfetch_permission(url)
    ):
        raise WebFetchError("permission.denied")
    await ensure_webfetch_capability_is_current(context)
    if policy != ToolExecutionPolicy.ASK and permissions_resolution.tool_execution_policy(
        context.tool_settings,
        "network.fetch",
        ToolExecutionPolicy.AUTO,
    ) == ToolExecutionPolicy.ASK:
        if not await tools_access.allow(context, webfetch_permission(url)):
            raise WebFetchError("permission.denied")
        await ensure_webfetch_capability_is_current(context)


def webfetch_available(settings: ToolSettings) -> bool:
    return (
        settings.tool_availability.get("webfetch", True)
        and permissions_resolution.tool_execution_policy(
            settings, "network.fetch", ToolExecutionPolicy.AUTO
        )
        != ToolExecutionPolicy.DISABLED
    )
