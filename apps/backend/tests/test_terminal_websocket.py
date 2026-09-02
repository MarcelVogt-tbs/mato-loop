import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import WebSocketDisconnect

from mato_loop_backend.api.routes import terminals
from mato_loop_backend.execution import (
    ExecutionClientError,
    TerminalControlResult,
    TerminalOpenResult,
    TerminalReadResult,
)
from mato_loop_backend.execution.terminals import BrowserTerminalConnection
from mato_loop_backend.models import ExecutionAgent, UserAccount


class FakeWebSocket:
    def __init__(self, origin=None, host=None) -> None:
        self.headers = {"origin": origin} if origin else {}
        if host:
            self.headers["host"] = host
        self.cookies = {}
        self.closed = []
        self.accepted = False
        self.sent = []

    async def close(self, code=1000, reason="") -> None:
        self.closed.append((code, reason))

    async def accept(self) -> None:
        self.accepted = True

    async def send_json(self, payload) -> None:
        self.sent.append(payload)

    async def send_bytes(self, payload) -> None:
        self.sent.append(payload)

    async def receive_json(self):
        raise WebSocketDisconnect()


class FakeDb:
    def __init__(self, agent) -> None:
        self.agent = agent

    async def get(self, model, key):
        return self.agent if model is ExecutionAgent and key == self.agent.id else None


class DbContext:
    def __init__(self, db) -> None:
        self.db = db

    async def __aenter__(self):
        return self.db

    async def __aexit__(self, *_args):
        pass


async def test_terminal_websocket_rejects_origin_and_auth(monkeypatch) -> None:
    agent_id = uuid4()
    monkeypatch.setattr(
        terminals, "get_runtime_config", lambda: SimpleNamespace(cors_origin_list=["https://ui.test"])
    )
    wrong_origin = FakeWebSocket("https://evil.test")
    await terminals.browser_terminal(wrong_origin, agent_id)  # type: ignore[arg-type]
    assert wrong_origin.closed[0][0] == 4403

    same_origin = FakeWebSocket("https://mato.example", "mato.example")
    monkeypatch.setattr(terminals, "_browser_login", AsyncMock(return_value=None))
    await terminals.browser_terminal(same_origin, agent_id)  # type: ignore[arg-type]
    assert same_origin.closed[0][0] == 4401

    unauthenticated = FakeWebSocket("https://ui.test")
    await terminals.browser_terminal(unauthenticated, agent_id)  # type: ignore[arg-type]
    assert unauthenticated.closed[0][0] == 4401


async def test_terminal_websocket_checks_owner_and_capabilities(monkeypatch) -> None:
    user = UserAccount(id=uuid4(), username="user", is_active=True)
    agent = ExecutionAgent(
        id=uuid4(),
        user_id=uuid4(),
        alias="Other",
        home_directory="/home/user",
    )
    monkeypatch.setattr(
        terminals, "get_runtime_config", lambda: SimpleNamespace(cors_origin_list=["https://ui.test"])
    )
    login = terminals._BrowserLogin(user, uuid4(), datetime.now(UTC) + timedelta(hours=1))
    monkeypatch.setattr(terminals, "_browser_login", AsyncMock(return_value=login))
    monkeypatch.setattr(terminals, "AsyncSessionFactory", lambda: DbContext(FakeDb(agent)))
    monkeypatch.setattr(
        terminals.execution_agent_connections,
        "snapshot",
        lambda _agent_id: terminals._TERMINAL_OPERATIONS,
    )

    wrong_owner = FakeWebSocket("https://ui.test")
    await terminals.browser_terminal(wrong_owner, agent.id)  # type: ignore[arg-type]
    assert wrong_owner.closed[0][0] == 4404

    agent.user_id = user.id
    monkeypatch.setattr(
        terminals.execution_agent_connections,
        "snapshot",
        lambda _agent_id: frozenset({"terminal.open"}),
    )
    missing_capability = FakeWebSocket("https://ui.test")
    await terminals.browser_terminal(missing_capability, agent.id)  # type: ignore[arg-type]
    assert missing_capability.closed[0][0] == 4403


async def test_terminal_websocket_closes_agent_pty_on_browser_disconnect(monkeypatch) -> None:
    user = UserAccount(id=uuid4(), username="user", is_active=True)
    agent = ExecutionAgent(
        id=uuid4(),
        user_id=user.id,
        alias="Computer",
        home_directory="/home/user",
    )
    calls = []
    personal_id = uuid4()
    active_id = uuid4()

    class FakeClient:
        def __init__(self, *_args):
            pass

        async def request(self, operation, arguments, _model, **_kwargs):
            calls.append((operation, arguments))
            if operation == "terminal.open":
                return TerminalOpenResult(terminal_id=personal_id)
            if operation == "terminal.read":
                await asyncio.sleep(60)
            return TerminalControlResult()

    monkeypatch.setattr(
        terminals, "get_runtime_config", lambda: SimpleNamespace(cors_origin_list=["https://ui.test"])
    )
    login = terminals._BrowserLogin(user, uuid4(), datetime.now(UTC) + timedelta(hours=1))
    monkeypatch.setattr(terminals, "_browser_login", AsyncMock(return_value=login))
    monkeypatch.setattr(terminals, "AsyncSessionFactory", lambda: DbContext(FakeDb(agent)))
    monkeypatch.setattr(terminals, "ExecutionClient", FakeClient)

    async def disconnect_during_run(connection):
        connection.active_terminal_id = str(active_id)
        raise WebSocketDisconnect()

    monkeypatch.setattr(terminals, "_handle_browser", disconnect_during_run)
    monkeypatch.setattr(
        terminals.execution_agent_connections,
        "snapshot",
        lambda _agent_id: terminals._TERMINAL_OPERATIONS,
    )
    websocket = FakeWebSocket("https://ui.test")

    await terminals.browser_terminal(websocket, agent.id)  # type: ignore[arg-type]

    assert websocket.accepted is True
    assert calls[-2:] == [
        ("terminal.close", {"terminalId": str(active_id)}),
        ("terminal.close", {"terminalId": str(personal_id)}),
    ]


async def test_terminal_login_watch_detects_revocation(monkeypatch) -> None:
    user = UserAccount(id=uuid4(), username="user", is_active=True)
    login = terminals._BrowserLogin(user, uuid4(), datetime.now(UTC) + timedelta(hours=1))
    monkeypatch.setattr(terminals.asyncio, "sleep", AsyncMock(return_value=None))
    monkeypatch.setattr(terminals, "AsyncSessionFactory", lambda: DbContext(FakeDb(None)))

    with pytest.raises(terminals.ExecutionClientError, match="auth.sessionExpired"):
        await terminals._watch_login(login)


async def test_terminal_read_retries_agent_timeout() -> None:
    class FakeClient:
        calls = 0

        async def request(self, *_args, **_kwargs):
            self.calls += 1
            if self.calls == 1:
                raise ExecutionClientError("executionAgent.timeout")
            return TerminalReadResult(data="", eof=True)

    client = FakeClient()
    websocket = FakeWebSocket()
    connection = BrowserTerminalConnection(
        uuid4(),
        uuid4(),
        str(uuid4()),
        client,  # type: ignore[arg-type]
        websocket,  # type: ignore[arg-type]
    )

    await terminals._read_terminal(connection)

    assert client.calls == 2
    assert websocket.sent == []


async def test_attached_idle_terminal_rejects_input_without_writing_personal_pty() -> None:
    class ControlWebSocket(FakeWebSocket):
        def __init__(self):
            super().__init__()
            self.controls = [
                {"type": "input", "data": "eA=="},
                {"type": "close"},
            ]

        async def receive_json(self):
            return self.controls.pop(0)

    class ControlClient:
        def __init__(self):
            self.requests = []

        async def request(self, *args, **kwargs):
            self.requests.append((args, kwargs))
            return TerminalControlResult()

    websocket = ControlWebSocket()
    client = ControlClient()
    connection = BrowserTerminalConnection(
        uuid4(),
        uuid4(),
        str(uuid4()),
        client,  # type: ignore[arg-type]
        websocket,  # type: ignore[arg-type]
        session_id=uuid4(),
        mode="auto",
    )

    await terminals._handle_browser(connection)

    assert client.requests == []
    assert websocket.sent[0]["error"]["code"] == "terminal.readOnly"


async def test_attached_idle_terminal_accepts_resize_without_error() -> None:
    class ControlWebSocket(FakeWebSocket):
        def __init__(self):
            super().__init__()
            self.controls = [
                {"type": "resize", "rows": 41, "columns": 132},
                {"type": "close"},
            ]

        async def receive_json(self):
            return self.controls.pop(0)

    client = SimpleNamespace(request=AsyncMock(return_value=TerminalControlResult()))
    websocket = ControlWebSocket()
    connection = BrowserTerminalConnection(
        uuid4(),
        uuid4(),
        str(uuid4()),
        client,  # type: ignore[arg-type]
        websocket,  # type: ignore[arg-type]
        session_id=uuid4(),
        mode="auto",
    )

    await terminals._handle_browser(connection)

    assert (connection.rows, connection.columns) == (41, 132)
    client.request.assert_not_awaited()
    assert websocket.sent == []
