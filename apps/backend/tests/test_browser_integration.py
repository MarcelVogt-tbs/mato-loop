import base64
import hashlib
import json
import sys
from io import BytesIO
from types import SimpleNamespace
from typing import get_args
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image
from pydantic import ValidationError

from mato_loop_backend.agent import tool_registry as registry_module
from mato_loop_backend.agent import tools as tools_module
from mato_loop_backend.agent.permissions import PermissionDecision
from mato_loop_backend.agent.runtime import _effective_execution_operations
from mato_loop_backend.agent.tool_registry import ToolContext
from mato_loop_backend.api.errors import register_error_handlers
from mato_loop_backend.api.routes import browser_integrations as routes
from mato_loop_backend.db.session import get_db_session
from mato_loop_backend.error_contract import describe_error
from mato_loop_backend.execution.browser_contracts import (
    BROWSER_SNAPSHOT_ACTIONS,
    BrowserActionArguments,
    BrowserActionResult,
    BrowserConfig,
    BrowserErrorCode,
    BrowserExecuteArguments,
    BrowserFileArguments,
    BrowserFileResult,
    BrowserInspectionData,
    BrowserSetupReadResult,
    BrowserSetupWriteArguments,
    BrowserStatusResult,
)
from mato_loop_backend.execution.client import ExecutionClient, ExecutionClientError
from mato_loop_backend.execution.manager import ExecutionAgentError, disconnect_error_code
from mato_loop_backend.execution.protocol import SUPPORTED_OPERATIONS, ResponseMessage
from mato_loop_backend.integrations import browser as browser_module
from mato_loop_backend.integrations.browser import BROWSER_ADAPTER, BROWSER_TOOL
from mato_loop_backend.integrations.registry import (
    effective_execution_integration_settings,
    effective_integration_contributions,
)
from mato_loop_backend.models import ChatSession
from mato_loop_backend.providers.base import ToolCall
from mato_loop_backend.security import require_user
from mato_loop_backend.settings import (
    AgentProfile,
    IntegrationInstance,
    IntegrationSettings,
    SettingsStore,
    ToolSettings,
    UserIntegrationSettings,
    UserToolSettings,
    load_effective_integrations,
)
from mato_loop_backend.settings.effective import filter_agent_profile_integrations


def _instance():
    return IntegrationInstance(
        name="Browser", integration_type="browser", adapter_type="browser",
        execution_agent_id=uuid4(), owner_id=uuid4(),
    )


@pytest.mark.parametrize("payload", [
    {"action": "eval", "text": "process.env"},
    {"action": "setup"}, {"action": "navigate", "url": "https://example.org"},
    {"action": "fill", "tabId": str(uuid4()), "selector": "input"},
    {"action": "upload", "tabId": str(uuid4()), "selector": "input", "uploadId": "/etc/passwd"},
    {"action": "tabs", "allowNetwork": True}, {"action": "tabs", "browserId": str(uuid4())},
    {"action": "tabs", "expectedBrowserId": str(uuid4())},
    {"action": "click", "tabId": str(uuid4()), "selector": "button", "expectedSnapshot": "a" * 64},
    {"action": "tabs", "text": "unexpected"}, {"action": "tabs", "profilePath": "/tmp"},
    {"action": "tabs", "tabId": "../profile"},
    {"action": "press", "tabId": str(uuid4()), "key": 123},
])
def test_model_action_boundary_rejects_invalid_or_host_only_fields(payload):
    with pytest.raises(ValidationError):
        BrowserActionArguments.model_validate(payload)


@pytest.mark.parametrize("url", [
    "file:///etc/passwd", "javascript:alert(1)", "data:text/html,hi", "about:blank",
    "https://user:password@example.org", "https://example.org\\@evil.test",
    "https://example.org\n", "https://example.org:99999", "https://",
])
def test_navigation_is_http_only_without_credentials_or_control_characters(url):
    with pytest.raises(ValidationError):
        BrowserActionArguments(action="navigate", tabId=str(uuid4()), url=url)


def test_config_empty_action_requirements_and_wire_host_fields():
    assert BrowserConfig().model_dump() == {}
    with pytest.raises(ValidationError):
        BrowserConfig(profilePath="/tmp/profile")
    args = BrowserActionArguments(action="fill", tabId=str(uuid4()), selector="input", text="")
    assert args.text == ""
    assert BrowserActionArguments(action="newtab").tab_id is None
    wire = BrowserExecuteArguments(browserId=str(uuid4()), allowNetwork=False, **args.model_dump())
    assert wire.model_dump()["allowNetwork"] is False
    schema = BROWSER_TOOL.arguments_model.model_json_schema()
    assert schema["additionalProperties"] is False
    assert not {"browserId", "ownerId", "allowNetwork", "profilePath", "command", "script"} & schema["properties"].keys()
    assert BROWSER_ADAPTER.personal_only


@pytest.mark.parametrize("payload", [
    {"text": "x" * 32769}, {"tabs": [{"id": str(uuid4()), "url": "a", "title": "b", "secret": "x"}]},
    {"screenshot": "not base64"}, {"screenshot": base64.b64encode(b"not png").decode()},
    {"data": {"large": "x" * 32769}}, {"data": {"nan": float("nan")}},
    {"downloads": [{"id": str(uuid4()), "filename": "../secret", "size": 1}]},
    {"downloads": [{"id": str(uuid4()), "filename": "secret\n", "size": 1}]},
    {"tabs": [], "unbounded": {}},
])
def test_bounded_action_result(payload):
    with pytest.raises(ValidationError):
        BrowserActionResult.model_validate(payload)


def test_setup_and_file_contracts_are_bounded():
    with pytest.raises(ValidationError):
        BrowserSetupWriteArguments(setupId=str(uuid4()), data=base64.b64encode(b"x" * 65537).decode())
    with pytest.raises(ValidationError):
        BrowserSetupReadResult(data="!", offset=0, running=True)
    with pytest.raises(ValidationError):
        BrowserStatusResult(state="ready", errorCode="private.secret")
    with pytest.raises(ValidationError):
        BrowserFileArguments(browserId=str(uuid4()), action="begin", filename="../x", size=1)
    with pytest.raises(ValidationError):
        BrowserFileResult(fileId=str(uuid4()), offset=2, size=1, complete=True)


def test_browser_schema_requires_live_binding_and_preserves_profile_exclusion():
    instance = _instance()
    settings = IntegrationSettings(instances=[instance])
    assert not effective_integration_contributions(settings).tools
    assert not effective_integration_contributions(effective_execution_integration_settings(
        settings, instance.execution_agent_id, {"integration.browser.execute"},
    )).tools
    active = effective_execution_integration_settings(
        settings, instance.execution_agent_id, {"integration.browser.execute"}, browser_integration_id=instance.id,
    )
    assert [tool.name for tool in effective_integration_contributions(active).tools] == ["browser"]
    excluded = filter_agent_profile_integrations(active, None, allowed_ids=[])
    assert not effective_integration_contributions(excluded).tools
    for agent, operations in [(uuid4(), {"integration.browser.execute"}), (instance.execution_agent_id, set())]:
        assert not effective_integration_contributions(effective_execution_integration_settings(
            settings, agent, operations, browser_integration_id=instance.id,
        )).tools
    assert _effective_execution_operations(SimpleNamespace(
        execution_operations=["integration.browser.execute", "filesystem.read"], current_directory_snapshot=None,
    )) == {"integration.browser.execute"}
    with pytest.raises(ValidationError):
        UserIntegrationSettings(instances=[instance, instance.model_copy(update={"id": uuid4()})])


@pytest.fixture
def browser_case(monkeypatch):
    instance = _instance()
    settings = IntegrationSettings(instances=[instance])
    settings.instances[0]._browser_attached = True
    session = ChatSession(id=uuid4(), user_id=instance.owner_id)
    connection = SimpleNamespace(
        browser_id=uuid4(), attachment_revision=1, integration_id=instance.id,
        agent_id=instance.execution_agent_id, owner_id=instance.owner_id,
        session_id=session.id, attached=True, closed=False,
    )
    coordinator = SimpleNamespace(
        attached_for=Mock(return_value=connection), execute=AsyncMock(return_value=BrowserActionResult()),
        snapshot=AsyncMock(return_value="a" * 64),
    )
    # The parent owns the coordinator; this test exercises only its published interface.
    monkeypatch.setitem(sys.modules, "mato_loop_backend.execution.browsers", SimpleNamespace(browser_coordinator=coordinator))
    current = AsyncMock(return_value=settings)
    monkeypatch.setattr(ToolContext, "current_integrations", current)
    monkeypatch.setattr(tools_module, "_profile_tool_settings", AsyncMock(return_value=ToolSettings()))
    call = ToolCall("browser-call", "browser", {"action": "navigate", "tabId": str(uuid4()), "url": "https://example.org"})
    context = ToolContext(
        db=AsyncMock(), run_id=uuid4(), session=session, tool_call=call,
        root_files=Mock(), integration_settings=settings, ask_handler=AsyncMock(return_value=PermissionDecision.ALLOW),
        execution_client=ExecutionClient(instance.execution_agent_id, frozenset({"integration.browser.execute"})),
    )
    context.db.get.return_value = SimpleNamespace(execution_agent_id=instance.execution_agent_id, execution_operations=["integration.browser.execute"])
    effective = tools_module.ToolRegistry([]).effective(settings)
    return SimpleNamespace(
        instance=instance, settings=settings, connection=connection, coordinator=coordinator,
        current=current, context=context, call=call, effective=effective,
    )


@pytest.mark.parametrize("action", ["navigate", "click"])
@pytest.mark.parametrize("change", ["viewer", "owner", "session", "agent", "integration", "binding", "reattach", "profile", "disabled", "run"])
async def test_browser_revalidates_after_approval(browser_case, change, action):
    case = browser_case
    if action == "click":
        case.call.arguments = {"action": "click", "tabId": str(uuid4()), "selector": "button"}

    async def approve(_request):
        if change == "viewer":
            case.connection.attached = False
        elif change in {"owner", "session", "agent", "integration"}:
            setattr(case.connection, f"{change}_id", uuid4())
        elif change == "binding":
            case.connection.browser_id = uuid4()
        elif change == "reattach":
            case.connection.attachment_revision += 2
        elif change == "profile":
            case.current.return_value = IntegrationSettings()
        elif change == "disabled":
            case.instance.enabled = False
        elif change == "run":
            case.context.db.get.return_value.execution_agent_id = uuid4()
        return PermissionDecision.ALLOW

    case.context.ask_handler.side_effect = approve
    result = await case.effective.execute(case.call, case.context)
    assert result.is_error
    case.coordinator.execute.assert_not_awaited()


async def test_no_viewer_or_wrong_owner_never_asks_or_executes(browser_case):
    case = browser_case
    for connection in (None, SimpleNamespace(**{**vars(case.connection), "owner_id": uuid4()})):
        case.coordinator.attached_for.return_value = connection
        result = await case.effective.execute(case.call, case.context)
        assert result.metadata["errorCode"] == "browser.notConnected"
    case.context.ask_handler.assert_not_awaited()
    case.coordinator.execute.assert_not_awaited()
    case.coordinator.snapshot.assert_not_awaited()


async def test_approval_scope_binds_exact_action_and_live_attachment(browser_case):
    case = browser_case
    result = await case.effective.execute(case.call, case.context)
    assert not result.is_error
    request = case.context.ask_handler.await_args.args[0]
    assert request.capability == "browser.write"
    assert request.policy_integration_id == case.instance.id
    scope = json.loads(request.resource_scope)
    assert scope["integrationId"] == str(case.instance.id)
    args = case.coordinator.execute.await_args.args[-1]
    assert args["expectedBrowserId"] == str(case.connection.browser_id)
    assert args["expectedAttachmentRevision"] == 1
    assert "allowNetwork" not in args
    case.connection.attachment_revision += 1
    await case.effective.execute(case.call, case.context)
    assert case.context.ask_handler.await_args.args[0].resource_scope != request.resource_scope
    assert result.metadata == {"privateIntegrationData": True}


async def test_denial_never_dispatch(browser_case):
    case = browser_case
    case.context.ask_handler.return_value = PermissionDecision.USER_DENIED
    result = await case.effective.execute(case.call, case.context)
    assert result.metadata["errorCode"] == "permission.denied"
    case.coordinator.execute.assert_not_awaited()


async def test_screenshot_is_a_model_image_not_base64_text(browser_case):
    case = browser_case
    output = BytesIO()
    Image.new("RGB", (2, 2), "white").save(output, format="PNG")
    image = output.getvalue()
    case.coordinator.execute.return_value = BrowserActionResult(screenshot=base64.b64encode(image).decode())
    case.call.arguments = {"action": "screenshot", "tabId": str(uuid4())}
    result = await case.effective.execute(case.call, case.context)
    assert not result.is_error
    assert result.attachments[0].data == image
    assert result.attachments[0].kind == "image"
    assert "screenshot" not in result.content
    assert case.context.ask_handler.await_args.args[0].capability == "browser.read"


async def test_flexible_result_data_is_only_diagnostics(browser_case):
    case = browser_case
    case.coordinator.execute.return_value = BrowserActionResult(data={"secret": "unexpected"})
    result = await case.effective.execute(case.call, case.context)
    assert result.metadata["errorCode"] == "executionAgent.protocolError"
    assert "unexpected" not in result.content


@pytest.mark.parametrize("data", [
    None, {}, {"snapshot_sha256": "a" * 64}, {"snapshotSha256": "a" * 64, "secret": "page"},
    {"snapshotSha256": "a" * 63}, {"snapshotSha256": "A" * 64},
    {"snapshotSha256": "a" * 64 + "\n"}, {"snapshotSha256": 123},
])
async def test_inspect_accepts_only_exact_snapshot_data(browser_case, data):
    case = browser_case
    with pytest.raises(ValidationError):
        BrowserInspectionData.model_validate(data)
    case.call.arguments = {"action": "inspect", "tabId": str(uuid4())}
    case.coordinator.execute.return_value = BrowserActionResult(text='{"elements":[]}', data=data)
    result = await case.effective.execute(case.call, case.context)
    assert result.metadata["errorCode"] == "executionAgent.protocolError"
    assert "elements" not in result.content


@pytest.mark.parametrize("arguments", [
    {"action": "closetab"}, {"action": "inspect"}, {"action": "screenshot"},
    {"action": "click", "selector": "button"},
    {"action": "fill", "selector": "input", "text": "value"},
    {"action": "press", "key": "Enter"}, {"action": "scroll", "direction": "down"},
    {"action": "upload", "selector": "input", "uploadId": str(uuid4())},
])
async def test_page_actions_bind_host_snapshot_before_approval(browser_case, arguments):
    case = browser_case
    events = []
    tab_id = str(uuid4())
    case.call.arguments = {**arguments, "tabId": tab_id}
    assert arguments["action"] in BROWSER_SNAPSHOT_ACTIONS
    inspection = '{"url":"https://private.example","text":"private page","elements":[]}'
    snapshot = hashlib.sha256(inspection.encode()).hexdigest()

    async def capture(*args):
        events.append("snapshot")
        assert case.context.bound_options["browser_id"] == case.connection.browser_id
        assert case.context.bound_options["attachment_revision"] == 1
        assert args == (case.context.session.id, case.connection.agent_id, case.instance.id, tab_id)
        return snapshot

    async def approve(request):
        events.append("approval")
        assert request.approval_details["browser"]["snapshotSha256"] == snapshot
        assert "private page" not in repr(request)
        assert "private.example" not in repr(request)
        assert "screenshot" not in request.approval_details["browser"]
        assert case.context.bound_options["expected_snapshot"] == snapshot
        return PermissionDecision.ALLOW

    async def execute(*args):
        events.append("execute")
        assert args[-1]["expectedSnapshot"] == snapshot
        assert args[-1]["expectedBrowserId"] == str(case.connection.browser_id)
        assert args[-1]["expectedAttachmentRevision"] == 1
        if arguments["action"] == "inspect":
            return BrowserActionResult(text=inspection, data={"snapshotSha256": snapshot})
        return BrowserActionResult()

    case.coordinator.snapshot.side_effect = capture
    case.context.ask_handler.side_effect = approve
    case.coordinator.execute.side_effect = execute
    result = await case.effective.execute(case.call, case.context)
    assert not result.is_error
    assert events == ["snapshot", "approval", "execute"]
    if arguments["action"] == "inspect":
        assert json.loads(result.content)["data"] == {"snapshotSha256": snapshot}
        assert json.loads(result.content)["text"] == inspection


@pytest.mark.parametrize("arguments", [
    {"action": "navigate", "tabId": str(uuid4()), "url": "https://example.org"},
    {"action": "newtab", "url": "https://example.org"},
    {"action": "tabs"}, {"action": "diagnostics"},
])
async def test_navigation_and_browser_level_actions_do_not_snapshot(browser_case, arguments):
    case = browser_case
    case.call.arguments = arguments
    result = await case.effective.execute(case.call, case.context)
    assert not result.is_error
    case.coordinator.snapshot.assert_not_awaited()
    assert "expectedSnapshot" not in case.coordinator.execute.await_args.args[-1]
    assert "snapshotSha256" not in case.context.ask_handler.await_args.args[0].approval_details["browser"]


@pytest.mark.parametrize("change", ["binding", "reattach", "viewer", "owner"])
async def test_attachment_changed_during_snapshot_never_asks(browser_case, change):
    case = browser_case
    case.call.arguments = {"action": "click", "tabId": str(uuid4()), "selector": "button"}

    async def capture(*_args):
        if change == "binding":
            case.connection.browser_id = uuid4()
        elif change == "reattach":
            case.connection.attachment_revision += 2
        elif change == "viewer":
            case.connection.attached = False
        else:
            case.connection.owner_id = uuid4()
        return "a" * 64

    case.coordinator.snapshot.side_effect = capture
    result = await case.effective.execute(case.call, case.context)
    assert result.metadata["errorCode"] in {"browser.bindingChanged", "browser.notConnected"}
    case.context.ask_handler.assert_not_awaited()
    case.coordinator.execute.assert_not_awaited()


@pytest.mark.parametrize("invalid", [None, "raw private page", "A" * 64, "a" * 65, {"snapshotSha256": "a" * 64}])
async def test_invalid_host_snapshot_never_asks_or_leaks_content(browser_case, invalid):
    case = browser_case
    case.call.arguments = {"action": "click", "tabId": str(uuid4()), "selector": "button"}
    case.coordinator.snapshot.return_value = invalid
    result = await case.effective.execute(case.call, case.context)
    assert result.metadata["errorCode"] == "executionAgent.protocolError"
    assert "private page" not in result.content
    case.context.ask_handler.assert_not_awaited()
    case.coordinator.execute.assert_not_awaited()


async def test_snapshot_changes_scope_and_locked_stale_rejection_is_not_retried(browser_case):
    case = browser_case
    case.call.arguments = {"action": "click", "tabId": str(uuid4()), "selector": "button"}
    await case.effective.execute(case.call, case.context)
    original_scope = case.context.ask_handler.await_args.args[0].resource_scope
    case.coordinator.snapshot.return_value = "b" * 64
    await case.effective.execute(case.call, case.context)
    assert case.context.ask_handler.await_args.args[0].resource_scope != original_scope

    # The coordinator owns the locked comparison; its stale result must stop the tool.
    case.coordinator.execute.reset_mock()
    case.coordinator.execute.side_effect = ExecutionClientError("browser.bindingChanged")
    result = await case.effective.execute(case.call, case.context)
    assert result.metadata["errorCode"] == "browser.bindingChanged"
    assert case.coordinator.execute.await_count == 1
    assert case.coordinator.execute.await_args.args[-1]["expectedSnapshot"] == "b" * 64


async def test_page_action_cannot_dispatch_without_captured_snapshot(browser_case):
    case = browser_case
    case.call.arguments = {"action": "click", "tabId": str(uuid4()), "selector": "button"}

    async def approve(_request):
        case.context.bound_options.pop("expected_snapshot")
        return PermissionDecision.ALLOW

    case.context.ask_handler.side_effect = approve
    result = await case.effective.execute(case.call, case.context)
    assert result.metadata["errorCode"] == "browser.bindingChanged"
    case.coordinator.execute.assert_not_awaited()


@pytest.mark.parametrize("code", get_args(BrowserErrorCode))
async def test_known_browser_errors_are_catalogued_and_remote_params_discarded(monkeypatch, code):
    assert describe_error(code).message != "The action failed."
    response = ResponseMessage(type="response", id=uuid4(), ok=False, error={"code": code, "params": {"secret": "token"}})
    monkeypatch.setattr("mato_loop_backend.execution.client.execution_agent_connections.request", AsyncMock(return_value=response))
    with pytest.raises(ExecutionClientError) as error:
        await ExecutionClient(uuid4(), frozenset({"integration.browser.execute"})).request("integration.browser.execute", {}, BrowserActionResult)
    assert error.value.code == code
    assert error.value.params is None


async def test_browser_mutation_disconnect_preserves_unknown_outcome(monkeypatch):
    for operation in ("execute", "setup", "setupwrite", "open", "close", "reset", "file"):
        operation = f"integration.browser.{operation}"
        assert operation in SUPPORTED_OPERATIONS
        assert disconnect_error_code(operation, True) == "browser.outcomeUnknown"
        assert disconnect_error_code(operation, False) == "executionAgent.unavailable"
    assert len(SUPPORTED_OPERATIONS) <= 64
    monkeypatch.setattr("mato_loop_backend.execution.client.execution_agent_connections.request", AsyncMock(side_effect=ExecutionAgentError("browser.outcomeUnknown")))
    with pytest.raises(ExecutionClientError, match="browser.outcomeUnknown"):
        await ExecutionClient(uuid4(), frozenset({"integration.browser.execute"})).request("integration.browser.execute", {}, BrowserActionResult)


@pytest.fixture
def browser_api(monkeypatch):
    user = SimpleNamespace(id=uuid4())
    agent = uuid4()
    other_agent = uuid4()
    documents = {(IntegrationSettings, None): IntegrationSettings()}
    db = AsyncMock()

    async def load(_db, settings_type, *, user_id=None, **_kwargs):
        return documents.get((settings_type, user_id), settings_type()).model_copy(deep=True)

    async def save(_db, settings, *, user_id=None):
        documents[type(settings), user_id] = settings.model_copy(deep=True)

    async def scalar(statement):
        params = statement.compile().params
        return agent if params.get("id_1") == agent and params.get("user_id_1") == user.id else None

    monkeypatch.setattr(SettingsStore, "load", AsyncMock(side_effect=load))
    monkeypatch.setattr(SettingsStore, "load_optional", AsyncMock(return_value=UserToolSettings()))
    monkeypatch.setattr(SettingsStore, "save", AsyncMock(side_effect=save))
    db.scalar.side_effect = scalar
    db.scalars.return_value = SimpleNamespace(all=lambda: [agent])
    app = FastAPI()
    register_error_handlers(app)
    app.include_router(routes.router, prefix="/api")
    app.dependency_overrides[require_user] = lambda: user
    app.dependency_overrides[get_db_session] = lambda: db
    return SimpleNamespace(client=TestClient(app), user=user, agent=agent, other_agent=other_agent, db=db, documents=documents)


def test_browser_crud_owner_scoped_empty_config_one_per_agent_and_no_rebind(browser_api):
    case = browser_api
    base = "/api/integrations/browser"
    assert case.client.get(base).json() == {"configured": False, "instances": []}
    assert case.client.post(base, json={"executionAgentId": str(case.other_agent)}).status_code == 404
    assert case.client.post(base, json={"executionAgentId": str(case.agent), "config": {"profilePath": "/tmp"}}).status_code == 422
    created = case.client.post(base, json={"executionAgentId": str(case.agent)})
    assert created.status_code == 201
    item = created.json()["instances"][0]
    assert item["config"] == {} and item["adapterType"] == "browser"
    url = f"{base}/{item['id']}"
    assert case.client.post(base, json={"executionAgentId": str(case.agent)}).status_code == 409
    assert case.client.patch(url, json={"executionAgentId": str(case.other_agent)}).json()["error"]["code"] == "browser.bindingChanged"
    assert case.client.patch(url, json={"enabled": None}).status_code == 422
    assert case.client.patch(url, json={"name": "Privater Browser", "enabled": False}).json()["instances"][0]["enabled"] is False
    assert case.client.get(f"{base}?scope=instance").status_code == 422
    previous_user = case.user.id
    case.user.id = uuid4()
    assert case.client.get(base).json()["configured"] is False
    for method, suffix in (("patch", ""), ("delete", ""), ("get", "/status"), ("post", "/test"), ("post", "/reset")):
        response = case.client.request(method, url + suffix, json={} if method == "patch" else None)
        assert response.status_code == 404
    case.user.id = previous_user
    assert case.client.delete(url).json() == {"configured": False, "instances": []}


def test_global_disable_blocks_use_but_allows_deletion(browser_api):
    case = browser_api
    base = "/api/integrations/browser"
    item = case.client.post(base, json={"executionAgentId": str(case.agent)}).json()["instances"][0]
    case.documents[IntegrationSettings, None] = IntegrationSettings(adapter_availability={"browser": "disabled"})
    assert case.client.post(base, json={"executionAgentId": str(case.agent)}).status_code == 503
    assert case.client.get(f"{base}/{item['id']}/status").status_code == 503
    assert case.client.delete(f"{base}/{item['id']}").status_code == 200


def test_status_test_reset_use_only_fixed_owner_bound_rpc(browser_api, monkeypatch):
    case = browser_api
    base = "/api/integrations/browser"
    item = case.client.post(base, json={"executionAgentId": str(case.agent)}).json()["instances"][0]
    url = f"{base}/{item['id']}"
    client = SimpleNamespace(request=AsyncMock(return_value=BrowserStatusResult(state="missing", missing=["chromium"])))
    factory = Mock(return_value=client)
    monkeypatch.setattr(routes, "browser_client", factory)
    for method, suffix in (("get", "status"), ("post", "test")):
        response = case.client.request(method, f"{url}/{suffix}")
        assert response.status_code == 200 and response.json()["state"] == "missing"
        client.request.assert_awaited_with("integration.browser.status", {}, BrowserStatusResult)
    client.request.side_effect = ExecutionClientError("browser.busy", {"secret": "token"})
    response = case.client.post(f"{url}/reset")
    assert response.status_code == 409
    assert "token" not in response.text
    assert client.request.await_args.args[1] == {"ownerId": str(case.user.id)}
    factory.assert_called_with(case.agent)


async def test_global_personal_only_browser_never_becomes_effective(monkeypatch):
    instance = _instance()
    monkeypatch.setattr(SettingsStore, "load", AsyncMock(return_value=IntegrationSettings(instances=[instance])))
    monkeypatch.setattr(SettingsStore, "load_optional", AsyncMock(return_value=None))
    effective = await load_effective_integrations(AsyncMock(), instance.owner_id)
    assert effective.instances == []


def test_browser_client_is_generation_bound(monkeypatch):
    monkeypatch.setattr(browser_module.execution_agent_connections, "snapshot", Mock(return_value=frozenset({"integration.browser.status"})))
    monkeypatch.setattr(browser_module.execution_agent_connections, "generation", Mock(return_value=7))
    assert browser_module.browser_client(uuid4()).expected_generation == 7


async def test_current_tool_context_cannot_bypass_profile_with_attachment(monkeypatch):
    instance = _instance()
    session = ChatSession(id=uuid4(), user_id=instance.owner_id, agent_profile_id=uuid4())
    profile = AgentProfile(id=session.agent_profile_id, name="Browser excluded", instructions="Test profile", integration_availability={str(instance.id): False})
    monkeypatch.setattr(registry_module, "load_effective_integrations", AsyncMock(return_value=IntegrationSettings(instances=[instance])))
    monkeypatch.setattr(registry_module, "load_agent_profile", AsyncMock(return_value=profile))
    coordinator = SimpleNamespace(attached_for=Mock())
    monkeypatch.setitem(sys.modules, "mato_loop_backend.execution.browsers", SimpleNamespace(browser_coordinator=coordinator))
    context = ToolContext(
        db=AsyncMock(), run_id=uuid4(), session=session, tool_call=ToolCall("test", "browser", {}),
        root_files=Mock(), integration_settings=IntegrationSettings(instances=[instance]),
        ask_handler=AsyncMock(), execution_client=ExecutionClient(instance.execution_agent_id, frozenset({"integration.browser.execute"})),
    )
    assert not (await context.current_integrations()).instances
    coordinator.attached_for.assert_not_called()


async def test_profile_disabled_browser_actions_are_not_offered():
    instance = _instance()
    settings = effective_execution_integration_settings(
        IntegrationSettings(instances=[instance]), instance.execution_agent_id,
        {"integration.browser.execute"}, browser_integration_id=instance.id,
    )
    tools = ToolSettings()
    for permissions in (tools.private_permissions, tools.public_permissions):
        permissions.policies.update({"browser.read": "disabled", "browser.write": "disabled"})
    effective = tools_module.tool_registry.effective(settings, tool_settings=tools)
    assert "browser" not in effective.tools
    assert {action.capability for action in tools_module.tool_registry.integration_resource_actions(instance)} == {"browser.read", "browser.write"}
