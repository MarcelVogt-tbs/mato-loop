import hashlib
import json
from io import BytesIO
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from mato_loop_backend.agent.permissions import (
    PermissionDecision,
    PermissionPolicy,
    PermissionRequest,
    shell_command_scope,
    shell_prefix_allowed,
)
from mato_loop_backend.agent.runtime import _tool_policy
from mato_loop_backend.agent.tool_registry import ToolContext
from mato_loop_backend.agent.tools import (
    _execution_path,
    tool_registry,
)
from mato_loop_backend.execution import (
    DirectoryResult,
    ExecutionClient,
    ExecutionClientError,
    ProcessResult,
    ShellInspectResult,
    WorkspaceReadResult,
    WorkspaceWriteResult,
    transfer_tickets,
)
from mato_loop_backend.execution.protocol import ResponseMessage
from mato_loop_backend.execution.terminals import TerminalDecision, terminal_coordinator
from mato_loop_backend.models import ChatSession, ExecutionAgent, PermissionGrant
from mato_loop_backend.providers.base import ToolCall
from mato_loop_backend.settings import IntegrationSettings, ToolExecutionPolicy, ToolSettings
from mato_loop_backend.workspace import RootFileService, SessionStorage


class FakeExecutionClient:
    def __init__(self, responses, operations, current_directory="/home/user/project") -> None:
        self.agent_id = uuid4()
        self.current_directory = current_directory
        self.operations = frozenset(operations)
        self.responses = responses
        self.requests = []

    def supports(self, operation: str) -> bool:
        return operation in self.operations

    async def request(self, operation, arguments, _result_model, **_kwargs):
        self.requests.append((operation, arguments))
        response = self.responses[operation]
        return response(arguments) if callable(response) else response


def _context(tmp_path, call, client, *, decision=PermissionDecision.ALLOW):
    session = ChatSession(
        id=uuid4(),
        user_id=uuid4(),
        title="Test",
        memory_mode="disabled",
        web_search_policy="available",
        current_directory=client.current_directory,
    )
    storage = SessionStorage(tmp_path / "sessions")
    storage.provision(session.id)
    return ToolContext(
        db=AsyncMock(),
        run_id=uuid4(),
        session=session,
        tool_call=call,
        root_files=RootFileService(session.id, session_storage=storage),
        integration_settings=IntegrationSettings(),
        ask_handler=AsyncMock(return_value=decision),
        tool_settings=ToolSettings(),
        execution_client=client,
    )


def _tools(operations):
    return tool_registry.effective(
        IntegrationSettings(),
        tool_settings=ToolSettings(),
        execution_operations=frozenset(operations),
    )


def test_process_tools_cannot_select_execution_targets() -> None:
    tools = _tools({"process.exec", "shell.run"})
    process = next(item for item in tools.schemas() if item["name"] == "process_exec")
    shell = next(item for item in tools.schemas() if item["name"] == "shell_run")
    assert set(process["parameters"]["properties"]) == {"program", "args"}
    assert set(shell["parameters"]["properties"]) == {"command"}


async def test_execution_client_forwards_exact_arguments_without_context(monkeypatch) -> None:
    agent_id = uuid4()
    client = ExecutionClient(agent_id, frozenset({"filesystem.read"}), "/home/user")
    request = AsyncMock(
        return_value=ResponseMessage.model_validate(
            {
                "type": "response",
                "id": uuid4(),
                "ok": True,
                "result": {
                    "content": "hello",
                    "linesRead": 1,
                    "hasMore": False,
                    "nextOffset": None,
                    "truncatedLines": [],
                    "sha256": "b" * 64,
                },
            }
        )
    )
    monkeypatch.setattr(
        "mato_loop_backend.execution.client.execution_agent_connections.request", request
    )

    await client.request(
        "filesystem.read", {"path": "/etc/hosts"}, WorkspaceReadResult
    )

    request.assert_awaited_once_with(
        agent_id, "filesystem.read", {"path": "/etc/hosts"}, timeout=None, on_event=None
    )


async def test_execution_client_rejects_relative_result_path(monkeypatch) -> None:
    client = ExecutionClient(uuid4(), frozenset({"filesystem.write"}))
    monkeypatch.setattr(
        "mato_loop_backend.execution.client.execution_agent_connections.request",
        AsyncMock(
            return_value=ResponseMessage.model_validate(
                {
                    "type": "response",
                    "id": uuid4(),
                    "ok": True,
                    "result": {"path": "relative.txt", "sha256": "a" * 64, "size": 1},
                }
            )
        ),
    )
    with pytest.raises(ExecutionClientError) as error:
        await client.request(
            "filesystem.write", {"path": "/tmp/file"}, WorkspaceWriteResult
        )
    assert error.value.code == "executionAgent.protocolError"


def test_execution_result_accepts_the_filesystem_root() -> None:
    assert DirectoryResult(path="/").path == "/"


async def test_execution_client_imports_absolute_path_with_transfer_ticket(monkeypatch) -> None:
    agent_id = uuid4()
    data = b"managed snapshot"
    digest = hashlib.sha256(data).hexdigest()
    client = ExecutionClient(agent_id, frozenset({"filesystem.import"}))
    monkeypatch.setattr(
        "mato_loop_backend.execution.client.execution_agent_connections.transfer_url",
        lambda _agent_id, ticket_id: f"https://backend.test/transfers/{ticket_id}",
    )

    async def request(selected_agent_id, _operation, arguments, **_kwargs):
        ticket_id = next(
            ticket_id
            for ticket_id in transfer_tickets._tickets
            if str(ticket_id) in arguments["transfer"]["url"]
        )
        ticket = transfer_tickets.claim(
            ticket_id,
            arguments["transfer"]["token"],
            "download",
            owner_id=selected_agent_id,
        )
        assert ticket.content is not None and ticket.content.read() == data
        return ResponseMessage.model_validate(
            {
                "type": "response",
                "id": uuid4(),
                "ok": True,
                "result": {
                    "path": "/tmp/snapshot.bin",
                    "size": len(data),
                    "sha256": digest,
                },
            }
        )

    monkeypatch.setattr(
        "mato_loop_backend.execution.client.execution_agent_connections.request", request
    )
    result = await client.write_bytes(
        "/tmp/snapshot.bin", BytesIO(data), limit=len(data)
    )
    assert result.path == "/tmp/snapshot.bin"


def test_model_paths_resolve_against_current_directory(tmp_path) -> None:
    context = _context(
        tmp_path,
        ToolCall("call", "read", {}),
        FakeExecutionClient({}, {"filesystem.read"}),
    )
    assert _execution_path(context, "src/../README.md") == "/home/user/project/README.md"
    assert _execution_path(context, "/etc/hosts") == "/etc/hosts"


async def test_workspace_read_dispatches_absolute_filesystem_path(tmp_path) -> None:
    call = ToolCall(
        "call",
        "read",
        {"root": "workspace", "path": "README.md", "offset": 1, "limit": 20},
    )
    client = FakeExecutionClient(
        {
            "filesystem.stat": lambda arguments: type(
                "Stat", (), {"kind": "file"}
            )(),
            "filesystem.read": WorkspaceReadResult(
                content="hello",
                lines_read=1,
                has_more=False,
                next_offset=None,
                truncated_lines=[],
                sha256="a" * 64,
            ),
        },
        {"filesystem.stat", "filesystem.read"},
    )
    context = _context(tmp_path, call, client)
    result = await _tools(client.operations).execute(call, context)
    assert not result.is_error
    assert client.requests[0] == (
        "filesystem.stat",
        {"path": "/home/user/project/README.md"},
    )


def _shell_permission(client, classification="write", prefixes=None, directory=None):
    return PermissionRequest(
        capability=f"shell.{classification}",
        resource_type="shell_command",
        resource_scope=shell_command_scope(
            client.agent_id,
            directory or client.current_directory,
            classification,
            prefixes or [],
            [],
            True,
        ),
        action_kind="shell_run",
        risk_level="high",
        title="Shell-Befehl ausführen?",
        summary="Test",
    )


def test_shell_grant_matches_prefix_class_agent_and_directory(tmp_path) -> None:
    client = FakeExecutionClient({}, {"shell.inspect", "shell.run"})
    context = _context(tmp_path, ToolCall("shell", "shell_run", {}), client)
    granted = _shell_permission(client, prefixes=[["git", "status"]])
    matching = _shell_permission(client, prefixes=[["git", "status"]])
    wrong_class = _shell_permission(client, classification="read", prefixes=[["git", "status"]])
    wrong_directory = _shell_permission(
        client, prefixes=[["git", "status"]], directory="/home/user/other"
    )
    grant = PermissionGrant(
        session_id=context.session.id,
        capability=granted.capability,
        resource_type=granted.resource_type,
        resource_scope=granted.resource_scope,
    )

    assert PermissionPolicy.grant_matches(grant, matching, context.root_files)
    assert not PermissionPolicy.grant_matches(grant, wrong_class, context.root_files)
    assert not PermissionPolicy.grant_matches(grant, wrong_directory, context.root_files)


def test_configured_shell_prefixes_are_class_specific(tmp_path) -> None:
    client = FakeExecutionClient({}, {"shell.inspect", "shell.run"})
    settings = ToolSettings(shell_prefixes={"read": [["cat"]], "write": [["git"]]})

    assert shell_prefix_allowed(
        settings, _shell_permission(client, prefixes=[["git", "status"]])
    )
    assert not shell_prefix_allowed(
        settings,
        _shell_permission(client, classification="read", prefixes=[["git", "status"]]),
    )
    request = _shell_permission(client, prefixes=[["git", "status"]])
    assert _tool_policy(settings, IntegrationSettings(), request) == ToolExecutionPolicy.AUTO
    settings.permissions.policies["shell.write"] = ToolExecutionPolicy.DISABLED
    assert _tool_policy(settings, IntegrationSettings(), request) == ToolExecutionPolicy.DISABLED


async def test_disabled_shell_zone_is_a_hard_boundary(tmp_path) -> None:
    call = ToolCall("shell", "shell_run", {"command": "cat README.md"})
    client = FakeExecutionClient({}, {"shell.inspect", "shell.run"})
    context = _context(tmp_path, call, client)
    context.db.scalar.return_value = ExecutionAgent(
        id=client.agent_id,
        user_id=context.session.user_id,
        alias="Computer",
        home_directory="/home/user",
        home_filesystem_policy="auto",
        external_filesystem_policy="ask",
        home_shell_enabled=False,
    )

    decision = await PermissionPolicy.evaluate(
        context.db,
        context.session,
        _shell_permission(client, classification="read", prefixes=[["cat"]]),
        context.root_files,
        policy="auto",
    )

    assert decision == PermissionDecision.DENY
    statement = context.db.scalar.await_args.args[0]
    assert statement.get_execution_options()["populate_existing"] is True


async def test_shell_dispatches_with_inspection_classification(tmp_path) -> None:
    call = ToolCall("shell", "shell_run", {"command": "cat README.md"})
    client = FakeExecutionClient(
        {
            "shell.inspect": ShellInspectResult(
                targets=[],
                analysis_complete=True,
                classification="read",
                read_only=True,
                commands=[
                    {"tokens": ["cat", "README.md"], "prefix": ["cat"], "classification": "read"}
                ],
                inspection_revision="a" * 64,
            ),
            "shell.run": ProcessResult(
                exit_code=0,
                timed_out=False,
                duration_ms=1,
                stdout_bytes=0,
                stderr_bytes=0,
                stdout_truncated=False,
                stderr_truncated=False,
            ),
        },
        {"shell.inspect", "shell.run"},
    )
    context = _context(tmp_path, call, client)

    result = await _tools(client.operations).execute(call, context)

    assert not result.is_error
    assert [operation for operation, _arguments in client.requests] == [
        "shell.inspect",
        "shell.run",
    ]
    request = context.ask_handler.await_args.args[0]
    assert request.capability == "shell.read"
    assert request.approval_details == {
        "shellClassification": "read",
        "shellAnalysisComplete": True,
        "shellPrefixes": [["cat"]],
    }


async def test_shell_labels_incomplete_inspection_as_unverified(tmp_path) -> None:
    call = ToolCall("shell", "shell_run", {"command": "printf '%s\\n' test"})
    client = FakeExecutionClient(
        {
            "shell.inspect": ShellInspectResult(
                targets=[],
                analysis_complete=False,
                classification="write",
                read_only=False,
                commands=[],
                inspection_revision="b" * 64,
            ),
            "shell.run": ProcessResult(
                exit_code=0,
                timed_out=False,
                duration_ms=1,
                stdout_bytes=0,
                stderr_bytes=0,
                stdout_truncated=False,
                stderr_truncated=False,
            ),
        },
        {"shell.inspect", "shell.run"},
    )
    context = _context(tmp_path, call, client)

    result = await _tools(client.operations).execute(call, context)

    assert not result.is_error
    request = context.ask_handler.await_args.args[0]
    assert request.capability == "shell.write"
    assert request.risk_level == "high"
    assert request.title == "Nicht vollständig geprüften Shell-Befehl ausführen?"
    assert request.summary == (
        "Der Befehl konnte nicht vollständig geprüft werden und wird vorsichtshalber "
        "wie ein schreibender Befehl behandelt."
    )
    assert request.approval_details == {
        "shellClassification": "write",
        "shellAnalysisComplete": False,
        "shellPrefixes": [],
    }


async def test_terminal_auto_keeps_policy_and_uses_terminal_run(tmp_path, monkeypatch) -> None:
    call = ToolCall("process", "process_exec", {"program": "python", "args": ["-V"]})
    client = FakeExecutionClient({}, {"process.exec", "terminal.run"})
    context = _context(tmp_path, call, client)
    monkeypatch.setattr(terminal_coordinator, "mode_for", lambda **_kwargs: "auto")
    run = AsyncMock(
        return_value=ProcessResult(
            exit_code=0,
            timed_out=False,
            duration_ms=1,
            stdout_bytes=0,
            stderr_bytes=0,
            stdout_truncated=False,
            stderr_truncated=False,
        )
    )
    monkeypatch.setattr(terminal_coordinator, "run", run)

    result = await _tools(client.operations).execute(call, context)

    assert not result.is_error
    assert context.ask_handler.await_count == 2
    assert run.await_args.kwargs["arguments"] == {"program": "python", "args": ["-V"]}


async def test_terminal_confirm_reinspects_edited_shell_and_reports_execution(
    tmp_path, monkeypatch
) -> None:
    call = ToolCall("shell", "shell_run", {"command": "cat README.md"})

    def inspection(arguments):
        edited = arguments["command"] == "cat pyproject.toml"
        return ShellInspectResult(
            targets=[],
            analysis_complete=True,
            classification="read",
            read_only=True,
            commands=[{"tokens": ["cat"], "prefix": ["cat"], "classification": "read"}],
            inspection_revision=("b" if edited else "a") * 64,
        )

    client = FakeExecutionClient(
        {"shell.inspect": inspection}, {"shell.inspect", "shell.run", "terminal.run"}
    )
    context = _context(tmp_path, call, client)
    monkeypatch.setattr(terminal_coordinator, "mode_for", lambda **_kwargs: "confirm")
    evaluate = AsyncMock(side_effect=AssertionError("confirm must bypass policy"))
    monkeypatch.setattr(PermissionPolicy, "evaluate", evaluate)
    propose = AsyncMock(return_value=TerminalDecision(True, "cat pyproject.toml"))
    monkeypatch.setattr(terminal_coordinator, "propose", propose)
    run = AsyncMock(
        return_value=ProcessResult(
            exit_code=0,
            timed_out=False,
            duration_ms=2,
            stdout_bytes=4,
            stderr_bytes=0,
            stdout_truncated=False,
            stderr_truncated=False,
        )
    )
    monkeypatch.setattr(terminal_coordinator, "run", run)

    result = await _tools(client.operations).execute(call, context)
    payload = json.loads(result.content)

    assert context.ask_handler.await_count == 0
    assert evaluate.await_count == 0
    assert [request[1]["command"] for request in client.requests] == ["cat pyproject.toml"]
    assert run.await_args.kwargs["arguments"] == {
        "command": "cat pyproject.toml",
        "expectedInspectionRevision": "b" * 64,
    }
    assert payload["requestedArguments"] == {"command": "cat README.md"}
    assert payload["executedArguments"] == {"command": "cat pyproject.toml"}
    assert payload["userEdited"] is True


async def test_terminal_confirm_process_uses_shlex_and_distinguishes_unavailable_from_denial(
    tmp_path, monkeypatch
) -> None:
    call = ToolCall("process", "process_exec", {"program": "python", "args": ["one"]})
    client = FakeExecutionClient({}, {"process.exec", "terminal.run"})
    context = _context(tmp_path, call, client)
    monkeypatch.setattr(terminal_coordinator, "mode_for", lambda **_kwargs: "confirm")
    evaluate = AsyncMock(side_effect=AssertionError("confirm must bypass policy"))
    monkeypatch.setattr(PermissionPolicy, "evaluate", evaluate)
    propose = AsyncMock(return_value=TerminalDecision(True, "python 'two words'"))
    monkeypatch.setattr(terminal_coordinator, "propose", propose)
    run = AsyncMock(
        return_value=ProcessResult(
            exit_code=0,
            timed_out=False,
            duration_ms=1,
            stdout_bytes=0,
            stderr_bytes=0,
            stdout_truncated=False,
            stderr_truncated=False,
        )
    )
    monkeypatch.setattr(terminal_coordinator, "run", run)

    result = await _tools(client.operations).execute(call, context)

    assert propose.await_args.kwargs["command_line"] == "python one"
    assert run.await_args.kwargs["arguments"] == {
        "program": "python",
        "args": ["two words"],
    }
    assert json.loads(result.content)["userEdited"] is True
    assert context.ask_handler.await_count == 0
    assert evaluate.await_count == 0

    context.user_denied = False
    propose.return_value = TerminalDecision(False)
    unavailable = await _tools(client.operations).execute(call, context)
    assert json.loads(unavailable.content)["error"]["code"] == "terminal.disconnected"
    assert context.user_denied is False

    propose.return_value = TerminalDecision(False, explicit_denial=True)
    denied = await _tools(client.operations).execute(call, context)
    assert json.loads(denied.content)["error"]["code"] == "permission.userDenied"
    assert context.user_denied is True
