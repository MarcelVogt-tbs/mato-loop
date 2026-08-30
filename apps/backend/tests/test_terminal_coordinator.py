import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from mato_loop_backend.execution import ExecutionClient, ProcessResult
from mato_loop_backend.execution.terminals import (
    BrowserTerminalConnection,
    TerminalCoordinator,
)
from mato_loop_backend.models import AgentRun, ApprovalRequest, ChatSession


class FakeWebSocket:
    def __init__(self) -> None:
        self.messages = []

    async def send_json(self, payload) -> None:
        self.messages.append(payload)

    async def send_bytes(self, _payload) -> None:
        pass


class FakeDb:
    def __init__(self, session, run) -> None:
        self.session = session
        self.run = run
        self.approval = None

    def add(self, value) -> None:
        if isinstance(value, ApprovalRequest):
            if value.id is None:
                value.id = uuid4()
            self.approval = value

    async def get(self, model, key, **_kwargs):
        if model is AgentRun and key == self.run.id:
            return self.run
        if model is ChatSession and key == self.session.id:
            return self.session
        if model is ApprovalRequest and self.approval is not None and key == self.approval.id:
            return self.approval
        return None

    async def flush(self) -> None:
        pass

    async def commit(self) -> None:
        pass

    async def rollback(self) -> None:
        pass

    async def refresh(self, _value) -> None:
        pass


def _connection(user_id, agent_id, terminal_id=None):
    websocket = FakeWebSocket()
    return BrowserTerminalConnection(
        user_id,
        agent_id,
        terminal_id or str(uuid4()),
        ExecutionClient(agent_id, frozenset({"terminal.run"}), "/home/user/project"),
        websocket,  # type: ignore[arg-type]
    )


async def test_terminal_proposal_persists_waits_reconnects_and_resolves_once() -> None:
    coordinator = TerminalCoordinator()
    user_id = uuid4()
    agent_id = uuid4()
    session = ChatSession(
        id=uuid4(),
        user_id=user_id,
        title="Test",
        preferred_execution_agent_id=agent_id,
        current_directory="/home/user/project",
    )
    run = AgentRun(
        id=uuid4(),
        session_id=session.id,
        status="running",
        execution_agent_id=agent_id,
        current_directory_snapshot=session.current_directory,
    )
    db = FakeDb(session, run)
    first = _connection(user_id, agent_id)
    await coordinator.attach(first, session, "confirm")

    waiter = asyncio.create_task(
        coordinator.propose(
            db,  # type: ignore[arg-type]
            user_id=user_id,
            session_id=session.id,
            run_id=run.id,
            tool_call_id="call-1",
            tool="shell_run",
            agent_id=agent_id,
            current_directory=session.current_directory,
            requested_arguments={"command": "pwd"},
            command_line="pwd",
        )
    )
    await asyncio.sleep(0)

    assert db.approval is not None
    assert db.approval.action_kind == "terminal_command"
    assert db.approval.status == "pending"
    assert run.status == "waiting_approval"
    assert first.websocket.messages[-1]["type"] == "command.proposed"

    await coordinator.disconnect(first)
    second = _connection(user_id, agent_id)
    await coordinator.attach(second, session, "confirm")
    assert second.websocket.messages[-1]["proposal"]["approvalId"] == str(db.approval.id)

    await coordinator.resolve(
        db,  # type: ignore[arg-type]
        second,
        db.approval.id,
        command_line="pwd -P",
    )
    decision = await waiter

    assert decision.execute is True
    assert decision.command_line == "pwd -P"
    assert db.approval.status == "approved"
    assert db.approval.payload["approvedCommand"] == "pwd -P"
    assert run.status == "running"
    assert second.websocket.messages[-1] == {
        "type": "command.resolved",
        "approvalId": str(db.approval.id),
        "decision": "execute",
    }
    with pytest.raises(ValueError, match="terminal.proposalUnavailable"):
        await coordinator.resolve(
            db,  # type: ignore[arg-type]
            second,
            db.approval.id,
            command_line="pwd",
        )


async def test_terminal_stale_proposal_does_not_execute() -> None:
    coordinator = TerminalCoordinator()
    user_id = uuid4()
    agent_id = uuid4()
    session = ChatSession(
        id=uuid4(),
        user_id=user_id,
        title="Test",
        preferred_execution_agent_id=agent_id,
        current_directory="/home/user/project",
    )
    run = AgentRun(
        id=uuid4(),
        session_id=session.id,
        status="running",
        execution_agent_id=agent_id,
        current_directory_snapshot=session.current_directory,
    )
    db = FakeDb(session, run)
    connection = _connection(user_id, agent_id)
    await coordinator.attach(connection, session, "confirm")
    waiter = asyncio.create_task(
        coordinator.propose(
            db,  # type: ignore[arg-type]
            user_id=user_id,
            session_id=session.id,
            run_id=run.id,
            tool_call_id="call-1",
            tool="process_exec",
            agent_id=agent_id,
            current_directory=session.current_directory,
            requested_arguments={"program": "python", "args": []},
            command_line="python",
        )
    )
    await asyncio.sleep(0)
    run.current_directory_snapshot = "/home/user/other"

    with pytest.raises(ValueError, match="terminal.proposalStale"):
        await coordinator.resolve(
            db,  # type: ignore[arg-type]
            connection,
            db.approval.id,
            command_line="python",
        )

    decision = await waiter
    assert decision.execute is False
    assert decision.explicit_denial is False
    assert db.approval.status == "expired"
    assert connection.websocket.messages[-1]["type"] == "command.cancelled"


async def test_terminal_resolution_finishes_if_browser_task_is_cancelled_during_commit() -> None:
    coordinator = TerminalCoordinator()
    user_id = uuid4()
    agent_id = uuid4()
    session = ChatSession(
        id=uuid4(),
        user_id=user_id,
        title="Test",
        preferred_execution_agent_id=agent_id,
        current_directory="/home/user/project",
    )
    run = AgentRun(
        id=uuid4(),
        session_id=session.id,
        status="running",
        execution_agent_id=agent_id,
        current_directory_snapshot=session.current_directory,
    )
    db = FakeDb(session, run)
    connection = _connection(user_id, agent_id)
    await coordinator.attach(connection, session, "confirm")
    waiter = asyncio.create_task(
        coordinator.propose(
            db,  # type: ignore[arg-type]
            user_id=user_id,
            session_id=session.id,
            run_id=run.id,
            tool_call_id="call-1",
            tool="shell_run",
            agent_id=agent_id,
            current_directory=session.current_directory,
            requested_arguments={"command": "pwd"},
            command_line="pwd",
        )
    )
    await asyncio.sleep(0)
    commit_started = asyncio.Event()
    release_commit = asyncio.Event()

    async def delayed_commit() -> None:
        commit_started.set()
        await release_commit.wait()

    db.commit = delayed_commit  # type: ignore[method-assign]
    resolution = asyncio.create_task(
        coordinator.resolve(
            db,  # type: ignore[arg-type]
            connection,
            db.approval.id,
            command_line="pwd -P",
        )
    )
    await commit_started.wait()
    resolution.cancel()
    release_commit.set()

    with pytest.raises(asyncio.CancelledError):
        await resolution
    decision = await waiter
    assert decision.execute is True
    assert decision.command_line == "pwd -P"
    assert db.approval.status == "approved"


async def test_terminal_deny_and_pending_cancellation_clear_proposal() -> None:
    coordinator = TerminalCoordinator()
    user_id = uuid4()
    agent_id = uuid4()
    session = ChatSession(
        id=uuid4(),
        user_id=user_id,
        title="Test",
        preferred_execution_agent_id=agent_id,
        current_directory="/home/user/project",
    )
    run = AgentRun(
        id=uuid4(),
        session_id=session.id,
        status="running",
        execution_agent_id=agent_id,
        current_directory_snapshot=session.current_directory,
    )
    db = FakeDb(session, run)
    connection = _connection(user_id, agent_id)
    await coordinator.attach(connection, session, "confirm")

    denied_waiter = asyncio.create_task(
        coordinator.propose(
            db,  # type: ignore[arg-type]
            user_id=user_id,
            session_id=session.id,
            run_id=run.id,
            tool_call_id="call-1",
            tool="shell_run",
            agent_id=agent_id,
            current_directory=session.current_directory,
            requested_arguments={"command": "pwd"},
            command_line="pwd",
        )
    )
    await asyncio.sleep(0)
    await coordinator.resolve(
        db,  # type: ignore[arg-type]
        connection,
        db.approval.id,
        command_line=None,
    )
    denied = await denied_waiter

    assert denied.explicit_denial is True
    assert connection.websocket.messages[-1]["type"] == "command.resolved"
    assert connection.websocket.messages[-1]["decision"] == "deny"

    cancelled_waiter = asyncio.create_task(
        coordinator.propose(
            db,  # type: ignore[arg-type]
            user_id=user_id,
            session_id=session.id,
            run_id=run.id,
            tool_call_id="call-2",
            tool="process_exec",
            agent_id=agent_id,
            current_directory=session.current_directory,
            requested_arguments={"program": "python", "args": []},
            command_line="python",
        )
    )
    await asyncio.sleep(0)
    cancelled_waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await cancelled_waiter

    assert db.approval.status == "expired"
    assert connection.websocket.messages[-1]["type"] == "command.cancelled"


async def test_terminal_run_uses_distinct_ids_dimensions_and_strict_control_targeting() -> None:
    coordinator = TerminalCoordinator()
    user_id = uuid4()
    agent_id = uuid4()
    session = ChatSession(
        id=uuid4(),
        user_id=user_id,
        title="Test",
        preferred_execution_agent_id=agent_id,
        current_directory="/home/user/project",
    )
    connection = _connection(user_id, agent_id)
    await coordinator.attach(connection, session, "confirm")

    assert coordinator.input_terminal_id(connection) is None
    assert coordinator.resize_terminal_id(connection, rows=41, columns=132) is None
    assert (connection.rows, connection.columns) == (41, 132)

    started = asyncio.Event()
    release = asyncio.Event()
    requests = []

    async def request(operation, arguments, _model, **_kwargs):
        requests.append((operation, arguments))
        started.set()
        await release.wait()
        return ProcessResult(
            exit_code=0,
            timed_out=False,
            duration_ms=1,
            stdout_bytes=0,
            stderr_bytes=0,
            stdout_truncated=False,
            stderr_truncated=False,
        )

    connection.client = SimpleNamespace(request=request)  # type: ignore[assignment]
    run_id = uuid4()
    task = asyncio.create_task(
        coordinator.run(
            user_id=user_id,
            session_id=session.id,
            run_id=run_id,
            tool_call_id="call-1",
            agent_id=agent_id,
            current_directory=session.current_directory,
            arguments={"command": "pwd", "expectedInspectionRevision": "a" * 64},
            confirm=True,
            on_event=None,
        )
    )
    await started.wait()
    active_id = connection.active_terminal_id

    assert active_id is not None and active_id != connection.terminal_id
    assert coordinator.input_terminal_id(connection) == active_id
    assert requests[0][1] == {
        "terminalId": active_id,
        "currentDirectory": session.current_directory,
        "rows": 41,
        "columns": 132,
        "command": "pwd",
        "expectedInspectionRevision": "a" * 64,
    }
    assert "timeoutSeconds" not in requests[0][1]
    assert connection.websocket.messages[-1]["terminalId"] == active_id

    release.set()
    await task
    assert coordinator.input_terminal_id(connection) is None

    await coordinator.run(
        user_id=user_id,
        session_id=session.id,
        run_id=uuid4(),
        tool_call_id="call-2",
        agent_id=agent_id,
        current_directory=session.current_directory,
        arguments={"program": "python", "args": []},
        confirm=True,
        on_event=None,
    )
    assert requests[1][1]["terminalId"] != active_id
    assert requests[1][1]["terminalId"] != connection.terminal_id

    await coordinator.detach(connection)
    assert coordinator.input_terminal_id(connection) == connection.terminal_id


async def test_terminal_run_cancellation_clears_active_command() -> None:
    coordinator = TerminalCoordinator()
    user_id = uuid4()
    agent_id = uuid4()
    session = ChatSession(
        id=uuid4(),
        user_id=user_id,
        title="Test",
        preferred_execution_agent_id=agent_id,
        current_directory="/home/user/project",
    )
    connection = _connection(user_id, agent_id)
    await coordinator.attach(connection, session, "confirm")
    started = asyncio.Event()

    async def request(*_args, **_kwargs):
        started.set()
        await asyncio.Event().wait()

    connection.client = SimpleNamespace(request=request)  # type: ignore[assignment]
    task = asyncio.create_task(
        coordinator.run(
            user_id=user_id,
            session_id=session.id,
            run_id=uuid4(),
            tool_call_id="call-1",
            agent_id=agent_id,
            current_directory=session.current_directory,
            arguments={"program": "python", "args": []},
            confirm=True,
            on_event=None,
        )
    )
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert connection.active_terminal_id is None
    assert connection.websocket.messages[-1]["type"] == "command.cancelled"


async def test_terminal_run_start_notification_failure_clears_active_command() -> None:
    coordinator = TerminalCoordinator()
    user_id = uuid4()
    agent_id = uuid4()
    session = ChatSession(
        id=uuid4(),
        user_id=user_id,
        title="Test",
        preferred_execution_agent_id=agent_id,
        current_directory="/home/user/project",
    )
    connection = _connection(user_id, agent_id)
    await coordinator.attach(connection, session, "confirm")
    connection.websocket.send_json = AsyncMock(side_effect=RuntimeError("disconnected"))

    with pytest.raises(RuntimeError, match="disconnected"):
        await coordinator.run(
            user_id=user_id,
            session_id=session.id,
            run_id=uuid4(),
            tool_call_id="call-1",
            agent_id=agent_id,
            current_directory=session.current_directory,
            arguments={"program": "python", "args": []},
            confirm=True,
            on_event=None,
        )

    assert connection.active_run_id is None
    assert connection.active_terminal_id is None
    connection.websocket.send_json = AsyncMock()
    await coordinator.detach(connection)
