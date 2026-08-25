from __future__ import annotations

import posixpath
from typing import Any
from urllib.parse import unquote, urlsplit

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
        from mato_loop_backend.agent.tools import ToolResult, _directory_allowed
        from mato_loop_backend.execution.client import DirectoryResult, ExecutionClientError
        from mato_loop_backend.models.runtime import AgentRun

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

        if operation.startswith("integration.git."):
            if client.current_directory is None or not await _directory_allowed(
                context, client.current_directory, ask=True
            ):
                return ToolResult.error("permission.denied")
            if operation == "integration.git.clone":
                source = str(getattr(arguments, "source", ""))
                parsed = urlsplit(source)
                if parsed.scheme in {"", "file"}:
                    if parsed.scheme == "file":
                        if parsed.netloc not in {"", "localhost"} or parsed.query or parsed.fragment:
                            return ToolResult.error("git.invalidRepository")
                        source = unquote(parsed.path)
                    source = posixpath.normpath(
                        source
                        if source.startswith("/")
                        else posixpath.join(client.current_directory, source)
                    )
                    try:
                        resolved = await client.request(
                            "filesystem.resolve", {"path": source}, DirectoryResult
                        )
                    except ExecutionClientError as exc:
                        return ToolResult.error(exc.code)
                    if not await _directory_allowed(context, resolved.path, ask=True):
                        return ToolResult.error("permission.denied")

        try:
            result = await client.request(
                operation,
                {
                    **arguments.model_dump(mode="json", by_alias=True),
                    **host_arguments,
                    **(
                        {"currentDirectory": client.current_directory}
                        if operation.startswith("integration.git.")
                        and client.current_directory is not None
                        else {}
                    ),
                },
                result_model,
            )
        except ExecutionClientError as exc:
            return ToolResult.error(exc.code)
        return ToolResult.json(result.model_dump(mode="json", by_alias=True))

    return execute
