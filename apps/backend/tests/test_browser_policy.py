import asyncio
import json
import sys
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

import mato_loop_backend.agent.loop as mock_agent_loop
import mato_loop_backend.agent.runtime as mock_agent_runtime
import mato_loop_backend.agent.runtime as runtime_module
import mato_loop_backend.permissions.resolution as mock_permissions_resolution
import mato_loop_backend.tools.access as tools_module
import mato_loop_backend.tools.catalog as mock_tools_catalog
import mato_loop_backend.tools.context as registry_module
from mato_loop_backend.agent.runtime import AgentRuntime
from mato_loop_backend.agent.subagents import permission_snapshot
from mato_loop_backend.execution.browser_contracts import BrowserActionResult
from mato_loop_backend.execution.client import ExecutionClient
from mato_loop_backend.integrations.browser.adapter import BROWSER_TOOL_ACTIONS
from mato_loop_backend.models import AgentRun, ChatSession
from mato_loop_backend.permissions.policy import PermissionDecision
from mato_loop_backend.providers.base import ToolCall
from mato_loop_backend.settings import (
    AgentProfile,
    IntegrationInstance,
    IntegrationSettings,
    ToolExecutionPolicy,
    ToolSettings,
    TrustLevel,
)
from mato_loop_backend.settings.models import AgentProfilePermissions
from mato_loop_backend.tools.context import ToolContext


@pytest.fixture(params=list(TrustLevel))
def browser_policy_case(request, monkeypatch):
    trust = request.param
    instance = IntegrationInstance(
        name="Browser", integration_type="browser", adapter_type="browser",
        execution_agent_id=uuid4(), owner_id=uuid4(),
    )
    instance._browser_attached = True
    integrations = IntegrationSettings(instances=[instance])
    settings = ToolSettings(private_permission_preset="auto_all", public_permission_preset="auto_all").select_trust(trust)
    session = ChatSession(id=uuid4(), user_id=instance.owner_id)
    run = AgentRun(
        id=uuid4(), session_id=session.id, trust_level=trust.value,
        execution_agent_id=instance.execution_agent_id,
        execution_operations=["integration.browser.execute"],
    )
    connection = SimpleNamespace(
        browser_id=uuid4(), attachment_revision=1, integration_id=instance.id,
        agent_id=instance.execution_agent_id, owner_id=instance.owner_id,
        session_id=session.id, attached=True, closed=False,
    )
    async def execute(*args):
        arguments = args[-1]
        if arguments["action"] in {"evaluate", "run_code"}:
            return BrowserActionResult(script={})
        if arguments["action"] in {"click", "fill_form"} and arguments.get("observe", True):
            return BrowserActionResult(
                text="- document", data={"snapshotSha256": "a" * 64},
                batch={"completed": 1, "total": 1} if arguments["action"] == "fill_form" else None,
            )
        return BrowserActionResult()

    coordinator = SimpleNamespace(
        attached_for=Mock(return_value=connection),
        execute=AsyncMock(side_effect=execute),
        snapshot=AsyncMock(return_value="a" * 64),
        attachment=AsyncMock(),
    )
    monkeypatch.setitem(
        sys.modules, "mato_loop_backend.execution.browsers",
        SimpleNamespace(browser_coordinator=coordinator),
    )

    async def load_tools(*_args):
        return settings, settings._agent_profile

    for module in (runtime_module, tools_module):
        monkeypatch.setattr(module, "load_agent_profile_tools", load_tools)
    for module in (runtime_module, registry_module):
        monkeypatch.setattr(module, "load_effective_integrations", AsyncMock(return_value=integrations))

    approval = AsyncMock(return_value="allow_once")
    monkeypatch.setattr(AgentRuntime, "_request_approval", approval)
    db = AsyncMock(spec=AsyncSession)
    db.get.return_value = run
    db.scalars.return_value = Mock(all=Mock(return_value=[]))
    call = ToolCall("browser-policy", "browser_tabs", {})

    async def authorize(permission, *, check_only=False):
        return await AgentRuntime._authorize(
            db, run, session, call, permission, context.root_files,
            approval_resolution=context.approval_resolution,
            check_only=check_only,
        )

    context = ToolContext(
        db=db, run_id=run.id, session=session, tool_call=call,
        root_files=Mock(), integration_settings=integrations, tool_settings=settings,
        model_trust_level=trust, ask_handler=AsyncMock(side_effect=authorize),
        execution_client=ExecutionClient(instance.execution_agent_id, frozenset(run.execution_operations)),
    )
    return SimpleNamespace(
        instance=instance, integrations=integrations, settings=settings, run=run,
        context=context, call=call, approval=approval, coordinator=coordinator,
        effective=mock_tools_catalog.ToolRegistry([]).effective(integrations),
    )


@pytest.fixture(params=["read", "write", "fill_form", "evaluate", "run_code"])
def browser_action_case(browser_policy_case, request):
    case = browser_policy_case
    case.capability = "browser.read" if request.param == "read" else "browser.write"
    case.call.name = "browser_tabs" if request.param == "read" else "browser_click"
    case.call.arguments = (
        {"action": "list"} if request.param == "read"
        else {"tabId": str(uuid4()), "ref": "e1"}
    )
    if request.param == "fill_form":
        case.call.name = "browser_fill_form"
        case.call.arguments = {
            "tabId": str(uuid4()),
            "fields": [{"name": "Name", "ref": "e1", "type": "textbox", "value": "Ada"}],
        }
    if request.param in {"evaluate", "run_code"}:
        case.capability = "browser.code"
        case.call.name = f"browser_{request.param}"
        case.call.arguments = {
            "tabId": str(uuid4()), "observe": False,
            **({"function": "() => 1"} if request.param == "evaluate" else {"code": "async def run(page):\n    return 1"}),
        }
    case.capabilities = {
        "browser.read": ["browser.read"],
        "browser.write": ["browser.write", "browser.read"],
        "browser.code": ["browser.read", "browser.write", "browser.code"],
    }[case.capability]
    return case


@pytest.fixture(params=["evaluate", "run_code"])
def browser_script_case(browser_policy_case, request):
    case = browser_policy_case
    case.call.name = f"browser_{request.param}"
    case.call.arguments = {
        "tabId": str(uuid4()), "observe": False,
        **({"function": "() => 1"} if request.param == "evaluate" else {"code": "async def run(page):\n    return 1"}),
    }
    case.settings.permissions.integration_policies.setdefault(str(case.instance.id), {})["browser.code"] = "ask"
    return case


def test_browser_catalog_uses_the_selected_base_preset(browser_policy_case):
    case = browser_policy_case
    case.settings.private_permission_preset = "ask_changes"
    case.settings.public_permission_preset = "ask_changes"
    actions = mock_tools_catalog.tool_registry.integration_resource_actions(case.instance)
    assert {action.capability for action in actions} == {
        "browser.read", "browser.write", "browser.code", "browser.control", "browser.release",
        "filesystem.read", "session.files.read", "filesystem.write",
    }
    for action in actions:
        for integration_id in (None, case.instance.id):
            resolved_default = ToolExecutionPolicy.AUTO if action.category == "read" else ToolExecutionPolicy.ASK
            resolution = mock_permissions_resolution.resolve_tool_execution_policy(
                case.settings,
                action.capability,
                action.default_policy(case.settings),
                integration_id=integration_id,
            )
            assert (resolution.policy, resolution.source, resolution.reason) == (
                resolved_default, "default", "trust_default",
            )


async def test_browser_release_uses_the_auto_preset(browser_policy_case):
    case = browser_policy_case
    case.call.name = "browser_release_control"
    case.call.arguments = {}

    result = await case.effective.execute(case.call, case.context)

    assert not result.is_error
    assert case.context.ask_handler.await_args.args[0].capability == "browser.release"
    case.approval.assert_not_awaited()
    case.coordinator.attachment.assert_awaited_once_with(
        case.coordinator.attached_for.return_value, False, reason="modelRelease",
    )


async def test_inherited_browser_actions_use_runtime_auto_without_approvals(browser_action_case):
    case = browser_action_case
    result = await case.effective.execute(case.call, case.context)
    assert not result.is_error
    capabilities = [call.args[0].capability for call in case.context.ask_handler.await_args_list]
    assert capabilities == case.capabilities
    case.approval.assert_not_awaited()
    case.coordinator.snapshot.assert_not_awaited()
    assert "expectedSnapshot" not in case.coordinator.execute.await_args.args[-1]
    case.context.db.add.assert_not_called()
    case.context.db.scalars.assert_not_awaited()
    case.coordinator.execute.assert_awaited_once()
    assert not case.settings.permissions.policies
    assert not case.settings.permissions.integration_policies


@pytest.mark.parametrize("scope", ["integration", "profile"])
@pytest.mark.parametrize("policy", ["ask", "disabled"])
async def test_explicit_browser_policy_survives_auto_defaults(browser_action_case, scope, policy):
    case = browser_action_case
    if scope == "integration":
        case.settings.permissions.integration_policies[str(case.instance.id)] = {case.capability: policy}
    else:
        case.settings._agent_permissions = AgentProfilePermissions(integration_policies={str(case.instance.id): {case.capability: policy}})
    result = await case.effective.execute(case.call, case.context)
    expected_checks = len(case.capabilities) if policy == "ask" else case.capabilities.index(case.capability) + 1
    assert sum(not call.kwargs.get("check_only", False) for call in case.context.ask_handler.await_args_list) == expected_checks
    if policy == "ask":
        assert not result.is_error
        case.approval.assert_awaited_once()
        assert case.approval.await_args.args[-1].capability == case.capability
        case.coordinator.execute.assert_awaited_once()
        if "tabId" in case.call.arguments:
            case.coordinator.snapshot.assert_awaited_once()
            assert case.coordinator.execute.await_args.args[-1]["expectedSnapshot"] == "a" * 64
    else:
        assert result.metadata["errorCode"] == "permission.denied"
        case.approval.assert_not_awaited()
        case.coordinator.execute.assert_not_awaited()


@pytest.mark.parametrize("global_policy", ["ask", "disabled"])
async def test_explicit_integration_auto_keeps_precedence(browser_action_case, global_policy):
    case = browser_action_case
    case.settings.permissions.policies[case.capability] = global_policy
    case.settings.permissions.integration_policies[str(case.instance.id)] = {case.capability: "auto"}
    result = await case.effective.execute(case.call, case.context)
    assert not result.is_error
    case.approval.assert_not_awaited()
    case.coordinator.execute.assert_awaited_once()


@pytest.mark.parametrize("restriction", ["profile", "delegation"])
async def test_inherited_auto_cannot_restore_excluded_browser(browser_action_case, restriction):
    case = browser_action_case
    if restriction == "profile":
        case.settings._agent_profile = AgentProfile(
            name="Excluded browser", instructions="Test profile",
            integrations_enabled_by_default=False,
        )
    else:
        case.settings._delegation_ceiling = permission_snapshot(case.settings, set(BROWSER_TOOL_ACTIONS))
    result = await case.effective.execute(case.call, case.context)
    assert result.metadata["errorCode"] == "tool.unavailable"
    case.approval.assert_not_awaited()
    case.coordinator.execute.assert_not_awaited()


@pytest.mark.parametrize("source", ["policy", "default"])
@pytest.mark.parametrize("policy", ["ask", "disabled"])
async def test_browser_delegation_ceiling_is_not_relaxed(browser_action_case, monkeypatch, source, policy):
    case = browser_action_case
    ceiling = permission_snapshot(case.settings, set(BROWSER_TOOL_ACTIONS), case.integrations)
    if source == "policy":
        ceiling["permissions"]["integration_policies"].setdefault(str(case.instance.id), {})[case.capability] = policy
    else:
        ceiling["integrationDefaults"][str(case.instance.id)][case.capability] = policy
    _mock_value = AsyncMock(return_value=ceiling)
    for _mock_target in (mock_agent_runtime, mock_agent_loop,):
        monkeypatch.setattr(_mock_target, 'delegation_snapshot', _mock_value)
    case.settings._delegation_ceiling = ceiling
    result = await case.effective.execute(case.call, case.context)
    assert result.is_error == (policy == "disabled")
    assert case.approval.await_count == (policy == "ask")
    assert case.coordinator.execute.await_count == (policy == "ask")
    if policy == "disabled":
        assert result.metadata["errorCode"] == "permission.denied"


@pytest.mark.parametrize("change", ["user_denied", "disabled"])
async def test_explicit_browser_ask_still_denies_or_revalidates(browser_action_case, change):
    case = browser_action_case
    policies = case.settings.permissions.integration_policies.setdefault(str(case.instance.id), {})
    policies[case.capability] = "ask"

    async def approve(*_args, **_kwargs):
        if change == "user_denied":
            return "deny"
        policies[case.capability] = "disabled"
        return "allow_once"

    case.approval.side_effect = approve
    result = await case.effective.execute(case.call, case.context)
    assert result.metadata["errorCode"] == "permission.denied"
    case.approval.assert_awaited_once()
    case.coordinator.execute.assert_not_awaited()
    assert case.context.user_denied == (change == PermissionDecision.USER_DENIED.value)


async def test_existing_grant_under_ask_does_not_capture_guard(browser_action_case, monkeypatch):
    case = browser_action_case
    case.settings.permissions.integration_policies.setdefault(str(case.instance.id), {})[case.capability] = "ask"
    evaluate = AsyncMock(return_value=PermissionDecision.ALLOW)
    monkeypatch.setattr(mock_agent_runtime.PermissionPolicy, "evaluate", evaluate)
    result = await case.effective.execute(case.call, case.context)
    assert not result.is_error
    assert any(call.args[4].value == "ask" for call in evaluate.await_args_list)
    case.approval.assert_not_awaited()
    case.coordinator.snapshot.assert_not_awaited()
    assert "expectedSnapshot" not in case.coordinator.execute.await_args.args[-1]


@pytest.mark.parametrize("capability", ["browser.read", "browser.write", "browser.code"])
@pytest.mark.parametrize("action,source", [("evaluate", {"function": "() => 1"}), ("run_code", {"code": "async def run(page):\n    return 1"})])
async def test_script_requires_each_permission_even_without_observation(browser_policy_case, capability, action, source):
    case = browser_policy_case
    case.call.name = f"browser_{action}"
    case.call.arguments = {"tabId": str(uuid4()), "observe": False, **source}
    case.settings.permissions.integration_policies.setdefault(str(case.instance.id), {})[capability] = "disabled"
    result = await case.effective.execute(case.call, case.context)
    assert result.metadata["errorCode"] == "permission.denied"
    case.coordinator.snapshot.assert_not_awaited()
    case.coordinator.execute.assert_not_awaited()


async def test_multiple_asks_share_one_independent_guard_and_exact_source(browser_policy_case):
    case = browser_policy_case
    case.call.name = "browser_run_code"
    code = "async def run(page):\n    await page.goto('https://example.org')\n    return 1"
    case.call.arguments = {"tabId": str(uuid4()), "code": code, "observe": False}
    case.settings.permissions.integration_policies.setdefault(str(case.instance.id), {}).update(dict.fromkeys(("browser.read", "browser.write", "browser.code"), "ask"))
    case.coordinator.snapshot.return_value = "b" * 64

    async def approve(*args, **_kwargs):
        case.coordinator.snapshot.assert_awaited_once()
        details = args[-1].approval_details["browser"]
        assert details["code"] == code
        assert details["tabId"] == case.call.arguments["tabId"]
        assert details["browserId"] == str(case.context.bound_options["browser_id"])
        assert details["attachmentRevision"] == 1
        assert details["snapshotSha256"] == "b" * 64
        return "allow_once"

    case.approval.side_effect = approve
    result = await case.effective.execute(case.call, case.context)
    assert not result.is_error
    assert case.approval.await_count == 3
    case.coordinator.snapshot.assert_awaited_once()
    assert case.coordinator.execute.await_args.args[-1]["expectedSnapshot"] == "b" * 64


async def test_legacy_availability_and_delegation_never_enable_new_browser_tools(browser_policy_case, monkeypatch):
    case = browser_policy_case
    case.settings.tool_availability = {"browser": False, "browser_run_code": True}
    assert not set(BROWSER_TOOL_ACTIONS) & mock_tools_catalog.ToolRegistry([]).effective(
        case.integrations, tool_settings=case.settings,
    ).tools.keys()
    case.call.name = "browser_run_code"
    case.call.arguments = {"tabId": str(uuid4()), "code": "async def run(page):\n    return 1", "observe": False}
    result = await case.effective.execute(case.call, case.context)
    assert result.metadata["errorCode"] == "tool.unavailable"
    case.settings.tool_availability.clear()
    ceiling = permission_snapshot(case.settings, {"browser"}, case.integrations)
    ceiling["integrationDefaults"][str(case.instance.id)].pop("browser.code")
    ceiling["actionDefaults"].pop("browser.code")
    _mock_value = AsyncMock(return_value=ceiling)
    for _mock_target in (mock_agent_runtime, mock_agent_loop,):
        monkeypatch.setattr(_mock_target, 'delegation_snapshot', _mock_value)
    result = await case.effective.execute(case.call, case.context)
    assert result.metadata["errorCode"] == "permission.denied"
    case.approval.assert_not_awaited()
    case.coordinator.execute.assert_not_awaited()


@pytest.mark.parametrize("capability", ["browser.read", "browser.write"])
@pytest.mark.parametrize("policy", ["ask", "disabled"])
@pytest.mark.parametrize("scope", ["integration", "profile", "delegation"])
async def test_browser_final_fence_rejects_revoked_earlier_permission_during_code_approval(
    browser_script_case, monkeypatch, capability, policy, scope,
):
    case = browser_script_case
    if scope == "integration":
        policies = case.settings.permissions.integration_policies.setdefault(str(case.instance.id), {})
    elif scope == "profile":
        case.settings._agent_permissions = AgentProfilePermissions()
        policies = case.settings._agent_permissions.integration_policies.setdefault(str(case.instance.id), {})
    else:
        ceiling = permission_snapshot(case.settings, set(BROWSER_TOOL_ACTIONS), case.integrations)
        _mock_value = AsyncMock(return_value=ceiling)
        for _mock_target in (mock_agent_runtime, mock_agent_loop,):
            monkeypatch.setattr(_mock_target, 'delegation_snapshot', _mock_value)
        policies = ceiling["integrationDefaults"][str(case.instance.id)]
    pending, release = asyncio.Event(), asyncio.Event()

    async def approve(*args, **_kwargs):
        assert args[-1].capability == "browser.code"
        pending.set()
        await release.wait()
        return "allow_once"

    case.approval.side_effect = approve
    task = asyncio.create_task(case.effective.execute(case.call, case.context))
    try:
        await asyncio.wait_for(pending.wait(), timeout=2)
        policies[capability] = policy
    finally:
        release.set()
        result = await task
    assert result.metadata["errorCode"] == "permission.denied"
    case.approval.assert_awaited_once()
    case.coordinator.snapshot.assert_awaited_once()
    case.coordinator.execute.assert_not_awaited()
    case.context.db.add.assert_not_called()
    rechecks = [call.args[0].capability for call in case.context.ask_handler.await_args_list if call.kwargs.get("check_only")]
    assert capability in rechecks


@pytest.mark.parametrize("capability", ["browser.read", "browser.write"])
@pytest.mark.parametrize("policy", ["auto", "ask", "disabled"])
async def test_browser_final_fence_retains_exact_once_approval_but_never_overrides_disabled(
    browser_script_case, capability, policy,
):
    case = browser_script_case
    policies = case.settings.permissions.integration_policies.setdefault(str(case.instance.id), {})
    policies[capability] = "ask"

    async def approve(*args, **_kwargs):
        if args[-1].capability == "browser.code":
            policies[capability] = policy
        return "allow_once"

    case.approval.side_effect = approve
    result = await case.effective.execute(case.call, case.context)
    assert [call.args[-1].capability for call in case.approval.await_args_list] == [capability, "browser.code"]
    assert result.is_error == (policy == "disabled")
    assert case.coordinator.execute.await_count == (policy != "disabled")
    if policy == "disabled":
        assert result.metadata["errorCode"] == "permission.denied"
    else:
        assert [call.args[0].capability for call in case.context.ask_handler.await_args_list if call.kwargs.get("check_only")] == [
            "browser.read", "browser.write", "browser.code",
        ]
    case.coordinator.snapshot.assert_awaited_once()
    case.context.db.add.assert_not_called()
    assert case.context.db.scalars.return_value.all() == []


@pytest.mark.parametrize("change", ["scope", "capability", "profile", "call", "generic"])
async def test_browser_once_proof_is_not_a_grant_for_other_scopes(browser_script_case, change):
    case = browser_script_case
    result = await case.effective.execute(case.call, case.context)
    assert not result.is_error
    request = case.approval.await_args.args[-1]
    assert await case.context.ask_handler(request, check_only=True) == PermissionDecision.ALLOW
    if change == "scope":
        scope = json.loads(request.resource_scope)
        scope["operationKey"] = "changed-source-or-target"
        request = replace(request, resource_scope=json.dumps(scope, sort_keys=True, separators=(",", ":")))
    elif change == "capability":
        request = replace(request, capability="browser.read")
        case.settings.permissions.integration_policies.setdefault(str(case.instance.id), {})["browser.read"] = "ask"
    elif change == "profile":
        case.context.session.agent_profile_id = uuid4()
    elif change == "call":
        case.call.id = "another-call"
    else:
        request = replace(request, action_kind="integration_operation")
    assert await case.context.ask_handler(request, check_only=True) == PermissionDecision.ASK
    case.approval.assert_awaited_once()
    case.coordinator.snapshot.assert_awaited_once()
    case.context.db.add.assert_not_called()


async def test_browser_once_proofs_are_cleared_before_the_next_execution(browser_script_case):
    case = browser_script_case
    result = await case.effective.execute(case.call, case.context)
    assert not result.is_error
    assert any(key.startswith("browser_approved:") for key in case.context.approval_resolution)

    async def approve(*_args, **_kwargs):
        assert not any(key.startswith("browser_approved:") for key in case.context.approval_resolution)
        return "allow_once"

    case.approval.side_effect = approve
    result = await case.effective.execute(case.call, case.context)
    assert not result.is_error
    assert case.approval.await_count == 2
