import asyncio
import gc
import threading
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4
from weakref import WeakValueDictionary, ref

import pytest
from test_browser_transport import ORIGIN, env, open_view, other_session  # noqa: F401

from mato_loop_backend.agent import runtime as runtime_module
from mato_loop_backend.agent import tool_registry as registry_module
from mato_loop_backend.agent import tools as tools_module
from mato_loop_backend.agent.permissions import PermissionDecision
from mato_loop_backend.agent.runtime import AgentRuntime
from mato_loop_backend.agent.tool_registry import ToolContext
from mato_loop_backend.execution import browser_control as control
from mato_loop_backend.execution import browsers as execution
from mato_loop_backend.execution.client import ExecutionClient
from mato_loop_backend.integrations.registry import effective_execution_integration_settings
from mato_loop_backend.models import AgentRun
from mato_loop_backend.providers.base import ToolCall
from mato_loop_backend.settings import IntegrationSettings, ToolSettings, TrustLevel


@pytest.fixture(params=list(TrustLevel))
def request_case(monkeypatch, request):
    case = request.getfixturevalue("env")
    trust = request.param
    run = AgentRun(id=uuid4(), session_id=case.session.id, status="running", trust_level=trust.value,
                   execution_agent_id=case.agent.id, execution_operations=["integration.browser.execute"])
    case.objects[AgentRun, run.id] = run
    settings = ToolSettings().select_trust(trust)
    integrations = IntegrationSettings(instances=[case.instance])
    effective = effective_execution_integration_settings(integrations, case.agent.id, {"integration.browser.execute"})
    registry = tools_module.ToolRegistry([]).effective(effective)
    assert set(registry.tools) == {"browser_request_control"}
    assert "browser_request_control" not in tools_module.ToolRegistry([]).effective(
        effective, tool_settings=ToolSettings(tool_availability={"browser": False}),
    ).tools
    factory = execution.AsyncSessionFactory

    class Db(factory):
        commit = AsyncMock()
        refresh = AsyncMock()
        scalars = AsyncMock(return_value=Mock(all=Mock(return_value=[])))

    monkeypatch.setattr(control, "AsyncSessionFactory", Db)
    monkeypatch.setattr(control, "browser_coordinator", case.coordinator)
    monkeypatch.setattr(control.browser_control_requests, "requests", {})
    monkeypatch.setattr(control.browser_control_requests, "preflights", WeakValueDictionary())
    for module in (runtime_module, registry_module):
        monkeypatch.setattr(module, "load_effective_integrations", AsyncMock(return_value=integrations))
    for module in (runtime_module, tools_module, control):
        monkeypatch.setattr(module, "load_agent_profile_tools", AsyncMock(return_value=(settings, case.profile)))
    approval = AsyncMock(return_value="allow_once")
    monkeypatch.setattr(AgentRuntime, "_request_approval", approval)
    published = threading.Event()
    async def publish(*_args):
        published.set()
    monkeypatch.setattr(control.run_events, "publish", AsyncMock(side_effect=publish))
    call = ToolCall("request-call", "browser_request_control", {"reason": "Read the requested website"})
    context = ToolContext(
        db=Db(), run_id=run.id, session=case.session, tool_call=call, root_files=Mock(),
        integration_settings=effective, tool_settings=settings, model_trust_level=trust,
        execution_client=ExecutionClient(case.agent.id, frozenset(run.execution_operations)), ask_handler=None,
    )
    async def authorize(permission, *, check_only=False):
        return await AgentRuntime._authorize(context.db, run, case.session, call, permission, context.root_files,
                                             context.approval_resolution, check_only=check_only)
    context.ask_handler = authorize
    case.run, case.context, case.settings, case.approval = run, context, settings, approval
    case.registry, case.published = registry, published
    case.get_request = lambda: case.client.get(f"/browsers/control-requests?sessionId={case.session.id}")
    tasks = []
    def start(*, authorized=True):
        published.clear()
        future = case.client.portal.start_task_soon(registry.execute, call, context)
        tasks.append(future)
        if authorized:
            assert published.wait(3), future.result(timeout=1) if future.done() else "No request event"
        return future
    case.start = start
    yield case
    for task in tasks:
        task.cancel()
    case.client.portal.call(asyncio.sleep, 0)


def connect_request(case, info):
    return case.client.websocket_connect(case.url + f"&controlRequestId={info['id']}", headers=ORIGIN)


def receive_state(socket):
    while True:
        message = socket.receive_json()
        if message["type"] != "availability":
            return message


@pytest.mark.parametrize("existing", ["absent", "same-session", "other-session"])
def test_request_opens_or_takes_over_in_background(request_case, existing):
    case = request_case
    previous = None
    old_socket = None
    if existing != "absent":
        session = case.session if existing == "same-session" else other_session(case)
        old_socket = SimpleNamespace(send_json=AsyncMock(), close=AsyncMock())
        previous = case.client.portal.call(case.coordinator.open, case.user.id, case.instance.id,
                                           session.id, case.login.id, old_socket)
    else:
        case.session.control_center = {"tabs": []}
    future = case.start()
    case.approval.assert_awaited_once()
    assert case.approval.await_args.args[-1].capability == "browser.control"
    info = case.get_request().json()["request"]
    assert set(info) == {"id", "sessionId", "integrationId", "agentId", "runId"}
    assert info["sessionId"] == str(case.session.id)
    assert any(tab.get("app_id") == "browser" for tab in case.session.control_center["tabs"])
    assert not future.done()
    if previous:
        assert not previous.closed
        old_socket.close.assert_not_awaited()
    case.session.control_center = {"tabs": [{"app_id": "browser"}]}
    with connect_request(case, info) as socket:
        state = receive_state(socket)
        assert state["type"] == "state" and not state["attached"]
        if previous:
            assert state["browserId"] == str(previous.browser_id)
            assert state["viewId"] != str(previous.view_id)
        socket.send_json({"type": "attach.requested", "requestId": info["id"]})
        assert socket.receive_json()["attached"] is True
        assert not future.result(timeout=3).is_error
        assert case.get_request().json() == {"request": None}
        assert not control.browser_control_requests.preflights
        assert case.coordinator.connections[case.agent.id].attached
        assert not any(operation == "integration.browser.view" for operation, _ in case.calls)
    assert [operation for operation, _ in case.calls].count("integration.browser.open") == 1


def test_deny_never_publishes_or_opens(request_case):
    case = request_case
    case.approval.return_value = "deny"
    result = case.start(authorized=False).result(timeout=3)
    assert result.metadata["errorCode"] == "permission.denied"
    assert case.get_request().json() == {"request": None}
    assert not case.calls and not case.published.is_set()


@pytest.mark.parametrize("change", ["integration", "generation", "run", "session", "policy", "legacy", "profile"])
def test_changes_during_approval_never_publish(request_case, change):
    case = request_case
    async def approve(*_args, **_kwargs):
        if change == "integration":
            case.instance.name = "Changed"
        elif change == "generation":
            case.generation += 1
        elif change == "run":
            case.run.status = "cancelled"
        elif change == "session":
            case.session.preferred_execution_agent_id = uuid4()
        elif change == "policy":
            case.settings.permissions.policies["browser.control"] = "disabled"
        elif change == "legacy":
            case.settings.tool_availability["browser"] = False
        else:
            case.profile.integration_availability[str(case.instance.id)] = False
        return "allow_once"
    case.approval.side_effect = approve
    result = case.start(authorized=False).result(timeout=3)
    assert result.is_error
    assert not case.calls and not case.published.is_set()


@pytest.mark.parametrize("change", ["generation", "policy", "run", "timeout", "cancel"])
def test_waiter_is_bounded_and_never_leaves_latent_authority(request_case, monkeypatch, change):
    case = request_case
    if change == "timeout":
        monkeypatch.setattr(control, "CONTROL_TIMEOUT", 0.05)
    future = case.start()
    info = case.get_request().json()["request"]
    if change == "generation":
        case.generation += 1
    elif change == "policy":
        case.settings.permissions.policies["browser.control"] = "disabled"
    elif change == "run":
        case.run.status = "cancelled"
    elif change == "cancel":
        future.cancel()
        case.client.portal.call(asyncio.sleep, 0.01)
    if change not in {"timeout", "cancel"}:
        assert case.get_request().json() == {"request": None}
    if change != "cancel":
        result = future.result(timeout=3)
        assert result.is_error
        if change == "timeout":
            assert result.metadata["errorCode"] == "browser.controlTimeout"
    assert case.get_request().json() == {"request": None}
    with connect_request(case, info) as socket:
        assert receive_state(socket)["type"] == "error"
    assert not case.calls


@pytest.mark.parametrize("action", ["detach", "close", "disconnect", "policy", "duplicate", "forged"])
def test_claim_and_attach_fences(request_case, action):
    case = request_case
    future = case.start()
    info = case.get_request().json()["request"]
    with connect_request(case, info) as socket:
        state = receive_state(socket)
        if action == "duplicate":
            with connect_request(case, info) as second:
                assert receive_state(second)["type"] == "error"
            assert not future.done()
            socket.send_json({"type": "detach"})
            socket.receive_json()
        elif action == "disconnect":
            socket.close()
        elif action in {"detach", "close"}:
            socket.send_json({"type": action})
            if action == "detach":
                socket.receive_json()
                socket.receive_json()
                socket.send_json({"type": "attach.requested", "requestId": info["id"]})
                assert socket.receive_json()["type"] == "error"
        else:
            with open_view(case, state):
                if action == "policy":
                    case.settings.permissions.policies["browser.control"] = "disabled"
                socket.send_json({"type": "attach.requested", "requestId": str(uuid4()) if action == "forged" else info["id"]})
                assert socket.receive_json()["type"] == "error"
    assert future.result(timeout=3).is_error
    assert case.get_request().json() == {"request": None}
    assert not any(arguments.get("manual") is False for _, arguments in case.calls)


def test_owner_checked_and_unapproved_request_is_not_visible(request_case):
    case = request_case
    assert case.get_request().json() == {"request": None}
    case.session.user_id = uuid4()
    assert case.get_request().status_code == 404


def test_control_once_proof_is_bound_to_operation(request_case):
    case = request_case
    future = case.start()
    request = next(iter(control.browser_control_requests.requests.values()))
    async def check():
        assert await case.context.ask_handler(request.permission, check_only=True) == PermissionDecision.ALLOW
        altered = replace(request.permission, resource_scope=request.permission.resource_scope.replace('operationKey', 'differentKey'))
        assert await case.context.ask_handler(altered, check_only=True) != PermissionDecision.ALLOW
    case.client.portal.call(check)
    future.cancel()


@pytest.mark.parametrize("change", ["new-browser", "open-close", "detach", "takeover"])
def test_approval_cannot_retarget_changed_browser_binding(request_case, change):
    case = request_case
    socket = SimpleNamespace(send_json=AsyncMock(), close=AsyncMock())
    previous = None
    if change in {"detach", "takeover"}:
        previous = case.client.portal.call(case.coordinator.open, case.user.id, case.instance.id,
                                           case.session.id, case.login.id, socket)
    async def approve(*_args, **_kwargs):
        if change == "detach":
            previous.detach()
        elif change == "takeover":
            await case.coordinator.open(case.user.id, case.instance.id, case.session.id, case.login.id,
                                        SimpleNamespace(send_json=AsyncMock(), close=AsyncMock()),
                                        expected_browser_id=previous.browser_id, expected_view_id=previous.view_id)
        else:
            connection = await case.coordinator.open(case.user.id, case.instance.id, case.session.id, case.login.id, socket)
            if change == "open-close":
                await case.coordinator.close(connection)
        return "allow_once"
    case.approval.side_effect = approve
    result = case.start(authorized=False).result(timeout=3)
    assert result.metadata["errorCode"] == "browser.bindingChanged"
    assert not case.published.is_set()


@pytest.mark.parametrize("change", ["policy", "detach", "close", "run", "generation"])
def test_final_attachment_rpc_revalidates_before_success(request_case, change):
    case = request_case
    started = threading.Event()
    release = asyncio.Event()
    async def reply(operation, arguments):
        if operation == "integration.browser.control" and arguments.get("manual") is False:
            started.set()
            await release.wait()
        return None
    case.reply = reply
    future = case.start()
    info = case.get_request().json()["request"]
    with connect_request(case, info) as socket:
        state = receive_state(socket)
        with open_view(case, state):
            socket.send_json({"type": "attach.requested", "requestId": info["id"]})
            try:
                assert started.wait(3)
                if change in {"detach", "close"}:
                    socket.send_json({"type": change})
                    # Cancellation is consumed even though attachment still owns the action lock.
                    assert future.result(timeout=3).is_error
                elif change == "policy":
                    case.settings.permissions.policies["browser.control"] = "disabled"
                elif change == "run":
                    case.run.status = "cancelled"
                elif change == "generation":
                    case.generation += 1
            finally:
                case.client.portal.call(release.set)
            assert future.result(timeout=3).is_error
            connection = case.coordinator.connections.get(case.agent.id)
            assert connection is None or not connection.attached
    assert case.get_request().json() == {"request": None}


def test_requested_attachment_does_not_require_a_viewer(request_case):
    case = request_case
    future = case.start()
    info = case.get_request().json()["request"]
    with connect_request(case, info) as socket:
        assert receive_state(socket)["type"] == "state"
        socket.send_json({"type": "attach.requested", "requestId": info["id"]})
        assert socket.receive_json()["attached"] is True
        assert not future.result(timeout=3).is_error
        assert case.coordinator.connections[case.agent.id].attached
        assert not case.coordinator.relays


@pytest.mark.parametrize("restriction", ["child-session", "child-run", "automation", "operations"])
def test_request_preflight_rejects_non_live_top_level_context(request_case, restriction):
    case = request_case
    if restriction == "child-session":
        case.session.parent_session_id = uuid4()
    elif restriction == "child-run":
        case.run.parent_run_id = uuid4()
    elif restriction == "automation":
        case.session.session_type = "automation"
    else:
        case.run.execution_operations = []
    result = case.start(authorized=False).result(timeout=3)
    assert result.is_error
    case.approval.assert_not_awaited()
    assert not case.calls


@pytest.mark.parametrize("close", [False, True])
def test_persisted_browser_tab_close_cancels_even_before_socket_claim(request_case, close):
    from mato_loop_backend.api.routes.sessions import SessionUpdateRequest
    from mato_loop_backend.sessions.service import update_session

    case = request_case
    future = case.start()
    info = case.get_request().json()["request"]
    async def update():
        payload = SessionUpdateRequest(control_center={
            "tabs": [] if close else [{"app_id": "browser", "pinned": False}], "active_app_id": None,
        })
        await update_session(case.session.id, payload, case.user, AsyncMock(), storage_factory=Mock(),
                             owned_session_query=AsyncMock(return_value=case.session), session_serializer=AsyncMock())
    case.client.portal.call(update)
    if close:
        assert future.result(timeout=3).is_error
        case.session.control_center = {"tabs": [{"app_id": "browser"}]}
        with connect_request(case, info) as socket:
            assert receive_state(socket)["type"] == "error"
        assert not case.calls
    else:
        assert not future.done()
        assert case.get_request().json()["request"] == info
        future.cancel()


@pytest.mark.parametrize("stage", ["before-attachment", "during-attachment"])
def test_auxiliary_controls_cannot_delay_detach_or_race_requested_attachment(request_case, stage):
    case = request_case
    started, release = threading.Event(), asyncio.Event()
    async def reply(operation, arguments):
        if operation == "integration.browser.control" and arguments.get("manual") is False:
            started.set()
            await release.wait()
    case.reply = reply
    future = case.start()
    info = case.get_request().json()["request"]
    with connect_request(case, info) as socket:
        state = receive_state(socket)
        with open_view(case, state):
            try:
                if stage == "during-attachment":
                    socket.send_json({"type": "attach.requested", "requestId": info["id"]})
                    assert started.wait(3)
                socket.send_json({"type": "navigation", "action": "state"})
                socket.send_json({"type": "resize", "width": 1280, "height": 720})
                socket.send_json({"type": "detach"})
                assert future.result(timeout=3).is_error
                for operation in ("navigation", "resize"):
                    message = socket.receive_json()
                    assert (message["type"], message["operation"], message["error"]["code"]) == (
                        "error", operation, "browser.busy",
                    )
                assert not any(op in {"integration.browser.navigation", "integration.browser.resize"} for op, _ in case.calls)
            finally:
                case.client.portal.call(release.set)
            scoped = stage == "before-attachment"
            reset = unlocked = stage == "before-attachment"
            while True:
                message = socket.receive_json()
                if message["type"] == "error":
                    assert message["operation"] == "control"
                    assert message["error"]["code"] == "browser.notConnected"
                    scoped = True
                if message["type"] == "view.reset":
                    reset = True
                if reset and message["type"] == "state" and not message["busy"]:
                    unlocked = True
                if scoped and unlocked:
                    break
            connection = case.coordinator.connections[case.agent.id]
            assert connection.manual_enabled and case.manual and not connection.attached
            assert not connection.closed and connection.websocket is not None
            socket.send_json({"type": "navigation", "action": "state"})
            while (message := socket.receive_json())["type"] != "navigation":
                assert message["type"] == "state"


def test_run_cancellation_reports_scoped_control_error_and_recovers_manual_input(request_case, monkeypatch):
    case = request_case
    monkeypatch.setattr(execution, "ACCESS_INTERVAL", 0.01)
    started, release = threading.Event(), asyncio.Event()
    async def reply(operation, arguments):
        if operation == "integration.browser.control" and arguments.get("manual") is False:
            started.set()
            await release.wait()
    case.reply = reply
    future = case.start()
    info = case.get_request().json()["request"]
    with connect_request(case, info) as socket:
        state = receive_state(socket)
        with open_view(case, state):
            socket.send_json({"type": "attach.requested", "requestId": info["id"]})
            try:
                assert started.wait(3)
                case.run.status = "cancelled"
            finally:
                case.client.portal.call(release.set)
            assert future.result(timeout=3).is_error
            scoped = reset = unlocked = False
            while True:
                message = socket.receive_json()
                if message["type"] == "error":
                    assert message["operation"] == "control"
                    scoped = True
                if message["type"] == "view.reset":
                    reset = True
                if reset and message["type"] == "state" and not message["busy"]:
                    unlocked = True
                if scoped and unlocked:
                    break
            connection = case.coordinator.connections[case.agent.id]
            assert connection.manual_enabled and case.manual and not connection.attached and not connection.closed


@pytest.mark.parametrize("before", [False, True])
def test_requested_attachment_serializes_auxiliary_message_bursts(request_case, before):
    case = request_case
    future = case.start()
    info = case.get_request().json()["request"]
    with connect_request(case, info) as socket:
        state = receive_state(socket)
        with open_view(case, state):
            attach = {"type": "attach.requested", "requestId": info["id"]}
            auxiliary = [{"type": "navigation", "action": "state"}, {"type": "resize", "width": 1280, "height": 720}]
            for message in [*auxiliary, attach] if before else [attach, *auxiliary]:
                socket.send_json(message)
            while True:
                message = socket.receive_json()
                if message["type"] == "state" and message["attached"]:
                    break
                if message["type"] == "error":
                    assert message["operation"] in {"navigation", "resize"}
                    assert message["error"]["code"] == "browser.busy"
            assert not future.result(timeout=3).is_error


def test_watch_does_not_revoke_attachment_during_final_authorization(request_case, monkeypatch):
    case = request_case
    monkeypatch.setattr(execution, "ACCESS_INTERVAL", 0.01)
    checking, release = threading.Event(), asyncio.Event()
    validate = control.ControlRequest.validate
    async def paused_validate(request, **kwargs):
        connection = request.connection
        if connection is not None and not connection.manual_enabled and not connection.attached:
            checking.set()
            await release.wait()
        await validate(request, **kwargs)
    monkeypatch.setattr(control.ControlRequest, "validate", paused_validate)
    future = case.start()
    info = case.get_request().json()["request"]
    with connect_request(case, info) as socket:
        state = receive_state(socket)
        with open_view(case, state):
            socket.send_json({"type": "attach.requested", "requestId": info["id"]})
            try:
                assert checking.wait(3)
                case.client.portal.call(asyncio.sleep, 0.05)
            finally:
                case.client.portal.call(release.set)
            assert not future.result(timeout=3).is_error
            assert socket.receive_json()["attached"] is True


@pytest.mark.parametrize("cancel", ["run", "task"])
def test_cancelled_queued_takeover_never_dispatches_control_rpc(request_case, cancel):
    case = request_case
    old_socket = SimpleNamespace(send_json=AsyncMock(), close=AsyncMock())
    previous = case.client.portal.call(case.coordinator.open, case.user.id, case.instance.id,
                                       case.session.id, case.login.id, old_socket)
    previous.attached = True
    case.client.portal.call(previous.action_lock.acquire)
    future = case.start()
    info = case.get_request().json()["request"]
    with connect_request(case, info) as socket:
        try:
            assert socket.receive_json()["status"] == "takingOver"
            assert previous.closed and not previous.attached
            if cancel == "run":
                case.run.status = "cancelled"
            else:
                future.cancel()
        finally:
            case.client.portal.call(previous.action_lock.release)
        assert socket.receive_json()["type"] == "error"
    if cancel == "run":
        assert future.result(timeout=3).is_error
    assert [operation for operation, _ in case.calls] == ["integration.browser.open", "integration.browser.close"]
    assert not case.coordinator.connections
    assert previous.closed and not previous.attached
    old_socket.close.assert_awaited()
    assert case.get_request().json() == {"request": None}


def test_cancellation_before_fresh_start_never_dispatches_open_rpc(request_case, monkeypatch):
    case = request_case
    entered, release = threading.Event(), asyncio.Event()
    start = case.coordinator._start
    async def delayed_start(connection, previous, request, color_scheme="light"):
        entered.set()
        await release.wait()
        return await start(connection, previous, request, color_scheme)
    monkeypatch.setattr(case.coordinator, "_start", delayed_start)
    future = case.start()
    info = case.get_request().json()["request"]
    with connect_request(case, info) as socket:
        try:
            assert entered.wait(3)
            case.run.status = "cancelled"
        finally:
            case.client.portal.call(release.set)
        assert socket.receive_json()["type"] == "error"
    assert future.result(timeout=3).is_error
    assert not any(operation == "integration.browser.open" for operation, _ in case.calls)
    assert not case.coordinator.connections


@pytest.mark.parametrize("cancel_at", ["after-first-rpc", "during-replaced"])
def test_takeover_revalidates_between_fences_before_next_rpc(request_case, cancel_at):
    case = request_case
    async def old_send(message):
        if message["type"] == "replaced" and cancel_at == "during-replaced":
            case.run.status = "cancelled"
    old_socket = SimpleNamespace(send_json=AsyncMock(side_effect=old_send), close=AsyncMock())
    previous = case.client.portal.call(case.coordinator.open, case.user.id, case.instance.id,
                                       case.session.id, case.login.id, old_socket)
    async def reply(operation, arguments):
        if operation == "integration.browser.control" and arguments.get("manual") is False and cancel_at == "after-first-rpc":
            case.run.status = "cancelled"
    case.reply = reply
    future = case.start()
    info = case.get_request().json()["request"]
    with connect_request(case, info) as socket:
        assert receive_state(socket)["type"] == "error"
    assert future.result(timeout=3).is_error
    assert [arguments["manual"] for operation, arguments in case.calls if operation == "integration.browser.control"] == [False]
    assert previous.closed and not previous.attached and not case.coordinator.connections


def test_preflight_invalidation_is_agent_local_and_does_not_retain_denied_contexts(request_case):
    case = request_case
    case.approval.return_value = "deny"
    assert case.start(authorized=False).result(timeout=3).is_error
    manager = control.browser_control_requests
    request = case.context.bound_options["control_request"]
    other = replace(request, info=request.info.model_copy(update={"id": uuid4(), "agent_id": uuid4()}), done=asyncio.Event())
    manager.preflights[other.info.id] = other
    manager.invalidate_agent(other.info.agent_id)
    assert other.done.is_set() and not request.done.is_set()
    assert list(manager.preflights) == [request.info.id]
    observed = ref(request)
    del request
    case.context.bound_options.clear()
    case.client.portal.call(gc.collect)
    assert observed() is None
    assert not manager.preflights
    assert not hasattr(case.coordinator, "binding_revisions")
