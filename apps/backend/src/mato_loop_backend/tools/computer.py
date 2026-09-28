from __future__ import annotations

from typing import Any
from uuid import uuid4

from sqlalchemy import select

import mato_loop_backend.files.access as files_access
import mato_loop_backend.tools.access as tools_access
from mato_loop_backend.events import run_events
from mato_loop_backend.execution import (
    DirectoryResult,
    ProcessResult,
    ShellInspectResult,
    SystemInfoResult,
)
from mato_loop_backend.execution.manager import execution_agent_connections
from mato_loop_backend.execution.protocol import MAX_REQUEST_BYTES, RequestMessage
from mato_loop_backend.execution.terminals import terminal_coordinator
from mato_loop_backend.models import AgentRun, ExecutionAgent
from mato_loop_backend.permissions.policy import (
    elevated_shell_permission,
    execution_agent_select_permission,
    execution_agents_list_permission,
    shell_command_permission,
    system_info_permission,
)
from mato_loop_backend.tools.actions import (
    _EXECUTION_AGENT_SELECT,
    _EXECUTION_AGENTS_LIST,
    _SHELL_READ,
    _SHELL_WRITE,
    _SYSTEM_INFO,
    ApprovalEditField,
)
from mato_loop_backend.tools.arguments import (
    ChangeDirectoryArguments,
    ListExecutionAgentsArguments,
    SelectExecutionAgentArguments,
    ShellRunArguments,
    SystemInfoArguments,
)
from mato_loop_backend.tools.context import ToolContext as ToolContext
from mato_loop_backend.tools.contracts import ToolDefinition as ToolDefinition
from mato_loop_backend.tools.contracts import ToolResult as ToolResult
from mato_loop_backend.tools.secrets import resolve_secret_refs, resolve_sudo_password


async def _get_system_info(
    _arguments: SystemInfoArguments, context: ToolContext
) -> ToolResult:
    client = files_access.workspace_client(context)
    if not await tools_access.allow(context, system_info_permission(client.agent_id)):
        return ToolResult.error("permission.denied")
    result = await client.request(
        "system.info",
        {"currentDirectory": client.current_directory} if client.current_directory else {},
        SystemInfoResult,
    )
    return ToolResult.json(result.model_dump(mode="json", by_alias=True))


async def _list_execution_agents(
    _arguments: ListExecutionAgentsArguments, context: ToolContext,
) -> ToolResult:
    if not await tools_access.allow(context, execution_agents_list_permission(context.session.user_id)):
        return ToolResult.error("permission.denied")
    agents = (await context.db.scalars(select(ExecutionAgent).where(
        ExecutionAgent.user_id == context.session.user_id,
        ExecutionAgent.revoked_at.is_(None),
    ).order_by(ExecutionAgent.created_at, ExecutionAgent.id))).all()
    return ToolResult.json({"items": [
        {"id": str(agent.id), "alias": agent.alias, "platform": agent.platform,
         "connected": execution_agent_connections.is_connected(agent.id),
         "selected": agent.id == context.session.preferred_execution_agent_id}
        for agent in agents
    ]})


async def _select_execution_agent(
    arguments: SelectExecutionAgentArguments, context: ToolContext,
) -> ToolResult:
    if context.session.session_type != "chat" or context.session.parent_session_id is not None:
        return ToolResult.error("tool.unavailable")
    agent = await context.db.scalar(select(ExecutionAgent).where(
        ExecutionAgent.id == arguments.agent_id,
        ExecutionAgent.user_id == context.session.user_id,
        ExecutionAgent.revoked_at.is_(None),
    ))
    if agent is None:
        return ToolResult.error("executionAgent.notFound")
    if agent.id == context.session.preferred_execution_agent_id:
        return ToolResult.json({"id": str(agent.id), "alias": agent.alias, "selected": True})
    if not await tools_access.allow(context, execution_agent_select_permission(agent.id, agent.alias)):
        return ToolResult.error("permission.denied")
    from mato_loop_backend.sessions.service import (
        SessionServiceError,
        validate_execution_preferences,
    )

    try:
        directory, _policy = await validate_execution_preferences(
            context.db, context.session.user_id, agent.id, None,
        )
    except SessionServiceError as exc:
        return ToolResult.error(exc.code)
    # Revalidate after approval; the target or its connection may have changed while waiting.
    agent = await context.db.scalar(select(ExecutionAgent).where(
        ExecutionAgent.id == arguments.agent_id,
        ExecutionAgent.user_id == context.session.user_id,
        ExecutionAgent.revoked_at.is_(None),
    ).execution_options(populate_existing=True))
    snapshot = execution_agent_connections.snapshot(arguments.agent_id)
    if agent is None or snapshot is None:
        return ToolResult.error("executionAgent.unavailable")
    run = await context.db.get(AgentRun, context.run_id)
    if run is None:
        return ToolResult.error("tool.unavailable")
    context.session.preferred_execution_agent_id = agent.id
    context.session.control_center = {
        key: value for key, value in (context.session.control_center or {}).items()
        if key != "terminal_auto_attach"
    }
    context.session.current_directory = directory
    run.execution_agent_id = agent.id
    run.execution_agent_revision = agent.revision
    run.current_directory_snapshot = directory
    run.execution_operations = sorted(snapshot)
    await context.db.flush()
    return ToolResult.json({
        "id": str(agent.id), "alias": agent.alias, "currentDirectory": directory,
        "selected": True,
    }, metadata={"executionAgentChanged": True})


async def _change_directory(
    arguments: ChangeDirectoryArguments, context: ToolContext
) -> ToolResult:
    client = files_access.workspace_client(context)
    result = await client.request(
        "filesystem.resolve",
        {"path": files_access.execution_path(context, arguments.path)},
        DirectoryResult,
    )
    assert isinstance(result, DirectoryResult)
    if not await files_access.directory_allowed(
        context,
        result.path,
        ask=True,
        changes_directory=True,
    ):
        return ToolResult.error("permission.denied")
    context.session.current_directory = result.path
    run = await context.db.get(AgentRun, context.run_id)
    if run is None:
        return ToolResult.error("tool.unavailable")
    run.current_directory_snapshot = result.path
    client.current_directory = result.path
    await context.db.flush()
    return ToolResult.json({"currentDirectory": result.path})


async def _run_process(
    context: ToolContext,
    operation: str,
    arguments: dict[str, Any],
    *,
    requested_arguments: dict[str, Any],
    executed_arguments: dict[str, Any],
    redacted_values: tuple[str, ...] = (),
) -> ToolResult:
    output = {"stdout": "", "stderr": ""}

    async def publish(event: Any) -> None:
        output[event.event] += event.data
        if not redacted_values:
            await run_events.publish(
                context.run_id,
                {
                    "type": "process.output",
                    "toolCallId": context.tool_call.id,
                    "output": dict(output),
                },
            )

    client = files_access.workspace_client(context)
    result = await client.request(
        operation, arguments, ProcessResult, timeout=3_610, on_event=publish,
    )
    assert isinstance(result, ProcessResult)
    truncated = result.stdout_truncated or result.stderr_truncated
    safe_output = dict(output)
    if redacted_values:
        if truncated:
            safe_output = {
                key: "[redacted]" if value else "" for key, value in output.items()
            }
        else:
            for secret in sorted(set(redacted_values), key=len, reverse=True):
                if secret:
                    safe_output = {
                        key: value.replace(secret, "[redacted]")
                        for key, value in safe_output.items()
                    }
        await run_events.publish(
            context.run_id,
            {
                "type": "process.output",
                "toolCallId": context.tool_call.id,
                "output": safe_output,
            },
        )
    tool_result = ToolResult.json(
        {
            **result.model_dump(by_alias=True),
            "requestedArguments": requested_arguments,
            "executedArguments": executed_arguments,
            "userEdited": requested_arguments != executed_arguments,
            "output": {
                **safe_output,
                "truncated": truncated,
            },
        }
    )
    if result.timed_out or result.exit_code != 0:
        tool_result.is_error = True
        tool_result.metadata["errorCode"] = (
            "process.timedOut" if result.timed_out else "process.commandFailed"
        )
    return tool_result


async def _shell_run(arguments: ShellRunArguments, context: ToolContext) -> ToolResult:
    client = files_access.workspace_client(context)
    if client.current_directory is None:
        return ToolResult.error("permission.denied")
    if arguments.elevated:
        if not client.supports("shell.elevated"):
            return ToolResult.error("executionAgent.operationUnavailable")
        if not await tools_access.allow(
            context, elevated_shell_permission(client.agent_id, client.current_directory, arguments.command)
        ):
            return ToolResult.error("permission.denied")
        password = await resolve_sudo_password(context, arguments.sudo_password_ref, client.agent_id)
        if isinstance(password, ToolResult):
            return password
        requested = arguments.model_dump(mode="json", exclude_none=True, exclude_defaults=True)
        return await _run_process(
            context, "shell.elevated",
            {"command": arguments.command, "currentDirectory": client.current_directory,
             **({"timeoutSeconds": arguments.timeout} if arguments.timeout is not None else {}),
             **({"sudoPassword": password} if password else {})},
            requested_arguments=requested, executed_arguments=requested,
            redacted_values=(password,) if password else (),
        )
    terminal = terminal_coordinator.attached_for(
        user_id=context.session.user_id,
        session_id=context.session.id,
        agent_id=client.agent_id,
    )
    if terminal is not None:
        from mato_loop_backend.agent.shared_shell import run_shared_shell

        resolved = await resolve_secret_refs(context, arguments.environment)
        if isinstance(resolved, ToolResult):
            return resolved
        environment = resolved
        return await run_shared_shell(
            context,
            arguments,
            terminal,
            environment=environment,
        )
    requested = arguments.model_dump(
        mode="json", exclude_none=True, exclude_defaults=True
    )
    executed = requested
    inspection = await client.request(
        "shell.inspect",
        {"command": executed["command"], "currentDirectory": client.current_directory},
        ShellInspectResult,
    )
    assert isinstance(inspection, ShellInspectResult)
    if arguments.environment:
        inspection = inspection.model_copy(
            update={
                "analysis_complete": False,
                "classification": "write",
                "read_only": False,
            }
        )
    permission = shell_command_permission(
        client.agent_id, client.current_directory, executed["command"], inspection,
    )
    if not await tools_access.allow(context, permission):
        return ToolResult.error("permission.denied")
    resolved = await resolve_secret_refs(context, arguments.environment)
    if isinstance(resolved, ToolResult):
        return resolved
    environment = resolved
    process_arguments = {
        "command": executed["command"],
        "currentDirectory": client.current_directory,
        "timeoutSeconds": arguments.timeout,
        "expectedInspectionRevision": inspection.inspection_revision,
        **({"environment": environment} if environment else {}),
    }
    request = RequestMessage(
        id=uuid4(), operation="shell.run", arguments=process_arguments
    )
    if len(request.model_dump_json(by_alias=True).encode()) > MAX_REQUEST_BYTES:
        return ToolResult.error("executionAgent.requestTooLarge")
    return await _run_process(
        context,
        "shell.run",
        process_arguments,
        requested_arguments=requested,
        executed_arguments=executed,
        redacted_values=tuple(environment.values()),
    )


LIST_EXECUTION_AGENTS = ToolDefinition(
            "list_execution_agents",
            "List your available MatoLoop Agents and the current session selection.",
            ListExecutionAgentsArguments, _list_execution_agents,
            group="agent", label="Computer anzeigen", icon="computer",
            actions=(_EXECUTION_AGENTS_LIST,),
        )

SELECT_EXECUTION_AGENT = ToolDefinition(
            "select_execution_agent",
            "Select one listed connected MatoLoop Agent for this session. Call alone; user approval is required for every change. The next model step uses the selected agent and its directory.",
            SelectExecutionAgentArguments, _select_execution_agent,
            group="agent", label="Computer übernehmen", icon="computer",
            actions=(_EXECUTION_AGENT_SELECT,),
        )

GET_SYSTEM_INFO = ToolDefinition(
            "get_system_info",
            "Get bounded basic information about the selected computer. This does not require a current directory.",
            SystemInfoArguments,
            _get_system_info,
            tool_type="computer",
            required_operation="system.info",
            group="process",
            label="Systeminformationen",
            icon="computer",
            actions=(_SYSTEM_INFO,),
        )

CHANGE_DIRECTORY = ToolDefinition(
            "change_directory",
            "Resolve a directory on the selected computer and make it the persistent current directory. "
            "Use this instead of shell cd; shell cd applies only to that shell call.",
            ChangeDirectoryArguments,
            _change_directory,
            tool_type="computer",
            required_operation="filesystem.resolve",
            group="process",
            label="Verzeichnis wechseln",
            icon="drive_file_move",
            requires_permission_reason=True,
        )

SHELL_RUN = ToolDefinition(
            "shell_run",
            "Run one command with the host-configured shell in the session directory. "
            "If a terminal is attached, use its persistent Bash shell and actual directory instead. "
            "Shell state and cd persist in an attached terminal, but do not change the session workspace. "
            "Shell options also persist; set -e can close the entire terminal on a command failure. "
            "Run self-contained scripts with set -e in a child shell using bash -c, "
            "rather than enabling it in the persistent shell. "
            "Without an attached terminal, shell cd applies only to this call. "
            "The host inspects the command and its file targets before authorization. "
            "The user can edit a staged command; results report the requested and executed arguments. "
            "Read classification means no known write action was detected, not absence of side effects. "
            "Analysis completeness is separate from classification. This is not an "
            "operating-system sandbox. "
            "Optional timeout is in seconds (0 < timeout <= 3600); null uses the agent default, "
            "subject to its local maximum. Nonzero exits and timeouts are errors with bounded "
            "output retained; interpret the exit code and output for the command's meaning. "
            "elevated=true runs this command as administrator in an isolated shell, even with an "
            "attached terminal. It uses shell.write policy and computer-wide hard restrictions. "
            "Use sudo_password_ref for secure input when sudo requires a password; do not put sudo, "
            "password pipes, or a password in the command. An existing session secret named "
            "SUDO_PASSWORD_<AGENT_ID_WITHOUT_DASHES> is reused automatically for that computer.",
            ShellRunArguments,
            _shell_run,
            tool_type="computer",
            required_operation="shell.run",
            group="process",
            label="Shell-Befehl ausführen",
            icon="terminal",
            actions=(_SHELL_READ, _SHELL_WRITE),
            alternative_action_groups=(frozenset({"shell.read", "shell.write"}),),
            permission_reason_instruction=(
                "Explain the command's purpose and relevant effects in plain language. Keep it short and do not merely repeat the command."
            ),
            approval_edit_fields=(
                ApprovalEditField(("command",), "Befehl", "textarea", True, 256 * 1024),
            ),
        )
