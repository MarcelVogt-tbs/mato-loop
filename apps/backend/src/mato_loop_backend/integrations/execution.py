from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from mato_loop_backend.settings.models import IntegrationInstance


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
        from mato_loop_backend.agent.tools import ToolResult
        from mato_loop_backend.execution.client import ExecutionClientError
        from mato_loop_backend.models import AgentRun

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
