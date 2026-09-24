from __future__ import annotations

import asyncio
import json
from contextlib import suppress
from uuid import uuid4

from mato_loop_backend.events import run_events
from mato_loop_backend.execution import (
    ExecutionClientError,
    SharedPrepareResult,
    SharedProcessResult,
    TerminalControlResult,
)
from mato_loop_backend.execution.protocol import MAX_REQUEST_BYTES, RequestMessage
from mato_loop_backend.execution.terminals import TerminalRuntime
from mato_loop_backend.permissions.policy import PermissionDecision, shell_command_permission
from mato_loop_backend.tools.context import ToolContext
from mato_loop_backend.tools.contracts import ToolResult


async def run_shared_shell(
    context: ToolContext,
    arguments,
    runtime: TerminalRuntime,
    *,
    environment: dict[str, str] | None = None,
) -> ToolResult:
    requested = arguments.model_dump(exclude_none=True)
    environment = environment or {}
    redacted_values = tuple(environment.values())
    await runtime.check_access()
    if runtime.command_id is not None or runtime.closed or not runtime.attached:
        return ToolResult.error("terminal.commandActive")
    command_id = uuid4()
    runtime.command_id = command_id
    runtime.active_tool_call_id = context.tool_call.id
    completed = False
    executing = False
    output = {"stdout": "", "stderr": ""}

    def redacted_output(truncated: bool) -> dict[str, str]:
        safe = dict(output)
        if truncated:
            return {key: "[redacted]" if value else "" for key, value in safe.items()}
        for secret in sorted(set(redacted_values), key=len, reverse=True):
            if secret:
                safe = {
                    key: value.replace(secret, "[redacted]")
                    for key, value in safe.items()
                }
        return safe

    async def publish(event) -> None:
        output[event.event] += event.data
        if not redacted_values:
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
        inspection = prepared.inspection
        if environment:
            inspection = inspection.model_copy(update={
                "analysis_complete": False,
                "classification": "write",
                "read_only": False,
            })
        permission = shell_command_permission(
            runtime.agent_id, prepared.current_directory, prepared.command, inspection,
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
            if environment:
                execute_arguments["environment"] = environment
            request = RequestMessage(
                id=uuid4(), operation="terminal.shared.execute", arguments=execute_arguments
            )
            if len(request.model_dump_json(by_alias=True).encode()) > MAX_REQUEST_BYTES:
                raise ExecutionClientError("executionAgent.requestTooLarge")
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
        truncated = result.stdout_truncated or result.stderr_truncated
        safe_output = redacted_output(truncated) if redacted_values else dict(output)
        if redacted_values:
            await run_events.publish(context.run_id, {
                "type": "process.output",
                "toolCallId": context.tool_call.id,
                "output": safe_output,
            })
        tool_result = ToolResult.json({
            **result.model_dump(by_alias=True),
            "requestedArguments": requested,
            "executedArguments": requested,
            "userEdited": False,
            "currentDirectory": prepared.current_directory,
            "output": {
                **safe_output,
                "truncated": truncated,
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
        if isinstance(exc, asyncio.CancelledError):
            code = "process.outcomeUnknown" if runtime.shell_exited else "process.interrupted"
        else:
            code = exc.code
        result = ToolResult.error(code)
        if executing:
            safe_output = redacted_output(False) if redacted_values else dict(output)
            if redacted_values:
                await run_events.publish(context.run_id, {
                    "type": "process.output",
                    "toolCallId": context.tool_call.id,
                    "output": safe_output,
                })
            result.content = json.dumps({
                **json.loads(result.content),
                "requestedArguments": requested,
                "executedArguments": requested,
                "userEdited": False,
                "output": safe_output,
            })
        return result
    finally:
        if not completed and not runtime.closed and not runtime.shell_exited:
            with suppress(Exception):
                async with asyncio.timeout(3):
                    await runtime.client.request(
                        "terminal.shared.cancel",
                        {"terminalId": runtime.terminal_id, "commandId": str(command_id)},
                        TerminalControlResult,
                        timeout=2,
                    )
        runtime.command_id = runtime.command_task = runtime.active_tool_call_id = None
        if not runtime.closed and not runtime.shell_exited:
            with suppress(ExecutionClientError):
                await runtime.refresh_status()
            await runtime.send_status()
