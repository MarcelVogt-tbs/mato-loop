from __future__ import annotations

import asyncio
import shlex
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID, uuid4

from fastapi import WebSocket
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.agent.coordinator import approval_coordinator
from mato_loop_backend.error_contract import describe_error
from mato_loop_backend.execution.client import ExecutionClient, ExecutionClientError, ProcessResult
from mato_loop_backend.models import AgentRun, ApprovalRequest, ChatSession

TerminalMode = Literal["auto", "confirm"]


@dataclass(frozen=True, slots=True)
class TerminalDecision:
    execute: bool
    command_line: str | None = None
    explicit_denial: bool = False


@dataclass(slots=True)
class BrowserTerminalConnection:
    user_id: UUID
    agent_id: UUID
    terminal_id: str
    client: ExecutionClient
    websocket: WebSocket
    session_id: UUID | None = None
    mode: TerminalMode = "auto"
    active_run_id: UUID | None = None
    active_terminal_id: str | None = None
    active_confirm: bool = False
    rows: int = 24
    columns: int = 80
    send_lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def send_json(self, payload: dict[str, Any]) -> None:
        async with self.send_lock:
            await self.websocket.send_json(payload)

    async def send_bytes(self, payload: bytes) -> None:
        async with self.send_lock:
            await self.websocket.send_bytes(payload)

    async def error(self, code: str) -> None:
        await self.send_json({"type": "error", "error": describe_error(code).payload()})


@dataclass(slots=True)
class _Proposal:
    approval_id: UUID
    user_id: UUID
    session_id: UUID
    run_id: UUID
    tool_call_id: str
    tool: Literal["shell_run", "process_exec"]
    agent_id: UUID
    current_directory: str
    requested_arguments: dict[str, Any]
    command_line: str
    resolved: bool = False

    def message(self) -> dict[str, Any]:
        return {
            "type": "command.proposed",
            "approvalId": str(self.approval_id),
            "sessionId": str(self.session_id),
            "runId": str(self.run_id),
            "toolCallId": self.tool_call_id,
            "tool": self.tool,
            "command": self.command_line,
            "currentDirectory": self.current_directory,
            "requestedArguments": self.requested_arguments,
        }


class TerminalCoordinator:
    def __init__(self) -> None:
        self._attachments: dict[UUID, BrowserTerminalConnection] = {}
        self._proposals: dict[UUID, _Proposal] = {}
        self._proposal_by_session: dict[UUID, UUID] = {}
        self._running_by_session: dict[UUID, UUID] = {}
        self._lock = asyncio.Lock()

    def mode_for(
        self, *, user_id: UUID, session_id: UUID, agent_id: UUID, current_directory: str | None
    ) -> TerminalMode | None:
        connection = self._attachments.get(session_id)
        if (
            connection is None
            or connection.user_id != user_id
            or connection.agent_id != agent_id
            or current_directory is None
        ):
            return None
        return connection.mode

    async def attach(
        self,
        connection: BrowserTerminalConnection,
        session: ChatSession,
        mode: TerminalMode,
    ) -> None:
        async with self._lock:
            existing = self._attachments.get(session.id)
            if existing is not None and existing is not connection:
                raise ValueError("terminal.sessionAttached")
            if connection.session_id is not None and connection.session_id != session.id:
                raise ValueError("terminal.alreadyAttached")
            connection.session_id = session.id
            connection.mode = mode
            self._attachments[session.id] = connection
            proposal_id = self._proposal_by_session.get(session.id)
            proposal = self._proposals.get(proposal_id) if proposal_id is not None else None
            if proposal is not None and not proposal.resolved:
                mode = "confirm"
                connection.mode = mode
        await connection.send_json(
            {
                "type": "session.attached",
                "sessionId": str(session.id),
                "mode": mode,
                "proposal": (
                    proposal.message()
                    if proposal is not None and not proposal.resolved
                    else None
                ),
            }
        )

    async def detach(self, connection: BrowserTerminalConnection) -> None:
        async with self._lock:
            session_id = connection.session_id
            if session_id is None:
                return
            if self._busy(connection, session_id):
                raise ValueError("terminal.commandActive")
            if self._attachments.get(session_id) is connection:
                self._attachments.pop(session_id, None)
            connection.session_id = None
        await connection.send_json({"type": "session.detached", "sessionId": str(session_id)})

    async def set_mode(
        self, connection: BrowserTerminalConnection, mode: TerminalMode
    ) -> None:
        async with self._lock:
            session_id = connection.session_id
            if session_id is None:
                raise ValueError("terminal.notAttached")
            if self._busy(connection, session_id):
                raise ValueError("terminal.commandActive")
            connection.mode = mode
        await connection.send_json({"type": "mode.changed", "mode": mode})

    async def disconnect(self, connection: BrowserTerminalConnection) -> None:
        async with self._lock:
            session_id = connection.session_id
            if session_id is not None and self._attachments.get(session_id) is connection:
                self._attachments.pop(session_id, None)
            connection.session_id = None

    @staticmethod
    def input_terminal_id(connection: BrowserTerminalConnection) -> str | None:
        if connection.session_id is None:
            return connection.terminal_id
        if connection.active_confirm:
            return connection.active_terminal_id
        return None

    @staticmethod
    def resize_terminal_id(
        connection: BrowserTerminalConnection, *, rows: int, columns: int
    ) -> str | None:
        connection.rows = rows
        connection.columns = columns
        if connection.session_id is None:
            return connection.terminal_id
        return connection.active_terminal_id

    async def propose(
        self,
        db: AsyncSession,
        *,
        user_id: UUID,
        session_id: UUID,
        run_id: UUID,
        tool_call_id: str,
        tool: Literal["shell_run", "process_exec"],
        agent_id: UUID,
        current_directory: str,
        requested_arguments: dict[str, Any],
        command_line: str,
    ) -> TerminalDecision:
        connection = self._attachments.get(session_id)
        if connection is None or connection.mode != "confirm":
            raise ExecutionClientError("terminal.disconnected")
        run = await db.get(AgentRun, run_id)
        if (
            run is None
            or run.session_id != session_id
            or run.execution_agent_id != agent_id
            or run.current_directory_snapshot != current_directory
        ):
            raise ExecutionClientError("terminal.disconnected")
        approval = ApprovalRequest(
            request_type="agent_action",
            user_id=user_id,
            session_id=session_id,
            run_id=run_id,
            action_kind="terminal_command",
            risk_level="high",
            title="Terminal-Befehl ausführen?",
            reason="Im Control Center vorgeschlagener Befehl",
            summary="Der Agent möchte einen Befehl im verbundenen Terminal ausführen.",
            capability="terminal.command",
            resource_type="terminal_command",
            resource_scope=current_directory,
            payload={
                "terminalControl": True,
                "toolCallId": tool_call_id,
                "tool": tool,
                "agentId": str(agent_id),
                "currentDirectory": current_directory,
                "requestedArguments": requested_arguments,
                "command": command_line,
            },
            available_decisions=["execute", "deny"],
            status="pending",
        )
        db.add(approval)
        run.status = "waiting_approval"
        await db.flush()
        proposal = _Proposal(
            approval.id,
            user_id,
            session_id,
            run_id,
            tool_call_id,
            tool,
            agent_id,
            current_directory,
            requested_arguments,
            command_line,
        )
        waiter = approval_coordinator.register(approval.id)
        async with self._lock:
            if session_id in self._proposal_by_session:
                approval_coordinator.discard(approval.id)
                raise RuntimeError("terminal proposal already exists")
            self._proposals[approval.id] = proposal
            self._proposal_by_session[session_id] = approval.id
        try:
            await db.commit()
        except Exception:
            await self._remove_proposal(proposal)
            approval_coordinator.discard(approval.id)
            raise
        current = self._attachments.get(session_id)
        if current is not None:
            with suppress(Exception):
                await current.send_json(proposal.message())
        try:
            decision = await asyncio.shield(waiter)
        except asyncio.CancelledError:
            await db.rollback()
            current_approval = await db.get(ApprovalRequest, approval.id)
            if current_approval is not None and current_approval.status == "pending":
                current_approval.status = "expired"
                current_approval.resolved_at = datetime.now(UTC)
                await db.commit()
            self._complete_cancelled(proposal)
            await self._notify_cancelled(proposal, "cancelled")
            raise
        finally:
            await self._remove_proposal(proposal)
            approval_coordinator.discard(approval.id)
        await db.refresh(run)
        if run.status == "waiting_approval":
            run.status = "running"
            await db.commit()
        if not isinstance(decision, TerminalDecision):
            raise ExecutionClientError("terminal.disconnected")
        return decision

    async def resolve(
        self,
        db: AsyncSession,
        connection: BrowserTerminalConnection,
        approval_id: UUID,
        *,
        command_line: str | None,
    ) -> None:
        proposal = self._proposals.get(approval_id)
        if proposal is None or proposal.resolved:
            raise ValueError("terminal.proposalUnavailable")
        if (
            connection.session_id != proposal.session_id
            or connection.user_id != proposal.user_id
            or connection.agent_id != proposal.agent_id
            or connection.mode != "confirm"
        ):
            raise ValueError("terminal.proposalUnavailable")
        if command_line is not None:
            self._validate_command(proposal.tool, command_line)
        approval = await db.get(ApprovalRequest, approval_id, with_for_update=True)
        session = await db.get(ChatSession, proposal.session_id)
        run = await db.get(AgentRun, proposal.run_id)
        valid = (
            approval is not None
            and approval.status == "pending"
            and approval.user_id == proposal.user_id
            and approval.session_id == proposal.session_id
            and approval.run_id == proposal.run_id
            and approval.action_kind == "terminal_command"
            and approval.payload.get("toolCallId") == proposal.tool_call_id
            and approval.payload.get("agentId") == str(proposal.agent_id)
            and approval.payload.get("currentDirectory") == proposal.current_directory
            and session is not None
            and session.user_id == proposal.user_id
            and session.preferred_execution_agent_id == proposal.agent_id
            and session.current_directory == proposal.current_directory
            and run is not None
            and run.status == "waiting_approval"
            and run.session_id == proposal.session_id
            and run.execution_agent_id == proposal.agent_id
            and run.current_directory_snapshot == proposal.current_directory
        )
        if not valid or not approval_coordinator.claim_resolution(approval_id):
            if approval is not None and approval.status == "pending":
                approval.status = "expired"
                approval.resolved_at = datetime.now(UTC)
                await db.commit()
            self._complete_cancelled(proposal)
            await self._notify_cancelled(proposal, "expired")
            raise ValueError("terminal.proposalStale")
        assert approval is not None
        approval.status = "approved" if command_line is not None else "denied"
        approval.decision = "execute" if command_line is not None else "deny"
        approval.resolved_at = datetime.now(UTC)
        if command_line is not None:
            approval.payload = {**approval.payload, "approvedCommand": command_line}
        commit_task = asyncio.create_task(db.commit())
        cancelled = False
        try:
            await asyncio.shield(commit_task)
        except asyncio.CancelledError:
            cancelled = True
            try:
                await commit_task
            except Exception:
                approval_coordinator.release_resolution(approval_id)
                raise
        except Exception:
            approval_coordinator.release_resolution(approval_id)
            raise
        if not approval_coordinator.complete_resolution(
            approval_id,
            TerminalDecision(
                command_line is not None,
                command_line,
                explicit_denial=command_line is None,
            ),
        ):
            approval.status = "expired"
            approval.decision = None
            await db.commit()
            proposal.resolved = True
            await self._notify_cancelled(proposal, "expired")
            raise ValueError("terminal.proposalStale")
        proposal.resolved = True
        if cancelled:
            raise asyncio.CancelledError
        await connection.send_json(
            {
                "type": "command.resolved",
                "approvalId": str(approval_id),
                "decision": approval.decision,
            }
        )

    async def run(
        self,
        *,
        user_id: UUID,
        session_id: UUID,
        run_id: UUID,
        tool_call_id: str,
        agent_id: UUID,
        current_directory: str,
        arguments: dict[str, Any],
        confirm: bool,
        on_event,
    ) -> ProcessResult:
        terminal_id = str(uuid4())
        async with self._lock:
            connection = self._attachments.get(session_id)
            if (
                connection is None
                or connection.user_id != user_id
                or connection.agent_id != agent_id
                or connection.active_run_id is not None
                or session_id in self._running_by_session
            ):
                raise ExecutionClientError("terminal.disconnected")
            connection.active_run_id = run_id
            connection.active_terminal_id = terminal_id
            connection.active_confirm = confirm
            self._running_by_session[session_id] = run_id
        result: ProcessResult | None = None
        cancelled = False
        try:
            await connection.send_json(
                {
                    "type": "command.started",
                    "runId": str(run_id),
                    "toolCallId": tool_call_id,
                    "terminalId": terminal_id,
                }
            )
            result = await connection.client.request(
                "terminal.run",
                {
                    "terminalId": terminal_id,
                    "currentDirectory": current_directory,
                    "rows": connection.rows,
                    "columns": connection.columns,
                    **arguments,
                },
                ProcessResult,
                timeout=3_610,
                on_event=on_event,
            )
            assert isinstance(result, ProcessResult)
            return result
        except asyncio.CancelledError:
            cancelled = True
            raise
        finally:
            async with self._lock:
                if connection.active_run_id == run_id:
                    connection.active_run_id = None
                    connection.active_terminal_id = None
                    connection.active_confirm = False
                if self._running_by_session.get(session_id) == run_id:
                    self._running_by_session.pop(session_id, None)
            with suppress(Exception):
                await connection.send_json(
                    {
                        "type": "command.cancelled" if cancelled else "command.finished",
                        "runId": str(run_id),
                        "toolCallId": tool_call_id,
                        "terminalId": terminal_id,
                        **(result.model_dump(by_alias=True) if result is not None else {}),
                    }
                )

    def _busy(self, connection: BrowserTerminalConnection, session_id: UUID) -> bool:
        return (
            connection.active_run_id is not None
            or session_id in self._proposal_by_session
            or session_id in self._running_by_session
        )

    async def _remove_proposal(self, proposal: _Proposal) -> None:
        async with self._lock:
            self._proposals.pop(proposal.approval_id, None)
            if self._proposal_by_session.get(proposal.session_id) == proposal.approval_id:
                self._proposal_by_session.pop(proposal.session_id, None)

    @staticmethod
    def _complete_cancelled(proposal: _Proposal) -> None:
        if approval_coordinator.claim_resolution(proposal.approval_id):
            approval_coordinator.complete_resolution(
                proposal.approval_id, TerminalDecision(False)
            )
        proposal.resolved = True

    async def _notify_cancelled(self, proposal: _Proposal, reason: str) -> None:
        connection = self._attachments.get(proposal.session_id)
        if connection is not None:
            with suppress(Exception):
                await connection.send_json(
                    {
                        "type": "command.cancelled",
                        "approvalId": str(proposal.approval_id),
                        "runId": str(proposal.run_id),
                        "toolCallId": proposal.tool_call_id,
                        "reason": reason,
                    }
                )

    @staticmethod
    def _validate_command(tool: str, command_line: str) -> None:
        if (
            not command_line
            or len(command_line) > 256 * 1024
            or any(ord(character) < 32 and character not in "\t\n" for character in command_line)
        ):
            raise ValueError("terminal.commandInvalid")
        if tool == "process_exec":
            try:
                tokens = shlex.split(command_line)
            except ValueError as exc:
                raise ValueError("terminal.commandInvalid") from exc
            if not tokens:
                raise ValueError("terminal.commandInvalid")


terminal_coordinator = TerminalCoordinator()


__all__ = [
    "BrowserTerminalConnection",
    "TerminalDecision",
    "TerminalMode",
    "terminal_coordinator",
]
