import asyncio
import base64
import json
from uuid import uuid4

import pytest
from pydantic import ValidationError

from mato_loop_backend.execution.client import (
    ExecutionClient,
    ExecutionClientError,
    SharedInspectResult,
    SharedProcessResult,
    ShellInspectResult,
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
        "integration.openscad.discover",
        "integration.openscad.render",
        "integration.openscad.render6",
        "integration.openscad.test",
        "integration.browser.status",
        "integration.browser.setup",
        "integration.browser.setupread",
        "integration.browser.setupwrite",
        "integration.browser.setupresize",
        "integration.browser.open",
        "integration.browser.control",
        "integration.browser.resize",
        "integration.browser.navigation",
        "integration.browser.clipboard",
        "integration.browser.view",
        "integration.browser.close",
        "integration.browser.reset",
        "integration.browser.execute",
        "integration.browser.file",
        "shell.inspect",
        "terminal.open",
        "terminal.read",
        "terminal.write",
        "terminal.resize",
        "terminal.close",
        "terminal.run",
        "terminal.shared.open",
        "terminal.shared.status",
        "terminal.shared.stage",
        "terminal.shared.inspect",
        "terminal.shared.execute",
        "terminal.shared.cancel",
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
    assert disconnect_error_code("shell.run", True) == "process.outcomeUnknown"
    assert disconnect_error_code("terminal.shared.execute", True) == "process.outcomeUnknown"


async def test_generation_pinned_client_never_dispatches_to_replacement(monkeypatch) -> None:
    from mato_loop_backend.execution import client as client_module

    manager = ExecutionAgentConnectionManager()
    hello = _hello(operations=["terminal.shared.execute"])
    first, second = FakeWebSocket(), FakeWebSocket()
    await manager.register(hello, first)
    client = ExecutionClient(hello.agent_id, frozenset(hello.operations), expected_generation=manager.generation(hello.agent_id))
    monkeypatch.setattr(client_module, "execution_agent_connections", manager)
    await manager.register(hello, second)
    with pytest.raises(ExecutionClientError, match="executionAgent.replaced"):
        await client.request("terminal.shared.execute", {}, SharedProcessResult)
    assert not first.sent and not second.sent


@pytest.mark.parametrize("classification", ["read", "write"])
@pytest.mark.parametrize("complete", [False, True])
@pytest.mark.parametrize("prefix", [None, ["pwd"]])
def test_shared_inspector_preserves_ordinary_inspection(classification, complete, prefix):
    data = {
        "command": "pwd", "currentDirectory": "/home/user", "revision": 1, "contextRevision": 1,
        "inspection": {"targets": [{"kind": "toolchain_metadata", "path": "/usr/bin/python3"}],
                       "commands": [{"tokens": ["pwd"], "prefix": prefix, "classification": classification}],
                       "classification": classification, "readOnly": classification == "read",
                       "analysisComplete": complete, "inspectionRevision": "a" * 64},
    }
    inspection = SharedInspectResult.model_validate(data).inspection
    assert inspection.classification == classification
    assert inspection.analysis_complete == complete
    assert inspection.commands[0].prefix == prefix
    assert inspection.targets[0].kind == "toolchain_metadata"


@pytest.mark.parametrize("command", ["pwd\0", "\u00e4" * (256 * 1024)])
def test_shared_inspector_keeps_command_validation(command):
    with pytest.raises(ValidationError):
        SharedInspectResult.model_validate({
            "command": command, "currentDirectory": "/home/user", "revision": 1, "contextRevision": 1,
            "inspection": {"targets": [], "commands": [], "classification": "read", "readOnly": True,
                           "analysisComplete": False, "inspectionRevision": "a" * 64},
        })


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


@pytest.mark.parametrize("directory", [
    "/usr/local/sbin", "/usr/local/bin", "/usr/sbin", "/usr/bin", "/sbin", "/bin",
])
@pytest.mark.parametrize("suffix", ["", "/python3"])
def test_shell_inspection_accepts_only_fixed_toolchain_metadata_locations(directory, suffix) -> None:
    result = ShellInspectResult.model_validate({
        "targets": [
            {"path": directory + suffix, "kind": "toolchain_metadata"},
            {"path": "/dev/null", "kind": "discard"},
        ],
        "analysisComplete": False,
        "classification": "read",
        "readOnly": True,
        "commands": [],
        "inspectionRevision": "a" * 64,
    })
    assert result.classification == "read"
    assert result.analysis_complete is False
    assert [target.path for target in result.targets] == [directory + suffix, "/dev/null"]


@pytest.mark.parametrize("kind", [
    "directory", "input", "output", "executable", "source", "script", "path",
])
def test_shell_inspection_preserves_legacy_target_kinds_and_incomplete_writes(kind) -> None:
    result = ShellInspectResult.model_validate({
        "targets": [{"path": "/outside/target", "kind": kind}],
        "analysisComplete": False,
        "classification": "write",
        "commands": [],
        "inspectionRevision": "a" * 64,
    })
    assert result.targets[0].kind == kind
    assert result.classification == "write"
    assert result.read_only is False


@pytest.mark.parametrize("target", [
    {"path": "/tmp/null", "kind": "discard"},
    {"path": "/dev/null/child", "kind": "discard"},
    {"path": "/usr/bin/python3/data", "kind": "toolchain_metadata"},
    {"path": "/usr/local/bin/python3/data", "kind": "toolchain_metadata"},
    {"path": "/usr/bin-extra/python3", "kind": "toolchain_metadata"},
    {"path": "/usr/local", "kind": "toolchain_metadata"},
    {"path": "/opt/bin", "kind": "toolchain_metadata"},
    {"path": "/usr/bin/../etc", "kind": "toolchain_metadata"},
    {"path": "/usr/bin//python3", "kind": "toolchain_metadata"},
    {"path": "/usr/bin/python3", "kind": "future_metadata"},
])
async def test_shell_client_rejects_invalid_special_targets(monkeypatch, target) -> None:
    client = ExecutionClient(uuid4(), frozenset({"shell.inspect"}))

    async def request(*_args, **_kwargs):
        return ResponseMessage.model_validate({
            "type": "response", "id": uuid4(), "ok": True,
            "result": {
                "targets": [target], "analysisComplete": False,
                "classification": "read", "readOnly": True, "commands": [],
                "inspectionRevision": "a" * 64,
            },
        })

    monkeypatch.setattr(
        "mato_loop_backend.execution.client.execution_agent_connections.request", request,
    )
    with pytest.raises(ExecutionClientError) as error:
        await client.request("shell.inspect", {}, ShellInspectResult)
    assert error.value.code == "executionAgent.protocolError"


@pytest.mark.parametrize("overrides", [
    {"readOnly": False},
    {"classification": "write"},
    {"commands": [{"tokens": ["touch", "file"], "classification": "write"}]},
])
def test_shell_inspection_rejects_inconsistent_classification(overrides) -> None:
    payload = {
        "targets": [], "analysisComplete": False, "classification": "read",
        "readOnly": True, "commands": [], "inspectionRevision": "a" * 64,
    }
    with pytest.raises(ValidationError):
        ShellInspectResult.model_validate({**payload, **overrides})
    payload.pop("readOnly")
    with pytest.raises(ValidationError):
        ShellInspectResult.model_validate(payload)


def test_shell_inspection_accepts_redirect_only_writes() -> None:
    result = ShellInspectResult.model_validate({
        "targets": [{"path": "/work/output", "kind": "output"}],
        "analysisComplete": True, "classification": "write", "commands": [],
        "inspectionRevision": "a" * 64,
    })
    assert result.read_only is False


@pytest.mark.parametrize("operation", ["shell.inspect", "shell.run", "terminal.run"])
async def test_shell_client_maps_invalid_syntax_without_retry(monkeypatch, operation) -> None:
    client = ExecutionClient(uuid4(), frozenset({operation}))
    calls = []

    async def request(*args, **_kwargs):
        calls.append(args)
        return ResponseMessage.model_validate({
            "type": "response", "id": uuid4(), "ok": False,
            "error": {"code": "process.invalidSyntax"},
        })

    monkeypatch.setattr(
        "mato_loop_backend.execution.client.execution_agent_connections.request", request,
    )
    with pytest.raises(ExecutionClientError) as error:
        await client.request(operation, {}, ShellInspectResult)
    assert error.value.code == "process.invalidSyntax"
    assert len(calls) == 1
