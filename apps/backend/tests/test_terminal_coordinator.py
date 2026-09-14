import asyncio
import hashlib
import json
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.agent import runtime as runtime_module
from mato_loop_backend.agent import shared_shell
from mato_loop_backend.agent.approval_decisions import (
    ApprovalDecisionError,
    decide_agent_action,
)
from mato_loop_backend.agent.permissions import PermissionPolicy, shell_command_permission
from mato_loop_backend.agent.runtime import AgentRuntime
from mato_loop_backend.agent.tool_arguments import ShellRunArguments
from mato_loop_backend.agent.tool_registry import ToolContext
from mato_loop_backend.execution import (
    ExecutionClientError,
    SharedInspectResult,
    SharedProcessResult,
    SharedTerminalStatus,
    TerminalControlResult,
)
from mato_loop_backend.execution.terminals import (
    BrowserTerminalConnection,
    TerminalCoordinator,
    TerminalRuntime,
)
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
    session = ChatSession(id=uuid4(), user_id=uuid4(), current_directory="/home/user/project", agent_profile_id=uuid4())
    agent = ExecutionAgent(id=uuid4(), user_id=session.user_id, home_directory="/home/user",
                           home_filesystem_policy="auto", external_filesystem_policy="auto",
                           home_shell_enabled=True, external_shell_enabled=True)
    session.preferred_execution_agent_id = agent.id
    run = AgentRun(id=uuid4(), session_id=session.id, status="running", trust_level="public",
                   execution_agent_id=agent.id, current_directory_snapshot=session.current_directory)
    db = AsyncMock(spec=AsyncSession)
    approvals = []
    grants = []
    state = SimpleNamespace(policy=ToolExecutionPolicy.ASK, session=session, agent=agent, run=run,
                            db=db, approvals=approvals, grants=grants, evaluated=[],
                            class_policies={}, prefixes={"read": [], "write": []})

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
    db.__aenter__.return_value = db
    monkeypatch.setattr(shared_shell, "AsyncSessionFactory", lambda: db)
    monkeypatch.setattr(shared_shell, "PENDING_POLL_SECONDS", 0.005)
    monkeypatch.setattr(runtime_module, "permission_origin", AsyncMock(return_value=session))
    monkeypatch.setattr(runtime_module, "delegation_snapshot", AsyncMock(return_value=None))
    async def load_tools(*_args):
        settings = ToolSettings(shell_prefixes=state.prefixes).select_trust(TrustLevel.PUBLIC)
        settings.permissions.policies.update({
            f"shell.{classification}": state.class_policies.get(classification, state.policy)
            for classification in ("read", "write")
        })
        return settings, object()

    monkeypatch.setattr(runtime_module, "load_agent_profile_tools", load_tools)
    monkeypatch.setattr(runtime_module, "load_effective_integrations", AsyncMock(return_value=IntegrationSettings()))
    monkeypatch.setattr(runtime_module, "filter_agent_profile_integrations", lambda settings, *_args, **_kwargs: settings)
    monkeypatch.setattr(runtime_module.WebPushService, "deliver_after_commit", AsyncMock())
    monkeypatch.setattr(runtime_module.run_events, "publish", AsyncMock())
    monkeypatch.setattr(TerminalRuntime, "check_access", AsyncMock())
    evaluate = PermissionPolicy.evaluate

    async def evaluated(*args, **kwargs):
        state.evaluated.append(args[2])
        return await evaluate(*args, **kwargs)

    monkeypatch.setattr(PermissionPolicy, "evaluate", evaluated)

    class Native:
        current_directory = session.current_directory
        command_id = None
        line = ""
        revision = 0
        context_revision = 1
        submitted = False
        cancelled = False
        state = "idle"
        executions = 0
        hold = False
        busy = False
        exit_code = 0
        targets = []
        analysis_complete = None

        def __init__(self):
            self.calls = []
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        def edit(self, line, cwd=None):
            self.line = line
            self.revision += 1
            if cwd:
                self.current_directory = cwd
                self.context_revision += 1

        def snapshot(self):
            verb = self.line.split()[0]
            classification = "write" if verb in {"rm", "touch"} or ">" in self.line else "read"
            known = verb in {"cat", "printf", "rm", "touch", "pwd"}
            inspection = {
                "targets": self.targets, "classification": classification, "readOnly": classification == "read",
                "analysisComplete": known if self.analysis_complete is None else self.analysis_complete,
                "commands": [{"tokens": [verb], "prefix": [verb] if known else None, "classification": classification}],
            }
            revision = hashlib.sha256(repr((self.line, self.revision, self.current_directory,
                                            self.context_revision, inspection)).encode()).hexdigest()
            return SharedInspectResult(command=self.line, current_directory=self.current_directory,
                                       revision=self.revision, context_revision=self.context_revision,
                                       inspection={**inspection, "inspectionRevision": revision})

        async def request(self, operation, args, _model, **kwargs):
            self.calls.append((operation, args))
            if operation == "terminal.shared.stage":
                if self.busy:
                    raise ExecutionClientError("terminal.commandActive")
                self.command_id, self.line = UUID(args["commandId"]), args["command"]
                self.revision, self.state = 1, "editing"
                self.submitted = self.cancelled = False
            if operation in {"terminal.shared.status", "terminal.shared.stage"}:
                return SharedTerminalStatus(terminal_id=state.terminal.terminal_id, current_directory=self.current_directory,
                                            state=self.state, context_revision=self.context_revision, command_id=self.command_id,
                                            command=self.line if self.command_id else None, revision=self.revision,
                                            submitted=self.submitted, cancelled=self.cancelled)
            if operation == "terminal.shared.inspect":
                if self.cancelled:
                    raise ExecutionClientError("approval.targetChanged")
                if args["freeze"]:
                    self.state = "checking"
                return self.snapshot()
            if operation == "terminal.shared.execute":
                assert self.state == "checking"
                if self.cancelled:
                    raise ExecutionClientError("approval.targetChanged")
                assert args["command"] == self.line
                assert args["expectedRevision"] == self.revision
                assert args["expectedContextRevision"] == self.context_revision
                if args["expectedInspectionRevision"] != self.snapshot().inspection.inspection_revision:
                    raise ExecutionClientError("approval.targetChanged")
                self.executions += 1
                self.state = "running"
                self.started.set()
                await kwargs["on_event"](SimpleNamespace(event="stdout", data="evidence"))
                if self.hold:
                    await self.release.wait()
                self.state = "idle"
                return SharedProcessResult(exit_code=self.exit_code, timed_out=False, duration_ms=1,
                                           stdout_bytes=8, stderr_bytes=0, stdout_truncated=False, stderr_truncated=False)
            if operation == "terminal.shared.cancel":
                self.cancelled, self.state = True, "idle"
            if operation == "terminal.close":
                self.state = "closed"
            return TerminalControlResult()

    native = Native()
    terminal = TerminalRuntime(session.user_id, session.id, agent.id, uuid4(), str(uuid4()), native, 1, attached=True)
    state.terminal, state.native = terminal, native
    call = ToolCall("call-1", "shell_run", {"command": "cat README.md"})
    context = ToolContext(db, run.id, session, call, RootFileService(session.id), IntegrationSettings(), AsyncMock())

    async def authorize(permission):
        return await AgentRuntime._authorize(db, run, session, call, permission, context.root_files)

    context.ask_handler = authorize
    state.context = context

    def start(command_line="cat README.md"):
        call.arguments.clear()
        call.arguments["command"] = command_line
        return asyncio.create_task(shared_shell.run_shared_shell(context, ShellRunArguments(command=command_line), terminal))

    state.start = start
    return state


async def pending(env):
    async with asyncio.timeout(2):
        while env.terminal.pending is None:
            await asyncio.sleep(0)
    return env.approvals[-1]


@pytest.mark.parametrize("policy", [ToolExecutionPolicy.AUTO, ToolExecutionPolicy.DISABLED])
async def test_shared_auto_and_disabled_use_normal_policy(env, policy):
    env.policy = policy
    result = await env.start()
    assert not env.approvals
    assert env.native.executions == (policy == ToolExecutionPolicy.AUTO)
    assert all(request.capability == "shell.read" and not request.shell_metadata_paths for request in env.evaluated)
    if policy == ToolExecutionPolicy.AUTO:
        payload = json.loads(result.content)
        assert payload["output"]["stdout"] == "evidence"
        assert payload["requestedArguments"] == payload["executedArguments"]
        assert payload["userEdited"] is False


async def test_enter_edited_command_uses_normal_persistent_approval_and_actual_request(env):
    task = env.start()
    approval = await pending(env)
    assert approval.action_kind == "shell_run" and approval.capability == "shell.read"
    assert approval.available_decisions == ["allow_once", "allow_session", "deny"]
    assert env.native.state == "editing"
    env.native.edit("rm changed.txt", "/outside")
    env.native.submitted = True
    result = await asyncio.wait_for(task, 2)
    payload = json.loads(result.content)
    assert env.native.executions == 1 and approval.status == "approved"
    assert approval.decision == "allow_once" and approval.payload["approvedCommand"] == "rm changed.txt"
    assert approval.capability == "shell.write" and approval.payload["shellPrefixes"] == [["rm"]]
    assert approval.risk_level == "high" and not env.grants
    assert payload["requestedArguments"] == {"command": "cat README.md"}
    assert payload["executedArguments"] == {"command": "rm changed.txt"} and payload["userEdited"]
    assert payload["currentDirectory"] == "/outside"
    assert json.loads(env.evaluated[-1].resource_scope)["currentDirectory"] == "/outside"
    assert env.session.current_directory == "/home/user/project"
    assert env.run.status == "running"
    assert [args["freeze"] for op, args in env.native.calls if op == "terminal.shared.inspect"] == [False, True]


@pytest.mark.parametrize("kind", ["external_disabled", "external_deny", "home_disabled", "disabled_policy"])
async def test_enter_rechecks_hard_denials(env, kind):
    env.native.analysis_complete = False
    task = env.start()
    approval = await pending(env)
    if kind == "external_disabled":
        env.agent.external_shell_enabled = False
    elif kind == "external_deny":
        env.agent.external_filesystem_policy = "deny"
    elif kind == "home_disabled":
        env.agent.home_shell_enabled = False
    else:
        env.policy = ToolExecutionPolicy.DISABLED
    env.native.submitted = True
    result = await asyncio.wait_for(task, 2)
    assert result.is_error and env.native.executions == 0
    assert approval.status == "expired"


async def test_stale_ui_review_cannot_approve_unseen_edits(env):
    task = env.start()
    approval = await pending(env)
    original = approval.payload["initialReviewRevision"]
    env.native.edit("printf changed")
    with pytest.raises(ApprovalDecisionError, match="approval.targetChanged"):
        await decide_agent_action(env.db, approval, "allow_once", expected_revision=original)
    assert approval.status == "pending" and not task.done() and env.native.executions == 0
    revision = approval.payload["sharedDraft"]["inspectionRevision"]
    await decide_agent_action(env.db, approval, "allow_once", expected_revision=revision)
    assert not (await asyncio.wait_for(task, 2)).is_error
    assert env.native.executions == 1

    assert [args["freeze"] for op, args in env.native.calls if op == "terminal.shared.inspect"] == [False, True]


@pytest.mark.parametrize("native_cancel", [False, True])
async def test_deny_and_native_ctrl_c_resume_parent_without_cancelling_run(env, native_cancel):
    viewer = BrowserTerminalConnection(env.terminal, SimpleNamespace())
    env.terminal.viewer = viewer
    task = env.start()
    approval = await pending(env)
    if native_cancel:
        env.native.cancelled = True
        env.native.state = "idle"
    else:
        await decide_agent_action(env.db, approval, "deny")
    result = await asyncio.wait_for(task, 2)
    assert result.metadata["errorCode"] == "permission.userDenied"
    assert env.context.user_denied and not task.cancelled()
    assert env.run.status == "running" and env.native.executions == 0
    assert approval.status == ("expired" if native_cancel else "denied")
    messages = []
    while not viewer.queue.empty():
        messages.append(json.loads(viewer.queue.get_nowait()[0]))
    assert messages[-1]["type"] == "terminal.status"
    assert messages[-1]["busy"] is False
    assert messages[-1]["activeToolCallId"] is None
    assert messages[-1]["pending"] is None


async def test_enter_and_ui_approval_race_executes_once(env):
    task = env.start()
    approval = await pending(env)
    env.native.submitted = True
    ui = asyncio.create_task(decide_agent_action(env.db, approval, "allow_once"))
    results = await asyncio.gather(ui, task, return_exceptions=True)
    assert env.native.executions == 1
    assert approval.status == "approved"
    assert not isinstance(results[-1], BaseException)


async def test_auto_concurrent_edit_reauthorizes_outside_workspace(env):
    env.policy = ToolExecutionPolicy.AUTO
    authorize = env.context.ask_handler
    first = True

    async def edit_after_authorization(permission):
        nonlocal first
        result = await authorize(permission)
        if first:
            first = False
            env.native.edit("touch other.txt", "/outside")
        return result

    env.context.ask_handler = edit_after_authorization
    task = env.start()
    approval = await pending(env)
    assert env.native.executions == 0
    assert approval.payload["sharedDraft"]["currentDirectory"] == "/outside"
    await decide_agent_action(env.db, approval, "allow_once")
    assert not (await asyncio.wait_for(task, 2)).is_error
    assert env.native.executions == 1


@pytest.mark.parametrize("classification", ["read", "write"])
async def test_normal_shell_prefix_grants_remain_class_bound_in_shared_shell(env, classification):
    env.native.line = "cat README.md"
    permission = shell_command_permission(
        env.agent.id,
        env.session.current_directory,
        "cat README.md",
        env.native.snapshot().inspection,
    )
    scope = replace(
        permission.effective_session_grants[0], access=classification
    )
    env.grants.append(PermissionGrant(
        session_id=env.session.id, capability=scope.capability,
        resource_type=scope.resource_type, resource_scope=scope.resource_scope,
    ))
    task = env.start()
    if classification == "read":
        assert not (await asyncio.wait_for(task, 2)).is_error
        assert env.native.executions == 1 and not env.approvals
        return
    approval = await pending(env)
    assert env.native.executions == 0
    await decide_agent_action(env.db, approval, "deny")
    await task


async def test_pending_approval_and_running_command_survive_browser_release(env, monkeypatch):
    from mato_loop_backend.execution import terminals as terminal_module

    coordinator = TerminalCoordinator()
    coordinator._runtimes[env.session.user_id, env.session.id, env.agent.id] = env.terminal
    monkeypatch.setattr(terminal_module, "validate_terminal_access", AsyncMock(return_value=env.agent))
    monkeypatch.setattr(terminal_module.execution_agent_connections, "generation", lambda _id: 1)
    socket = SimpleNamespace(send_text=AsyncMock(), send_bytes=AsyncMock())
    viewer = BrowserTerminalConnection(env.terminal, socket)
    env.terminal.viewer = viewer
    task = env.start()
    approval = await pending(env)
    command_id = env.terminal.command_id
    await coordinator.release(viewer)
    assert env.terminal.attached and env.terminal.pending["approvalId"] == str(approval.id)
    restored = await coordinator.open(env.session.user_id, env.session.id, env.agent.id, env.terminal.login_id, socket)
    opened = json.loads(restored.queue.get_nowait()[0])
    snapshot = json.loads(restored.queue.get_nowait()[0])
    assert opened["restored"] and opened["terminalId"] == env.terminal.terminal_id
    assert snapshot["pending"]["approvalId"] == str(approval.id)
    env.native.hold = True
    env.native.submitted = True
    await asyncio.wait_for(env.native.started.wait(), 2)
    assert env.terminal.command_id == command_id and not task.done()
    assert env.terminal.status()["activeToolCallId"] == "call-1"
    await coordinator.release(restored)
    restored = await coordinator.open(env.session.user_id, env.session.id, env.agent.id, env.terminal.login_id, socket)
    restored.queue.get_nowait()
    snapshot = json.loads(restored.queue.get_nowait()[0])
    assert snapshot["busy"] and snapshot["activeToolCallId"] == "call-1" and snapshot["pending"] is None
    env.native.release.set()
    assert not (await task).is_error
    assert env.terminal.command_id is None
    await coordinator.shutdown()


async def test_runtime_close_child_cancellation_is_controlled(env):
    env.policy, env.native.hold = ToolExecutionPolicy.AUTO, True
    task = env.start()
    await asyncio.wait_for(env.native.started.wait(), 2)
    await TerminalCoordinator().terminate(env.terminal)
    result = await asyncio.wait_for(task, 2)
    assert result.metadata["errorCode"] == "process.interrupted" and not task.cancelled()


async def test_busy_prompt_wait_is_cancellable_without_isolated_fallback(env):
    env.native.busy = True
    task = env.start()
    await asyncio.sleep(0.02)
    assert not task.done() and env.native.executions == 0
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not env.approvals and env.terminal.command_id is None
    assert not any(op == "shell.run" for op, _ in env.native.calls)


@pytest.mark.parametrize("path", ["/usr/bin/python3", "/home/user/project/.env"])
async def test_shared_shell_keeps_normal_external_and_confidential_path_checks(env, path):
    env.policy = ToolExecutionPolicy.AUTO
    env.native.targets = [{"path": path, "kind": "path"}]
    task = env.start()
    approval = await pending(env)
    assert approval.available_decisions == (
        ["allow_once", "deny"] if path.endswith(".env") else ["allow_once", "allow_session", "deny"]
    )
    assert not env.native.executions
    await decide_agent_action(env.db, approval, "deny")
    await task


async def test_ui_cancellation_during_commit_does_not_lose_resolution(env):
    task = env.start()
    approval = await pending(env)
    committing, release = asyncio.Event(), asyncio.Event()

    async def commit():
        committing.set()
        await release.wait()

    env.db.commit.side_effect = commit
    ui = asyncio.create_task(decide_agent_action(env.db, approval, "allow_once"))
    await committing.wait()
    ui.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await ui
    assert not (await asyncio.wait_for(task, 2)).is_error
    assert approval.status == "approved" and env.native.executions == 1


async def test_ctrl_c_races_approval_without_unseen_execution(env):
    task = env.start()
    approval = await pending(env)
    env.native.cancelled = True
    ui = asyncio.create_task(decide_agent_action(env.db, approval, "allow_once"))
    results = await asyncio.gather(ui, task, return_exceptions=True)
    assert env.native.executions == 0
    assert not isinstance(results[-1], BaseException)
    assert env.run.status == "running" and approval.status == "expired"


async def test_unknown_completion_is_not_replayed(env):
    env.policy = ToolExecutionPolicy.AUTO
    env.native.exit_code = None
    result = await env.start()
    assert result.metadata["errorCode"] == "process.outcomeUnknown"
    assert json.loads(result.content)["exitCode"] is None
    assert env.native.executions == 1


async def test_uncertain_execution_retains_audit_and_captured_output(env):
    env.policy = ToolExecutionPolicy.AUTO
    request = env.native.request

    async def uncertain(operation, args, model, **kwargs):
        if operation == "terminal.shared.execute":
            await kwargs["on_event"](SimpleNamespace(event="stdout", data="partial"))
            raise ExecutionClientError("process.outcomeUnknown")
        return await request(operation, args, model, **kwargs)

    env.native.request = uncertain
    result = await env.start()
    payload = json.loads(result.content)
    assert payload["error"]["code"] == "process.outcomeUnknown"
    assert payload["requestedArguments"] == payload["executedArguments"] == {"command": "cat README.md"}
    assert payload["output"]["stdout"] == "partial"


async def test_process_closes_before_pending_approval_audit(env):
    task = env.start()
    approval = await pending(env)

    async def commit():
        assert any(op == "terminal.close" for op, _ in env.native.calls)

    env.db.commit.side_effect = commit
    await TerminalCoordinator().terminate(env.terminal)
    result = await asyncio.wait_for(task, 2)
    assert result.metadata["errorCode"] == "permission.userDenied"
    assert approval.status == "expired" and not task.cancelled()


async def test_personal_cd_does_not_promote_external_directory_to_workspace(env):
    env.policy = ToolExecutionPolicy.AUTO
    env.native.current_directory = "/outside"
    task = env.start()
    approval = await pending(env)
    assert approval.payload["sharedDraft"]["currentDirectory"] == "/outside"
    assert env.session.current_directory == env.run.current_directory_snapshot == "/home/user/project"
    assert env.native.executions == 0
    await decide_agent_action(env.db, approval, "deny")
    await task


@pytest.mark.parametrize("command_line,classification", [
    ("cat README.md", "read"), ("custom-command --option", "read"), ("touch generated.txt", "write"),
])
@pytest.mark.parametrize("complete", [False, True])
@pytest.mark.parametrize("policy", [ToolExecutionPolicy.AUTO, ToolExecutionPolicy.ASK, ToolExecutionPolicy.DISABLED])
async def test_attached_commands_use_normal_class_policy_even_when_incomplete(env, command_line, classification, complete, policy):
    env.native.analysis_complete = complete
    env.class_policies = {"read": ToolExecutionPolicy.DISABLED, "write": ToolExecutionPolicy.DISABLED, classification: policy}
    task = env.start(command_line)
    if policy == ToolExecutionPolicy.ASK:
        approval = await pending(env)
        assert approval.capability == f"shell.{classification}"
        assert approval.payload["shellAnalysisComplete"] == complete
        await decide_agent_action(env.db, approval, "allow_once")
    result = await asyncio.wait_for(task, 2)
    assert result.is_error == (policy == ToolExecutionPolicy.DISABLED)
    assert env.native.executions == (policy != ToolExecutionPolicy.DISABLED)
    assert all(request.capability == f"shell.{classification}" for request in env.evaluated)
    assert all(request.approval_details["shellAnalysisComplete"] == complete for request in env.evaluated)
    if policy != ToolExecutionPolicy.ASK:
        assert not env.approvals
    if policy != ToolExecutionPolicy.DISABLED:
        assert [args["freeze"] for op, args in env.native.calls if op == "terminal.shared.inspect"] == [False, True]
    ordinary = shell_command_permission(env.agent.id, env.native.current_directory, command_line, env.native.snapshot().inspection)
    assert replace(env.evaluated[0], approval_details=ordinary.approval_details, shared_command_id=None) == ordinary


@pytest.mark.parametrize("command_line,classification", [("cat README.md", "read"), ("touch generated.txt", "write")])
async def test_shared_allowlists_are_class_specific_and_do_not_override_disabled_policy(env, command_line, classification):
    verb = command_line.split()[0]
    env.prefixes[classification] = [[verb]]
    assert not (await asyncio.wait_for(env.start(command_line), 2)).is_error
    assert not env.approvals and env.native.executions == 1
    env.class_policies[classification] = ToolExecutionPolicy.DISABLED
    assert (await asyncio.wait_for(env.start(command_line), 2)).is_error
    assert env.native.executions == 1


@pytest.mark.parametrize("command_line,classification", [("cat README.md", "read"), ("touch generated.txt", "write")])
async def test_shared_allowlist_from_other_class_cannot_authorize_command(env, command_line, classification):
    env.prefixes["write" if classification == "read" else "read"] = [[command_line.split()[0]]]
    task = env.start(command_line)
    approval = await pending(env)
    assert env.native.executions == 0
    await decide_agent_action(env.db, approval, "deny")
    await task


@pytest.mark.parametrize("edited,targets", [
    ("touch changed.txt", []), ("custom-command", []),
    ("cat .env", [{"path": "/home/user/project/.env", "kind": "input"}]),
])
async def test_edit_refresh_updates_entire_permission_review_together(env, edited, targets):
    task = env.start()
    approval = await pending(env)
    env.native.targets = targets
    env.native.edit(edited)
    async with asyncio.timeout(2):
        while approval.payload["sharedDraft"]["command"] != edited:
            await asyncio.sleep(0.001)
    ordinary = shell_command_permission(env.agent.id, env.native.current_directory, edited, env.native.snapshot().inspection)
    for field in ("capability", "resource_scope", "title", "summary", "risk_level"):
        assert getattr(approval, field) == getattr(ordinary, field)
    assert approval.available_decisions == list(ordinary.available_decisions)
    for key, value in ordinary.approval_details.items():
        assert approval.payload[key] == value
    assert env.native.state == "editing"
    env.native.submitted = True
    assert not (await asyncio.wait_for(task, 2)).is_error
    assert not env.grants and approval.decision == "allow_once"


async def test_edited_allow_session_grants_only_actual_class_and_area(env):
    task = env.start()
    approval = await pending(env)
    original = approval.payload["initialReviewRevision"]
    env.native.edit("touch changed.txt")
    with pytest.raises(ApprovalDecisionError, match="approval.targetChanged"):
        await decide_agent_action(env.db, approval, "allow_session", expected_revision=original)
    assert approval.capability == "shell.write" and approval.risk_level == "high"
    await decide_agent_action(env.db, approval, "allow_session", expected_revision=approval.payload["sharedDraft"]["inspectionRevision"])
    assert not (await asyncio.wait_for(task, 2)).is_error
    assert len(env.grants) == 1
    grant = env.grants[0]
    assert grant.capability == "resource.write"
    assert json.loads(grant.resource_scope)["path"] == env.session.current_directory
    assert grant.agent_profile_id == env.session.agent_profile_id and grant.trust_level == "public"
    assert [args["freeze"] for op, args in env.native.calls if op == "terminal.shared.inspect"] == [False, True]
    assert not (await asyncio.wait_for(env.start("touch next.txt"), 2)).is_error
    assert len(env.approvals) == 1
    read = env.start("cat README.md")
    read_approval = await pending(env)
    assert read_approval.capability == "shell.read"
    await decide_agent_action(env.db, read_approval, "deny")
    await read


async def test_edit_without_prefix_keeps_area_session_grant_decision(env):
    task = env.start()
    approval = await pending(env)
    original = approval.payload["initialReviewRevision"]
    env.native.edit("custom-command")
    with pytest.raises(ApprovalDecisionError, match="approval.targetChanged"):
        await decide_agent_action(env.db, approval, "allow_session", expected_revision=original)
    assert approval.available_decisions == ["allow_once", "allow_session", "deny"]
    assert approval.capability == "shell.read" and not approval.payload["shellAnalysisComplete"]
    assert approval.payload["shellPrefixes"] == []
    await decide_agent_action(
        env.db,
        approval,
        "allow_session",
        expected_revision=approval.payload["sharedDraft"]["inspectionRevision"],
    )
    assert not (await asyncio.wait_for(task, 2)).is_error
    assert env.grants[0].capability == "resource.read"
    assert approval.decision == "allow_session"


async def test_read_grant_cannot_authorize_edit_to_write(env):
    env.native.line = "cat README.md"
    request = shell_command_permission(
        env.agent.id,
        env.session.current_directory,
        "cat README.md",
        env.native.snapshot().inspection,
    )
    scope = request.effective_session_grants[0]
    env.grants.append(PermissionGrant(
        session_id=env.session.id, capability=scope.capability,
        resource_type=scope.resource_type, resource_scope=scope.resource_scope,
    ))
    authorize = env.context.ask_handler
    first = True

    async def edit_after_authorization(permission):
        nonlocal first
        decision = await authorize(permission)
        if first:
            first = False
            env.native.edit("cat README.md > copied.md")
        return decision

    env.context.ask_handler = edit_after_authorization
    task = env.start()
    approval = await pending(env)
    assert approval.capability == "shell.write" and env.native.executions == 0
    assert approval.payload["shellPrefixes"] == [["cat"]]
    await decide_agent_action(env.db, approval, "allow_once")
    assert not (await asyncio.wait_for(task, 2)).is_error
    assert len(env.grants) == 1 and env.grants[0].capability == "resource.read"
    assert [args["freeze"] for op, args in env.native.calls if op == "terminal.shared.inspect"] == [False, True]


@pytest.mark.parametrize("complete", [False, True])
async def test_shared_metadata_targets_use_the_ordinary_permission_builder(env, complete):
    env.policy = ToolExecutionPolicy.AUTO
    env.native.analysis_complete = complete
    env.native.targets = [{"path": "/usr/bin/python3", "kind": "toolchain_metadata"}]
    assert not (await asyncio.wait_for(env.start(), 2)).is_error
    assert env.evaluated[0].shell_metadata_paths == frozenset({"/usr/bin/python3"})
    assert not env.approvals
    env.agent.external_filesystem_policy = "deny"
    assert (await asyncio.wait_for(env.start(), 2)).is_error
    assert env.native.executions == 1


async def test_auto_read_cannot_execute_edited_write_when_write_policy_is_disabled(env):
    env.class_policies = {"read": ToolExecutionPolicy.AUTO, "write": ToolExecutionPolicy.DISABLED}
    authorize = env.context.ask_handler

    async def edit_after_authorization(permission):
        decision = await authorize(permission)
        if permission.capability == "shell.read":
            env.native.edit("touch changed.txt")
        return decision

    env.context.ask_handler = edit_after_authorization
    result = await asyncio.wait_for(env.start(), 2)
    assert result.metadata["errorCode"] == "permission.denied"
    assert [request.capability for request in env.evaluated] == ["shell.read", "shell.write"]
    assert env.native.executions == 0 and not env.approvals
    assert [args["freeze"] for op, args in env.native.calls if op == "terminal.shared.inspect"] == [False, True]


@pytest.mark.parametrize("change", ["cancel", "inspection"])
async def test_native_execute_rechecks_after_approval_without_another_freeze(env, change):
    task = env.start()
    approval = await pending(env)

    async def commit():
        if approval.status == "approved":
            if change == "cancel":
                env.native.cancelled = True
            else:
                env.native.targets = [{"path": "/home/user/project/changed.txt", "kind": "input"}]

    env.db.commit.side_effect = commit
    await decide_agent_action(env.db, approval, "allow_once")
    result = await asyncio.wait_for(task, 2)
    assert result.metadata["errorCode"] == "process.interrupted"
    assert not task.cancelled() and env.native.executions == 0
    assert [args["freeze"] for op, args in env.native.calls if op == "terminal.shared.inspect"] == [False, True]
