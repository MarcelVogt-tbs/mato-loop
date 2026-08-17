import asyncio
import json
from uuid import uuid4

import pytest
from pydantic import ValidationError

from mato_loop_backend.execution.manager import (
    ExecutionAgentConnectionManager,
    ExecutionAgentError,
    disconnect_error_code,
)
from mato_loop_backend.execution.protocol import SUPPORTED_OPERATIONS, HelloMessage


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
            "protocolVersion": 3,
            "agentId": agent_id or uuid4(),
            "name": "Test Agent",
            "platform": "linux",
            "operations": operations
            or ["workspace.list", "workspace.read", "workspace.write"],
            "roots": [
                {"id": uuid4(), "label": "Projects", "revision": "a" * 64}
            ],
        }
    )


def test_protocol_is_strict_and_requires_unique_snapshots() -> None:
    payload = _hello().model_dump(by_alias=True)
    payload["extra"] = True
    with pytest.raises(ValidationError):
        HelloMessage.model_validate(payload)

    payload.pop("extra")
    payload["operations"] *= 2
    with pytest.raises(ValidationError):
        HelloMessage.model_validate(payload)

    payload = _hello().model_dump(by_alias=True)
    payload["homeDirectory"] = "/workspace"
    with pytest.raises(ValidationError):
        HelloMessage.model_validate(payload)


def test_protocol_accepts_complete_home_metadata() -> None:
    payload = _hello().model_dump(by_alias=True)
    root = payload["roots"][0]
    payload.update(
        homeDirectory="/workspace",
        homeRootId=root["id"],
        homeRootRevision=root["revision"],
        homeRelativeDirectory=".",
    )

    hello = HelloMessage.model_validate(payload)

    assert hello.home_directory == "/workspace"

def test_supported_operations_match_the_execution_agent() -> None:
    assert SUPPORTED_OPERATIONS == {
        "workspace.stat",
        "workspace.glob",
        "workspace.grep",
        "workspace.list",
        "workspace.read",
        "workspace.write",
        "workspace.mkdir",
        "workspace.rename",
        "workspace.resolve",
        "workspace.delete",
        "workspace.copy",
        "workspace.move",
        "workspace.import",
        "workspace.export",
        "process.exec",
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
    }


async def test_manager_correlates_events_and_response_and_ignores_unknown_operations() -> None:
    manager = ExecutionAgentConnectionManager()
    websocket = FakeWebSocket()
    hello = _hello(operations=["workspace.read", "future.unknown"])
    await manager.register(hello, websocket)  # type: ignore[arg-type]
    events = []

    task = asyncio.create_task(
        manager.request(
            hello.agent_id,
            "workspace.read",
            {"path": "README.md"},
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
    task = asyncio.create_task(manager.request(agent_id, "workspace.read", {}))
    await asyncio.sleep(0)

    await manager.register(hello, new)  # type: ignore[arg-type]

    with pytest.raises(ExecutionAgentError) as exc_info:
        await task
    assert exc_info.value.code == "executionAgent.replaced"
    assert old.closed == [(4001, "Replaced by a newer connection")]
    assert manager.snapshot(agent_id) is not None
    assert disconnect_error_code("workspace.read", True) == "executionAgent.unavailable"
    assert disconnect_error_code("workspace.write", True) == "executionAgent.outcomeUnknown"
    assert disconnect_error_code("workspace.rename", True) == "executionAgent.outcomeUnknown"
    assert disconnect_error_code("integration.git.clone", True) == "executionAgent.outcomeUnknown"
    assert disconnect_error_code("integration.git.add", True) == "executionAgent.outcomeUnknown"
    assert disconnect_error_code("integration.git.commit", True) == "executionAgent.outcomeUnknown"
    assert disconnect_error_code("process.exec", True) == "process.outcomeUnknown"


async def test_replacement_cannot_miss_request_waiting_to_send() -> None:
    manager = ExecutionAgentConnectionManager()
    agent_id = uuid4()
    old = FakeWebSocket()
    new = FakeWebSocket()
    hello = _hello(agent_id)
    await manager.register(hello, old)  # type: ignore[arg-type]
    connection = manager._connections[agent_id]
    await connection.send_lock.acquire()
    task = asyncio.create_task(manager.request(agent_id, "workspace.read", {}))
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
    task = asyncio.create_task(manager.request(hello.agent_id, "workspace.read", {}))
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


async def test_caller_cancellation_waits_for_dispatched_workspace_mutation() -> None:
    manager = ExecutionAgentConnectionManager()
    websocket = FakeWebSocket()
    hello = _hello(operations=["workspace.write"])
    await manager.register(hello, websocket)  # type: ignore[arg-type]
    deferred = []
    task = asyncio.create_task(
        manager.request(
            hello.agent_id,
            "workspace.write",
            {},
            on_cancellation_deferred=lambda: deferred.append(True),
        )
    )
    await asyncio.sleep(0)
    request_id = json.loads(websocket.sent[0])["id"]

    task.cancel()
    await asyncio.sleep(0)

    assert deferred == [True]
    assert not task.done()
    assert [json.loads(item)["type"] for item in websocket.sent] == ["request"]

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
    assert (await task).result == {"path": "a.txt"}


async def test_cancelled_dispatched_workspace_mutation_disconnect_is_outcome_unknown() -> None:
    manager = ExecutionAgentConnectionManager()
    websocket = FakeWebSocket()
    hello = _hello(operations=["workspace.write"])
    await manager.register(hello, websocket)  # type: ignore[arg-type]
    task = asyncio.create_task(
        manager.request(hello.agent_id, "workspace.write", {})
    )
    await asyncio.sleep(0)

    task.cancel()
    await asyncio.sleep(0)
    await manager.unregister(hello.agent_id, websocket)  # type: ignore[arg-type]

    with pytest.raises(ExecutionAgentError) as exc_info:
        await task
    assert exc_info.value.code == "executionAgent.outcomeUnknown"
    assert [json.loads(item)["type"] for item in websocket.sent] == ["request"]


async def test_default_timeout_is_bounded_and_cancels_request() -> None:
    manager = ExecutionAgentConnectionManager(default_timeout=0.01)
    websocket = FakeWebSocket()
    hello = _hello()
    await manager.register(hello, websocket)  # type: ignore[arg-type]

    with pytest.raises(ExecutionAgentError) as exc_info:
        await manager.request(hello.agent_id, "workspace.read", {})

    assert exc_info.value.code == "executionAgent.timeout"
    assert [json.loads(item)["type"] for item in websocket.sent] == ["request", "cancel"]
    assert manager.request_ids(hello.agent_id) == ()


async def test_dispatched_mutation_and_process_timeouts_have_unknown_outcomes() -> None:
    for operation, expected_code in (
        ("workspace.write", "executionAgent.outcomeUnknown"),
        ("process.exec", "process.outcomeUnknown"),
    ):
        manager = ExecutionAgentConnectionManager(default_timeout=0.01)
        websocket = FakeWebSocket()
        hello = _hello(operations=[operation])
        await manager.register(hello, websocket)  # type: ignore[arg-type]

        with pytest.raises(ExecutionAgentError) as exc_info:
            await manager.request(hello.agent_id, operation, {})

        assert exc_info.value.code == expected_code
        assert json.loads(websocket.sent[-1])["type"] == "cancel"
