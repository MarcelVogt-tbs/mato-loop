import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.agent import runtime as runtime_module
from mato_loop_backend.agent import tool_registry as registry_module
from mato_loop_backend.agent import tools as tools_module
from mato_loop_backend.agent.permissions import PermissionDecision
from mato_loop_backend.agent.runtime import AgentRuntime
from mato_loop_backend.agent.subagents import permission_snapshot
from mato_loop_backend.agent.tool_registry import ToolContext
from mato_loop_backend.execution.browser_contracts import BrowserActionResult
from mato_loop_backend.execution.client import ExecutionClient
from mato_loop_backend.models import AgentRun, ChatSession
from mato_loop_backend.providers.base import ToolCall
from mato_loop_backend.settings import (
    AgentProfile,
    IntegrationInstance,
    IntegrationSettings,
    ToolSettings,
    TrustLevel,
)
from mato_loop_backend.settings.models import AgentProfilePermissions


@pytest.fixture(params=list(TrustLevel))
def browser_policy_case(request, monkeypatch):
    trust = request.param
    instance = IntegrationInstance(
        name="Browser", integration_type="browser", adapter_type="browser",
        execution_agent_id=uuid4(), owner_id=uuid4(),
    )
    instance._browser_attached = True
    integrations = IntegrationSettings(instances=[instance])
    settings = ToolSettings().select_trust(trust)
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
    coordinator = SimpleNamespace(
        attached_for=Mock(return_value=connection),
        execute=AsyncMock(return_value=BrowserActionResult()),
        snapshot=AsyncMock(return_value="a" * 64),
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
    call = ToolCall("browser-policy", "browser", {})

    async def authorize(permission):
        return await AgentRuntime._authorize(db, run, session, call, permission, context.root_files)

    context = ToolContext(
        db=db, run_id=run.id, session=session, tool_call=call,
        root_files=Mock(), integration_settings=integrations, tool_settings=settings,
        model_trust_level=trust, ask_handler=AsyncMock(side_effect=authorize),
        execution_client=ExecutionClient(instance.execution_agent_id, frozenset(run.execution_operations)),
    )
    return SimpleNamespace(
        instance=instance, integrations=integrations, settings=settings, run=run,
        context=context, call=call, approval=approval, coordinator=coordinator,
        effective=tools_module.ToolRegistry([]).effective(integrations),
    )


@pytest.fixture(params=["read", "write"])
def browser_action_case(browser_policy_case, request):
    case = browser_policy_case
    case.capability = f"browser.{request.param}"
    case.call.arguments = (
        {"action": "tabs"} if request.param == "read"
        else {"action": "click", "tabId": str(uuid4()), "selector": "button"}
    )
    return case


async def test_inherited_browser_actions_use_runtime_auto_without_approvals(browser_action_case):
    case = browser_action_case
    result = await case.effective.execute(case.call, case.context)
    assert not result.is_error
    case.context.ask_handler.assert_awaited_once()
    assert case.context.ask_handler.await_args.args[0].capability == case.capability
    case.approval.assert_not_awaited()
    case.context.db.add.assert_not_called()
    case.context.db.scalars.assert_not_awaited()
    case.coordinator.execute.assert_awaited_once()
    assert not case.settings.permissions.policies
    assert not case.settings.permissions.integration_policies


@pytest.mark.parametrize("scope", ["global", "integration", "profile"])
@pytest.mark.parametrize("policy", ["ask", "disabled"])
async def test_explicit_browser_policy_survives_auto_defaults(browser_action_case, scope, policy):
    case = browser_action_case
    if scope == "global":
        case.settings.permissions.policies[case.capability] = policy
    elif scope == "integration":
        case.settings.permissions.integration_policies[str(case.instance.id)] = {case.capability: policy}
    else:
        case.settings._agent_permissions = AgentProfilePermissions(policies={case.capability: policy})
    result = await case.effective.execute(case.call, case.context)
    case.context.ask_handler.assert_awaited_once()
    if policy == "ask":
        assert not result.is_error
        case.approval.assert_awaited_once()
        assert case.approval.await_args.args[-1].capability == case.capability
        case.coordinator.execute.assert_awaited_once()
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
            integration_availability={str(case.instance.id): False},
        )
    else:
        case.settings._delegation_ceiling = permission_snapshot(case.settings, {"browser"})
    result = await case.effective.execute(case.call, case.context)
    assert result.metadata["errorCode"] == "tool.unavailable"
    case.approval.assert_not_awaited()
    case.coordinator.execute.assert_not_awaited()


@pytest.mark.parametrize("source", ["policy", "default"])
@pytest.mark.parametrize("policy", ["ask", "disabled"])
async def test_browser_delegation_ceiling_is_not_relaxed(browser_action_case, monkeypatch, source, policy):
    case = browser_action_case
    ceiling = permission_snapshot(case.settings, {"browser"}, case.integrations)
    if source == "policy":
        ceiling["permissions"]["policies"][case.capability] = policy
    else:
        ceiling["integrationDefaults"][str(case.instance.id)][case.capability] = policy
    monkeypatch.setattr(runtime_module, "delegation_snapshot", AsyncMock(return_value=ceiling))
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
    case.settings.permissions.policies[case.capability] = "ask"

    async def approve(*_args):
        if change == "user_denied":
            return "deny"
        case.settings.permissions.policies[case.capability] = "disabled"
        return "allow_once"

    case.approval.side_effect = approve
    result = await case.effective.execute(case.call, case.context)
    assert result.metadata["errorCode"] == "permission.denied"
    case.approval.assert_awaited_once()
    case.coordinator.execute.assert_not_awaited()
    assert case.context.user_denied == (change == PermissionDecision.USER_DENIED.value)
