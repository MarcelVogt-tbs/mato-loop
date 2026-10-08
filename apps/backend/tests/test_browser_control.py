import asyncio
import threading
from concurrent.futures import CancelledError
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4
from weakref import WeakValueDictionary

import pytest
from browser_helpers import ORIGIN, open_view, other_session
from browser_helpers import env as env

import mato_loop_backend.agent.runtime as runtime_module
import mato_loop_backend.tools.access as tools_module
import mato_loop_backend.tools.catalog as mock_tools_catalog
import mato_loop_backend.tools.context as registry_module
from mato_loop_backend.agent.runtime import AgentRuntime
from mato_loop_backend.execution import browser_control as control
from mato_loop_backend.execution import browsers as execution
from mato_loop_backend.execution.client import ExecutionClient
from mato_loop_backend.integrations.registry import effective_execution_integration_settings
from mato_loop_backend.models import AgentRun
from mato_loop_backend.providers.base import ToolCall
from mato_loop_backend.settings import IntegrationSettings, ToolSettings, TrustLevel
from mato_loop_backend.tools.context import ToolContext


@pytest.fixture(params=list(TrustLevel))
def request_case(monkeypatch, request):
    case = request.getfixturevalue("env")
    trust = request.param
    run = AgentRun(
        id=uuid4(),
        session_id=case.session.id,
        status="running",
        trust_level=trust.value,
        execution_agent_id=case.agent.id,
        execution_operations=["integration.browser.execute"],
    )
    case.objects[AgentRun, run.id] = run
    settings = ToolSettings().select_trust(trust)
    integrations = IntegrationSettings(instances=[case.instance])
    effective = effective_execution_integration_settings(
        integrations, case.agent.id, {"integration.browser.execute"}
    )
    registry = mock_tools_catalog.ToolRegistry([]).effective(effective)
    assert set(registry.tools) == {"browser_request_control"}
    assert "browser_request_control" not in mock_tools_catalog.ToolRegistry([]).effective(
        effective, tool_settings=ToolSettings(tool_availability={"browser": False})
    ).tools
    factory = execution.AsyncSessionFactory

    class Db(factory):
        commit = AsyncMock()
        refresh = AsyncMock()
        scalars = AsyncMock(return_value=Mock(all=Mock(return_value=[])))

    monkeypatch.setattr(control, "AsyncSessionFactory", Db)
    monkeypatch.setattr(control, "browser_coordinator", case.coordinator)
    monkeypatch.setattr(control.browser_control_requests, "requests", {})
    monkeypatch.setattr(
        control.browser_control_requests, "preflights", WeakValueDictionary()
    )
    for module in (runtime_module, registry_module):
        monkeypatch.setattr(
            module, "load_effective_integrations", AsyncMock(return_value=integrations)
        )
    for module in (runtime_module, tools_module, control):
        monkeypatch.setattr(
            module,
            "load_agent_profile_tools",
            AsyncMock(return_value=(settings, case.profile)),
        )
    approval = AsyncMock(return_value="allow_once")
    monkeypatch.setattr(AgentRuntime, "_request_approval", approval)
    call = ToolCall(
        "request-call",
        "browser_request_control",
        {"_reason": "Read the requested website"},
    )
    context = ToolContext(
        db=Db(),
        run_id=run.id,
        session=case.session,
        tool_call=call,
        root_files=Mock(),
        integration_settings=effective,
        tool_settings=settings,
        model_trust_level=trust,
        execution_client=ExecutionClient(
            case.agent.id, frozenset(run.execution_operations)
        ),
        ask_handler=None,
    )

    async def authorize(permission, *, check_only=False):
        return await AgentRuntime._authorize(
            context.db,
            run,
            case.session,
            call,
            permission,
            context.root_files,
            context.approval_resolution,
            check_only=check_only,
        )

    context.ask_handler = authorize
    case.run, case.context, case.settings, case.approval = (
        run,
        context,
        settings,
        approval,
    )
    case.registry = registry
    tasks = []

    def start():
        future = case.client.portal.start_task_soon(registry.execute, call, context)
        tasks.append(future)
        return future

    case.start = start
    yield case
    for task in tasks:
        task.cancel()
    case.client.portal.call(asyncio.sleep, 0)


@pytest.mark.parametrize("existing", ["absent", "same-session", "other-session"])
def test_request_starts_and_attaches_without_frontend(request_case, existing):
    case = request_case
    previous = None
    old_socket = None
    if existing != "absent":
        session = case.session if existing == "same-session" else other_session(case)
        old_socket = SimpleNamespace(send_json=AsyncMock(), close=AsyncMock())
        previous = case.client.portal.call(
            case.coordinator.open,
            case.user.id,
            case.instance.id,
            session.id,
            case.login.id,
            old_socket,
        )
        case.calls.clear()
    else:
        case.session.control_center = {"tabs": []}

    result = case.start().result(timeout=3)

    assert not result.is_error
    assert case.approval.await_count == 1
    assert case.approval.await_args.args[-1].capability == "browser.control"
    assert any(
        tab.get("app_id") == "browser"
        for tab in case.session.control_center["tabs"]
    )
    connection = case.coordinator.connections[case.agent.id]
    assert connection.attached and connection.websocket is None
    assert connection.session_id == case.session.id
    assert not case.coordinator.relays
    assert not control.browser_control_requests.requests
    assert not control.browser_control_requests.preflights
    assert not any(operation == "integration.browser.view" for operation, _ in case.calls)
    if previous is None:
        assert [operation for operation, _ in case.calls[:3]] == [
            "integration.browser.open",
            "integration.browser.control",
            "integration.browser.control",
        ]
    else:
        assert previous.closed
        old_socket.close.assert_awaited()
        assert [
            arguments["manual"]
            for operation, arguments in case.calls
            if operation == "integration.browser.control"
        ] == [False, True, False]


def test_optional_viewer_connects_after_model_attachment(request_case):
    case = request_case
    assert not case.start().result(timeout=3).is_error

    with case.client.websocket_connect(case.url, headers=ORIGIN) as socket:
        state = socket.receive_json()
        assert state["type"] == "state" and state["attached"] is True
        with open_view(case, state):
            assert case.coordinator.relays
            assert case.coordinator.connections[case.agent.id].attached


def test_browser_control_without_reason_keeps_normal_approval(request_case):
    case = request_case
    case.context.tool_call.arguments.clear()

    assert not case.start().result(timeout=3).is_error
    assert case.approval.await_count == 1


def test_denial_never_opens_browser(request_case):
    case = request_case
    case.approval.return_value = "deny"

    result = case.start().result(timeout=3)

    assert result.metadata["errorCode"] == "permission.denied"
    assert not case.calls
    assert not case.coordinator.connections


def test_request_reports_disconnected_execution_agent(request_case, monkeypatch):
    case = request_case
    monkeypatch.setattr(
        control.execution_agent_connections, "generation", lambda _agent_id: None
    )

    result = case.start().result(timeout=3)

    assert result.metadata["errorCode"] == "executionAgent.unavailable"
    case.approval.assert_not_awaited()
    assert not case.calls


@pytest.mark.parametrize("phase", ["startup", "attachment"])
def test_request_timeout_closes_incomplete_binding(request_case, monkeypatch, phase):
    case = request_case
    monkeypatch.setattr(control, "CONTROL_TIMEOUT", 0.01)

    async def reply(operation, arguments):
        if (phase == "startup" and operation == "integration.browser.open") or (
            phase == "attachment" and operation == "integration.browser.control" and arguments["manual"] is False
        ):
            await asyncio.sleep(1)

    case.reply = reply
    result = case.start().result(timeout=3)

    assert result.metadata["errorCode"] == "browser.controlTimeout"
    assert not case.coordinator.connections
    assert [
        (call.kwargs["payload"]["reason"], call.kwargs["payload"]["errorCode"])
        for call in case.diagnostics.call_args_list if call.args[1] == "browser.closed"
    ] == [("controlTimeout", "browser.controlTimeout")]


def test_fresh_start_revalidates_before_manual_control(request_case):
    case = request_case

    async def reply(operation, _arguments):
        if operation == "integration.browser.open":
            case.run.status = "cancelled"

    case.reply = reply
    result = case.start().result(timeout=3)

    assert result.is_error
    assert [operation for operation, _ in case.calls] == [
        "integration.browser.open",
        "integration.browser.close",
    ]
    assert not case.coordinator.connections


@pytest.mark.parametrize(
    "change", ["integration", "generation", "run", "session", "policy", "profile"]
)
def test_changes_during_approval_prevent_start(request_case, change):
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
            case.settings.permissions.integration_policies.setdefault(str(case.instance.id), {})["browser.control"] = "disabled"
        else:
            case.profile.integration_availability[str(case.instance.id)] = False
        return "allow_once"

    case.approval.side_effect = approve

    result = case.start().result(timeout=3)

    assert result.is_error
    assert not case.calls
    assert not case.coordinator.connections


@pytest.mark.parametrize("change", ["policy", "run", "generation", "cancel"])
def test_final_control_rpc_cannot_leave_latent_attachment(request_case, change):
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
    assert started.wait(3)
    if change == "policy":
        case.settings.permissions.integration_policies.setdefault(str(case.instance.id), {})["browser.control"] = "disabled"
    elif change == "run":
        case.run.status = "cancelled"
    elif change == "generation":
        case.generation += 1
    else:
        future.cancel()
    case.client.portal.call(release.set)

    if change != "cancel":
        assert future.result(timeout=3).is_error
    else:
        with pytest.raises(CancelledError):
            future.result(timeout=3)

        async def wait_for_cleanup():
            async with asyncio.timeout(3):
                while control.browser_control_requests.requests:
                    await asyncio.sleep(0)

        case.client.portal.call(wait_for_cleanup)
    connection = case.coordinator.connections.get(case.agent.id)
    assert connection is None or not connection.attached
    assert not control.browser_control_requests.requests


@pytest.mark.parametrize(
    "restriction", ["child-session", "child-run", "automation", "operations"]
)
def test_request_preflight_rejects_non_live_context(request_case, restriction):
    case = request_case
    if restriction == "child-session":
        case.session.parent_session_id = uuid4()
    elif restriction == "child-run":
        case.run.parent_run_id = uuid4()
    elif restriction == "automation":
        case.session.session_type = "automation"
    else:
        case.run.execution_operations = []

    result = case.start().result(timeout=3)

    assert result.is_error
    case.approval.assert_not_awaited()
    assert not case.calls
