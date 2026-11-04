from __future__ import annotations

import hashlib
import json
from typing import Any
from uuid import UUID

from pydantic import BaseModel

from mato_loop_backend.settings.models import IntegrationInstance


def bound_integration_ids(context: Any) -> set[str]:
    value = context.bound_options.get("integration_ids")
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return set()
    if not isinstance(value, (list, tuple)) or not all(
        isinstance(item, str) for item in value
    ):
        return set()
    return set(value)


def selected_integration_binding(
    context: Any, integration_id: UUID, *, integration_type: str, adapter_type: str,
) -> tuple[IntegrationInstance, str]:
    selected_id = context.bound_options.get("integration_id")
    revision = context.bound_options.get("binding_revision")
    if selected_id != str(integration_id) or not isinstance(revision, str):
        raise ValueError("missing selected integration binding")
    instance = next(
        (
            item
            for item in context.integration_settings.instances
            if str(item.id) == selected_id
            and item.enabled
            and item.integration_type == integration_type
            and item.adapter_type == adapter_type
        ),
        None,
    )
    if instance is None or json.dumps(
        instance.binding_data(),
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ) != revision:
        raise ValueError("stale selected integration binding")
    return instance, revision


def operation_key(name: str, arguments: BaseModel) -> str:
    material = json.dumps(
        arguments.model_dump(mode="json", exclude_unset=True),
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return f"{name}:{hashlib.sha256(material.encode()).hexdigest()}"


def execution_tool_handler(
    instance: IntegrationInstance,
    operation: str | None,
    result_model: type[BaseModel] | None,
    host_arguments: dict[str, Any],
    required_operations: tuple[str, ...] = (),
):
    if instance.execution_agent_id is None or operation is None or result_model is None:
        raise ValueError("invalid agent integration binding")

    async def execute(arguments: Any, context: Any):
        from mato_loop_backend.execution.client import ExecutionClientError
        from mato_loop_backend.models import AgentRun
        from mato_loop_backend.tools.contracts import ToolResult

        run = await context.db.get(AgentRun, context.run_id)
        if (
            run is None
            or run.execution_agent_id != instance.execution_agent_id
            or not {operation, *required_operations}
            <= frozenset(run.execution_operations or [])
        ):
            return ToolResult.error("tool.unavailable")
        client = context.execution_client
        if client is None or client.agent_id != instance.execution_agent_id:
            return ToolResult.error("tool.unavailable")

        try:
            result = await client.request(
                operation,
                {
                    **arguments.model_dump(mode="json", by_alias=True),
                    **host_arguments,
                },
                result_model,
            )
        except ExecutionClientError as exc:
            return ToolResult.error(exc.code)
        return ToolResult.json(result.model_dump(mode="json", by_alias=True))

    return execute
