import asyncio
import base64
import hashlib
import json
from contextlib import asynccontextmanager, contextmanager
from datetime import UTC, datetime, timedelta
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI, UploadFile, WebSocketDisconnect
from fastapi.testclient import TestClient
from PIL import Image

# Initialize the runtime before routes, matching application import order.
from mato_loop_backend.agent import tools as tools_module
from mato_loop_backend.agent.permissions import PermissionDecision
from mato_loop_backend.agent.runtime import AgentRuntime  # noqa: F401
from mato_loop_backend.agent.tool_registry import ToolContext
from mato_loop_backend.api.errors import register_error_handlers
from mato_loop_backend.api.routes import browsers as routes
from mato_loop_backend.api.routes.terminals import _BrowserLogin
from mato_loop_backend.db.session import get_db_session
from mato_loop_backend.execution import browsers as execution
from mato_loop_backend.execution.client import ExecutionClientError
from mato_loop_backend.execution.manager import ExecutionAgentConnectionManager
from mato_loop_backend.execution.protocol import SUPPORTED_OPERATIONS, HelloMessage, ResponseMessage
from mato_loop_backend.models import ChatSession, ExecutionAgent, LoginSession, UserAccount
from mato_loop_backend.providers.base import ToolCall
from mato_loop_backend.security import require_user
from mato_loop_backend.settings import (
    AgentProfile,
    IntegrationInstance,
    IntegrationSettings,
    SettingsStore,
    ToolSettings,
    UserIntegrationSettings,
)

ORIGIN = {"origin": "https://ui.test"}


@pytest.fixture
def env(monkeypatch):
    user = UserAccount(id=uuid4(), username="owner", is_active=True)
    agent = ExecutionAgent(id=uuid4(), user_id=user.id, alias="Computer", home_directory="/home/user")
    session = ChatSession(id=uuid4(), user_id=user.id, session_type="chat",
                          preferred_execution_agent_id=agent.id, control_center={"tabs": [{"app_id": "browser"}]})
    login = LoginSession(id=uuid4(), user_id=user.id, expires_at=datetime.now(UTC) + timedelta(hours=1))
    instance = IntegrationInstance(name="Browser", integration_type="browser", adapter_type="browser",
                                   execution_agent_id=agent.id, owner_id=user.id)
    objects = {(type(obj), obj.id): obj for obj in (user, agent, session, login)}
    documents = {(IntegrationSettings, None): IntegrationSettings(),
                 (UserIntegrationSettings, user.id): UserIntegrationSettings(instances=[instance])}
    profile = AgentProfile(name="Test", instructions="Test profile")
    coordinator = execution.BrowserCoordinator()
    case = SimpleNamespace(user=user, agent=agent, session=session, login=login, instance=instance,
                           objects=objects, documents=documents, profile=profile, coordinator=coordinator,
                           calls=[], reply=None, generation=1, operations=SUPPORTED_OPERATIONS,
                           setup_id=uuid4(), file_id=str(uuid4()), file_size=0, file_offset=0,
                            manual=True, clipboard="")
    case.navigation = {
        "tabs": [{"id": str(uuid4()), "url": "about:blank", "title": "", "loading": False}],
        "activeTabId": None, "canGoBack": False, "canGoForward": False,
        "loading": False, "nativeChrome": False,
    }
    case.navigation["activeTabId"] = case.navigation["tabs"][0]["id"]

    class Db:
        async def get(self, model, key):
            return objects.get((model, key))

        async def scalar(self, statement):
            params = statement.compile().params
            if (params.get("id_1") == agent.id and params.get("user_id_1") == agent.user_id
                    and agent.revoked_at is None):
                return agent.id
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            pass

    async def load(_db, model, *, user_id=None, **_kwargs):
        return documents.get((model, user_id), model()).model_copy(deep=True)

    async def request(agent_id, operation, arguments, **kwargs):
        assert agent_id == agent.id
        assert kwargs["expected_generation"] == case.generation
        case.calls.append((operation, arguments))
        result = await case.reply(operation, arguments) if case.reply else None
        if result is None:
            if operation == "integration.browser.open":
                result = {"browserId": arguments["browserId"]}
            elif operation == "integration.browser.view":
                coordinator.relays[UUID(arguments["relayId"])].accepted = True
                result = {"ok": True}
            elif operation == "integration.browser.control":
                assert arguments["browserId"] == str(coordinator.connections[agent_id].browser_id)
                assert type(arguments["manual"]) is bool
                if arguments["manual"] and not case.manual:
                    for relay in list(coordinator.relays.values()):
                        relay.ended.set()
                    await asyncio.sleep(0)
                case.manual = arguments["manual"]
                result = {"ok": True}
            elif operation == "integration.browser.clipboard":
                if arguments["action"] == "write":
                    assert case.manual, "Clipboard write reached an agent locked against manual input"
                    case.clipboard = arguments["text"]
                else:
                    assert arguments["action"] in {"read", "copy"} and "text" not in arguments
                result = {"text": case.clipboard}
            elif operation == "integration.browser.resize":
                result = {"width": arguments["width"], "height": arguments["height"]}
            elif operation == "integration.browser.navigation":
                result = case.navigation
            elif operation == "integration.browser.status":
                result = {"state": "missing"}
            elif operation == "integration.browser.setup":
                result = {"state": "installing", "setupId": str(case.setup_id)}
            elif operation == "integration.browser.setupread":
                await asyncio.Event().wait()
            elif operation == "integration.browser.execute":
                result = ({"tabId": arguments["tabId"], "url": "https://page.test/",
                           "data": {"snapshotSha256": "a" * 64}}
                          if arguments["action"] == "inspect" else {})
            elif operation == "integration.browser.file":
                action = arguments["action"]
                if action in {"choose", "delete"}:
                    result = {"ok": True}
                else:
                    if action == "begin":
                        case.file_size, case.file_offset = arguments["size"], 0
                    elif action == "write":
                        case.file_offset += len(base64.b64decode(arguments["data"], validate=True))
                    result = {"fileId": case.file_id, "offset": case.file_offset,
                              "size": case.file_size, "complete": action == "finish"}
            elif operation in {"integration.browser.close", "integration.browser.setupwrite", "integration.browser.setupresize"}:
                result = {"ok": True}
            else:
                pytest.fail(f"Unexpected RPC: {operation}")
        return ResponseMessage(type="response", id=uuid4(), ok=True, result=result)

    monkeypatch.setattr(execution, "AsyncSessionFactory", Db)
    monkeypatch.setattr(SettingsStore, "load", load)
    monkeypatch.setattr(execution, "load_agent_profile", AsyncMock(return_value=profile))
    monkeypatch.setattr(execution.execution_agent_connections, "generation", lambda _id: case.generation)
    monkeypatch.setattr(execution.execution_agent_connections, "snapshot", lambda _id: case.operations)
    monkeypatch.setattr(execution.execution_agent_connections, "request", request)
    monkeypatch.setattr(routes, "browser_coordinator", coordinator)
    monkeypatch.setattr(routes, "_setup_viewers", {})
    monkeypatch.setattr(routes, "_setup_starts", {})
    monkeypatch.setattr(routes, "get_runtime_config", lambda: SimpleNamespace(cors_origin_list=[ORIGIN["origin"]]))
    monkeypatch.setattr(routes, "_browser_login", AsyncMock(return_value=_BrowserLogin(user, login.id, login.expires_at)))

    @asynccontextmanager
    async def lifespan(_app):
        yield
        await coordinator.shutdown()
        starts = list(routes._setup_starts.values())
        for task in starts:
            task.cancel()
        await asyncio.gather(*starts, return_exceptions=True)
        assert not routes._setup_starts

    app = FastAPI(lifespan=lifespan)
    register_error_handlers(app)
    app.include_router(routes.router)
    app.dependency_overrides[require_user] = lambda: user
    app.dependency_overrides[get_db_session] = Db
    case.app = app
    case.url = f"/browsers/{instance.id}?sessionId={session.id}"
    case.setup_url = f"/browsers/{instance.id}/setup"
    with TestClient(app) as client:
        case.client = client
        yield case


def view_url(env, state):
    return file_url(env, state, "view")


@contextmanager
def open_view(env, state):
    with env.client.websocket_connect(view_url(env, state), headers=ORIGIN) as socket:
        try:
            yield socket
        finally:
            # Complete the protocol close before TestClient cancels the ASGI task.
            socket.close()
            while socket.receive()["type"] != "websocket.close":
                pass


@pytest.fixture
def wire(env, monkeypatch):
    """Exercise real request/cancel frames while keeping agent replies and storage fake."""
    manager = ExecutionAgentConnectionManager()
    request = execution.execution_agent_connections.request
    sent, responders = [], []

    async def send_text(raw):
        message = json.loads(raw)
        sent.append(message)
        if message["type"] == "request":
            async def respond():
                response = await request(env.agent.id, message["operation"], message["arguments"], expected_generation=env.generation)
                response.id = UUID(message["id"])
                await manager.receive(env.agent.id, socket, response.model_dump_json())

            responders.append(asyncio.create_task(respond()))

    socket = SimpleNamespace(send_text=send_text)
    hello = HelloMessage(type="hello", protocol_version=1, agent_id=env.agent.id, agent_version="test",
                         computer_name="test", platform="linux", operations=list(SUPPORTED_OPERATIONS),
                         home_directory="/home/user")
    env.client.portal.call(manager.register, hello, socket)
    monkeypatch.setattr(execution.execution_agent_connections, "request", manager.request)
    yield sent

    async def cleanup():
        await env.coordinator.shutdown()
        await manager.unregister(env.agent.id, socket)
        for task in responders:
            task.cancel()
        await asyncio.gather(*responders, return_exceptions=True)

    env.client.portal.call(cleanup)


def other_session(env, title="Session B"):
    session = ChatSession(id=uuid4(), user_id=env.user.id, session_type="chat", title=title,
                          preferred_execution_agent_id=env.agent.id, control_center={"tabs": [{"app_id": "browser"}]})
    env.objects[ChatSession, session.id] = session
    return session


@pytest.mark.parametrize("invalid", [
    "origin", "missing-origin", "login", "expired", "login-owner", "inactive", "missing-session",
    "session-owner", "child", "automation", "agent-owner", "revoked", "binding", "tab", "tab-kind",
    "integration", "disabled", "global-disabled",
])
def test_websocket_access_denied_before_any_rpc(env, monkeypatch, invalid):
    headers, url = ORIGIN, env.url
    if invalid == "origin":
        headers = {"origin": "https://evil.test"}
    elif invalid == "missing-origin":
        headers = {}
    elif invalid == "login":
        monkeypatch.setattr(routes, "_browser_login", AsyncMock(return_value=None))
    elif invalid == "expired":
        env.login.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    elif invalid == "login-owner":
        env.login.user_id = uuid4()
    elif invalid == "inactive":
        env.user.is_active = False
    elif invalid == "missing-session":
        url = url.split("?")[0]
    elif invalid == "session-owner":
        env.session.user_id = uuid4()
    elif invalid == "child":
        env.session.parent_session_id = uuid4()
    elif invalid == "automation":
        env.session.session_type = "automation"
    elif invalid == "agent-owner":
        env.agent.user_id = uuid4()
    elif invalid == "revoked":
        env.agent.revoked_at = datetime.now(UTC)
    elif invalid == "binding":
        env.session.preferred_execution_agent_id = uuid4()
    elif invalid == "tab":
        env.session.control_center = {"tabs": []}
    elif invalid == "tab-kind":
        env.session.control_center = {"tabs": [{"app_id": "browser", "kind": "file"}]}
    elif invalid == "integration":
        env.documents[UserIntegrationSettings, env.user.id].instances.clear()
    elif invalid == "disabled":
        env.documents[UserIntegrationSettings, env.user.id].instances[0].enabled = False
    else:
        env.documents[IntegrationSettings, None].adapter_availability["browser"] = "disabled"
    with pytest.raises(WebSocketDisconnect) as error, env.client.websocket_connect(url, headers=headers):
        pytest.fail("Unauthorized WebSocket was accepted")
    if invalid in {"origin", "missing-origin", "login"}:
        assert error.value.code == (4401 if invalid == "login" else 4403)
    assert env.calls == []


@pytest.mark.parametrize("unavailable", ["offline", "capability"])
def test_unavailable_agent_never_receives_open(env, unavailable):
    env.operations = None if unavailable == "offline" else frozenset()
    with env.client.websocket_connect(env.url, headers=ORIGIN) as socket:
        event = socket.receive()
        assert event["type"] == "websocket.close" or json.loads(event["text"])["type"] == "error"
    assert env.calls == []
    assert not env.coordinator.connections


def test_exclusive_viewer_explicit_attachment_and_detached_resume(env):
    with env.client.websocket_connect(env.url, headers=ORIGIN) as control:
        state = control.receive_json()
        assert state["canAttach"] and not state["attached"]
        control.send_json({"type": "attach"})
        assert control.receive_json()["error"]["code"] == "browser.busy"
        with env.client.websocket_connect(env.url, headers=ORIGIN) as duplicate:
            offer = duplicate.receive_json()
            assert offer["status"] == "occupied" and offer["canTakeover"]
            assert offer["browserId"] == state["browserId"] and offer["viewId"] == state["viewId"]
        with env.client.websocket_connect(view_url(env, state), headers=ORIGIN) as viewer:
            control.send_json({"type": "attach"})
            assert control.receive_json()["attached"]
            connection = env.coordinator.connections[env.agent.id]
            assert env.coordinator.attached_for(env.session.id, env.agent.id) is connection
            viewer.close()
            assert not control.receive_json()["attached"]
            assert viewer.receive()["type"] == "websocket.close"
    with env.client.websocket_connect(env.url, headers=ORIGIN) as resumed:
        assert resumed.receive_json()["status"] == "takingOver"
        restored = resumed.receive_json()
        assert restored["browserId"] == state["browserId"] and not restored["attached"]
        assert restored["viewId"] != state["viewId"]
        assert env.coordinator.connections[env.agent.id] is not connection
        assert connection.closed
    assert sum(op == "integration.browser.open" for op, _ in env.calls) == 1
    assert not any(op == "integration.browser.setup" for op, _ in env.calls)


def test_detach_resets_view_before_unlock_and_reopens_same_browser_without_attachment(env):
    started, finish = asyncio.Event(), asyncio.Event()

    async def reply(operation, arguments):
        if operation == "integration.browser.control" and arguments["manual"]:
            assert env.coordinator.connections[env.agent.id].resetting_view
            started.set()
            await finish.wait()

    env.reply = reply
    with env.client.websocket_connect(env.url, headers=ORIGIN) as control:
        state = control.receive_json()
        with open_view(env, state):
            connection = env.coordinator.connections[env.agent.id]
            control.send_json({"type": "attach"})
            assert control.receive_json()["attached"] and not env.manual
            control.send_json({"type": "detach"})
            detached = control.receive_json()
            assert detached["type"] == "state" and not detached["attached"]
            assert detached["browserId"] == state["browserId"]
            assert control.receive_json() == {"type": "view.reset"}
            try:
                env.client.portal.call(asyncio.wait_for, started.wait(), 1)
                assert not env.manual and connection.resetting_view
                assert env.coordinator.attached_for(env.session.id, env.agent.id) is None
            finally:
                env.client.portal.call(finish.set)
            unlocked = control.receive_json()
            assert unlocked["type"] == "state" and not unlocked["attached"] and not unlocked["busy"]
            assert unlocked["browserId"] == state["browserId"] and env.manual
            assert not connection.resetting_view and connection.view_socket is None
        with open_view(env, state):
            assert connection.browser_id == UUID(state["browserId"])
            assert not connection.attached and env.manual
            assert env.coordinator.attached_for(env.session.id, env.agent.id) is None
            control.send_json({"type": "attach"})
            assert control.receive_json()["attached"] and not env.manual
        assert [args["manual"] for op, args in env.calls if op == "integration.browser.control"] == [False, True, False]
        assert sum(op == "integration.browser.open" for op, _ in env.calls) == 1
        assert sum(op == "integration.browser.view" for op, _ in env.calls) == 2


@pytest.mark.parametrize("gate", ["default", "explicit", "missing-profile"])
def test_profile_gate_allows_manual_view_but_never_attachment(env, monkeypatch, gate):
    if gate == "default":
        env.profile.integrations_enabled_by_default = False
    elif gate == "explicit":
        env.profile.integration_availability[str(env.instance.id)] = False
    else:
        monkeypatch.setattr(execution, "load_agent_profile", AsyncMock(side_effect=execution.AgentProfileUnavailable))
    with env.client.websocket_connect(env.url, headers=ORIGIN) as control:
        state = control.receive_json()
        assert not state["canAttach"] and state["attachReason"] == "tool.unavailable"
        with open_view(env, state):
            control.send_json({"type": "attach"})
            assert control.receive_json()["error"]["code"] == "tool.unavailable"
            assert env.coordinator.attached_for(env.session.id, env.agent.id) is None


@pytest.mark.parametrize("change", ["login", "expired", "tab", "binding", "disabled", "global-disabled", "generation", "profile"])
def test_live_access_revocation_closes_or_detaches_viewer(env, monkeypatch, change):
    monkeypatch.setattr(execution, "ACCESS_INTERVAL", 0.01)
    with env.client.websocket_connect(env.url, headers=ORIGIN) as control:
        state = control.receive_json()
        with open_view(env, state):
            control.send_json({"type": "attach"})
            assert control.receive_json()["attached"]
            if change == "login":
                env.objects.pop((LoginSession, env.login.id))
            elif change == "expired":
                env.login.expires_at = datetime.now(UTC) - timedelta(seconds=1)
            elif change == "tab":
                env.session.control_center = {"tabs": []}
            elif change == "binding":
                env.session.preferred_execution_agent_id = uuid4()
            elif change == "disabled":
                env.documents[UserIntegrationSettings, env.user.id].instances[0].enabled = False
            elif change == "global-disabled":
                env.documents[IntegrationSettings, None].adapter_availability["browser"] = "disabled"
            elif change == "generation":
                env.generation = 2
            else:
                env.profile.integration_availability[str(env.instance.id)] = False
            event = control.receive_json()
            assert event["type"] == ("state" if change == "profile" else "error")
            assert env.coordinator.attached_for(env.session.id, env.agent.id) is None


def test_detach_during_busy_action_is_immediate_and_does_not_cancel_rpc(env):
    started, finish = asyncio.Event(), asyncio.Event()
    cancelled = []

    async def reply(operation, arguments):
        if operation == "integration.browser.execute" and arguments["action"] == "navigate":
            started.set()
            try:
                await finish.wait()
            except asyncio.CancelledError:
                cancelled.append(operation)
                raise
            return {}

    env.reply = reply
    with env.client.websocket_connect(env.url, headers=ORIGIN) as control:
        state = control.receive_json()
        with open_view(env, state):
            control.send_json({"type": "attach"})
            control.receive_json()
            connection = env.coordinator.connections[env.agent.id]
            args = {"action": "navigate", "tabId": str(uuid4()), "url": "https://page.test/",
                    "expectedBrowserId": state["browserId"], "expectedAttachmentRevision": connection.attachment_revision}
            task = env.client.portal.start_task_soon(env.coordinator.execute, env.session.id, env.agent.id, env.instance.id, args)
            try:
                env.client.portal.call(asyncio.wait_for, started.wait(), 1)
                assert control.receive_json()["busy"]
                control.send_json({"type": "detach"})
                detached = control.receive_json()
                assert detached["busy"] and not detached["attached"]
                assert not task.done() and not cancelled
            finally:
                env.client.portal.call(finish.set)
            task.result(timeout=2)
            assert control.receive_json()["busy"]  # Manual input stays locked until viewer rotation finishes.
            assert control.receive_json() == {"type": "view.reset"}
            detached = control.receive_json()
            assert detached["type"] == "state" and not detached["busy"] and not detached["attached"]
            assert detached["browserId"] == state["browserId"] and env.manual


@pytest.mark.parametrize("action", ["click", "inspect"])
def test_detach_during_locked_snapshot_prevents_approved_action_dispatch(env, action):
    started, finish = asyncio.Event(), asyncio.Event()
    with env.client.websocket_connect(env.url, headers=ORIGIN) as control:
        state = control.receive_json()
        with open_view(env, state):
            control.send_json({"type": "attach"})
            control.receive_json()
            connection = env.coordinator.connections[env.agent.id]
            tab_id = str(uuid4())
            snapshot = env.client.portal.call(env.coordinator.snapshot, env.session.id, env.agent.id, env.instance.id, tab_id)

            async def reply(operation, arguments):
                if operation == "integration.browser.execute" and arguments["action"] == "inspect":
                    started.set()
                    await finish.wait()

            env.reply = reply
            args = {"action": action, "tabId": tab_id, **({"selector": "button"} if action == "click" else {}), "expectedSnapshot": snapshot,
                    "expectedBrowserId": state["browserId"], "expectedAttachmentRevision": connection.attachment_revision}
            task = env.client.portal.start_task_soon(env.coordinator.execute, env.session.id, env.agent.id, env.instance.id, args)
            try:
                env.client.portal.call(asyncio.wait_for, started.wait(), 1)
                assert control.receive_json()["busy"]
                control.send_json({"type": "detach"})
                assert not control.receive_json()["attached"]
            finally:
                env.client.portal.call(finish.set)
            with pytest.raises(ExecutionClientError, match="browser.bindingChanged"):
                task.result(timeout=2)
            assert control.receive_json()["busy"]
            assert control.receive_json() == {"type": "view.reset"}
            detached = control.receive_json()
            assert detached["type"] == "state" and not detached["busy"] and not detached["attached"]
            assert detached["browserId"] == state["browserId"] and env.manual
            assert not any(arguments.get("action") == "click" for _, arguments in env.calls)


@pytest.mark.parametrize("action", ["click", "inspect"])
@pytest.mark.parametrize("change", ["digest", "url", "tab", "missing", "revision"])
def test_stale_snapshot_after_approval_never_dispatches_action(env, change, action, caplog):
    caplog.set_level("INFO", logger=execution.__name__)
    with env.client.websocket_connect(env.url, headers=ORIGIN) as control:
        state = control.receive_json()
        with open_view(env, state):
            control.send_json({"type": "attach"})
            control.receive_json()
            connection = env.coordinator.connections[env.agent.id]
            tab_id = str(uuid4())
            snapshot = env.client.portal.call(env.coordinator.snapshot, env.session.id, env.agent.id, env.instance.id, tab_id)
            args = {"action": action, "tabId": tab_id, **({"selector": "button"} if action == "click" else {}), "expectedSnapshot": snapshot,
                    "expectedBrowserId": state["browserId"], "expectedAttachmentRevision": connection.attachment_revision}

            async def reply(operation, arguments):
                if operation == "integration.browser.execute" and arguments["action"] == "inspect":
                    return {"tabId": str(uuid4()) if change == "tab" else tab_id,
                            "url": "https://changed.test/" if change == "url" else "https://page.test/",
                            "data": {} if change == "missing" else {"snapshotSha256": ("b" if change == "digest" else "a") * 64}}

            env.reply = reply
            if change == "revision":
                connection.attachment_revision += 2
            code = "browser.snapshotChanged" if change in {"digest", "url"} else "browser.bindingChanged"
            with pytest.raises(ExecutionClientError, match=code):
                env.client.portal.call(env.coordinator.execute, env.session.id, env.agent.id, env.instance.id, args)
            assert not connection.busy
            assert not any(args.get("action") == "click" for _, args in env.calls)
            record = [item for item in caplog.records if item.name == execution.__name__][-1]
            assert record.args[0] == "execute" and record.args[2] == "error"
            timings = record.args[-1]
            assert "total_ms" in timings and "action_rpc_ms" not in timings
            assert ("reinspection_rpc_ms" in timings) == (change != "revision")


@pytest.mark.parametrize("action", ["tabs", "inspect", "screenshot", "diagnostics"])
def test_browser_auto_read_from_registry_through_coordinator(env, monkeypatch, action):
    env.instance._browser_attached = True
    integrations = IntegrationSettings(instances=[env.instance])
    monkeypatch.setattr(execution, "browser_coordinator", env.coordinator)
    monkeypatch.setattr(ToolContext, "current_integrations", AsyncMock(return_value=integrations))
    monkeypatch.setattr(tools_module, "_profile_tool_settings", AsyncMock(return_value=ToolSettings()))
    output = BytesIO()
    Image.new("RGB", (2, 2), "white").save(output, format="PNG")
    image = output.getvalue()

    async def reply(operation, arguments):
        if operation == "integration.browser.execute":
            assert "observe" not in arguments
            assert "snapshotSha256" not in arguments
            if arguments["action"] == "inspect":
                return {"tabId": arguments["tabId"], "text": "- document",
                        "data": {"snapshotSha256": "a" * 64}}
            if arguments["action"] == "screenshot":
                return {"screenshot": {"fileId": env.file_id, "size": len(image)}}
            return {}
        if operation == "integration.browser.file" and arguments["action"] == "read":
            return {"fileId": env.file_id, "size": len(image), "offset": len(image),
                    "complete": True, "data": base64.b64encode(image).decode()}

    env.reply = reply
    with env.client.websocket_connect(env.url, headers=ORIGIN) as control:
        state = control.receive_json()
        with open_view(env, state):
            control.send_json({"type": "attach"})
            assert control.receive_json()["attached"]
            connection = env.coordinator.connections[env.agent.id]
            name = {"tabs": "browser_tabs", "inspect": "browser_snapshot", "screenshot": "browser_take_screenshot", "diagnostics": "browser_diagnostics"}[action]
            arguments = {"tabId": env.navigation["activeTabId"]} if action in {"inspect", "screenshot"} else {"action": "list"} if action == "tabs" else {}
            call = ToolCall("browser-read", name, arguments)
            context = ToolContext(
                db=AsyncMock(), run_id=uuid4(), session=env.session, tool_call=call,
                root_files=Mock(), integration_settings=integrations, execution_client=connection.client,
                ask_handler=AsyncMock(return_value=PermissionDecision.ALLOW),
            )
            context.db.get.return_value = SimpleNamespace(
                execution_agent_id=env.agent.id, execution_operations=["integration.browser.execute"],
            )
            registry = tools_module.ToolRegistry([]).effective(integrations)
            result = env.client.portal.call(registry.execute, call, context)
            assert not result.is_error, result.content
            context.ask_handler.assert_awaited_once()
            assert context.ask_handler.await_args.args[0].capability == "browser.read"
            calls = [args["action"] for op, args in env.calls if op == "integration.browser.execute"]
            assert calls == [action]
            inspections = [args for op, args in env.calls if op == "integration.browser.execute" and args["action"] == "inspect"]
            assert [args["settle"] for args in inspections] == ([True] if inspections else [])
            assert all(args["guard"] is False for args in inspections)
            if action == "screenshot":
                assert result.attachments[0].ephemeral and result.attachments[0].data == image


@pytest.fixture
def browser_tool(env, monkeypatch, wire):
    env.instance._browser_attached = True
    integrations = IntegrationSettings(instances=[env.instance])
    monkeypatch.setattr(execution, "browser_coordinator", env.coordinator)
    monkeypatch.setattr(ToolContext, "current_integrations", AsyncMock(return_value=integrations))
    monkeypatch.setattr(tools_module, "_profile_tool_settings", AsyncMock(return_value=ToolSettings()))

    async def reply(operation, arguments):
        if operation != "integration.browser.execute":
            return None
        if arguments["action"] == "inspect":
            return {"tabId": arguments["tabId"], "url": "https://page.test/", "text": "- document",
                    "data": {"snapshotSha256": "a" * 64}}
        assert arguments["action"] == "fill_form"
        return {"batch": {"completed": len(arguments["fields"]), "total": len(arguments["fields"])},
                **({"url": "https://page.test/", "text": "- document after fill",
                    "data": {"snapshotSha256": "b" * 64}} if arguments["observe"] else {})}

    env.reply = reply
    with env.client.websocket_connect(env.url, headers=ORIGIN) as control:
        state = control.receive_json()
        with open_view(env, state):
            control.send_json({"type": "attach"})
            assert control.receive_json()["attached"]
            connection = env.coordinator.connections[env.agent.id]
            fields = [
                {"name": "Name", "ref": "e1", "type": "textbox", "value": "Ada"},
                {"name": "Active", "ref": "e2", "type": "checkbox", "value": "false"},
                {"name": "Choice", "ref": "f1e3", "type": "radio", "value": "true"},
                {"name": "Country", "ref": "e4", "type": "combobox", "value": "de"},
            ]
            call = ToolCall("browser-form", "browser_fill_form", {
                "tabId": env.navigation["activeTabId"], "fields": fields,
            })
            context = ToolContext(
                db=AsyncMock(), run_id=uuid4(), session=env.session, tool_call=call,
                root_files=Mock(), integration_settings=integrations, execution_client=connection.client,
                ask_handler=AsyncMock(return_value=PermissionDecision.ALLOW),
            )
            context.db.get.return_value = SimpleNamespace(
                execution_agent_id=env.agent.id, execution_operations=["integration.browser.execute"],
            )
            yield SimpleNamespace(
                env=env, call=call, context=context, connection=connection, frames=wire,
                registry=tools_module.ToolRegistry([]).effective(integrations),
            )


@pytest.mark.parametrize("observe", [True, False])
@pytest.mark.parametrize("error_code", [None, "browser.snapshotChanged", "browser.outcomeUnknown"])
@pytest.mark.parametrize("observation_failed", [False, True])
def test_fill_form_permission_wire_and_partial_prefix(browser_tool, observe, error_code, observation_failed):
    case = browser_tool
    case.call.arguments["observe"] = observe
    reply = case.env.reply
    lines = [
        "await page.locator('aria-ref=e1').fill('Ada', timeout=1000)",
        "await page.locator('aria-ref=e2').set_checked(False, timeout=1000)",
        "await page.locator('aria-ref=f1e3').set_checked(True, timeout=1000)",
        "await page.locator('aria-ref=e4').select_option(label='de', timeout=1000)",
    ]
    feedback = {
        "completed": "\n".join(lines[:1] if error_code else lines), "truncated": False,
        **({"errorCode": error_code} if error_code else {}),
        **({"attempted": lines[1]} if error_code == "browser.outcomeUnknown" else {}),
    }

    async def result_reply(operation, arguments):
        result = await reply(operation, arguments)
        if operation == "integration.browser.execute" and arguments["action"] == "fill_form":
            result["playwrightCode"] = feedback
            if error_code:
                result["batch"].update(completed=1, errorCode=error_code)
            if observe and observation_failed:
                result.pop("text")
                result["data"] = {"observationFailed": True}
        return result

    case.env.reply = result_reply
    result = case.env.client.portal.call(case.registry.execute, case.call, case.context)
    assert result.is_error == (error_code is not None), result.content
    payload = json.loads(result.content)
    assert payload["playwrightCode"] == feedback
    assert payload["batch"] == {
        "completed": 1 if error_code else 4, "total": 4,
        **({"errorCode": error_code} if error_code else {}),
    }
    assert result.metadata == {"privateIntegrationData": True, **({"errorCode": error_code} if error_code else {})}
    if observe:
        assert payload["data"] == ({"observationFailed": True} if observation_failed else {
            "snapshotSha256": hashlib.sha256(f"https://page.test/\n{'b' * 64}".encode()).hexdigest(),
        })
    else:
        assert "data" not in payload and "text" not in payload
    requests = [call.args[0] for call in case.context.ask_handler.await_args_list]
    assert [request.capability for request in requests] == ["browser.write", *(["browser.read"] if observe else [])]
    for request in requests:
        arguments = case.registry.tools[case.call.name].definition.validate_arguments(case.call.arguments).to_action().model_dump(mode="json")
        operation = {
            **arguments, "browserId": str(case.connection.browser_id),
            "attachmentRevision": case.connection.attachment_revision,
            "capability": request.capability,
        }
        key = json.loads(request.resource_scope)["operationKey"]
        assert key == hashlib.sha256(json.dumps(operation, sort_keys=True).encode()).hexdigest()
        assert request.approval_details["browser"]["fields"] == case.call.arguments["fields"]
        for fields in (list(reversed(operation["fields"])), [{**operation["fields"][0], "value": "Changed"}, *operation["fields"][1:]]):
            assert key != hashlib.sha256(json.dumps({**operation, "fields": fields}, sort_keys=True).encode()).hexdigest()
    calls = [frame["arguments"] for frame in case.frames if frame.get("operation") == "integration.browser.execute"]
    assert [call["action"] for call in calls] == ["fill_form"]
    assert [call["settle"] for call in calls] == [False]
    assert all("snapshotSha256" not in call for call in calls)
    assert calls[-1] == {
        **case.registry.tools[case.call.name].definition.validate_arguments(case.call.arguments).to_action().model_dump(mode="json", exclude={"snapshot_sha256", "code", "function", "slowly", "submit"}),
        "browserId": str(case.connection.browser_id), "allowNetwork": True, "settle": False, "guard": False,
    }


@pytest.mark.parametrize("change,code", [
    ("source", "tool.invalidArguments"), ("digest", "browser.snapshotChanged"),
    ("url", "browser.snapshotChanged"), ("revision", "browser.bindingChanged"),
    ("binding", "browser.bindingChanged"), ("deny-write", "permission.denied"),
    ("deny-read", "permission.denied"),
])
def test_fill_form_stale_or_denied_never_dispatches(browser_tool, change, code):
    case = browser_tool
    reply = case.env.reply
    if change == "source":
        case.call.arguments["snapshotSha256"] = "0" * 64

    async def approve(request):
        from mato_loop_backend.integrations.browser import capture_browser_approval

        await capture_browser_approval(
            request, case.call, case.context.session,
            SimpleNamespace(execution_agent_id=case.env.agent.id), case.context.approval_resolution,
        )
        if change == "revision":
            case.connection.attachment_revision += 1
        elif change == "binding":
            case.connection.browser_id = uuid4()
        if change == f"deny-{request.capability.split('.')[-1]}":
            return PermissionDecision.USER_DENIED
        return PermissionDecision.ALLOW

    async def changed_reply(operation, arguments):
        result = await reply(operation, arguments)
        if operation == "integration.browser.execute" and arguments["action"] == "inspect" and not arguments["settle"]:
            if change == "digest":
                result["data"]["snapshotSha256"] = "b" * 64
            elif change == "url":
                result["url"] = "https://changed.test/"
        return result

    case.context.ask_handler.side_effect = approve
    case.env.reply = changed_reply
    result = case.env.client.portal.call(case.registry.execute, case.call, case.context)
    assert result.is_error and result.metadata["errorCode"] == code
    calls = [frame["arguments"] for frame in case.frames if frame.get("operation") == "integration.browser.execute"]
    assert all(call["action"] == "inspect" for call in calls)
    assert len(calls) == (0 if change == "source" else 2 if change in {"digest", "url"} else 1)
    assert all(call["guard"] is True for call in calls)
    if change == "source":
        case.context.ask_handler.assert_not_awaited()


@pytest.mark.parametrize("action,batch", [
    ("fill_form", None), ("fill_form", {"completed": 1, "total": 1}),
    ("tabs", {"completed": 1, "total": 1}), ("inspect", {"completed": 1, "total": 1}),
])
def test_browser_wire_rejects_batch_for_wrong_action_or_total(browser_tool, action, batch):
    case = browser_tool
    if action != "fill_form":
        case.call.name = "browser_snapshot" if action == "inspect" else "browser_tabs"
        case.call.arguments = {"tabId": case.call.arguments["tabId"]} if action == "inspect" else {"action": "list"}
    reply = case.env.reply

    async def invalid_reply(operation, arguments):
        if operation == "integration.browser.execute" and arguments["action"] == action:
            return {"batch": batch} if action != "inspect" else {
                **await reply(operation, arguments), "batch": batch,
            }
        return await reply(operation, arguments)

    case.env.reply = invalid_reply
    result = case.env.client.portal.call(case.registry.execute, case.call, case.context)
    assert result.is_error and result.metadata["errorCode"] == "executionAgent.protocolError"


@pytest.mark.parametrize("name,action,arguments", [
    ("browser_run_code", "run_code", {"code": "async def run(page):\n    return await page.title()"}),
    ("browser_evaluate", "evaluate", {"function": "element => element.textContent", "ref": "e1"}),
    ("browser_type", "fill", {"ref": "e1", "text": "Ada", "slowly": True, "submit": True}),
    ("browser_tabs", "select", {"action": "select"}),
])
def test_slim_browser_tools_convert_to_one_unguarded_rpc(browser_tool, name, action, arguments):
    case = browser_tool
    case.call.name = name
    case.call.arguments = {"tabId": case.env.navigation["activeTabId"], "observe": False, **arguments}
    script = action in {"run_code", "evaluate"}

    async def reply(operation, wire):
        if operation == "integration.browser.execute":
            assert wire["action"] == action
            assert wire["guard"] is False and wire["settle"] is False
            assert "snapshotSha256" not in wire and "ref" not in wire
            assert ("code" in wire) == (action == "run_code")
            assert ("function" in wire) == (action == "evaluate")
            assert ("slowly" in wire) == (action == "fill")
            assert ("submit" in wire) == (action == "fill")
            if "ref" in arguments:
                assert wire["selector"] == "aria-ref=e1"
            if action == "fill":
                assert wire["slowly"] is True and wire["submit"] is True
            return {"script": {"value": {"changed": True}, "output": "bounded",
                               "errorCode": "browser.actionFailed", "failureReason": "runtimeError",
                               "line": 2}} if script else {}

    case.env.reply = reply
    result = case.env.client.portal.call(case.registry.execute, case.call, case.context)
    assert result.is_error == script, result.content
    if script:
        assert json.loads(result.content)["script"]["value"] == {"changed": True}
        assert result.metadata == {"errorCode": "browser.actionFailed", "privateIntegrationData": True}
    calls = [frame["arguments"] for frame in case.frames if frame.get("operation") == "integration.browser.execute"]
    assert len(calls) == 1


def test_auto_inspection_still_checks_returned_tab_binding(browser_tool):
    case = browser_tool
    case.call.name = "browser_snapshot"
    case.call.arguments = {"tabId": case.env.navigation["activeTabId"]}

    async def reply(operation, _arguments):
        if operation == "integration.browser.execute":
            return {"tabId": str(uuid4()), "text": "wrong page", "data": {"snapshotSha256": "a" * 64}}

    case.env.reply = reply
    result = case.env.client.portal.call(case.registry.execute, case.call, case.context)
    assert result.metadata["errorCode"] == "browser.bindingChanged"
    assert "wrong page" not in result.content
    calls = [frame["arguments"] for frame in case.frames if frame.get("operation") == "integration.browser.execute"]
    assert len(calls) == 1 and calls[0]["guard"] is False


@pytest.mark.parametrize("action", ["inspect", "click"])
def test_browser_inspection_reuse_and_latency_phases(env, monkeypatch, caplog, action):
    caplog.set_level("INFO", logger=execution.__name__)
    clock, inspections = [0.0], []
    monkeypatch.setattr(execution, "monotonic", lambda: clock[0])
    check_access = execution.BrowserConnection.check_access

    async def access(connection):
        await check_access(connection)
        clock[0] += 0.2

    monkeypatch.setattr(execution.BrowserConnection, "check_access", access)

    async def reply(operation, arguments):
        if operation != "integration.browser.execute":
            return None
        if arguments["action"] == "inspect":
            clock[0] += 0.4
            result = {"tabId": arguments["tabId"], "url": "https://private.test/",
                      "text": f"private-inspection-{len(inspections) + 1}",
                      "tabs": [{"id": arguments["tabId"], "url": "https://private.test/", "title": "private title"}],
                      "data": {"snapshotSha256": "a" * 64}}
            inspections.append(result)
            return result
        assert arguments["action"] == "click"
        clock[0] += 0.5
        return {}

    env.reply = reply
    with env.client.websocket_connect(env.url, headers=ORIGIN) as control:
        state = control.receive_json()
        with open_view(env, state):
            control.send_json({"type": "attach"})
            assert control.receive_json()["attached"]
            connection = env.coordinator.connections[env.agent.id]
            tab_id = str(uuid4())
            snapshot = env.client.portal.call(env.coordinator.snapshot, env.session.id, env.agent.id, env.instance.id, tab_id)
            args = {"action": action, "tabId": tab_id, "expectedSnapshot": snapshot,
                    **({"selector": "#private-selector"} if action == "click" else {}),
                    "expectedBrowserId": state["browserId"], "expectedAttachmentRevision": connection.attachment_revision}

            async def execute_after_lock_wait():
                await connection.action_lock.acquire()
                task = asyncio.create_task(env.coordinator.execute(env.session.id, env.agent.id, env.instance.id, args))
                try:
                    await asyncio.sleep(0)
                    assert not task.done()
                    clock[0] += 1
                finally:
                    connection.action_lock.release()
                return await task

            result = env.client.portal.call(execute_after_lock_wait)
            assert control.receive_json()["busy"]
            assert not control.receive_json()["busy"]
            assert len(inspections) == 2
            calls = [args["action"] for op, args in env.calls if op == "integration.browser.execute"]
            assert calls == (["inspect", "inspect"] if action == "inspect" else ["inspect", "inspect", "click"])
            if action == "inspect":
                expected = execution.BrowserActionResult.model_validate(inspections[-1])
                assert result == expected.model_copy(update={"data": {"snapshotSha256": snapshot}})
                assert result.text == "private-inspection-2"
            assert not connection.busy
            records = [item for item in caplog.records if item.name == execution.__name__]
            assert len(records) == 2
            authorization, executed = records
            assert authorization.args[:3] == ("snapshot", env.session.id, "ok")
            assert executed.args[:3] == ("execute", env.session.id, "ok")
            assert authorization.args[-1]["total_ms"] == pytest.approx(600)
            timings = executed.args[-1]
            assert timings["lock_wait_ms"] == pytest.approx(1000)
            assert timings["access_ms"] == pytest.approx(200)
            assert timings["reinspection_rpc_ms"] == pytest.approx(400)
            assert timings["total_ms"] == pytest.approx(1600 if action == "inspect" else 2100)
            if action == "inspect":
                assert "action_rpc_ms" not in timings
            else:
                assert timings["action_rpc_ms"] == pytest.approx(500)
            assert "private" not in caplog.text and snapshot not in caplog.text


@pytest.mark.parametrize("failure", [None, "missing", "id", "size", "empty", "offset", "complete", "detach", "detach-delete", "detach-state"])
def test_screenshot_chunks_are_private_bounded_and_deleted(env, failure, monkeypatch):
    image = b"temporary screenshot bytes" * 30000
    screenshot_id = str(uuid4())
    with env.client.websocket_connect(env.url, headers=ORIGIN) as control:
        state = control.receive_json()
        with open_view(env, state):
            control.send_json({"type": "attach"})
            control.receive_json()
            connection = env.coordinator.connections[env.agent.id]
            tab_id = str(uuid4())
            snapshot = env.client.portal.call(env.coordinator.snapshot, env.session.id, env.agent.id, env.instance.id, tab_id)
            send_state = execution.BrowserConnection.state

            async def changed_state(current):
                if failure == "detach-state" and current is connection and not current.busy:
                    current.detach()
                await send_state(current)

            monkeypatch.setattr(execution.BrowserConnection, "state", changed_state)

            async def reply(operation, arguments):
                if operation == "integration.browser.execute" and arguments["action"] == "screenshot":
                    assert "snapshotSha256" not in arguments and "observe" not in arguments
                    return {} if failure == "missing" else {
                        "tabId": tab_id, "screenshot": {"fileId": screenshot_id, "size": len(image)},
                    }
                if operation == "integration.browser.file" and arguments["action"] == "read":
                    assert connection.busy and arguments["fileId"] == screenshot_id
                    offset = arguments["offset"]
                    data = image[offset:offset + arguments["maxBytes"]]
                    assert len(data) <= 65536
                    if failure == "detach":
                        connection.detach()
                    return {
                        "fileId": str(uuid4()) if failure == "id" else screenshot_id,
                        "size": len(image) + (failure == "size"),
                        "offset": offset + len(data) + (failure == "offset"),
                        "complete": True if failure == "complete" else offset + len(data) == len(image),
                        "data": base64.b64encode(b"" if failure == "empty" else data).decode(),
                    }
                if operation == "integration.browser.file" and arguments["action"] == "delete" and failure == "detach-delete":
                    connection.detach()

            env.reply = reply
            args = {"action": "screenshot", "tabId": tab_id, "expectedSnapshot": snapshot,
                    "expectedBrowserId": state["browserId"], "expectedAttachmentRevision": connection.attachment_revision}
            if failure is None:
                result = env.client.portal.call(env.coordinator.execute, env.session.id, env.agent.id, env.instance.id, args)
                assert len(result._screenshot_bytes) > 500000
                assert result._screenshot_bytes == image
                assert "temporary screenshot bytes" not in result.model_dump_json()
                assert len(result.model_dump_json()) < 1000
            else:
                code = "browser.bindingChanged" if failure in {"detach", "detach-delete", "detach-state"} else "executionAgent.protocolError"
                with pytest.raises(ExecutionClientError, match=code):
                    env.client.portal.call(env.coordinator.execute, env.session.id, env.agent.id, env.instance.id, args)
            deletions = [args for op, args in env.calls if op == "integration.browser.file" and args["action"] == "delete"]
            assert [args["fileId"] for args in deletions] == ([] if failure == "missing" else [screenshot_id])
            assert not connection.busy


@pytest.mark.parametrize("phase", ["capture", "read"])
def test_cancelled_screenshot_drains_capture_and_deletes_staging(env, phase):
    started, finish = asyncio.Event(), asyncio.Event()
    screenshot_id = str(uuid4())
    with env.client.websocket_connect(env.url, headers=ORIGIN) as control:
        state = control.receive_json()
        with open_view(env, state):
            control.send_json({"type": "attach"})
            control.receive_json()
            connection = env.coordinator.connections[env.agent.id]
            tab_id = str(uuid4())
            snapshot = env.client.portal.call(env.coordinator.snapshot, env.session.id, env.agent.id, env.instance.id, tab_id)

            async def reply(operation, arguments):
                capture = operation == "integration.browser.execute" and arguments["action"] == "screenshot"
                read = operation == "integration.browser.file" and arguments["action"] == "read"
                if capture and phase == "capture" or read and phase == "read":
                    started.set()
                    await finish.wait()
                if capture:
                    return {"screenshot": {"fileId": screenshot_id, "size": 3}}
                if read:
                    return {"fileId": screenshot_id, "size": 3, "offset": 3, "complete": True, "data": "YWJj"}

            env.reply = reply
            args = {"action": "screenshot", "tabId": tab_id, "expectedSnapshot": snapshot,
                    "expectedBrowserId": state["browserId"], "expectedAttachmentRevision": connection.attachment_revision}

            async def cancel():
                task = asyncio.create_task(env.coordinator.execute(env.session.id, env.agent.id, env.instance.id, args))
                try:
                    await asyncio.wait_for(started.wait(), 2)
                    task.cancel()
                    await asyncio.sleep(0)
                    assert not task.done() and connection.busy
                finally:
                    finish.set()
                    with pytest.raises(asyncio.CancelledError):
                        await task

            env.client.portal.call(cancel)
            deletions = [args for op, args in env.calls if op == "integration.browser.file" and args["action"] == "delete"]
            assert [args["fileId"] for args in deletions] == [screenshot_id]
            assert not connection.busy


@pytest.mark.parametrize("digest", [None, 123, "not-a-digest"])
def test_malformed_snapshot_is_rejected_before_becoming_approval_binding(env, digest):
    async def reply(operation, arguments):
        if operation == "integration.browser.execute":
            return {"tabId": arguments["tabId"], "url": "https://page.test/", "data": {"snapshotSha256": digest}}

    env.reply = reply
    with env.client.websocket_connect(env.url, headers=ORIGIN) as control:
        state = control.receive_json()
        with open_view(env, state):
            control.send_json({"type": "attach"})
            control.receive_json()
            with pytest.raises(ExecutionClientError, match="executionAgent.protocolError"):
                env.client.portal.call(env.coordinator.snapshot, env.session.id, env.agent.id, env.instance.id, str(uuid4()))


@pytest.mark.parametrize("phase", ["lock", "reinspection"])
def test_cancelled_inspect_never_returns_content_and_records_partial_latency(env, caplog, phase):
    caplog.set_level("INFO", logger=execution.__name__)
    started = asyncio.Event()
    with env.client.websocket_connect(env.url, headers=ORIGIN) as control:
        state = control.receive_json()
        with open_view(env, state):
            control.send_json({"type": "attach"})
            assert control.receive_json()["attached"]
            connection = env.coordinator.connections[env.agent.id]
            tab_id = str(uuid4())
            snapshot = env.client.portal.call(env.coordinator.snapshot, env.session.id, env.agent.id, env.instance.id, tab_id)

            async def reply(operation, arguments):
                if operation == "integration.browser.execute":
                    assert arguments["action"] == "inspect"
                    started.set()
                    await asyncio.Event().wait()

            env.reply = reply
            args = {"action": "inspect", "tabId": tab_id, "expectedSnapshot": snapshot,
                    "expectedBrowserId": state["browserId"], "expectedAttachmentRevision": connection.attachment_revision}

            async def cancel():
                if phase == "lock":
                    await connection.action_lock.acquire()
                task = asyncio.create_task(env.coordinator.execute(env.session.id, env.agent.id, env.instance.id, args))
                try:
                    if phase == "lock":
                        await asyncio.sleep(0)
                    else:
                        await asyncio.wait_for(started.wait(), 1)
                finally:
                    task.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await task
                    if phase == "lock":
                        connection.action_lock.release()

            env.client.portal.call(cancel)
            assert not connection.busy and not connection.action_lock.locked()
            assert len([op for op, _ in env.calls if op == "integration.browser.execute"]) == (1 if phase == "lock" else 2)
            record = [item for item in caplog.records if item.name == execution.__name__][-1]
            assert record.args[:3] == ("execute", env.session.id, "cancelled")
            timings = record.args[-1]
            assert "total_ms" in timings and "lock_wait_ms" in timings and "action_rpc_ms" not in timings
            assert ("reinspection_rpc_ms" in timings) == (phase == "reinspection")


def test_setup_only_explicit_start_and_viewer_close_does_not_cancel_installer(env):
    cancelled = []

    async def reply(operation, _arguments):
        if operation == "integration.browser.setupread":
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.append(operation)
                raise

    env.reply = reply
    env.documents[UserIntegrationSettings, env.user.id].instances[0].enabled = False
    with env.client.websocket_connect(env.setup_url, headers=ORIGIN) as setup:
        env.client.portal.call(asyncio.sleep, 0)
        assert not env.calls
        setup.send_json({"type": "status"})
        assert setup.receive_json()["state"] == "missing"
        assert [op for op, _ in env.calls] == ["integration.browser.status"]
        with pytest.raises(WebSocketDisconnect), env.client.websocket_connect(env.setup_url, headers=ORIGIN):
            pytest.fail("A second setup viewer was accepted")
        setup.send_json({"type": "start"})
        assert setup.receive_json()["state"] == "installing"
        setup.send_json({"type": "input", "data": base64.b64encode(b"y\r").decode()})
        setup.send_json({"type": "resize", "rows": 30, "columns": 100})
        setup.send_json({"type": "status"})
        setup.receive_json()
        setup.close()
        assert setup.receive()["type"] == "websocket.close"
    assert not routes._setup_viewers
    assert cancelled == ["integration.browser.setupread"]
    assert [args for op, args in env.calls if op == "integration.browser.setup"] == [{"ownerId": str(env.user.id)}]
    assert any(op == "integration.browser.setupwrite" and args["setupId"] == str(env.setup_id) for op, args in env.calls)
    assert not any("cancel" in op or op == "integration.browser.close" for op, _ in env.calls)


def test_closing_viewer_during_setup_start_never_sends_wire_cancel(env, monkeypatch):
    manager = ExecutionAgentConnectionManager()
    sent, started = [], asyncio.Event()

    async def send_text(raw):
        message = json.loads(raw)
        sent.append(message)
        if message.get("operation") == "integration.browser.setup":
            started.set()

    agent_socket = SimpleNamespace(send_text=send_text)
    hello = HelloMessage(type="hello", protocol_version=1, agent_id=env.agent.id, agent_version="test",
                         computer_name="test", platform="linux", operations=list(SUPPORTED_OPERATIONS),
                         home_directory="/home/user")
    env.client.portal.call(manager.register, hello, agent_socket)
    monkeypatch.setattr(execution.execution_agent_connections, "request", manager.request)
    try:
        with env.client.websocket_connect(env.setup_url, headers=ORIGIN) as setup:
            setup.send_json({"type": "start"})
            env.client.portal.call(asyncio.wait_for, started.wait(), 1)
        request_id = next(item["id"] for item in sent if item.get("operation") == "integration.browser.setup")
        assert not any(item["type"] == "cancel" and item["id"] == request_id for item in sent)
        task = routes._setup_starts[env.agent.id]
        assert not task.done()

        async def complete_start():
            response = ResponseMessage(type="response", id=UUID(request_id), ok=True,
                                       result={"state": "installing", "setupId": str(env.setup_id)})
            await manager.receive(env.agent.id, agent_socket, response.model_dump_json())
            await asyncio.wait_for(asyncio.shield(task), 1)
            await asyncio.sleep(0)
            assert not routes._setup_starts

        env.client.portal.call(complete_start)
    finally:
        env.client.portal.call(manager.unregister, env.agent.id, agent_socket)


@pytest.mark.parametrize("channel", ["setup", "view"])
@pytest.mark.parametrize("invalid", ["origin", "login"])
def test_other_websocket_channels_require_origin_and_login(env, monkeypatch, channel, invalid):
    headers = {"origin": "https://evil.test"} if invalid == "origin" else ORIGIN
    if invalid == "login":
        monkeypatch.setattr(routes, "_browser_login", AsyncMock(return_value=None))
    url = env.setup_url if channel == "setup" else f"/browsers/{env.instance.id}/view"
    with pytest.raises(WebSocketDisconnect) as error, env.client.websocket_connect(url, headers=headers):
        pytest.fail("Unauthorized viewer was accepted")
    assert error.value.code == (4403 if invalid == "origin" else 4401)
    assert not env.calls


@pytest.mark.parametrize("invalid", ["session", "integration", "browser", "view", "missing-view", "owner", "login", "duplicate"])
def test_view_socket_is_bound_to_control_viewer_and_exclusive(env, monkeypatch, invalid):
    with env.client.websocket_connect(env.url, headers=ORIGIN) as control:
        state = control.receive_json()
        with open_view(env, state):
            url = view_url(env, state)
            if invalid == "session":
                url = url.replace(str(env.session.id), str(uuid4()))
            elif invalid == "integration":
                url = url.replace(str(env.instance.id), str(uuid4()))
            elif invalid == "browser":
                url = url.replace(state["browserId"], str(uuid4()))
            elif invalid == "view":
                url = url.replace(state["viewId"], str(uuid4()))
            elif invalid == "missing-view":
                url = url.split("&viewId=")[0]
            elif invalid == "owner":
                monkeypatch.setattr(routes, "_browser_login", AsyncMock(return_value=_BrowserLogin(
                    SimpleNamespace(id=uuid4()), env.login.id, env.login.expires_at,
                )))
            elif invalid == "login":
                monkeypatch.setattr(routes, "_browser_login", AsyncMock(return_value=_BrowserLogin(
                    env.user, uuid4(), env.login.expires_at,
                )))
            with pytest.raises(WebSocketDisconnect), env.client.websocket_connect(url, headers=ORIGIN):
                pytest.fail("Unbound or second view socket was accepted")
            assert len(env.coordinator.relays) == 1
            assert sum(op == "integration.browser.view" for op, _ in env.calls) == 1


@pytest.mark.parametrize("payload", ['{"type":"start","command":"touch /tmp/unsafe"}', '{', 'x' * 100001])
def test_malformed_setup_controls_never_start_installation(env, payload):
    with env.client.websocket_connect(env.setup_url, headers=ORIGIN) as setup:
        setup.send_text(payload)
        assert setup.receive_json()["error"]["code"] == "browser.invalidTarget"
    assert not env.calls


@pytest.mark.parametrize("invalid", ["token", "reused", "ended", "closed"])
def test_relay_token_is_one_use_and_bound_to_live_connection(env, invalid):
    with env.client.websocket_connect(env.url, headers=ORIGIN) as control:
        control.receive_json()
        connection = env.coordinator.connections[env.agent.id]
        relay = execution.BrowserRelay(connection)
        env.coordinator.relays[relay.relay_id] = relay
        token = relay.token
        if invalid == "token":
            token += "wrong"
        elif invalid == "reused":
            relay.accepted = True
        elif invalid == "ended":
            relay.ended.set()
        else:
            connection.closed = True
        try:
            with pytest.raises(WebSocketDisconnect) as error, env.client.websocket_connect(
                f"/browser-relay/{relay.relay_id}", headers={"authorization": f"Bearer {token}"},
            ):
                pytest.fail("Invalid relay was accepted")
            assert error.value.code == 4403
        finally:
            connection.closed = False


def test_real_binary_relay_bidirectional_bounded_and_one_use(env):
    async def reply(operation, arguments):
        if operation == "integration.browser.view":
            relay = env.coordinator.relays[UUID(arguments["relayId"])]
            async with asyncio.timeout(2):
                while not relay.accepted:
                    await asyncio.sleep(0.001)
            return {"ok": True}

    env.reply = reply
    with env.client.websocket_connect(env.url, headers=ORIGIN) as control:
        state = control.receive_json()
        with env.client.websocket_connect(view_url(env, state), headers=ORIGIN) as viewer:
            env.client.portal.call(asyncio.sleep, 0)
            relay = next(iter(env.coordinator.relays.values()))
            assert relay.incoming.maxsize == relay.outgoing.maxsize == 4
            headers = {"authorization": f"Bearer {relay.token}"}
            url = f"/browser-relay/{relay.relay_id}"
            with env.client.websocket_connect(url, headers=headers) as agent:
                agent.send_bytes(b"RFB 003.008\n")
                assert viewer.receive_bytes() == b"RFB 003.008\n"
                viewer.send_bytes(b"\x05\x01\x00\x01\x00\x02")
                assert agent.receive_bytes() == b"\x05\x01\x00\x01\x00\x02"
                with pytest.raises(WebSocketDisconnect) as error, env.client.websocket_connect(url, headers=headers):
                    pytest.fail("Relay token was reused")
                assert error.value.code == 4403
                agent.send_bytes(b"x" * (1024 * 1024))
                assert len(viewer.receive_bytes()) == 1024 * 1024
                agent.close()
                assert viewer.receive()["type"] == "websocket.close"
                assert not control.receive_json()["attached"]
    assert not env.coordinator.relays


def test_relay_backpressure_blocks_until_queue_space_is_available(env):
    with env.client.websocket_connect(env.url, headers=ORIGIN) as control:
        control.receive_json()
        relay = execution.BrowserRelay(env.coordinator.connections[env.agent.id])
        env.coordinator.relays[relay.relay_id] = relay
        with env.client.websocket_connect(f"/browser-relay/{relay.relay_id}",
                                          headers={"authorization": f"Bearer {relay.token}"}) as agent:
            for index in range(6):
                agent.send_bytes(bytes([index]))

            async def full():
                async with asyncio.timeout(1):
                    while not relay.incoming.full():
                        await asyncio.sleep(0.001)

            env.client.portal.call(full)
            assert relay.incoming.qsize() == 4

            async def drain():
                async with asyncio.timeout(1):
                    return [await relay.incoming.get() for _ in range(6)]

            assert env.client.portal.call(drain) == [bytes([index]) for index in range(6)]


async def test_rfb_requests_pass_while_browser_action_lock_is_held():
    release = asyncio.Event()

    async def receive():
        if not release.is_set():
            release.set()
            return {"type": "websocket.receive", "bytes": b"rfb-update-request"}
        await asyncio.Event().wait()

    connection = SimpleNamespace(action_lock=asyncio.Lock(), closed=False)
    await connection.action_lock.acquire()
    outgoing = asyncio.Queue(maxsize=4)
    task = asyncio.create_task(
        routes._pump_socket(
            SimpleNamespace(receive=receive, send_bytes=AsyncMock()),
            asyncio.Queue(maxsize=4), outgoing, connection=connection,
        )
    )
    try:
        assert await asyncio.wait_for(outgoing.get(), 1) == b"rfb-update-request"
        assert connection.action_lock.locked()
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        connection.action_lock.release()


@pytest.mark.parametrize("payload", ["text-not-rfb", b"x" * (1024 * 1024 + 1)])
def test_viewer_rejects_nonbinary_and_oversized_frames_without_relaying(env, payload):
    with env.client.websocket_connect(env.url, headers=ORIGIN) as control:
        state = control.receive_json()
        with env.client.websocket_connect(view_url(env, state), headers=ORIGIN) as viewer:
            env.client.portal.call(asyncio.sleep, 0)
            relay = next(iter(env.coordinator.relays.values()))
            (viewer.send_text if isinstance(payload, str) else viewer.send_bytes)(payload)
            assert control.receive_json()["error"]["code"] == "browser.invalidTarget"
            assert viewer.receive()["type"] == "websocket.close"
            assert relay.outgoing.empty()


def file_url(env, state, suffix):
    return (f"/browsers/{env.instance.id}/{suffix}?sessionId={env.session.id}"
            f"&browserId={state['browserId']}&viewId={state['viewId']}")


@pytest.mark.parametrize("text", [
    "", "Plain text\nsecond line", "Gr\u00fc\u00dfe \u65e5\u672c\u8a9e \U0001f642 e\u0301",
    "x" * 16384, "\u00e4" * 8192, "\U0001f642" * 4096,
], ids=["empty", "ascii", "unicode", "ascii-limit", "two-byte-limit", "four-byte-limit"])
def test_clipboard_round_trip_preserves_text_and_utf8_byte_limit(env, text):
    with env.client.websocket_connect(env.url, headers=ORIGIN) as control:
        state = control.receive_json()
        url = file_url(env, state, "clipboard")
        written = env.client.put(url, json={"text": text})
        assert written.status_code == 200 and written.json() == {"text": text}
        read = env.client.get(url)
        assert read.status_code == 200 and read.json() == {"text": text}
        assert [args for op, args in env.calls if op == "integration.browser.clipboard"] == [
            {"browserId": state["browserId"], "action": "write", "text": text},
            {"browserId": state["browserId"], "action": "read"},
        ]


@pytest.mark.parametrize("method", ["GET", "PUT"])
@pytest.mark.parametrize("invalid, code", [
    ("unauthenticated", "auth.notAuthenticated"), ("owner", "browser.notConnected"),
    ("session", "browser.notConnected"), ("browser", "browser.notConnected"),
    ("integration", "browser.notConnected"), ("expired", "auth.sessionExpired"),
    ("inactive", "auth.sessionExpired"), ("binding", "browser.bindingChanged"),
    ("agent-owner", "executionAgent.notFound"), ("tab", "browser.notConnected"),
    ("disabled", "browser.unavailable"), ("attached", "browser.busy"), ("busy", "browser.busy"),
    ("view", "browser.notConnected"), ("login", "browser.notConnected"),
    ("global-disabled", "browser.unavailable"),
])
def test_clipboard_requires_live_authorized_manual_browser(env, monkeypatch, method, invalid, code):
    with env.client.websocket_connect(env.url, headers=ORIGIN) as control:
        state = control.receive_json()
        with open_view(env, state):
            url = file_url(env, state, "clipboard")
            connection = env.coordinator.connections[env.agent.id]
            if invalid == "unauthenticated":
                monkeypatch.setattr(routes, "_browser_login", AsyncMock(return_value=None))
            elif invalid == "owner":
                monkeypatch.setattr(routes, "_browser_login", AsyncMock(return_value=_BrowserLogin(
                    SimpleNamespace(id=uuid4()), env.login.id, env.login.expires_at,
                )))
            elif invalid == "login":
                monkeypatch.setattr(routes, "_browser_login", AsyncMock(return_value=_BrowserLogin(
                    env.user, uuid4(), env.login.expires_at,
                )))
            elif invalid == "view":
                url = url.replace(state["viewId"], str(uuid4()))
            elif invalid == "session":
                url = url.replace(str(env.session.id), str(uuid4()))
            elif invalid == "browser":
                url = url.replace(state["browserId"], str(uuid4()))
            elif invalid == "integration":
                url = url.replace(str(env.instance.id), str(uuid4()))
            elif invalid == "expired":
                env.login.expires_at = datetime.now(UTC) - timedelta(seconds=1)
            elif invalid == "inactive":
                env.user.is_active = False
            elif invalid == "binding":
                env.session.preferred_execution_agent_id = uuid4()
            elif invalid == "agent-owner":
                env.agent.user_id = uuid4()
            elif invalid == "tab":
                env.session.control_center = {"tabs": []}
            elif invalid == "disabled":
                env.documents[UserIntegrationSettings, env.user.id].instances[0].enabled = False
            elif invalid == "global-disabled":
                env.documents[IntegrationSettings, None].adapter_availability["browser"] = "disabled"
            elif invalid == "attached":
                control.send_json({"type": "attach"})
                assert control.receive_json()["attached"] and not env.manual
            else:
                connection.busy = True
            response = env.client.request(method, url, **({"json": {"text": "private clipboard"}} if method == "PUT" else {}))
            assert response.status_code >= 400 and response.json()["error"]["code"] == code
            if invalid == "unauthenticated":
                assert response.status_code == 401
            elif invalid in {"attached", "busy"}:
                assert response.status_code == 409
            assert "private clipboard" not in response.text
            assert not any(op == "integration.browser.clipboard" for op, _ in env.calls)


@pytest.mark.parametrize("payload", [
    {}, {"text": None}, {"text": 1}, {"text": []}, {"text": "private clipboard", "action": "read"},
    {"text": "x" * 16385}, {"text": "\u00e4" * 8192 + "x"}, {"text": "\U0001f642" * 4096 + "x"},
    {"text": "\ud800"},
], ids=["missing", "null", "number", "list", "host-field", "ascii-overflow", "two-byte-overflow", "four-byte-overflow", "surrogate"])
def test_invalid_clipboard_input_is_localized_and_never_dispatched(env, payload):
    with env.client.websocket_connect(env.url, headers=ORIGIN) as control:
        state = control.receive_json()
        response = env.client.put(file_url(env, state, "clipboard"), content=json.dumps(payload),
                                  headers={"content-type": "application/json"})
        assert response.status_code == 422
        assert response.json()["error"]["code"].startswith("validation.")
        assert "private clipboard" not in response.text and "surrogates" not in response.text
        assert not any(op == "integration.browser.clipboard" for op, _ in env.calls)


@pytest.mark.parametrize("method", ["GET", "PUT"])
@pytest.mark.parametrize("payload", [
    {}, {"text": 123}, {"text": "\u00e4" * 8193}, {"text": "private clipboard", "unexpected": "secret"},
], ids=["missing", "wrong-type", "unicode-overflow", "extra-field"])
def test_malformed_remote_clipboard_result_is_sanitized(env, method, payload):
    async def reply(operation, _arguments):
        if operation == "integration.browser.clipboard":
            return payload

    env.reply = reply
    with env.client.websocket_connect(env.url, headers=ORIGIN) as control:
        state = control.receive_json()
        response = env.client.request(method, file_url(env, state, "clipboard"),
                                      **({"json": {"text": "value"}} if method == "PUT" else {}))
        assert response.status_code >= 400
        assert response.json()["error"]["code"] == "executionAgent.protocolError"
        assert "private clipboard" not in response.text and "secret" not in response.text


@pytest.mark.parametrize("text", [None, "value"], ids=["read", "write"])
def test_clipboard_revalidates_login_after_waiting_for_action_lock(env, monkeypatch, text):
    checked, release = asyncio.Event(), asyncio.Event()
    original = env.coordinator.manual

    async def manual(*args):
        connection = await original(*args)
        checked.set()
        return connection

    monkeypatch.setattr(env.coordinator, "manual", manual)
    with env.client.websocket_connect(env.url, headers=ORIGIN) as control:
        state = control.receive_json()
        connection = env.coordinator.connections[env.agent.id]

        async def hold_lock():
            async with connection.action_lock:
                await release.wait()

        holder = env.client.portal.start_task_soon(hold_lock)
        env.client.portal.call(asyncio.sleep, 0)
        assert connection.action_lock.locked()
        async def clipboard():
            authorized = await env.coordinator.manual(env.user.id, env.instance.id, env.session.id,
                                                       UUID(state["browserId"]), UUID(state["viewId"]), env.login.id)
            return await routes._clipboard(authorized, text)

        task = env.client.portal.start_task_soon(clipboard)
        try:
            env.client.portal.call(asyncio.wait_for, checked.wait(), 1)
            env.objects.pop((LoginSession, env.login.id))
        finally:
            env.client.portal.call(release.set)
            holder.result(timeout=2)
        with pytest.raises(routes.ApiError) as error:
            task.result(timeout=2)
        assert error.value.code == "auth.sessionExpired"
        assert not any(op == "integration.browser.clipboard" for op, _ in env.calls)


@pytest.mark.parametrize("operation", ["integration.browser.clipboard", "integration.browser.file"])
def test_pending_manual_mutation_blocks_attachment_until_it_finishes(env, operation):
    started, finish = asyncio.Event(), asyncio.Event()

    async def reply(op, arguments):
        if op == operation and arguments["action"] == ("write" if op.endswith("clipboard") else "finish"):
            started.set()
            await finish.wait()

    env.reply = reply
    with env.client.websocket_connect(env.url, headers=ORIGIN) as control:
        state = control.receive_json()
        with open_view(env, state):
            connection = env.coordinator.connections[env.agent.id]
            if operation.endswith("clipboard"):
                task = env.client.portal.start_task_soon(routes._clipboard, connection, "value")
            else:
                upload = UploadFile(BytesIO(b"x"), size=1, filename="sample.txt")
                task = env.client.portal.start_task_soon(routes.browser_upload, connection, upload)
            try:
                env.client.portal.call(asyncio.wait_for, started.wait(), 1)
                control.send_json({"type": "attach"})
                assert control.receive_json()["error"]["code"] == "browser.busy"
                assert env.manual and not env.coordinator.connections[env.agent.id].attached
            finally:
                env.client.portal.call(finish.set)
            task.result(timeout=2)
            if operation.endswith("file"):
                assert any(args.get("action") == "choose" for _, args in env.calls)
            control.send_json({"type": "attach"})
            assert control.receive_json()["attached"] and not env.manual


def test_upload_chunks_are_bounded_and_finish_precedes_choose(env):
    with env.client.websocket_connect(env.url, headers=ORIGIN) as control:
        state = control.receive_json()
        data = b"x" * 65536 + b"end"
        response = env.client.post(file_url(env, state, "upload"), files={"file": ("sample.txt", data)})
        assert response.status_code == 200 and response.json() == {"uploadId": env.file_id, "chosen": True}
        calls = [args for op, args in env.calls if op == "integration.browser.file"]
        assert [args["action"] for args in calls] == ["begin", "write", "write", "finish", "choose"]
        writes = [args for args in calls if args["action"] == "write"]
        assert [args["offset"] for args in writes] == [0, 65536]
        assert b"".join(base64.b64decode(args["data"], validate=True) for args in writes) == data


@pytest.mark.parametrize("invalid", ["filename", "oversize"])
def test_invalid_upload_rejected_before_rpc(env, monkeypatch, invalid):
    monkeypatch.setattr(routes, "MAX_BROWSER_FILE_BYTES", 3)
    with env.client.websocket_connect(env.url, headers=ORIGIN) as control:
        state = control.receive_json()
        response = env.client.post(file_url(env, state, "upload"), files={
            "file": ("../secret" if invalid == "filename" else "sample.txt", b"x" if invalid == "filename" else b"xxxx"),
        })
        assert response.status_code >= 400
        assert response.json()["error"]["code"] == ("browser.invalidTarget" if invalid == "filename" else "browser.fileTooLarge")
        assert not any(op == "integration.browser.file" for op, _ in env.calls)


@pytest.mark.parametrize("invalid", ["file-id", "offset", "size", "complete", "malformed", "finish"])
def test_malformed_upload_reply_aborts_and_deletes_staging(env, invalid):
    async def reply(operation, arguments):
        if operation == "integration.browser.file" and arguments["action"] == ("finish" if invalid == "finish" else "write"):
            return {"fileId": str(uuid4()) if invalid == "file-id" else env.file_id,
                    "offset": 0 if invalid == "offset" else 1, "size": 2 if invalid == "size" else 1,
                    "complete": invalid == "complete", **({"data": "!"} if invalid == "malformed" else {})}

    env.reply = reply
    with env.client.websocket_connect(env.url, headers=ORIGIN) as control:
        state = control.receive_json()
        response = env.client.post(file_url(env, state, "upload"), files={"file": ("sample.txt", b"x")})
        assert response.status_code >= 400
        assert response.json()["error"]["code"] == "executionAgent.protocolError"
        calls = [args for op, args in env.calls if op == "integration.browser.file"]
        assert calls[-1]["action"] == "delete" and calls[-1]["fileId"] == env.file_id
        assert not any(args["action"] == "choose" for args in calls)


@pytest.mark.parametrize("change", ["login", "tab"])
def test_upload_revalidates_access_after_finish_before_choosing_file(env, change):
    async def reply(operation, arguments):
        if operation == "integration.browser.file" and arguments["action"] == "finish":
            if change == "login":
                env.objects.pop((LoginSession, env.login.id))
            else:
                env.session.control_center = {"tabs": []}

    env.reply = reply
    with env.client.websocket_connect(env.url, headers=ORIGIN) as control:
        state = control.receive_json()
        with open_view(env, state):
            response = env.client.post(file_url(env, state, "upload"), files={"file": ("sample.txt", b"x")})
            assert not any(args.get("action") == "choose" for _, args in env.calls)
            assert response.status_code >= 400


@pytest.mark.parametrize("invalid", ["file-id", "base64", "oversize", "offset", "filename"])
def test_malformed_download_rejected_before_streaming(env, invalid):
    download_id = str(uuid4())

    async def reply(operation, _arguments):
        if operation == "integration.browser.file":
            return {"fileId": str(uuid4()) if invalid == "file-id" else download_id,
                    "offset": 0 if invalid == "offset" else 1, "size": 1, "complete": invalid != "offset",
                    "filename": "../secret" if invalid == "filename" else "sample.txt",
                    "data": "!" if invalid == "base64" else base64.b64encode(b"x" * (65537 if invalid == "oversize" else 1)).decode()}

    env.reply = reply
    with env.client.websocket_connect(env.url, headers=ORIGIN) as control:
        state = control.receive_json()
        response = env.client.get(file_url(env, state, f"download/{download_id}"))
        assert response.status_code >= 400
        assert response.json()["error"]["code"] == "executionAgent.protocolError"


def test_download_stream_uses_exact_offsets_and_safe_headers(env):
    download_id = str(uuid4())

    async def reply(operation, arguments):
        if operation == "integration.browser.file":
            offset = arguments["offset"]
            return {"fileId": download_id, "offset": offset + 2, "size": 4, "complete": offset == 2,
                    "filename": "a b.txt", "data": base64.b64encode(b"ab" if offset == 0 else b"cd").decode()}

    env.reply = reply
    with env.client.websocket_connect(env.url, headers=ORIGIN) as control:
        state = control.receive_json()
        response = env.client.get(file_url(env, state, f"download/{download_id}"))
        assert response.status_code == 200 and response.content == b"abcd"
        assert response.headers["content-length"] == "4"
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["content-disposition"] == "attachment; filename*=UTF-8''a%20b.txt"
        assert [args["offset"] for op, args in env.calls if op == "integration.browser.file"] == [0, 2]


@pytest.mark.parametrize("invalid", ["offset", "size", "filename", "empty", "base64"])
def test_download_aborts_on_malformed_later_chunk(env, invalid):
    download_id = str(uuid4())

    async def reply(operation, arguments):
        if operation == "integration.browser.file":
            later = arguments["offset"] != 0
            return {"fileId": download_id, "offset": 1 if not later or invalid in {"empty", "offset"} else 2,
                    "size": 4 if later and invalid == "size" else 3, "complete": False,
                    "filename": "changed.txt" if later and invalid == "filename" else "sample.txt",
                    "data": ("" if invalid == "empty" else "!") if later and invalid in {"empty", "base64"} else "eA=="}

    env.reply = reply
    with env.client.websocket_connect(env.url, headers=ORIGIN) as control:
        state = control.receive_json()
        with pytest.raises(ExecutionClientError, match="executionAgent.protocolError"):
            env.client.get(file_url(env, state, f"download/{download_id}"))
        assert [args["offset"] for op, args in env.calls if op == "integration.browser.file"] == [0, 1]


@pytest.mark.parametrize("attached", [False, True])
def test_desktop_resize_preserves_runtime_and_attachment(env, attached):
    with env.client.websocket_connect(env.url, headers=ORIGIN) as control:
        state = control.receive_json()
        with open_view(env, state):
            if attached:
                control.send_json({"type": "attach"})
                assert control.receive_json()["attached"]
            control.send_json({"type": "resize", "width": 1001, "height": 701})
            assert control.receive_json() == {
                "type": "resized", "browserId": state["browserId"], "width": 1001, "height": 701,
            }
            connection = env.coordinator.connections[env.agent.id]
            assert connection.attached is attached and str(connection.browser_id) == state["browserId"]
            assert sum(op == "integration.browser.open" for op, _ in env.calls) == 1


@pytest.mark.parametrize("field,value", [("width", True), ("width", "640"), ("height", 400.5),
                                        ("height", 0), ("width", 3841), ("height", 2161), ("display", ":0")])
def test_invalid_desktop_size_never_reaches_agent(env, field, value):
    with env.client.websocket_connect(env.url, headers=ORIGIN) as control:
        control.receive_json()
        control.send_json({"type": "resize", "width": 640, "height": 480, field: value})
        assert control.receive_json()["error"]["code"] == "browser.invalidTarget"
        assert not any(op == "integration.browser.resize" for op, _ in env.calls)


def test_resize_requests_keep_only_latest_pending_geometry(env):
    started, finish = asyncio.Event(), asyncio.Event()

    async def reply(op, args):
        if op == "integration.browser.resize" and args["width"] == 640:
            started.set()
            await finish.wait()

    env.reply = reply
    with env.client.websocket_connect(env.url, headers=ORIGIN) as control:
        control.receive_json()
        control.send_json({"type": "resize", "width": 640, "height": 480})
        env.client.portal.call(asyncio.wait_for, started.wait(), 1)
        try:
            for width in range(700, 750):
                control.send_json({"type": "resize", "width": width, "height": 500})
            env.client.portal.call(asyncio.sleep, 0.1)
        finally:
            env.client.portal.call(finish.set)
        assert control.receive_json()["width"] == 640
        assert control.receive_json()["width"] == 749
        assert [args["width"] for op, args in env.calls if op == "integration.browser.resize"] == [640, 749]


def test_resize_failure_is_scoped_and_does_not_close_browser(env):
    async def reply(op, _args):
        if op == "integration.browser.resize":
            raise ExecutionClientError("browser.actionFailed")

    env.reply = reply
    with env.client.websocket_connect(env.url, headers=ORIGIN) as control:
        control.receive_json()
        control.send_json({"type": "resize", "width": 640, "height": 480})
        event = control.receive_json()
        assert event["operation"] == "resize" and event["error"]["code"] == "browser.actionFailed"
        assert not env.coordinator.connections[env.agent.id].closed


@pytest.mark.parametrize("blocked", [None, "attached", "login"])
def test_copy_clipboard_uses_fixed_host_action_and_live_authorization(env, blocked):
    env.clipboard = "Grüße\n世界\n"
    with env.client.websocket_connect(env.url, headers=ORIGIN) as control:
        state = control.receive_json()
        with open_view(env, state):
            if blocked == "attached":
                control.send_json({"type": "attach"})
                control.receive_json()
            elif blocked == "login":
                env.objects.pop((LoginSession, env.login.id))
            response = env.client.post(file_url(env, state, "clipboard/copy"))
            calls = [args for op, args in env.calls if op == "integration.browser.clipboard"]
            if blocked:
                assert response.status_code >= 400 and calls == []
            else:
                assert response.json() == {"text": env.clipboard}
                assert calls == [{"browserId": state["browserId"], "action": "copy"}]


@pytest.mark.parametrize("action", ["state", "new", "select", "close", "navigate", "back", "forward", "reload", "stop", "chrome"])
def test_navigation_commands_are_bound_and_acknowledged(env, action):
    fields = {} if action in {"state", "new", "chrome"} else {"tabId": env.navigation["activeTabId"]}
    if action == "navigate":
        fields["url"] = "https://example.test/"
    if action == "chrome":
        fields["nativeChrome"] = True
    with env.client.websocket_connect(env.url, headers=ORIGIN) as control:
        state = control.receive_json()
        with open_view(env, state):
            control.send_json({"type": "navigation", "action": action, **fields})
            response = control.receive_json()
            assert response == {"type": "navigation", "browserId": state["browserId"],
                                "completed": True, **env.navigation}
            calls = [args for op, args in env.calls if op == "integration.browser.navigation"]
            assert calls == [{"browserId": state["browserId"], "action": action, **fields}]


@pytest.mark.parametrize("action", ["state", "new", "select", "navigate", "chrome"])
def test_attached_browser_navigation_only_observes(env, action):
    fields = {"tabId": env.navigation["activeTabId"]} if action in {"select", "navigate"} else {}
    if action == "navigate":
        fields["url"] = "https://example.test/"
    if action == "chrome":
        fields["nativeChrome"] = True
    with env.client.websocket_connect(env.url, headers=ORIGIN) as control:
        state = control.receive_json()
        with open_view(env, state):
            control.send_json({"type": "attach"})
            assert control.receive_json()["attached"]
            control.send_json({"type": "navigation", "action": action, **fields})
            response = control.receive_json()
            if action == "state":
                assert response["type"] == "navigation" and response["completed"]
            else:
                assert response["operation"] == "navigation" and response["error"]["code"] == "browser.busy"
                assert not any(op == "integration.browser.navigation" for op, _ in env.calls)


@pytest.mark.parametrize("fields", [
    {"action": "state", "tabId": None}, {"action": "new", "nativeChrome": True},
    {"action": "chrome", "nativeChrome": "true"}, {"action": "select"},
    {"action": "evaluate", "text": "alert(1)"},
    {"action": "new", "url": "javascript:alert(1)"}, {"action": "new", "url": "file:///etc/passwd"},
    {"action": "new", "url": "https://secret:password@example.test/"},
])
def test_invalid_navigation_never_dispatches(env, fields):
    with env.client.websocket_connect(env.url, headers=ORIGIN) as control:
        control.receive_json()
        control.send_json({"type": "navigation", **fields})
        assert control.receive_json()["error"]["code"] == "browser.invalidTarget"
        assert not any(op == "integration.browser.navigation" for op, _ in env.calls)


def test_navigation_observation_publishes_changes_without_command_ack(env, monkeypatch):
    monkeypatch.setattr(execution, "ACCESS_INTERVAL", 0.05)
    with env.client.websocket_connect(env.url, headers=ORIGIN) as control:
        control.receive_json()
        control.send_json({"type": "navigation", "action": "state"})
        assert control.receive_json()["completed"]
        env.navigation["tabs"][0]["title"] = "After redirect"
        response = control.receive_json()
        assert response["type"] == "navigation" and response["completed"] is False
        assert response["tabs"][0]["title"] == "After redirect"


def test_navigation_error_does_not_end_healthy_browser(env):
    async def reply(operation, _arguments):
        if operation == "integration.browser.navigation":
            return {**env.navigation, "activeTabId": str(uuid4())}

    env.reply = reply
    with env.client.websocket_connect(env.url, headers=ORIGIN) as control:
        control.receive_json()
        control.send_json({"type": "navigation", "action": "state"})
        response = control.receive_json()
        assert response["operation"] == "navigation" and response["error"]["code"] == "executionAgent.protocolError"
        assert not env.coordinator.connections[env.agent.id].closed


@pytest.mark.parametrize("attached", [False, True])
def test_takeover_requires_explicit_offer_and_preserves_machine_but_never_old_view_authority(env, attached):
    env.session.title = "Session A"
    session_b = other_session(env)
    url_b = env.url.replace(str(env.session.id), str(session_b.id))
    with env.client.websocket_connect(env.url, headers=ORIGIN) as control_a:
        state_a = control_a.receive_json()
        with env.client.websocket_connect(view_url(env, state_a), headers=ORIGIN):
            old = env.coordinator.connections[env.agent.id]
            if attached:
                control_a.send_json({"type": "attach"})
                assert control_a.receive_json()["attached"]
            before = list(env.calls)
            with env.client.websocket_connect(url_b, headers=ORIGIN) as control_b:
                offer = control_b.receive_json()
                assert offer == {"type": "availability", "status": "occupied", "browserId": state_a["browserId"],
                                 "viewId": state_a["viewId"], "ownerSession": {"id": str(env.session.id), "title": "Session A"},
                                 "busy": False, "canTakeover": True}
                assert env.calls == before and env.coordinator.connections[env.agent.id] is old
                control_b.send_json({"type": "takeover", "browserId": offer["browserId"], "viewId": offer["viewId"]})
                assert control_b.receive_json()["status"] == "takingOver"
                state_b = control_b.receive_json()
                current = env.coordinator.connections[env.agent.id]
                assert current is not old and current.action_lock is old.action_lock and current.client is old.client
                assert current.browser_id == old.browser_id and current.view_id != old.view_id
                assert current.session_id == session_b.id and old.closed and not old.attached
                assert state_b["type"] == "state" and not state_b["attached"] and not state_b["busy"]
                assert control_a.receive_json() == {"type": "replaced"}
                assert control_a.receive()["code"] == 4001
                assert [args["manual"] for op, args in env.calls[len(before):] if op == "integration.browser.control"] == [False, True]
                env.client.portal.call(env.coordinator.close, old)
                env.client.portal.call(env.coordinator.release, old, old.websocket)
                assert env.coordinator.connections[env.agent.id] is current and not current.closed

                # Returning to A must not resurrect A's original URLs or B's retired URLs.
                with env.client.websocket_connect(env.url, headers=ORIGIN) as returned:
                    offer_b = returned.receive_json()
                    assert offer_b["ownerSession"] == {"id": str(session_b.id), "title": "Session B"}
                    returned.send_json({"type": "takeover", "browserId": state_b["browserId"], "viewId": state_b["viewId"]})
                    assert returned.receive_json()["status"] == "takingOver"
                    state_returned = returned.receive_json()
                    assert state_returned["browserId"] == state_a["browserId"]
                    assert len({state_a["viewId"], state_b["viewId"], state_returned["viewId"]}) == 3
                    before_stale = list(env.calls)
                    for stale, session in ((state_a, env.session), (state_b, session_b)):
                        for method, suffix, kwargs in (
                            ("GET", "clipboard", {}), ("PUT", "clipboard", {"json": {"text": "private clipboard"}}),
                            ("POST", "clipboard/copy", {}), ("GET", "downloads", {}),
                            ("POST", "upload", {"files": {"file": ("sample.txt", b"x")}}),
                            ("POST", f"upload/{env.file_id}/choose", {}), ("GET", f"download/{env.file_id}", {}),
                        ):
                            url = file_url(env, stale, suffix).replace(str(env.session.id), str(session.id))
                            response = env.client.request(method, url, **kwargs)
                            assert response.status_code >= 400 and response.json()["error"]["code"] == "browser.notConnected"
                            assert "private clipboard" not in response.text
                        with pytest.raises(WebSocketDisconnect), env.client.websocket_connect(
                            view_url(env, stale).replace(str(env.session.id), str(session.id)), headers=ORIGIN,
                        ):
                            pytest.fail("Retired view was accepted after returning to the original session")
                    assert env.calls == before_stale
                    assert env.client.get(file_url(env, state_returned, "clipboard")).status_code == 200
    assert sum(op == "integration.browser.open" for op, _ in env.calls) == 1
    assert not any(op in {"integration.browser.close", "integration.browser.setup"} for op, _ in env.calls)


@pytest.mark.parametrize("owned", [False, True])
def test_availability_never_discloses_foreign_session_title(env, owned):
    async def scenario():
        connection = await env.coordinator.open(env.user.id, env.instance.id, env.session.id, env.login.id, AsyncMock())
        env.session.title = "Private title"
        if not owned:
            env.session.user_id = uuid4()
        offer = await env.coordinator.availability(connection)
        assert offer["ownerSession"]["title"] == ("Private title" if owned else "Session")

    env.client.portal.call(scenario)


@pytest.mark.parametrize("suffix,method", [("clipboard", "GET"), ("clipboard", "PUT"), ("clipboard/copy", "POST"),
                                          ("downloads", "GET"), ("upload", "POST"),
                                          ("upload/{file_id}/choose", "POST"), ("download/{file_id}", "GET")])
def test_all_manual_http_routes_require_view_id_before_rpc(env, suffix, method):
    with env.client.websocket_connect(env.url, headers=ORIGIN) as control:
        state = control.receive_json()
        before = list(env.calls)
        kwargs = {"files": {"file": ("sample.txt", b"x")}} if suffix == "upload" else {}
        if method == "PUT":
            kwargs = {"json": {"text": "private clipboard"}}
        response = env.client.request(method, file_url(env, state, suffix.format(file_id=env.file_id)).split("&viewId=")[0], **kwargs)
        assert response.status_code == 422 and response.json()["error"]["code"].startswith("validation.")
        assert "private clipboard" not in response.text and env.calls == before


@pytest.mark.parametrize("action", ["navigate", "inspect"])
def test_takeover_drains_active_ai_and_fences_queued_old_actions_without_wire_cancel(env, wire, action):
    async def scenario():
        started, finish, queued = asyncio.Event(), asyncio.Event(), asyncio.Event()
        old = await env.coordinator.open(env.user.id, env.instance.id, env.session.id, env.login.id, AsyncMock())
        old.view_socket = AsyncMock()
        await env.coordinator.attachment(old, True)
        tab_id = str(uuid4())
        snapshot = await env.coordinator.snapshot(env.session.id, env.agent.id, env.instance.id, tab_id)

        async def reply(operation, arguments):
            if operation == "integration.browser.execute" and arguments["action"] == action:
                started.set()
                await finish.wait()

        env.reply = reply
        arguments = {"action": "navigate", "tabId": tab_id, "url": "https://page.test/",
                     "expectedBrowserId": str(old.browser_id), "expectedAttachmentRevision": old.attachment_revision}
        if action == "inspect":
            arguments = {**arguments, "action": "click", "selector": "button", "expectedSnapshot": snapshot}
            arguments.pop("url")
        active = asyncio.create_task(env.coordinator.execute(env.session.id, env.agent.id, env.instance.id, arguments))
        await asyncio.wait_for(started.wait(), 1)

        async def queued_action():
            queued.set()
            return await env.coordinator.execute(env.session.id, env.agent.id, env.instance.id, arguments)

        pending = asyncio.create_task(queued_action())
        await asyncio.wait_for(queued.wait(), 1)
        taking_over = asyncio.create_task(env.coordinator.open(
            env.user.id, env.instance.id, other_session(env).id, env.login.id, AsyncMock(),
            expected_browser_id=old.browser_id, expected_view_id=old.view_id,
        ))
        try:
            async with asyncio.timeout(1):
                while not old.closed:
                    await asyncio.sleep(0)
            assert not active.done() and not taking_over.done()
            assert not old.attached and env.coordinator.attached_for(env.session.id, env.agent.id) is None
            assert [args["manual"] for op, args in env.calls if op == "integration.browser.control"] == [False]
            assert not any(item["type"] == "cancel" for item in wire)
        finally:
            finish.set()
        results = await asyncio.wait_for(asyncio.gather(active, pending, taking_over, return_exceptions=True), 2)
        if action == "inspect":
            assert isinstance(results[0], ExecutionClientError) and results[0].code == "browser.bindingChanged"
            assert not any(args.get("action") == "click" for _, args in env.calls)
        else:
            assert isinstance(results[0], routes.BrowserActionResult)
            assert sum(args.get("action") == "navigate" for _, args in env.calls) == 1
        assert isinstance(results[1], ExecutionClientError) and results[1].code == "browser.notConnected"
        assert results[2] is env.coordinator.connections[env.agent.id] and results[2].ready
        assert not results[2].attached and not any(item["type"] == "cancel" for item in wire)
        assert [args["manual"] for op, args in env.calls if op == "integration.browser.control"] == [False, False, True]

    env.client.portal.call(scenario)


def test_concurrent_candidates_keep_waiting_for_explicit_current_offer(env, monkeypatch):
    monkeypatch.setattr(routes, "ACCESS_INTERVAL", 0.02)
    started, finish = asyncio.Event(), asyncio.Event()
    session_b, session_c = other_session(env), other_session(env, "Session C")

    async def reply(operation, arguments):
        if operation == "integration.browser.control" and not arguments["manual"]:
            started.set()
            await finish.wait()

    env.reply = reply
    with env.client.websocket_connect(env.url, headers=ORIGIN) as control:
        state = control.receive_json()
        with env.client.websocket_connect(env.url.replace(str(env.session.id), str(session_b.id)), headers=ORIGIN) as b:
            offer = b.receive_json()
            with env.client.websocket_connect(env.url.replace(str(env.session.id), str(session_c.id)), headers=ORIGIN) as c:
                assert c.receive_json() == offer
                takeover = {"type": "takeover", "browserId": state["browserId"], "viewId": state["viewId"]}
                b.send_json(takeover)
                assert b.receive_json()["status"] == "takingOver"
                try:
                    env.client.portal.call(asyncio.wait_for, started.wait(), 1)
                    c.send_json(takeover)
                    waiting = c.receive_json()
                    assert waiting["status"] == "waiting" and not waiting["canTakeover"]
                    assert waiting["ownerSession"]["id"] == str(session_b.id)
                    assert env.coordinator.connections[env.agent.id].session_id == session_b.id
                finally:
                    env.client.portal.call(finish.set)
                state_b = b.receive_json()
                offer_b = c.receive_json()
                assert offer_b["status"] == "occupied" and offer_b["canTakeover"]
                assert offer_b["viewId"] == state_b["viewId"] != offer["viewId"]
                before = list(env.calls)
                c.send_json({"type": "release"})
                assert c.receive()["type"] == "websocket.close"
                assert env.calls == before and env.coordinator.connections[env.agent.id].session_id == session_b.id
                assert [args["manual"] for op, args in env.calls if op == "integration.browser.control"] == [False, True]


def test_waiting_candidate_never_opens_new_runtime_after_owner_closes(env, monkeypatch):
    monkeypatch.setattr(routes, "ACCESS_INTERVAL", 0.01)
    session_b = other_session(env)
    with env.client.websocket_connect(env.url, headers=ORIGIN) as owner:
        owner.receive_json()
        with env.client.websocket_connect(env.url.replace(str(env.session.id), str(session_b.id)), headers=ORIGIN) as candidate:
            assert candidate.receive_json()["status"] == "occupied"
            owner.send_json({"type": "close"})
            assert owner.receive()["type"] == "websocket.close"
            assert candidate.receive_json()["error"]["code"] == "browser.notConnected"
            assert candidate.receive()["type"] == "websocket.close"
        assert not env.coordinator.connections
        assert [op for op, _ in env.calls] == ["integration.browser.open", "integration.browser.close"]


@pytest.mark.parametrize("change,code", [("expired", "auth.sessionExpired"), ("global-disabled", "browser.unavailable"),
                                        ("tab", "browser.notConnected"), ("binding", "browser.bindingChanged")])
def test_waiting_takeover_rechecks_authorization_before_any_fence(env, change, code):
    async def scenario():
        old = await env.coordinator.open(env.user.id, env.instance.id, env.session.id, env.login.id, AsyncMock())
        session_b = other_session(env)
        await old.action_lock.acquire()
        taking_over = asyncio.create_task(env.coordinator.open(
            env.user.id, env.instance.id, session_b.id, env.login.id, AsyncMock(),
            expected_browser_id=old.browser_id, expected_view_id=old.view_id,
        ))
        try:
            async with asyncio.timeout(1):
                while not old.closed:
                    await asyncio.sleep(0)
            if change == "expired":
                env.login.expires_at = datetime.now(UTC) - timedelta(seconds=1)
            elif change == "global-disabled":
                env.documents[IntegrationSettings, None].adapter_availability["browser"] = "disabled"
            elif change == "tab":
                session_b.control_center = {"tabs": []}
            else:
                session_b.preferred_execution_agent_id = uuid4()
        finally:
            old.action_lock.release()
        with pytest.raises(ExecutionClientError, match=code):
            await asyncio.wait_for(taking_over, 2)
        assert not env.coordinator.connections
        assert [op for op, _ in env.calls] == ["integration.browser.open", "integration.browser.close"]

    env.client.portal.call(scenario)


@pytest.mark.parametrize("change", ["expired", "global-disabled"])
def test_candidate_periodic_access_check_revokes_only_requester(env, monkeypatch, change):
    monkeypatch.setattr(routes, "ACCESS_INTERVAL", 0.01)
    session_b = other_session(env)
    login_b = LoginSession(id=uuid4(), user_id=env.user.id, expires_at=env.login.expires_at)
    env.objects[LoginSession, login_b.id] = login_b
    with env.client.websocket_connect(env.url, headers=ORIGIN) as control:
        control.receive_json()
        old = env.coordinator.connections[env.agent.id]
        monkeypatch.setattr(routes, "_browser_login", AsyncMock(return_value=_BrowserLogin(env.user, login_b.id, login_b.expires_at)))
        with env.client.websocket_connect(env.url.replace(str(env.session.id), str(session_b.id)), headers=ORIGIN) as candidate:
            assert candidate.receive_json()["status"] == "occupied"
            if change == "expired":
                login_b.expires_at = datetime.now(UTC) - timedelta(seconds=1)
            else:
                env.documents[IntegrationSettings, None].adapter_availability["browser"] = "disabled"
            assert candidate.receive_json()["error"]["code"] == ("auth.sessionExpired" if change == "expired" else "browser.unavailable")
            assert candidate.receive()["type"] == "websocket.close"
        assert env.coordinator.connections[env.agent.id] is old and not old.closed
        assert [op for op, _ in env.calls] == ["integration.browser.open"]


@pytest.mark.parametrize("cancelled", [False, True])
@pytest.mark.parametrize("operation", ["copy", "upload-begin", "upload", "choose", "download", "download-later"])
def test_native_helpers_drain_before_takeover_and_never_send_wire_cancel(env, wire, operation, cancelled):
    async def scenario():
        started, finish = asyncio.Event(), asyncio.Event()
        old = await env.coordinator.open(env.user.id, env.instance.id, env.session.id, env.login.id, AsyncMock())

        async def reply(op, arguments):
            target = (op == "integration.browser.clipboard" if operation == "copy" else
                      op == "integration.browser.file" and arguments["action"] ==
                      ("begin" if operation == "upload-begin" else "write" if operation == "upload" else
                       "choose" if operation == "choose" else "read"))
            if target and (operation != "download-later" or arguments["offset"] == 1):
                started.set()
                await finish.wait()
            if op == "integration.browser.file" and arguments["action"] == "read":
                offset = arguments["offset"]
                return {"fileId": env.file_id, "filename": "sample.txt", "size": 2,
                        "offset": offset + 1, "complete": offset == 1, "data": "eA=="}

        env.reply = reply
        upload = UploadFile(BytesIO(b"x"), size=1, filename="sample.txt")

        async def operation_task():
            if operation == "copy":
                return await routes._clipboard(old, action="copy")
            if operation in {"upload", "upload-begin"}:
                return await routes.browser_upload(old, upload)
            if operation == "choose":
                return await routes.browser_choose(UUID(env.file_id), old)
            response = await routes.browser_download(UUID(env.file_id), old)
            if operation == "download-later":
                assert await anext(response.body_iterator) == b"x"
                return await anext(response.body_iterator)
            return response

        active = asyncio.create_task(operation_task())
        await asyncio.wait_for(started.wait(), 1)
        if cancelled:
            active.cancel()
        await asyncio.sleep(0)
        taking_over = asyncio.create_task(env.coordinator.open(
            env.user.id, env.instance.id, other_session(env).id, env.login.id, AsyncMock(),
            expected_browser_id=old.browser_id, expected_view_id=old.view_id,
        ))
        try:
            async with asyncio.timeout(1):
                while not old.closed:
                    await asyncio.sleep(0)
            assert not active.done() and not taking_over.done() and old.action_lock.locked()
            assert not any(op == "integration.browser.control" for op, _ in env.calls)
            assert not any(message["type"] == "cancel" for message in wire)
        finally:
            finish.set()
        with pytest.raises(asyncio.CancelledError if cancelled else (routes.ApiError, ExecutionClientError)) as error:
            await asyncio.wait_for(active, 2)
        if not cancelled:
            assert error.value.code == "browser.notConnected"
        current = await asyncio.wait_for(taking_over, 2)
        assert current.ready and not current.attached and not current.action_lock.locked()
        assert not any(message["type"] == "cancel" for message in wire)
        if operation in {"upload", "upload-begin"}:
            assert upload.file.closed
        if operation in {"upload", "upload-begin"}:
            expected = ["begin", "write", "delete"] if operation == "upload" else ["begin", "delete"]
            assert [args["action"] for op, args in env.calls if op == "integration.browser.file"] == expected
            assert next(i for i, (op, args) in enumerate(env.calls) if args.get("action") == "delete") < next(
                i for i, (op, _) in enumerate(env.calls) if op == "integration.browser.control"
            )
        else:
            await upload.close()

    env.client.portal.call(scenario)


@pytest.mark.parametrize("mismatch", ["browser", "view", "retired"])
def test_stale_takeover_offer_does_not_implicitly_accept_new_view(env, mismatch):
    async def scenario():
        old = await env.coordinator.open(env.user.id, env.instance.id, env.session.id, env.login.id, AsyncMock())
        current = await env.coordinator.open(
            env.user.id, env.instance.id, other_session(env).id, env.login.id, AsyncMock(),
            expected_browser_id=old.browser_id, expected_view_id=old.view_id,
        )
        before = list(env.calls)
        candidate = await env.coordinator.open(
            env.user.id, env.instance.id, env.session.id, env.login.id, AsyncMock(),
            expected_browser_id=uuid4() if mismatch == "browser" else current.browser_id,
            expected_view_id=uuid4() if mismatch == "view" else old.view_id if mismatch == "retired" else current.view_id,
        )
        assert candidate is current and env.coordinator.connections[env.agent.id] is current
        assert not current.closed and current.ready and env.calls == before

    env.client.portal.call(scenario)


@pytest.mark.parametrize("operation", ["copy", "upload", "choose", "download", "downloads", "resize", "navigation"])
def test_queued_old_manual_operations_fail_after_takeover_without_dispatch(env, operation):
    async def scenario():
        old = await env.coordinator.open(env.user.id, env.instance.id, env.session.id, env.login.id, AsyncMock())
        old.view_socket = AsyncMock()
        entered = asyncio.Event()
        upload = UploadFile(BytesIO(b"x"), size=1, filename="sample.txt")

        async def queued():
            entered.set()
            if operation == "copy":
                return await routes._clipboard(old, action="copy")
            if operation == "upload":
                return await routes.browser_upload(old, upload)
            if operation == "choose":
                return await routes.browser_choose(UUID(env.file_id), old)
            if operation == "download":
                return await routes.browser_download(UUID(env.file_id), old)
            if operation == "downloads":
                return await routes.browser_downloads(old)
            if operation == "resize":
                return await env.coordinator.resize(old, 800, 600)
            return await env.coordinator.navigate(old, routes.BrowserNavigationArguments(action="new"))

        await old.action_lock.acquire()
        pending = asyncio.create_task(queued())
        await asyncio.wait_for(entered.wait(), 1)
        taking_over = asyncio.create_task(env.coordinator.open(
            env.user.id, env.instance.id, other_session(env).id, env.login.id, AsyncMock(),
            expected_browser_id=old.browser_id, expected_view_id=old.view_id,
        ))
        try:
            async with asyncio.timeout(1):
                while not old.closed:
                    await asyncio.sleep(0)
        finally:
            old.action_lock.release()
        with pytest.raises((routes.ApiError, ExecutionClientError)) as error:
            await asyncio.wait_for(pending, 1)
        assert error.value.code == "browser.notConnected"
        await asyncio.wait_for(taking_over, 2)
        assert [op for op, _ in env.calls] == ["integration.browser.open", "integration.browser.control", "integration.browser.control"]
        if operation == "upload":
            assert upload.file.closed
        else:
            await upload.close()

    env.client.portal.call(scenario)


def test_requester_cancellation_drains_takeover_then_expires_without_orphan_ai(env, wire, monkeypatch):
    monkeypatch.setattr(execution, "IDLE_SECONDS", 0.02)

    async def scenario():
        started, finish = asyncio.Event(), asyncio.Event()
        old = await env.coordinator.open(env.user.id, env.instance.id, env.session.id, env.login.id, AsyncMock())
        old.view_socket = AsyncMock()
        await env.coordinator.attachment(old, True)

        async def reply(operation, _arguments):
            if operation == "integration.browser.execute":
                started.set()
                await finish.wait()

        env.reply = reply
        active = asyncio.create_task(env.coordinator.execute(env.session.id, env.agent.id, env.instance.id, {
            "action": "navigate", "tabId": str(uuid4()), "url": "https://page.test/",
            "expectedBrowserId": str(old.browser_id), "expectedAttachmentRevision": old.attachment_revision,
        }))
        await asyncio.wait_for(started.wait(), 1)
        session_b = other_session(env)
        taking_over = asyncio.create_task(env.coordinator.open(
            env.user.id, env.instance.id, session_b.id, env.login.id, AsyncMock(),
            expected_browser_id=old.browser_id, expected_view_id=old.view_id,
        ))
        try:
            async with asyncio.timeout(1):
                while not old.closed:
                    await asyncio.sleep(0)
            current = env.coordinator.connections[env.agent.id]
            taking_over.cancel()
            await asyncio.sleep(0)
            assert not taking_over.done() and not active.done()
            assert current.action_lock.locked() and not current.attached
            assert not any(message["type"] == "cancel" for message in wire)
        finally:
            finish.set()
        await asyncio.wait_for(active, 1)
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(taking_over, 2)
        assert current.websocket is None and not current.attached
        assert env.coordinator.attached_for(session_b.id, env.agent.id) is None
        with pytest.raises(ExecutionClientError, match="browser.notConnected"):
            await env.coordinator.execute(session_b.id, env.agent.id, env.instance.id, {"action": "tabs"})
        async with asyncio.timeout(1):
            while env.coordinator.connections:
                await asyncio.sleep(0.001)
        assert sum(op == "integration.browser.open" for op, _ in env.calls) == 1
        assert sum(op == "integration.browser.close" for op, _ in env.calls) == 1
        assert not any(message["type"] == "cancel" for message in wire)

    env.client.portal.call(scenario)


def test_takeover_drains_cancelled_metadata_watch_without_publishing_retired_navigation(env, wire, monkeypatch):
    monkeypatch.setattr(execution, "ACCESS_INTERVAL", 0.01)

    async def scenario():
        started, finish = asyncio.Event(), asyncio.Event()
        old = await env.coordinator.open(env.user.id, env.instance.id, env.session.id, env.login.id, AsyncMock())
        old.navigation = execution.BrowserNavigationResult.model_validate(env.navigation)
        old_watch = old.task

        async def reply(operation, _arguments):
            if operation == "integration.browser.navigation":
                started.set()
                await finish.wait()
                return {**env.navigation, "nativeChrome": True}

        env.reply = reply
        await asyncio.wait_for(started.wait(), 1)
        taking_over = asyncio.create_task(env.coordinator.open(
            env.user.id, env.instance.id, other_session(env).id, env.login.id, AsyncMock(),
            expected_browser_id=old.browser_id, expected_view_id=old.view_id,
        ))
        try:
            async with asyncio.timeout(1):
                while not old.closed:
                    await asyncio.sleep(0)
            assert not old_watch.done() and old.action_lock.locked() and not taking_over.done()
            assert not any(message["type"] == "cancel" for message in wire)
        finally:
            finish.set()
        current = await asyncio.wait_for(taking_over, 2)
        assert old_watch.cancelled() and current.ready and current.navigation is None
        assert not any(call.args[0]["type"] == "navigation" for call in old.websocket.send_json.await_args_list)
        assert not any(message["type"] == "cancel" for message in wire)

    env.client.portal.call(scenario)


def test_old_access_check_rechecks_closed_binding_after_await(env, monkeypatch):
    async def scenario():
        started, finish = asyncio.Event(), asyncio.Event()
        old = await env.coordinator.open(env.user.id, env.instance.id, env.session.id, env.login.id, AsyncMock())
        validate = execution.validate_browser_access

        async def delayed(*args, **kwargs):
            result = await validate(*args, **kwargs)
            if kwargs.get("session_id") == env.session.id:
                started.set()
                await finish.wait()
            return result

        monkeypatch.setattr(execution, "validate_browser_access", delayed)
        checking = asyncio.create_task(old.check_access())
        await asyncio.wait_for(started.wait(), 1)
        try:
            current = await env.coordinator.open(
                env.user.id, env.instance.id, other_session(env).id, env.login.id, AsyncMock(),
                expected_browser_id=old.browser_id, expected_view_id=old.view_id,
            )
        finally:
            finish.set()
        with pytest.raises(ExecutionClientError, match="browser.bindingChanged"):
            await asyncio.wait_for(checking, 1)
        assert current.ready and not current.closed

    env.client.portal.call(scenario)


def test_setup_start_remains_independent_of_takeover_and_browser_close(env, wire):
    started, finish = asyncio.Event(), asyncio.Event()

    async def reply(operation, _arguments):
        if operation == "integration.browser.setup":
            started.set()
            await finish.wait()

    env.reply = reply
    session_b = other_session(env)
    with env.client.websocket_connect(env.setup_url, headers=ORIGIN) as setup:
        setup.send_json({"type": "start"})
        env.client.portal.call(asyncio.wait_for, started.wait(), 1)
        installer = routes._setup_starts[env.agent.id]
        try:
            with env.client.websocket_connect(env.url, headers=ORIGIN) as a:
                state_a = a.receive_json()
                with env.client.websocket_connect(env.url.replace(str(env.session.id), str(session_b.id)), headers=ORIGIN) as b:
                    assert b.receive_json()["status"] == "occupied"
                    b.send_json({"type": "takeover", "browserId": state_a["browserId"], "viewId": state_a["viewId"]})
                    assert b.receive_json()["status"] == "takingOver"
                    assert b.receive_json()["type"] == "state"
                    b.send_json({"type": "close"})
                    assert b.receive()["type"] == "websocket.close"
            assert not installer.done() and not env.coordinator.connections
            assert env.agent.id in routes._setup_viewers
            assert not any(message["type"] == "cancel" for message in wire)
            assert sum(op == "integration.browser.setup" for op, _ in env.calls) == 1
            assert sum(op == "integration.browser.open" for op, _ in env.calls) == 1
        finally:
            env.client.portal.call(finish.set)
        assert setup.receive_json()["state"] == "installing"
        env.client.portal.call(asyncio.sleep, 0)
        assert not routes._setup_starts
