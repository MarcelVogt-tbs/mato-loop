import asyncio
import base64
import json
from uuid import uuid4

import pytest
from pydantic import ValidationError

from mato_loop_backend.execution.client import (
    ExecutionClient,
    ExecutionClientError,
    TerminalOpenResult,
    TerminalReadResult,
    WorkspaceWriteResult,
)
from mato_loop_backend.execution.manager import (
    ExecutionAgentConnectionManager,
    ExecutionAgentError,
    disconnect_error_code,
)
from mato_loop_backend.execution.protocol import (
    SUPPORTED_OPERATIONS,
    HelloMessage,
    ResponseMessage,
)


class FakeWebSocket:
    def __init__(self) -> None:
        self.sent: list[str] = []
        self.closed: list[tuple[int, str]] = []

    async def send_text(self, payload: str) -> None:
        self.sent.append(payload)

    async def close(self, *, code: int, reason: str) -> None:
        self.closed.append((code, reason))


def _hello(agent_id=None, *, operations=None) -> HelloMessage:
    return HelloMessage.model_validate(
        {
            "type": "hello",
            "protocolVersion": 1,
            "agentId": agent_id or uuid4(),
            "agentVersion": "1.0.0",
            "computerName": "test-host",
            "platform": "linux",
            "operations": operations
            or ["filesystem.list", "filesystem.read", "filesystem.write"],
            "homeDirectory": "/home/test",
        }
    )


def test_protocol_is_strict_and_requires_unique_operations() -> None:
    payload = _hello().model_dump(by_alias=True)
    payload["extra"] = True
    with pytest.raises(ValidationError):
        HelloMessage.model_validate(payload)

    payload.pop("extra")
    payload["operations"] *= 2
    with pytest.raises(ValidationError):
        HelloMessage.model_validate(payload)

    payload = _hello().model_dump(by_alias=True)
    payload["protocolVersion"] = 3
    with pytest.raises(ValidationError):
        HelloMessage.model_validate(payload)


def test_hello_home_directory_requires_a_normalized_absolute_posix_path() -> None:
    payload = _hello().model_dump(by_alias=True)
    payload["homeDirectory"] = "/"
    assert HelloMessage.model_validate(payload).home_directory == "/"

    for path in (
        "home/test",
        "/home\\test",
        "/home//test",
        "/home/./test",
        "/home/../test",
        "/home/\x1ftest",
        "/home/\x80test",
    ):
        payload["homeDirectory"] = path
        with pytest.raises(ValidationError):
            HelloMessage.model_validate(payload)


def test_supported_operations_match_the_execution_agent() -> None:
    assert SUPPORTED_OPERATIONS == {
        "filesystem.stat",
        "filesystem.glob",
        "filesystem.grep",
        "filesystem.list",
        "filesystem.read",
        "filesystem.write",
        "filesystem.mkdir",
        "filesystem.rename",
        "filesystem.delete",
        "filesystem.edit",
        "filesystem.copy",
        "filesystem.move",
        "filesystem.patch",
        "filesystem.import",
        "filesystem.export",
        "filesystem.upsert",
        "shell.run",
        "cache.ensure",
        "cache.export",
        "cache.store",
        "cache.clear",
        "system.info",
        "filesystem.resolve",
        "integration.git.add",
        "integration.git.clone",
        "integration.git.commit",
        "integration.git.diff",
        "integration.git.log",
        "integration.git.status",
        "integration.openscad.discover",
        "integration.openscad.render",
        "integration.openscad.render6",
        "integration.openscad.test",
        "shell.inspect",
        "terminal.open",
        "terminal.read",
        "terminal.write",
        "terminal.resize",
        "terminal.close",
        "terminal.run",
    }


def test_terminal_results_use_typed_wire_fields() -> None:
    terminal_id = uuid4()
    opened = TerminalOpenResult.model_validate({"terminalId": str(terminal_id)})
    read = TerminalReadResult.model_validate(
        {"data": "aGVsbG8=", "eof": True, "exitCode": 0}
    )

    assert opened.terminal_id == terminal_id
    with pytest.raises(ValidationError):
        TerminalOpenResult.model_validate({"terminalId": "personal-1"})
    assert read.data == "aGVsbG8="
    assert read.eof is True
    assert read.exit_code == 0
    with pytest.raises(ValidationError):
        TerminalReadResult.model_validate({"data": "not base64", "eof": False})
    with pytest.raises(ValidationError):
        TerminalReadResult.model_validate(
            {"data": base64.b64encode(b"a" * 65_537).decode(), "eof": False}
        )


async def test_manager_correlates_events_and_response_and_ignores_unknown_operations() -> None:
    manager = ExecutionAgentConnectionManager()
    websocket = FakeWebSocket()
    hello = _hello(operations=["filesystem.read", "future.unknown"])
    await manager.register(hello, websocket)  # type: ignore[arg-type]
    events = []

    task = asyncio.create_task(
        manager.request(
            hello.agent_id,
            "filesystem.read",
            {"path": "/home/test/README.md"},
            on_event=events.append,
        )
    )
    await asyncio.sleep(0)
    request = json.loads(websocket.sent[0])
    await manager.receive(
        hello.agent_id,
        websocket,  # type: ignore[arg-type]
        json.dumps(
            {"type": "event", "id": request["id"], "event": "stdout", "data": "one"}
        ),
    )
    await manager.receive(
        hello.agent_id,
        websocket,  # type: ignore[arg-type]
        json.dumps({"type": "response", "id": request["id"], "ok": True, "result": {"x": 1}}),
    )

    response = await task
    assert response.result == {"x": 1}
    assert [event.data for event in events] == ["one"]
    with pytest.raises(ExecutionAgentError) as exc_info:
        await manager.request(hello.agent_id, "future.unknown", {})
    assert exc_info.value.code == "executionAgent.operationUnavailable"


async def test_latest_connection_replaces_old_and_disconnects_are_classified() -> None:
    manager = ExecutionAgentConnectionManager()
    agent_id = uuid4()
    old = FakeWebSocket()
    new = FakeWebSocket()
    hello = _hello(agent_id)
    await manager.register(hello, old)  # type: ignore[arg-type]
    task = asyncio.create_task(manager.request(agent_id, "filesystem.read", {}))
    await asyncio.sleep(0)

    await manager.register(hello, new)  # type: ignore[arg-type]

    with pytest.raises(ExecutionAgentError) as exc_info:
        await task
    assert exc_info.value.code == "executionAgent.replaced"
    assert old.closed == [(4001, "Replaced by a newer connection")]
    assert manager.snapshot(agent_id) is not None
    assert disconnect_error_code("filesystem.read", True) == "executionAgent.unavailable"
    assert disconnect_error_code("filesystem.write", True) == "executionAgent.outcomeUnknown"
    assert disconnect_error_code("filesystem.upsert", True) == "executionAgent.outcomeUnknown"
    assert disconnect_error_code("filesystem.edit", True) == "executionAgent.outcomeUnknown"
    assert disconnect_error_code("filesystem.patch", True) == "executionAgent.outcomeUnknown"
    assert disconnect_error_code("filesystem.rename", True) == "executionAgent.outcomeUnknown"
    assert disconnect_error_code("integration.git.clone", True) == "executionAgent.outcomeUnknown"
    assert disconnect_error_code("integration.git.add", True) == "executionAgent.outcomeUnknown"
    assert disconnect_error_code("integration.git.commit", True) == "executionAgent.outcomeUnknown"
    assert disconnect_error_code("shell.run", True) == "process.outcomeUnknown"


async def test_replacement_cannot_miss_request_waiting_to_send() -> None:
    manager = ExecutionAgentConnectionManager()
    agent_id = uuid4()
    old = FakeWebSocket()
    new = FakeWebSocket()
    hello = _hello(agent_id)
    await manager.register(hello, old)  # type: ignore[arg-type]
    connection = manager._connections[agent_id]
    await connection.send_lock.acquire()
    task = asyncio.create_task(manager.request(agent_id, "filesystem.read", {}))
    await asyncio.sleep(0)

    await manager.register(hello, new)  # type: ignore[arg-type]
    connection.send_lock.release()

    with pytest.raises(ExecutionAgentError) as exc_info:
        await task
    assert exc_info.value.code == "executionAgent.replaced"
    assert old.sent == []


async def test_caller_cancellation_sends_cancel_and_ignores_late_response() -> None:
    manager = ExecutionAgentConnectionManager()
    websocket = FakeWebSocket()
    hello = _hello()
    await manager.register(hello, websocket)  # type: ignore[arg-type]
    task = asyncio.create_task(manager.request(hello.agent_id, "filesystem.read", {}))
    await asyncio.sleep(0)
    request_id = json.loads(websocket.sent[0])["id"]

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert json.loads(websocket.sent[1]) == {"type": "cancel", "id": request_id}
    await manager.receive(
        hello.agent_id,
        websocket,  # type: ignore[arg-type]
        json.dumps(
            {"type": "response", "id": request_id, "ok": True, "result": {}}
        ),
    )
    assert manager.is_connected(hello.agent_id)


async def test_caller_cancellation_abandons_dispatched_workspace_mutation() -> None:
    manager = ExecutionAgentConnectionManager()
    websocket = FakeWebSocket()
    hello = _hello(operations=["filesystem.write"])
    await manager.register(hello, websocket)  # type: ignore[arg-type]
    task = asyncio.create_task(
        manager.request(hello.agent_id, "filesystem.write", {})
    )
    await asyncio.sleep(0)
    request_id = json.loads(websocket.sent[0])["id"]

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert [json.loads(item)["type"] for item in websocket.sent] == ["request", "cancel"]

    await manager.receive(
        hello.agent_id,
        websocket,  # type: ignore[arg-type]
        json.dumps(
            {
                "type": "response",
                "id": request_id,
                "ok": True,
                "result": {"path": "a.txt"},
            }
        ),
    )
    assert manager.request_ids(hello.agent_id) == ()


async def test_default_timeout_is_bounded_and_cancels_request() -> None:
    manager = ExecutionAgentConnectionManager(default_timeout=0.01)
    websocket = FakeWebSocket()
    hello = _hello()
    await manager.register(hello, websocket)  # type: ignore[arg-type]

    with pytest.raises(ExecutionAgentError) as exc_info:
            await manager.request(hello.agent_id, "filesystem.read", {})

    assert exc_info.value.code == "executionAgent.timeout"
    assert [json.loads(item)["type"] for item in websocket.sent] == ["request", "cancel"]
    assert manager.request_ids(hello.agent_id) == ()


async def test_dispatched_mutation_and_shell_timeouts_have_unknown_outcomes() -> None:
    for operation, expected_code in (
        ("filesystem.write", "executionAgent.outcomeUnknown"),
        ("shell.run", "process.outcomeUnknown"),
    ):
        manager = ExecutionAgentConnectionManager(default_timeout=0.01)
        websocket = FakeWebSocket()
        hello = _hello(operations=[operation])
        await manager.register(hello, websocket)  # type: ignore[arg-type]

        with pytest.raises(ExecutionAgentError) as exc_info:
            await manager.request(hello.agent_id, operation, {})

        assert exc_info.value.code == expected_code
        assert json.loads(websocket.sent[-1])["type"] == "cancel"


@pytest.mark.parametrize("code", ["file.editTextNotFound", "file.editTextNotUnique"])
async def test_execution_client_maps_model_edit_errors(monkeypatch, code) -> None:
    client = ExecutionClient(uuid4(), frozenset({"filesystem.edit"}))

    async def request(*_args, **_kwargs):
        return ResponseMessage.model_validate(
            {
                "type": "response",
                "id": uuid4(),
                "ok": False,
                "error": {"code": code},
            }
        )

    monkeypatch.setattr(
        "mato_loop_backend.execution.client.execution_agent_connections.request", request
    )

    with pytest.raises(ExecutionClientError) as exc_info:
        await client.request("filesystem.edit", {}, WorkspaceWriteResult)
    assert exc_info.value.code == code
