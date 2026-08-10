from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ValidationError

from mato_loop_backend.settings.models import IntegrationInstance


def execution_tool_handler(
    instance: IntegrationInstance,
    operation: str | None,
    result_model: type[BaseModel] | None,
):
    if instance.execution_agent_id is None or operation is None or result_model is None:
        raise ValueError("invalid agent integration binding")

    async def execute(arguments: Any, context: Any):
        from mato_loop_backend.agent.tools import ToolResult
        from mato_loop_backend.execution.manager import (
            ExecutionAgentError,
            execution_agent_connections,
        )
        from mato_loop_backend.models.runtime import AgentRun

        run = await context.db.get(AgentRun, context.run_id)
        if (
            run is None
            or run.execution_agent_id != instance.execution_agent_id
            or operation not in frozenset(run.execution_operations or [])
        ):
            return ToolResult.error("tool.unavailable")

        try:
            response = await execution_agent_connections.request(
                instance.execution_agent_id,
                operation,
                arguments.model_dump(mode="json", by_alias=True),
            )
        except ExecutionAgentError as exc:
            return ToolResult.error(exc.code)
        if not response.ok:
            return ToolResult.error(
                "tool.invalidArguments"
                if response.error is not None and response.error.code == "request.invalid"
                else "tool.executionFailed"
            )
        try:
            result = result_model.model_validate(getattr(response, "result", None))
        except ValidationError:
            return ToolResult.error("tool.executionFailed")
        return ToolResult.json(result.model_dump(mode="json", by_alias=True))

    return execute
