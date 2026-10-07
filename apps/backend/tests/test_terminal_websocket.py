import asyncio
import base64
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from fastapi import FastAPI, WebSocketDisconnect
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

import mato_loop_backend.api.routes.terminals as api_routes_terminals
import mato_loop_backend.api.websocket_access as api_websocket_access
from mato_loop_backend.agent.tool_round import persist_tool_result
from mato_loop_backend.execution import (
    ExecutionClientError,
    ProcessResult,
    TerminalControlResult,
    TerminalOpenResult,
)
from mato_loop_backend.execution import terminals as execution
from mato_loop_backend.models import ChatSession, ExecutionAgent, LoginSession, UserAccount
from mato_loop_backend.providers.base import ToolCall
from mato_loop_backend.settings import InstanceLimits, TrustLevel
from mato_loop_backend.tools import computer


class FakeWebSocket:
    def __init__(self, session_id=None, origin="https://ui.test"):
        self.headers = {"origin": origin}
        self.query_params = {"sessionId": str(session_id)} if session_id else {}
        self.cookies = {}
        self.sent, self.closed = [], []
        self.accepted = False

    async def accept(self):
        self.accepted = True

    async def close(self, code=1000, reason=""):
        self.closed.append((code, reason))

    async def send_text(self, data):
        self.sent.append(json.loads(data))

    async def send_json(self, data):
        self.sent.append(data)

    async def send_bytes(self, data):
        self.sent.append(data)

    async def receive_json(self):
        raise WebSocketDisconnect()


@pytest.fixture
def env(monkeypatch):
    user = UserAccount(id=uuid4(), username="owner", is_active=True)
    agent = ExecutionAgent(id=uuid4(), user_id=user.id, alias="Computer", home_directory="/home/user")
    session = ChatSession(id=uuid4(), user_id=user.id, session_type="chat", preferred_execution_agent_id=agent.id,
                          control_center={"tabs": [{"app_id": "terminal"}]})
    login = LoginSession(id=uuid4(), user_id=user.id, expires_at=datetime.now(UTC) + timedelta(hours=1))
    objects = {(type(obj), obj.id): obj for obj in (user, agent, session, login)}
    calls = []

    class Db:
        failure = False

        async def get(self, model, key, **_kwargs):
            if self.failure:
                raise OperationalError("select", {}, Exception("unavailable"))
            return objects.get((model, key))

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            pass

    class Client:
        def __init__(self, _id, _ops, current_directory, **_kwargs):
            self.current_directory = current_directory

        async def request(self, operation, arguments, _model, **_kwargs):
            calls.append((operation, arguments))
            if operation == "terminal.open":
                return TerminalOpenResult(terminal_id=arguments["terminalId"])
            if operation == "terminal.read":
                await asyncio.Event().wait()
            return TerminalControlResult()

    db = Db()
    db.scalars = AsyncMock(return_value=Mock(all=Mock(return_value=[])))
    db.commit = AsyncMock()
    coordinator = execution.TerminalCoordinator()
    monkeypatch.setattr(api_routes_terminals, "get_runtime_config", lambda: SimpleNamespace(cors_origin_list=["https://ui.test"]))
    monkeypatch.setattr(api_websocket_access, "_browser_login", AsyncMock(return_value=api_websocket_access._BrowserLogin(user, login.id, login.expires_at)))
    monkeypatch.setattr(api_websocket_access, "AsyncSessionFactory", lambda: db)
    monkeypatch.setattr(execution, "AsyncSessionFactory", lambda: db)
    monkeypatch.setattr(execution, "ExecutionClient", Client)
    monkeypatch.setattr(execution.execution_agent_connections, "generation", lambda _id: 1)
    monkeypatch.setattr(execution.execution_agent_connections, "snapshot", lambda _id: execution.TERMINAL_OPERATIONS)
    monkeypatch.setattr(api_routes_terminals, "terminal_coordinator", coordinator)
    return SimpleNamespace(user=user, agent=agent, session=session, login=login, db=db, objects=objects,
                           calls=calls, coordinator=coordinator)


async def open_viewer(env):
    return await env.coordinator.open(env.user.id, env.session.id, env.agent.id, env.login.id, FakeWebSocket())


@pytest.mark.parametrize("opened_before_shell", [False, True])
async def test_opening_terminal_keeps_output_when_provider_reuses_call_id_in_one_run(env, monkeypatch, opened_before_shell):
    monkeypatch.setattr(computer, "terminal_coordinator", env.coordinator)
    viewer = snapshot = None

    async def execute(_operation, arguments, _model, *, on_event, **_kwargs):
        nonlocal viewer, snapshot
        await on_event(SimpleNamespace(event="stdout", data=arguments["command"] + "\n"))
        if arguments["command"] == "running":
            viewer = await open_viewer(env)
            viewer.queue.get_nowait()
            snapshot = json.loads(viewer.queue.get_nowait()[0])
            await on_event(SimpleNamespace(event="stderr", data="live\n"))
        return ProcessResult(
            exit_code=0, timed_out=False, duration_ms=1, stdout_bytes=8, stderr_bytes=5,
            stdout_truncated=False, stderr_truncated=False,
        )

    context = SimpleNamespace(
        session=env.session, tool_call=SimpleNamespace(id="isolated"), run_id=uuid4(),
        execution_client=SimpleNamespace(agent_id=env.agent.id, request=execute),
    )
    env.db.add = Mock()
    env.db.flush = AsyncMock()
    try:
        if opened_before_shell:
            await env.coordinator.release(await open_viewer(env))
        for command in ("completed", "running"):
            result = await computer._run_process(
                context, "shell.run", {"command": command},
                requested_arguments={"command": command}, executed_arguments={"command": command},
            )
            if command == "completed":
                message, _, _ = await persist_tool_result(
                    env.db, SimpleNamespace(id=context.run_id), env.session, ToolCall("isolated", "shell_run", {}),
                    content=result.content, metadata=result.metadata, attachments=(), file_actions=(),
                    trust_level=TrustLevel.PUBLIC, ephemeral_output=False, limits=InstanceLimits(),
                    attachment_service=lambda **_kwargs: None,
                )
                env.db.scalars.return_value = Mock(all=Mock(return_value=[message]))
        messages = [json.loads(viewer.queue.get_nowait()[0]) for _ in range(viewer.queue.qsize())]
        delivered = b"".join(base64.b64decode(item["data"]) for item in messages if item["type"] == "terminal.agentOutput")
        assert (
            snapshot["busy"], base64.b64decode(snapshot["agentData"]),
            delivered, viewer.runtime.status()["busy"],
        ) == (
            True, b"\r\n$ completed\r\ncompleted\r\n\r\n$ running\r\nrunning\r\n",
            b"live\r\n", False,
        )
    finally:
        await env.coordinator.shutdown()


async def test_resume_keeps_output_and_partial_input_without_clearing_screen(env):
    first = await open_viewer(env)
    history = b"$ ls\r\none.txt  two.txt\r\n$ cat \x1b[2D"
    try:
        await first.runtime.send_bytes(history)
        await env.coordinator.release(first)
        second = await open_viewer(env)
        opened = json.loads(second.queue.get_nowait()[0])
        snapshot = json.loads(second.queue.get_nowait()[0])
        assert opened["restored"] and second.runtime is first.runtime
        assert base64.b64decode(snapshot["personalData"]) == history
    finally:
        await env.coordinator.shutdown()


@pytest.mark.parametrize("invalid", ["origin", "login", "session", "owner", "child", "automation", "agent", "tab", "revoked", "capability"])
async def test_access_checked_before_open_or_replay(env, monkeypatch, invalid):
    socket = FakeWebSocket(env.session.id)
    if invalid == "origin":
        socket.headers["origin"] = "https://evil.test"
    elif invalid == "login":
        monkeypatch.setattr(api_websocket_access, "_browser_login", AsyncMock(return_value=None))
    elif invalid == "session":
        socket.query_params = {}
    elif invalid == "owner":
        env.session.user_id = uuid4()
    elif invalid == "child":
        env.session.parent_session_id = uuid4()
    elif invalid == "automation":
        env.session.session_type = "automation"
    elif invalid == "agent":
        env.agent.user_id = uuid4()
    elif invalid == "tab":
        env.session.control_center = {"tabs": []}
    elif invalid == "revoked":
        env.agent.revoked_at = datetime.now(UTC)
    else:
        monkeypatch.setattr(execution.execution_agent_connections, "snapshot", lambda _id: frozenset({"terminal.open"}))
    await api_routes_terminals.browser_terminal(socket, env.agent.id)
    assert not socket.accepted and socket.closed and not env.calls


def test_actual_websocket_resume_takeover_and_native_input(env):
    app = FastAPI()
    app.include_router(api_routes_terminals.router)
    url = f"/terminals/{env.agent.id}?sessionId={env.session.id}"
    with TestClient(app) as client:
        with client.websocket_connect(url, headers={"origin": "https://ui.test"}) as first:
            opened = first.receive_json()
            assert not opened["restored"] and "mode" not in opened
            assert first.receive_json()["agentData"] == ""
            runtime = next(iter(env.coordinator._runtimes.values()))
            client.portal.call(runtime.send_bytes, b"shared history")
            assert first.receive_bytes() == b"shared history"
            # Await the server close before TestClient cancels its application task.
            first.close()
            assert first.receive()["type"] == "websocket.close"
        with client.websocket_connect(url, headers={"origin": "https://ui.test"}) as second:
            restored = second.receive_json()
            assert restored["restored"]
            assert restored["terminalId"] == opened["terminalId"]
            assert base64.b64decode(second.receive_json()["personalData"]) == b"shared history"
            with client.websocket_connect(url, headers={"origin": "https://ui.test"}) as third:
                assert third.receive_json()["terminalId"] == opened["terminalId"]
                third.receive_json()
                # Resume may refresh status on the old socket before replacement.
                event = second.receive_json()
                if event["type"] == "terminal.status":
                    event = second.receive_json()
                assert event == {"type": "terminal.replaced", "reason": "replaced"}
                second.send_json({"type": "close"})
                assert second.receive()["type"] == "websocket.close"
                third.send_json({"type": "resize", "rows": 30, "columns": 100})
                third.send_json({"type": "input", "data": "eA=="})
                third.send_json({"type": "mode", "mode": "auto"})
                assert third.receive_json()["error"]["code"] == "terminal.messageInvalid"
                third.send_json({"type": "close"})
                assert third.receive_json()["reason"] == "terminated"
                assert third.receive()["type"] == "websocket.close"
        client.portal.call(env.coordinator.shutdown)
    assert sum(op == "terminal.open" for op, _ in env.calls) == 1
    assert sum(op == "terminal.close" for op, _ in env.calls) == 1
    assert sum(op == "terminal.write" for op, _ in env.calls) == 1
    assert ("terminal.resize", {
        "terminalId": opened["terminalId"], "rows": 30, "columns": 100,
    }) in env.calls


@pytest.mark.parametrize("invalid", ["login", "expiry", "inactive", "session", "tab", "agent", "generation"])
async def test_no_viewer_validity_cleanup(env, monkeypatch, invalid):
    monkeypatch.setattr(execution, "VALIDITY_INTERVAL", 0.01)
    viewer = await open_viewer(env)
    await env.coordinator.release(viewer)
    if invalid == "login":
        env.objects.pop((LoginSession, env.login.id))
    elif invalid == "expiry":
        env.login.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    elif invalid == "inactive":
        env.user.is_active = False
    elif invalid == "session":
        env.objects.pop((ChatSession, env.session.id))
    elif invalid == "tab":
        env.session.control_center = {"tabs": []}
    elif invalid == "agent":
        env.session.preferred_execution_agent_id = uuid4()
    else:
        monkeypatch.setattr(execution.execution_agent_connections, "generation", lambda _id: 2)
    await asyncio.wait_for(viewer.runtime.task, 1)
    assert viewer.runtime.closed and not env.coordinator._runtimes


async def test_transient_db_failure_suspends_controls_without_killing_shell(env, monkeypatch):
    monkeypatch.setattr(execution, "VALIDITY_INTERVAL", 0.01)
    viewer = await open_viewer(env)
    env.db.failure = True
    await asyncio.sleep(0.03)
    assert viewer.runtime.suspended_since is not None and not viewer.runtime.closed
    with pytest.raises(ExecutionClientError, match="terminal.unavailable"):
        await viewer.runtime.check_access()
    env.db.failure = False
    await asyncio.sleep(0.03)
    assert viewer.runtime.suspended_since is None
    await env.coordinator.shutdown()


async def test_db_failure_grace_is_bounded(env, monkeypatch):
    monkeypatch.setattr(execution, "VALIDITY_INTERVAL", 0.01)
    monkeypatch.setattr(execution, "VALIDITY_GRACE", 0.02)
    viewer = await open_viewer(env)
    env.db.failure = True
    await asyncio.wait_for(viewer.runtime.task, 1)
    assert viewer.runtime.closed and env.calls[-1][0] == "terminal.close"


async def test_login_takeover_and_stale_controls(env):
    first = await open_viewer(env)
    login = LoginSession(id=uuid4(), user_id=env.user.id, expires_at=env.login.expires_at)
    env.objects[LoginSession, login.id] = login
    second = await env.coordinator.open(env.user.id, env.session.id, env.agent.id, login.id, FakeWebSocket())
    env.objects.pop((LoginSession, env.login.id))
    first.websocket.receive_json = AsyncMock(return_value={"type": "close"})
    await api_routes_terminals._handle_browser(first)
    await env.coordinator.release(first)
    await second.runtime.check_access()
    assert second.runtime.viewer is second and not second.runtime.closed
    await env.coordinator.shutdown()


async def test_shell_exit_rejects_new_work_but_allows_browser_close(env):
    viewer = await open_viewer(env)
    runtime = viewer.runtime
    runtime.shell_exited = True
    try:
        assert runtime.status()["state"] == "closed"
        with pytest.raises(ExecutionClientError, match="terminal.disconnected"):
            await runtime.check_access()
        viewer.websocket.receive_json = AsyncMock(return_value={"type": "close"})
        await asyncio.wait_for(api_routes_terminals._handle_browser(viewer), 1)
        assert not env.coordinator._runtimes
        assert env.calls[-1][0] == "terminal.close"
    finally:
        await env.coordinator.shutdown()


async def test_bounded_raw_replay_and_slow_viewer_resync(env):
    first = await open_viewer(env)
    for _ in range(140):
        await first.runtime.send_bytes(b"x" * 65536)
    assert first.reason == "resyncRequired" and first.queue.empty()
    second = await open_viewer(env)
    second.queue.get_nowait()
    snapshot = json.loads(second.queue.get_nowait()[0])
    await first.runtime.send_bytes(b"after")
    assert second.queue.get_nowait()[0] == b"after"
    assert snapshot["personalTruncated"]
    assert len(base64.b64decode(snapshot["personalData"])) == execution.REPLAY_BYTES
    await env.coordinator.shutdown()


async def test_concurrent_open_cancel_and_uncertain_response_cleanup(env, monkeypatch):
    started, release = asyncio.Event(), asyncio.Event()
    original = execution.ExecutionClient.request

    async def request(self, operation, *args, **kwargs):
        if operation == "terminal.open":
            started.set()
            await release.wait()
        return await original(self, operation, *args, **kwargs)

    monkeypatch.setattr(execution.ExecutionClient, "request", request)
    first = asyncio.create_task(open_viewer(env))
    await started.wait()
    first.cancel()
    second = asyncio.create_task(open_viewer(env))
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await first
    viewer = await second
    assert sum(op == "terminal.open" for op, _ in env.calls) == 1
    await env.coordinator.terminate(viewer.runtime)
    assert env.calls[-1] == ("terminal.close", {"terminalId": viewer.runtime.terminal_id})

    async def uncertain(self, operation, arguments, *args, **kwargs):
        if operation == "terminal.open":
            env.calls.append((operation, arguments))
            raise ExecutionClientError("executionAgent.timeout")
        return await original(self, operation, arguments, *args, **kwargs)

    monkeypatch.setattr(execution.ExecutionClient, "request", uncertain)
    with pytest.raises(ExecutionClientError):
        await open_viewer(env)
    assert env.calls[-1][0] == "terminal.close"
    assert env.calls[-1][1]["terminalId"] == env.calls[-2][1]["terminalId"]
    assert not env.coordinator._runtimes


@pytest.mark.parametrize("limit", ["MAX_RUNTIMES", "MAX_OWNER_RUNTIMES", "MAX_AGENT_RUNTIMES"])
async def test_runtime_limits_allow_resume_but_not_another_shell(env, monkeypatch, limit):
    first = await open_viewer(env)
    monkeypatch.setattr(execution, limit, 1)
    assert (await open_viewer(env)).runtime is first.runtime
    env.session = ChatSession(id=uuid4(), user_id=env.user.id, session_type="chat", preferred_execution_agent_id=env.agent.id,
                              control_center={"tabs": [{"app_id": "terminal"}]})
    env.objects[ChatSession, env.session.id] = env.session
    with pytest.raises(ExecutionClientError, match="terminal.unavailable"):
        await open_viewer(env)
    await env.coordinator.shutdown()


async def test_slow_open_does_not_hold_a_global_rpc_lock(env, monkeypatch):
    started, release = asyncio.Event(), asyncio.Event()
    original = execution.ExecutionClient.request

    async def request(self, operation, *args, **kwargs):
        if operation == "terminal.open" and not started.is_set():
            started.set()
            await release.wait()
        return await original(self, operation, *args, **kwargs)

    monkeypatch.setattr(execution.ExecutionClient, "request", request)
    first = asyncio.create_task(open_viewer(env))
    await started.wait()
    env.session = ChatSession(id=uuid4(), user_id=env.user.id, session_type="chat", preferred_execution_agent_id=env.agent.id,
                              control_center={"tabs": [{"app_id": "terminal"}]})
    env.objects[ChatSession, env.session.id] = env.session
    second = await asyncio.wait_for(open_viewer(env), 1)
    assert second.runtime.terminal_id and not first.done()
    release.set()
    await first
    await env.coordinator.shutdown()


async def test_explicit_close_queued_during_open_precedes_failed_snapshot_send(env, monkeypatch):
    started, release = asyncio.Event(), asyncio.Event()
    original = execution.ExecutionClient.request

    async def request(self, operation, *args, **kwargs):
        if operation == "terminal.open":
            started.set()
            await release.wait()
        return await original(self, operation, *args, **kwargs)

    monkeypatch.setattr(execution.ExecutionClient, "request", request)
    socket = FakeWebSocket(env.session.id)
    socket.receive_json = AsyncMock(return_value={"type": "close"})
    socket.send_text = AsyncMock(side_effect=WebSocketDisconnect())
    browser = asyncio.create_task(api_routes_terminals.browser_terminal(socket, env.agent.id))
    await started.wait()
    release.set()
    await asyncio.wait_for(browser, 1)
    assert not env.coordinator._runtimes
    assert sum(op == "terminal.close" for op, _ in env.calls) == 1
