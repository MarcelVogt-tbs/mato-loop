import asyncio
import hashlib
import json
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.agent import runtime as runtime_module
from mato_loop_backend.agent import shared_shell
from mato_loop_backend.agent.approval_decisions import decide_agent_action
from mato_loop_backend.agent.permissions import PermissionPolicy
from mato_loop_backend.agent.runtime import AgentRuntime, _argument_edit_permission_binding
from mato_loop_backend.agent.tool_arguments import ShellRunArguments
from mato_loop_backend.agent.tool_contracts import ApprovalEditField
from mato_loop_backend.agent.tool_registry import (
    BoundTool,
    EffectiveToolSet,
    ToolContext,
    ToolDefinition,
    ToolRegistry,
)
from mato_loop_backend.execution import (
    ExecutionClientError,
    SharedPrepareResult,
    SharedProcessResult,
    SharedTerminalStatus,
    ShellInspectResult,
    TerminalControlResult,
)
from mato_loop_backend.execution.terminals import TerminalCoordinator, TerminalRuntime
from mato_loop_backend.models import (
    AgentRun,
    ApprovalRequest,
    ChatSession,
    ExecutionAgent,
    PermissionGrant,
)
from mato_loop_backend.providers.base import ToolCall
from mato_loop_backend.settings import (
    IntegrationSettings,
    ToolExecutionPolicy,
    ToolSettings,
    TrustLevel,
)
from mato_loop_backend.workspace import RootFileService


@pytest.fixture
def env(monkeypatch):
    session = ChatSession(
        id=uuid4(), user_id=uuid4(), current_directory="/home/user/project",
        agent_profile_id=uuid4(),
    )
    agent = ExecutionAgent(
        id=uuid4(), user_id=session.user_id, home_directory="/home/user",
        home_filesystem_policy="auto", external_filesystem_policy="auto",
        home_shell_enabled=True, external_shell_enabled=True,
    )
    session.preferred_execution_agent_id = agent.id
    run = AgentRun(
        id=uuid4(), session_id=session.id, status="running", trust_level="public",
        execution_agent_id=agent.id, current_directory_snapshot=session.current_directory,
    )
    db = AsyncMock(spec=AsyncSession)
    approvals = []
    grants = []
    state = SimpleNamespace(
        policy=ToolExecutionPolicy.ASK, session=session, agent=agent, run=run,
        db=db, approvals=approvals, grants=grants, evaluated=[],
    )

    def add(obj):
        if isinstance(obj, ApprovalRequest):
            obj.id = uuid4()
            obj.status = "pending"
            approvals.append(obj)
        elif isinstance(obj, PermissionGrant):
            grants.append(obj)

    async def get(model, key, **_kwargs):
        if model is ChatSession:
            return session
        if model is AgentRun:
            return run
        return next((item for item in approvals if item.id == key), None)

    db.add.side_effect = add
    db.get.side_effect = get
    db.scalar.side_effect = lambda statement: (
        None if statement.column_descriptions[0]["entity"] is PermissionGrant else agent
    )
    db.scalars.return_value = Mock(all=Mock(side_effect=lambda: grants))
    monkeypatch.setattr(runtime_module, "permission_origin", AsyncMock(return_value=session))
    monkeypatch.setattr(runtime_module, "delegation_snapshot", AsyncMock(return_value=None))

    async def load_tools(*_args):
        settings = ToolSettings().select_trust(TrustLevel.PUBLIC)
        settings.permissions.policies["shell.read"] = state.policy
        settings.permissions.policies["shell.write"] = state.policy
        return settings, object()

    monkeypatch.setattr(runtime_module, "load_agent_profile_tools", load_tools)
    monkeypatch.setattr(
        runtime_module, "load_effective_integrations",
        AsyncMock(return_value=IntegrationSettings()),
    )
    monkeypatch.setattr(
        runtime_module, "filter_agent_profile_integrations",
        lambda settings, *_args, **_kwargs: settings,
    )
    monkeypatch.setattr(runtime_module.WebPushService, "deliver_after_commit", AsyncMock())
    monkeypatch.setattr(runtime_module.run_events, "publish", AsyncMock())
    monkeypatch.setattr(shared_shell.run_events, "publish", AsyncMock())
    monkeypatch.setattr(TerminalRuntime, "check_access", AsyncMock())
    evaluate = PermissionPolicy.evaluate

    async def evaluated(*args, **kwargs):
        state.evaluated.append(args[2])
        return await evaluate(*args, **kwargs)

    monkeypatch.setattr(PermissionPolicy, "evaluate", evaluated)

    class Native:
        current_directory = "/home/user/project"

        def __init__(self):
            self.calls = []
            self.acquired = True
            self.running = False
            self.executions = 0
            self.hold = False
            self.started = asyncio.Event()
            self.release = asyncio.Event()
            self.exit_code = 0
            self.timed_out = False
            self.execute_error = None

        def status(self, terminal_id):
            return SharedTerminalStatus(
                terminal_id=terminal_id,
                current_directory="/home/user/project",
                state="running" if self.running else "agent" if self.acquired else "human",
                command_id=state.terminal.command_id if self.running else None,
            )

        async def request(self, operation, arguments, _model, **kwargs):
            self.calls.append((operation, arguments))
            if operation == "terminal.shared.status":
                return self.status(arguments["terminalId"])
            if operation == "terminal.shared.acquire":
                self.acquired = True
                return TerminalControlResult()
            if operation == "terminal.shared.release":
                self.acquired = False
                return TerminalControlResult()
            if operation == "terminal.shared.prepare":
                command = arguments["command"]
                classification = "write" if command.startswith("touch ") else "read"
                revision = hashlib.sha256(command.encode()).hexdigest()
                return SharedPrepareResult(
                    command=command,
                    current_directory="/home/user/project",
                    inspection={
                        "targets": [],
                        "classification": classification,
                        "readOnly": classification == "read",
                        "analysisComplete": True,
                        "commands": [{
                            "tokens": command.split(),
                            "prefix": [command.split()[0]],
                            "classification": classification,
                        }],
                        "inspectionRevision": revision,
                    },
                )
            if operation == "terminal.shared.execute":
                self.executions += 1
                self.running = True
                self.started.set()
                await kwargs["on_event"](SimpleNamespace(event="stdout", data="evidence"))
                if self.hold:
                    await self.release.wait()
                if self.execute_error:
                    raise ExecutionClientError(self.execute_error)
                self.running = False
                return SharedProcessResult(
                    exit_code=self.exit_code, timed_out=self.timed_out, duration_ms=1,
                    stdout_bytes=8, stderr_bytes=0,
                    stdout_truncated=False, stderr_truncated=False,
                )
            if operation == "terminal.shared.cancel":
                self.running = False
                return TerminalControlResult()
            return TerminalControlResult()

    native = Native()
    terminal = TerminalRuntime(
        session.user_id, session.id, agent.id, uuid4(), str(uuid4()), native, 1,
        attached=True,
    )
    state.terminal, state.native = terminal, native
    call = ToolCall("call-1", "shell_run", {"command": "cat README.md"})
    context = ToolContext(
        db, run.id, session, call, RootFileService(session.id),
        IntegrationSettings(), AsyncMock(),
    )

    async def authorize(permission):
        return await AgentRuntime._authorize(
            db, run, session, call, permission, context.root_files,
            approval_resolution=context.approval_resolution,
        )

    context.ask_handler = authorize
    state.context = context

    def start(command="cat README.md", timeout=None):
        call.arguments = {"command": command}
        return asyncio.create_task(shared_shell.run_shared_shell(
            context, ShellRunArguments(command=command, timeout=timeout), terminal,
        ))

    state.start = start
    return state


async def pending(env):
    async with asyncio.timeout(2):
        while not env.approvals:
            await asyncio.sleep(0)
    return env.approvals[-1]


async def test_shared_shell_prepares_immutable_command_then_uses_ordinary_approval(env):
    task = env.start("cat README.md", timeout=4)
    approval = await pending(env)

    assert "sharedDraft" not in approval.payload
    assert approval.payload["displayItems"][0]["value"] == "cat README.md"
    await decide_agent_action(env.db, approval, "allow_once")
    result = await task

    assert not result.is_error
    assert json.loads(result.content)["output"]["stdout"] == "evidence"
    assert [operation for operation, _ in env.native.calls] == [
        "terminal.shared.prepare", "terminal.shared.execute", "terminal.shared.status",
    ]
    execute = env.native.calls[1][1]
    assert execute == {
        "terminalId": env.terminal.terminal_id,
        "commandId": execute["commandId"],
        "expectedInspectionRevision": hashlib.sha256(b"cat README.md").hexdigest(),
        "timeoutSeconds": 4.0,
    }
    assert all("command" not in arguments for operation, arguments in env.native.calls if operation == "terminal.shared.execute")


async def test_shared_shell_reinspects_and_executes_edited_command_once(env):
    async def handler(arguments, context):
        return await shared_shell.run_shared_shell(context, arguments, env.terminal)

    definition = ToolDefinition(
        "shell_run",
        "Run shell command",
        ShellRunArguments,
        handler,
        approval_edit_fields=(
            ApprovalEditField(("command",), "Befehl", "textarea", True, 256 * 1024),
        ),
    )
    tools = EffectiveToolSet(ToolRegistry([]), {"shell_run": BoundTool(definition)})
    task = asyncio.create_task(tools.execute(env.context.tool_call, env.context))
    approval = await pending(env)
    edit = approval.payload["argumentEdit"]

    await decide_agent_action(
        env.db,
        approval,
        "allow_once",
        argument_revision=edit["revision"],
        argument_edits=((('command',), "touch changed.txt"),),
    )
    result = await task

    assert not result.is_error
    assert env.context.tool_call.arguments["command"] == "touch changed.txt"
    content = json.loads(result.content)
    assert content["requestedArguments"]["command"] == "cat README.md"
    assert content["executedArguments"]["command"] == "touch changed.txt"
    assert content["userEdited"] is True
    assert env.native.executions == 1
    prepares = [args["command"] for operation, args in env.native.calls if operation == "terminal.shared.prepare"]
    assert prepares == ["cat README.md", "touch changed.txt"]
    execute = next(args for operation, args in env.native.calls if operation == "terminal.shared.execute")
    assert execute["expectedInspectionRevision"] == hashlib.sha256(b"touch changed.txt").hexdigest()


async def test_argument_edit_approval_is_consumed_only_by_matching_action(env):
    env.policy = ToolExecutionPolicy.AUTO
    request = shared_shell.shell_command_permission(
        env.agent.id,
        env.session.current_directory,
        "touch changed.txt",
        ShellInspectResult.model_validate({
            "targets": [],
            "classification": "write",
            "readOnly": False,
            "analysisComplete": True,
            "commands": [],
            "inspectionRevision": "a" * 64,
        }),
    )
    resolution = {
        "argument_edit_approved": json.dumps({
            "arguments": env.context.tool_call.arguments,
            "permission": _argument_edit_permission_binding(request),
        })
    }

    await AgentRuntime._authorize(
        env.db,
        env.run,
        env.session,
        env.context.tool_call,
        replace(request, action_kind="personal_variable_access"),
        env.context.root_files,
        approval_resolution=resolution,
    )
    assert "argument_edit_approved" in resolution

    await AgentRuntime._authorize(
        env.db,
        env.run,
        env.session,
        env.context.tool_call,
        request,
        env.context.root_files,
        approval_resolution=resolution,
    )
    assert "argument_edit_approved" not in resolution


@pytest.mark.parametrize("policy,executions", [
    (ToolExecutionPolicy.AUTO, 1),
    (ToolExecutionPolicy.DISABLED, 0),
])
async def test_shared_shell_uses_normal_policy_without_fallback(env, policy, executions):
    env.policy = policy
    result = await env.start()
    assert result.is_error == (policy == ToolExecutionPolicy.DISABLED)
    assert env.native.executions == executions
    assert not any(operation == "shell.run" for operation, _ in env.native.calls)


@pytest.mark.parametrize(
    "exit_code,timed_out,error_code",
    [
        (2, False, "process.commandFailed"),
        (124, True, "process.timedOut"),
        (None, False, "process.outcomeUnknown"),
    ],
)
async def test_shared_shell_preserves_failed_timeout_and_unknown_outcomes(
    env, exit_code, timed_out, error_code,
):
    env.policy = ToolExecutionPolicy.AUTO
    env.native.exit_code = exit_code
    env.native.timed_out = timed_out
    result = await env.start()
    assert result.metadata["errorCode"] == error_code


async def test_uncertain_execution_keeps_streamed_output_and_is_not_retried(env):
    env.policy = ToolExecutionPolicy.AUTO
    env.native.execute_error = "process.outcomeUnknown"
    result = await env.start()
    payload = json.loads(result.content)
    assert result.metadata["errorCode"] == "process.outcomeUnknown"
    assert payload["output"]["stdout"] == "evidence"
    assert env.native.executions == 1


async def test_shared_shell_cancellation_cancels_prepared_command(env):
    env.policy = ToolExecutionPolicy.AUTO
    env.native.hold = True
    task = env.start()
    await env.native.started.wait()
    command_id = env.native.calls[-1][1]["commandId"]
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert (
        "terminal.shared.cancel",
        {"terminalId": env.terminal.terminal_id, "commandId": command_id},
    ) in env.native.calls


async def test_attach_and_detach_change_local_state_only_after_remote_success(env):
    coordinator = TerminalCoordinator()
    env.terminal.attached = False
    original = env.native.request

    async def fail_acquire(operation, *args, **kwargs):
        if operation == "terminal.shared.acquire":
            raise ExecutionClientError("terminal.unavailable")
        return await original(operation, *args, **kwargs)

    env.native.request = fail_acquire
    with pytest.raises(ExecutionClientError):
        await coordinator.attach(env.terminal)
    assert not env.terminal.attached

    env.native.request = original
    await coordinator.attach(env.terminal)
    assert env.terminal.attached

    async def fail_release(operation, *args, **kwargs):
        if operation == "terminal.shared.release":
            raise ExecutionClientError("terminal.unavailable")
        return await original(operation, *args, **kwargs)

    env.native.request = fail_release
    with pytest.raises(ExecutionClientError):
        await coordinator.detach(env.terminal)
    assert env.terminal.attached
