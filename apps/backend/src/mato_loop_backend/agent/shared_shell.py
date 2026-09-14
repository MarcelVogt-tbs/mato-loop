from __future__ import annotations

import asyncio
import json
import logging
from contextlib import suppress
from dataclasses import dataclass, field, replace
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy.exc import SQLAlchemyError

from mato_loop_backend.agent.permissions import (
    PermissionDecision,
    PermissionRequest,
    session_grants_display,
    session_grants_fingerprint,
    session_grants_payload,
    shell_command_permission,
)
from mato_loop_backend.agent.tool_registry import ToolContext, ToolResult
from mato_loop_backend.db.session import AsyncSessionFactory
from mato_loop_backend.events import run_events
from mato_loop_backend.execution import (
    ExecutionClientError,
    SharedInspectResult,
    SharedProcessResult,
    SharedTerminalStatus,
    TerminalControlResult,
)
from mato_loop_backend.execution.terminals import TerminalRuntime
from mato_loop_backend.models import AgentRun, ApprovalRequest, ChatSession

PENDING_POLL_SECONDS = 0.2
logger = logging.getLogger(__name__)


@dataclass(slots=True)
class SharedCommand:
    id: UUID
    runtime: TerminalRuntime
    context: ToolContext
    requested: dict[str, Any]
    workspace_directory: str
    reviewed: SharedInspectResult | None = None
    frozen: SharedInspectResult | None = None
    authorized: SharedInspectResult | None = None
    approval_id: UUID | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def permission(self, snapshot: SharedInspectResult) -> PermissionRequest:
        permission = shell_command_permission(
            self.runtime.agent_id, snapshot.current_directory, snapshot.command, snapshot.inspection,
        )
        return replace(
            permission,
            approval_details={
                **permission.approval_details,
                "sharedDraft": {
                    "terminalId": self.runtime.terminal_id, "commandId": str(self.id),
                    "command": snapshot.command, "currentDirectory": snapshot.current_directory,
                    "revision": snapshot.revision, "contextRevision": snapshot.context_revision,
                    "inspectionRevision": snapshot.inspection.inspection_revision,
                    "readOnly": True,
                },
            },
            shared_command_id=self.id,
        )

    def update_approval(self, approval: ApprovalRequest, snapshot: SharedInspectResult) -> PermissionRequest:
        permission = self.permission(snapshot)
        grant_payload = session_grants_payload(permission)
        for name in ("capability", "resource_type", "resource_scope", "title", "summary", "risk_level"):
            setattr(approval, name, getattr(permission, name))
        approval.available_decisions = list(permission.available_decisions)
        approval.payload = {
            **approval.payload, **permission.approval_details,
            "resourceDisplay": permission.resource_display,
            "sessionGrants": grant_payload,
            "sessionGrantsFingerprint": session_grants_fingerprint(
                permission.capability,
                permission.resource_type,
                permission.resource_scope,
                grant_payload,
            ),
            "displayItems": [
                {"label": item.label, "value": item.value, "format": item.format}
                for item in permission.display_items
            ] + ([{
                "label": "Für diese Session",
                "value": session_grants_display(permission),
                "format": "list",
            }] if "allow_session" in permission.available_decisions else []),
        }
        self.reviewed = snapshot
        return permission

    async def inspect(self, *, freeze: bool) -> SharedInspectResult:
        if self.runtime.closed:
            raise ExecutionClientError("process.interrupted")
        if self.runtime.suspended_since is not None:
            raise ExecutionClientError("terminal.unavailable")
        if self.frozen is not None:
            return self.frozen
        result = await self.runtime.client.request(
            "terminal.shared.inspect",
            {"terminalId": self.runtime.terminal_id, "commandId": str(self.id), "freeze": freeze},
            SharedInspectResult, timeout=3,
        )
        if freeze:
            self.frozen = result
        return result


_commands: dict[UUID, SharedCommand] = {}


def get_command(command_id: UUID) -> SharedCommand:
    command = _commands.get(command_id)
    if command is None:
        raise ExecutionClientError("approval.runInactive")
    return command


def approval_command(approval: ApprovalRequest) -> SharedCommand:
    draft = approval.payload.get("sharedDraft")
    try:
        command = get_command(UUID(draft["commandId"]))
    except (TypeError, ValueError, KeyError) as exc:
        raise ExecutionClientError("approval.runInactive") from exc
    if (
        command.approval_id != approval.id or command.runtime.terminal_id != draft.get("terminalId")
        or command.runtime.user_id != approval.user_id
        or command.runtime.owner_session_id != approval.session_id
        or command.context.run_id != approval.run_id
        or command.context.tool_call.id != approval.payload.get("toolCallId")
        or approval.action_kind != "shell_run"
        or approval.payload.get("shellClassification") not in {"read", "write"}
        or approval.capability != f"shell.{approval.payload.get('shellClassification')}"
        or approval.resource_type != "shell_command"
    ):
        raise ExecutionClientError("approval.runInactive")
    return command


async def update_review(db, approval: ApprovalRequest, command: SharedCommand, snapshot: SharedInspectResult) -> None:
    permission = command.update_approval(approval, snapshot)
    await db.commit()
    from mato_loop_backend.agent.runtime import approval_payload

    command.runtime.pending = {"approvalId": str(approval.id), **permission.approval_details["sharedDraft"]}
    await command.runtime.send_status()
    await run_events.publish(approval.run_id, {"type": "approval.updated", "approval": approval_payload(approval)})


async def prepare_decision(db, approval, command, decision, expected_revision, *, submitted: bool) -> PermissionRequest | None:
    from mato_loop_backend.agent.approval_decisions import ApprovalDecisionError
    from mato_loop_backend.agent.runtime import AgentRuntime

    if decision == "deny":
        with suppress(ExecutionClientError):
            await command.runtime.client.request(
                "terminal.shared.cancel", {"terminalId": command.runtime.terminal_id, "commandId": str(command.id)},
                TerminalControlResult, timeout=2,
            )
        return None
    await command.runtime.check_access()
    session = await db.get(ChatSession, approval.session_id, populate_existing=True)
    run = await db.get(AgentRun, approval.run_id, populate_existing=True)
    if (
        session is None or run is None or run.status != "waiting_approval"
        or run.session_id != session.id or run.execution_agent_id != command.runtime.agent_id
        or run.current_directory_snapshot != command.workspace_directory
        or session.current_directory != command.workspace_directory
        or session.user_id != command.runtime.user_id or not command.runtime.attached
    ):
        raise ApprovalDecisionError(409, "approval.runInactive")
    status = await command.runtime.refresh_status()
    if status.command_id != command.id or status.cancelled or status.state == "closed":
        raise ApprovalDecisionError(409, "approval.targetChanged")
    if submitted and not status.submitted:
        raise ApprovalDecisionError(409, "approval.targetChanged")
    frozen = await command.inspect(freeze=True)
    expected_revision = expected_revision or approval.payload.get("initialReviewRevision")
    if not submitted and frozen.inspection.inspection_revision != expected_revision:
        await update_review(db, approval, command, frozen)
        raise ApprovalDecisionError(409, "approval.targetChanged")
    permission = command.permission(frozen)
    if decision not in permission.available_decisions:
        await update_review(db, approval, command, frozen)
        raise ApprovalDecisionError(400, "approval.decisionUnavailable")
    decision_now = await AgentRuntime._authorize(
        db, run, session, command.context.tool_call, permission, command.context.root_files, check_only=True,
    )
    if decision_now == PermissionDecision.DENY:
        await update_review(db, approval, command, frozen)
        raise ApprovalDecisionError(409, "approval.policyChanged")
    command.update_approval(approval, frozen)
    approval.payload = {
        **approval.payload,
        "approvedRevision": frozen.inspection.inspection_revision,
        "approvedCommand": frozen.command,
        "executedArguments": {**command.requested, "command": frozen.command},
        "userEdited": command.requested["command"] != frozen.command,
    }
    return permission


async def cancel_pending(command_id: UUID) -> None:
    from mato_loop_backend.agent.approval_decisions import (
        ApprovalDecisionError,
        decide_agent_action,
    )
    from mato_loop_backend.agent.coordinator import approval_coordinator
    from mato_loop_backend.agent.runtime import ApprovalResolution

    command = _commands.get(command_id)
    if command is None or command.approval_id is None:
        return
    approval_id = command.approval_id
    try:
        async with asyncio.timeout(3), AsyncSessionFactory() as db:
            approval = await db.get(ApprovalRequest, approval_id)
            if approval is not None:
                with suppress(ApprovalDecisionError):
                    await decide_agent_action(db, approval, "deny", terminal_cancelled=True)
    finally:
        # A database outage must not strand a tool or turn terminal interruption into run cancellation.
        approval_coordinator.resolve(approval_id, ApprovalResolution("deny"))


async def watch_approval(command: SharedCommand) -> None:
    from mato_loop_backend.agent.approval_decisions import (
        ApprovalDecisionError,
        decide_agent_action,
    )

    last_checked = None
    while command.approval_id is not None:
        await asyncio.sleep(PENDING_POLL_SECONDS)
        if command.runtime.closed:
            await cancel_pending(command.id)
            return
        if command.runtime.suspended_since is not None:
            continue
        status = None
        try:
            status = await command.runtime.refresh_status()
            if status.cancelled or status.command_id != command.id or status.state == "closed":
                await cancel_pending(command.id)
                return
            if status.submitted:
                async with AsyncSessionFactory() as db:
                    approval = await db.get(ApprovalRequest, command.approval_id)
                    if approval is not None:
                        await decide_agent_action(db, approval, "allow_once", terminal_submitted=True)
                    else:
                        await cancel_pending(command.id)
                return
            if command.reviewed is not None and (
                status.revision != command.reviewed.revision
                or status.context_revision != command.reviewed.context_revision
            ) and last_checked != (status.revision, status.context_revision):
                last_checked = (status.revision, status.context_revision)
                async with asyncio.timeout(5), command.lock, AsyncSessionFactory() as db:
                    approval = await db.get(ApprovalRequest, command.approval_id)
                    if approval is not None and approval.status == "pending":
                        snapshot = await command.inspect(freeze=False)
                        await update_review(db, approval, command, snapshot)
        except ExecutionClientError as exc:
            if exc.code in {"approval.targetChanged", "terminal.commandActive"}:
                last_checked = None
                continue
            if status is not None and status.state == "editing" and exc.code in {
                "process.invalidSyntax", "process.inspectionLimitExceeded", "process.unsupportedTargetQuoting",
                "file.invalidPath", "file.pathNotFound", "file.directoryNotFound",
            }:
                continue
            await cancel_pending(command.id)
            return
        except ApprovalDecisionError as exc:
            if exc.code == "approval.alreadyDecided":
                return
            await cancel_pending(command.id)
            return
        except (SQLAlchemyError, TimeoutError):
            if command.runtime.suspended_since is None:
                command.runtime.suspended_since = asyncio.get_running_loop().time()
                await command.runtime.send_status()
            last_checked = None
        except Exception:
            logger.exception("Shared terminal approval monitoring failed")
            await cancel_pending(command.id)
            return


async def run_shared_shell(context: ToolContext, arguments, runtime: TerminalRuntime) -> ToolResult:
    requested = arguments.model_dump(exclude_none=True)
    await runtime.check_access()
    if runtime.command_id is not None or runtime.closed or not runtime.attached:
        return ToolResult.error("terminal.commandActive")
    command = SharedCommand(uuid4(), runtime, context, requested, context.session.current_directory)
    _commands[command.id] = command
    runtime.command_id = command.id
    runtime.active_tool_call_id = context.tool_call.id
    completed = False
    executed = None
    output = {"stdout": "", "stderr": ""}

    async def publish(event) -> None:
        output[event.event] += event.data
        await run_events.publish(context.run_id, {
            "type": "process.output", "toolCallId": context.tool_call.id, "output": dict(output),
        })

    try:
        await runtime.send_status()
        while True:
            if runtime.closed:
                return ToolResult.error("process.interrupted")
            if runtime.suspended_since is not None:
                await asyncio.sleep(PENDING_POLL_SECONDS)
                continue
            try:
                runtime.native_status = await runtime.client.request(
                    "terminal.shared.stage",
                    {"terminalId": runtime.terminal_id, "commandId": str(command.id), "command": arguments.command},
                    SharedTerminalStatus, timeout=2,
                )
                break
            except ExecutionClientError as exc:
                if exc.code != "terminal.commandActive":
                    raise
                await asyncio.sleep(PENDING_POLL_SECONDS)
        while True:
            try:
                snapshot = await command.inspect(freeze=False)
            except ExecutionClientError as exc:
                if exc.code not in {
                    "approval.targetChanged", "terminal.commandActive", "process.invalidSyntax",
                    "process.inspectionLimitExceeded", "process.unsupportedTargetQuoting",
                }:
                    raise
                status = await runtime.refresh_status()
                if status.cancelled or status.command_id != command.id:
                    context.user_denied = True
                    return ToolResult.error("permission.userDenied")
                if status.submitted and exc.code.startswith("process."):
                    raise
                await asyncio.sleep(PENDING_POLL_SECONDS)
                continue
            command.reviewed, command.authorized = snapshot, None
            permission = command.permission(snapshot)
            decision = await context.ask(permission)
            if decision != PermissionDecision.ALLOW:
                return ToolResult.error("permission.userDenied" if decision == PermissionDecision.USER_DENIED else "permission.denied")
            frozen = command.authorized or await command.inspect(freeze=True)
            if command.authorized is None and frozen != snapshot:
                # Approval of one draft never authorizes text edited during policy evaluation.
                continue

            async def execute(frozen=frozen):
                nonlocal executed
                await runtime.check_access()
                executed = {**requested, "command": frozen.command}
                return await runtime.client.request(
                    "terminal.shared.execute",
                    {"terminalId": runtime.terminal_id, "commandId": str(command.id),
                     "command": frozen.command, "expectedRevision": frozen.revision,
                     "expectedContextRevision": frozen.context_revision,
                     "expectedInspectionRevision": frozen.inspection.inspection_revision,
                     "timeoutSeconds": arguments.timeout},
                    SharedProcessResult, timeout=3_610, on_event=publish,
                )

            runtime.command_task = asyncio.create_task(execute())
            await runtime.send_status()
            result = await runtime.command_task
            completed = True
            tool_result = ToolResult.json({
                **result.model_dump(by_alias=True), "requestedArguments": requested,
                "executedArguments": executed, "userEdited": requested != executed,
                "currentDirectory": frozen.current_directory,
                "output": {**output, "truncated": result.stdout_truncated or result.stderr_truncated},
            })
            if result.exit_code != 0 or result.timed_out:
                tool_result.is_error = True
                tool_result.metadata["errorCode"] = (
                    "process.outcomeUnknown" if result.exit_code is None else
                    "process.timedOut" if result.timed_out else "process.commandFailed"
                )
            return tool_result
    except (asyncio.CancelledError, ExecutionClientError) as exc:
        if isinstance(exc, asyncio.CancelledError) and asyncio.current_task().cancelling():
            raise
        code = (
            "process.interrupted"
            if isinstance(exc, asyncio.CancelledError) or exc.code == "approval.targetChanged" else exc.code
        )
        result = ToolResult.error(code)
        if executed is not None:
            result.content = json.dumps({
                **json.loads(result.content), "requestedArguments": requested,
                "executedArguments": executed, "userEdited": requested != executed,
                "output": output,
            })
        return result
    finally:
        if not completed and not runtime.closed:
            with suppress(Exception):
                async with asyncio.timeout(3):
                    await runtime.client.request(
                        "terminal.shared.cancel", {"terminalId": runtime.terminal_id, "commandId": str(command.id)},
                        TerminalControlResult, timeout=2,
                    )
        _commands.pop(command.id, None)
        runtime.command_id = runtime.command_task = runtime.active_tool_call_id = runtime.pending = None
        if not runtime.closed:
            with suppress(ExecutionClientError):
                await runtime.refresh_status()
            await runtime.send_status()
