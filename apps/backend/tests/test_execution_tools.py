import hashlib
from io import BytesIO
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from mato_loop_backend.agent.permissions import PermissionDecision, PermissionPolicy
from mato_loop_backend.agent.tool_registry import ToolContext
from mato_loop_backend.agent.tools import (
    _execution_path,
    _process_permission,
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
from mato_loop_backend.models import ChatSession, ExecutionAgent, PermissionGrant
from mato_loop_backend.providers.base import ToolCall
from mato_loop_backend.settings import IntegrationSettings, ToolSettings
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


def test_read_only_shell_grant_does_not_match_risky_inspection(tmp_path) -> None:
    client = FakeExecutionClient({}, {"shell.inspect", "shell.run"})
    context = _context(tmp_path, ToolCall("shell", "shell_run", {}), client)
    read_only = _process_permission(
        context, {"command": "cat README.md"}, shell=True, read_only=True
    )
    risky = _process_permission(context, {"command": "cat README.md"}, shell=True)
    grant = PermissionGrant(
        session_id=context.session.id,
        capability=read_only.capability,
        resource_type=read_only.resource_type,
        resource_scope=read_only.resource_scope,
    )

    assert PermissionPolicy.grant_matches(grant, read_only, context.root_files)
    assert not PermissionPolicy.grant_matches(grant, risky, context.root_files)


async def test_shell_policy_deny_after_approval_blocks_dispatch(tmp_path) -> None:
    call = ToolCall("shell", "shell_run", {"command": "cat README.md"})
    client = FakeExecutionClient(
        {
            "shell.inspect": ShellInspectResult(
                targets=[],
                analysis_complete=True,
                read_only=True,
                inspection_revision="a" * 64,
            ),
        },
        {"shell.inspect", "shell.run"},
    )
    context = _context(tmp_path, call, client)
    context.db.scalar.side_effect = [
        ExecutionAgent(
            id=client.agent_id,
            user_id=context.session.user_id,
            alias="Computer",
            home_directory="/home/user",
            home_shell_policy="ask",
            external_shell_policy="ask",
        ),
        ExecutionAgent(
            id=client.agent_id,
            user_id=context.session.user_id,
            alias="Computer",
            home_directory="/home/user",
            home_shell_policy="deny",
            external_shell_policy="deny",
        ),
    ]

    result = await _tools(client.operations).execute(call, context)

    assert result.is_error
    assert [operation for operation, _arguments in client.requests] == ["shell.inspect"]
    context.ask_handler.assert_awaited_once()


async def test_auto_shell_policy_dispatches_without_approval(tmp_path) -> None:
    call = ToolCall("shell", "shell_run", {"command": "cat README.md"})
    client = FakeExecutionClient(
        {
            "shell.inspect": ShellInspectResult(
                targets=[],
                analysis_complete=True,
                read_only=True,
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
    context.db.scalar.return_value = ExecutionAgent(
        id=client.agent_id,
        user_id=context.session.user_id,
        alias="Computer",
        home_directory="/home/user",
        home_shell_policy="auto",
        external_shell_policy="deny",
    )

    result = await _tools(client.operations).execute(call, context)

    assert not result.is_error
    assert [operation for operation, _arguments in client.requests] == [
        "shell.inspect",
        "shell.run",
    ]
    context.ask_handler.assert_not_awaited()
