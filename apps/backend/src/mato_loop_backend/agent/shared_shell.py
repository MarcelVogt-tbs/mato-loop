from __future__ import annotations

import asyncio
import json
from contextlib import suppress
from uuid import uuid4

from mato_loop_backend.agent.permissions import PermissionDecision, shell_command_permission
from mato_loop_backend.agent.tool_registry import ToolContext, ToolResult
from mato_loop_backend.events import run_events
from mato_loop_backend.execution import (
    ExecutionClientError,
    SharedPrepareResult,
    SharedProcessResult,
    TerminalControlResult,
)
from mato_loop_backend.execution.terminals import TerminalRuntime


async def run_shared_shell(context: ToolContext, arguments, runtime: TerminalRuntime) -> ToolResult:
    requested = arguments.model_dump(exclude_none=True)
    await runtime.check_access()
    if runtime.command_id is not None or runtime.closed or not runtime.attached:
        return ToolResult.error("terminal.commandActive")
    command_id = uuid4()
    runtime.command_id = command_id
    runtime.active_tool_call_id = context.tool_call.id
    completed = False
    executing = False
    output = {"stdout": "", "stderr": ""}

    async def publish(event) -> None:
        output[event.event] += event.data
        await run_events.publish(context.run_id, {
            "type": "process.output", "toolCallId": context.tool_call.id, "output": dict(output),
        })

    try:
        await runtime.send_status()
        prepared = await runtime.client.request(
            "terminal.shared.prepare",
            {
                "terminalId": runtime.terminal_id,
                "commandId": str(command_id),
                "command": arguments.command,
            },
            SharedPrepareResult,
            timeout=5,
        )
        if prepared.command != arguments.command:
            raise ExecutionClientError("executionAgent.protocolError")
        permission = shell_command_permission(
            runtime.agent_id, prepared.current_directory, prepared.command, prepared.inspection,
        )
        decision = await context.ask(permission)
        if decision != PermissionDecision.ALLOW:
            return ToolResult.error(
                "permission.userDenied"
                if decision == PermissionDecision.USER_DENIED
                else "permission.denied"
            )

        async def execute():
            nonlocal executing
            await runtime.check_access()
            executing = True
            execute_arguments = {
                "terminalId": runtime.terminal_id,
                "commandId": str(command_id),
                "expectedInspectionRevision": prepared.inspection.inspection_revision,
            }
            if arguments.timeout is not None:
                execute_arguments["timeoutSeconds"] = arguments.timeout
            return await runtime.client.request(
                "terminal.shared.execute",
                execute_arguments,
                SharedProcessResult,
                timeout=3_610,
                on_event=publish,
            )

        runtime.command_task = asyncio.create_task(execute())
        await runtime.send_status()
        result = await runtime.command_task
        completed = True
        tool_result = ToolResult.json({
            **result.model_dump(by_alias=True),
            "requestedArguments": requested,
            "executedArguments": requested,
            "userEdited": False,
            "currentDirectory": prepared.current_directory,
            "output": {
                **output,
                "truncated": result.stdout_truncated or result.stderr_truncated,
            },
        })
        if result.exit_code != 0 or result.timed_out:
            tool_result.is_error = True
            tool_result.metadata["errorCode"] = (
                "process.outcomeUnknown"
                if result.exit_code is None
                else "process.timedOut"
                if result.timed_out
                else "process.commandFailed"
            )
        return tool_result
    except (asyncio.CancelledError, ExecutionClientError) as exc:
        if isinstance(exc, asyncio.CancelledError) and asyncio.current_task().cancelling():
            raise
        code = "process.interrupted" if isinstance(exc, asyncio.CancelledError) else exc.code
        result = ToolResult.error(code)
        if executing:
            result.content = json.dumps({
                **json.loads(result.content),
                "requestedArguments": requested,
                "executedArguments": requested,
                "userEdited": False,
                "output": output,
            })
        return result
    finally:
        if not completed and not runtime.closed:
            with suppress(Exception):
                async with asyncio.timeout(3):
                    await runtime.client.request(
                        "terminal.shared.cancel",
                        {"terminalId": runtime.terminal_id, "commandId": str(command_id)},
                        TerminalControlResult,
                        timeout=2,
                    )
        runtime.command_id = runtime.command_task = runtime.active_tool_call_id = None
        if not runtime.closed:
            with suppress(ExecutionClientError):
                await runtime.refresh_status()
            await runtime.send_status()
