import json
from dataclasses import replace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.agent.permissions import PermissionDecision, PermissionPolicy
from mato_loop_backend.agent.tools import ToolContext, tool_registry
from mato_loop_backend.execution import (
    DirectoryResult,
    ExecutionClient,
    ExecutionClientError,
    ProcessResult,
    ShellInspectResult,
    SystemInfoResult,
    WorkspaceDeleteResult,
    WorkspaceGlobResult,
    WorkspaceGrepResult,
    WorkspaceListResult,
    WorkspacePathResult,
    WorkspaceReadResult,
    WorkspaceStatResult,
    WorkspaceWriteResult,
)
from mato_loop_backend.execution.client import WorkspaceGrepMatch
from mato_loop_backend.execution.protocol import EventMessage, ResponseMessage
from mato_loop_backend.models import ChatSession, PermissionGrant
from mato_loop_backend.providers.base import ToolCall
from mato_loop_backend.settings import (
    IntegrationSettings,
    ToolExecutionPolicy,
    ToolSettings,
)
from mato_loop_backend.workspace import RootFileService, SessionStorage


class FakeExecutionClient:
    def __init__(self, responses, operations) -> None:
        self.agent_id = uuid4()
        self.root_id = uuid4()
        self.root_revision = "a" * 64
        self.working_directory = "project"
        self.current_directory = "/workspace/project"
        self.operations = frozenset(operations)
        self.responses = responses
        self.requests = []

    def supports(self, operation: str) -> bool:
        return operation in self.operations

    async def request(self, operation, arguments, result_model, **kwargs):
        self.requests.append((operation, arguments))
        if on_event := kwargs.get("on_event"):
            await on_event(
                EventMessage(
                    type="event", id=uuid4(), event="stdout", data="streamed\n"
                )
            )
        response = self.responses[operation]
        return response(arguments) if callable(response) else response


def _context(tmp_path, call, client, *, decision=PermissionDecision.ALLOW):
    session = ChatSession(
        id=uuid4(),
        user_id=uuid4(),
        title="Test",
        memory_mode="disabled",
        web_search_policy="available",
        working_directory="project",
        workspace_access="read-write",
    )
    storage = SessionStorage(tmp_path / "sessions")
    storage.provision(session.id)
    return ToolContext(
        db=AsyncMock(),
        run_id=uuid4(),
        session=session,
        tool_call=call,
        root_files=RootFileService(
            session.id,
            session_storage=storage,
            workspace_home=tmp_path / "backend-home",
            working_directory=session.working_directory,
        ),
        integration_settings=IntegrationSettings(),
        ask_handler=AsyncMock(return_value=decision),
        tool_settings=ToolSettings(),
        execution_client=client,
    )


def _tools(operations):
    return tool_registry.effective(
        IntegrationSettings(),
        "read-write",
        tool_settings=ToolSettings(),
        execution_operations=frozenset(operations),
    )


def test_process_tools_are_gated_and_cannot_select_execution_targets() -> None:
    only_exec = _tools({"process.exec"})
    assert "process_exec" in only_exec.tools
    assert "shell_run" not in only_exec.tools

    schema = next(item for item in only_exec.schemas() if item["name"] == "process_exec")
    assert set(schema["parameters"]["properties"]) == {"program", "args"}
    assert not {"root", "rootId", "workingDirectory", "executable"} & set(
        schema["parameters"]["properties"]
    )


async def test_execution_client_adds_only_snapshot_root_and_sanitizes_agent_errors(
    monkeypatch,
) -> None:
    agent_id = uuid4()
    root_id = uuid4()
    client = ExecutionClient(
        agent_id, root_id, "a" * 64, "project", frozenset({"workspace.read"}), "/workspace/project"
    )
    request = AsyncMock(
        return_value=ResponseMessage.model_validate(
            {
                "type": "response",
                "id": uuid4(),
                "ok": True,
                "result": {
                    "content": "1: hello",
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
        "workspace.read", {"path": "README.md"}, WorkspaceReadResult
    )
    assert request.await_args.args == (
        agent_id,
        "workspace.read",
        {
            "rootId": str(root_id),
            "rootRevision": "a" * 64,
            "workingDirectory": "project",
            "path": "README.md",
        },
    )

    request.return_value = ResponseMessage.model_validate(
        {
            "type": "response",
            "id": uuid4(),
            "ok": False,
            "error": {"code": "private.rawFailure", "params": {"secret": "value"}},
        }
    )
    with pytest.raises(ExecutionClientError) as exc_info:
        await client.request(
            "workspace.read", {"path": "README.md"}, WorkspaceReadResult
        )
    assert exc_info.value.code == "executionAgent.protocolError"
    assert "secret" not in str(exc_info.value)


async def test_execution_client_supports_root_independent_operations(monkeypatch) -> None:
    agent_id = uuid4()
    client = ExecutionClient(
        agent_id,
        None,
        None,
        None,
        frozenset({"system.info", "filesystem.resolve"}),
    )
    request = AsyncMock()
    monkeypatch.setattr(
        "mato_loop_backend.execution.client.execution_agent_connections.request", request
    )
    request.return_value = ResponseMessage.model_validate(
        {
            "type": "response",
            "id": uuid4(),
            "ok": True,
            "result": {
                "agentVersion": "1",
                "platform": "linux",
                "operatingSystem": "Linux",
                "kernel": "6",
                "architecture": "x86_64",
                "logicalCpuCount": 4,
            },
        }
    )
    await client.request("system.info", {}, SystemInfoResult)
    assert request.await_args.args[:3] == (agent_id, "system.info", {})

    request.return_value = ResponseMessage.model_validate(
        {
            "type": "response",
            "id": uuid4(),
            "ok": True,
            "result": {
                "path": "/workspace",
                    "rootId": str(uuid4()),
                "rootRevision": "a" * 64,
                "relativePath": ".",
            },
        }
    )
    result = await client.request(
        "filesystem.resolve", {"path": "/workspace"}, DirectoryResult
    )
    assert result.path == "/workspace"


async def test_system_info_and_shell_inspection_use_selected_computer(tmp_path) -> None:
    system_call = ToolCall("system", "get_system_info", {})
    client = FakeExecutionClient(
        {
            "system.info": SystemInfoResult(
                agent_version="1",
                platform="linux",
                operating_system="Linux",
                kernel="6",
                architecture="x86_64",
                logical_cpu_count=4,
            ),
            "shell.inspect": ShellInspectResult(
                targets=[], analysis_complete=False, inspection_revision="c" * 64
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
        {"system.info", "shell.inspect", "shell.run"},
    )
    context = _context(tmp_path, system_call, client)

    info = await _tools(client.operations).execute(system_call, context)
    context.tool_call = ToolCall("shell", "shell_run", {"command": "pwd"})
    shell = await _tools(client.operations).execute(context.tool_call, context)

    assert json.loads(info.content)["operatingSystem"] == "Linux"
    assert not shell.is_error
    assert [request[0] for request in client.requests] == [
        "system.info",
        "shell.inspect",
        "shell.run",
    ]
    assert context.ask_handler.await_args_list[-1].args[0].force_ask is True
    assert "Administratorrechten" in context.ask_handler.await_args_list[-1].args[0].summary
    assert [
        (item.label, item.value, item.format)
        for item in context.ask_handler.await_args_list[-1].args[0].display_items
    ] == [("Shell-Befehl", "pwd", "code")]


async def test_workspace_tools_delegate_and_derive_canonical_results(tmp_path) -> None:
    call = ToolCall("write-1", "write_file", {"root": "workspace", "path": "a.txt", "content": "x"})
    client = FakeExecutionClient(
        {
            "workspace.write": WorkspaceWriteResult(
                path="a.txt", sha256="a" * 64, size=1
            ),
            "workspace.stat": WorkspaceStatResult(
                path="a.txt", kind="file", size=1, modified_at=1.0
            ),
        },
        {"workspace.write", "workspace.stat"},
    )
    context = _context(tmp_path, call, client)
    context.ask_handler = AsyncMock(return_value=PermissionDecision.ALLOW)

    written = await _tools(client.operations).execute(call, context)
    assert json.loads(written.content) == {"root": "workspace", "written": "a.txt"}
    action = written.file_actions[0]
    assert action.root_identity == str(client.root_id)
    assert action.execution_agent_id == str(client.agent_id)
    assert action.root_id == str(client.root_id)
    assert action.root_revision == client.root_revision
    assert action.working_directory == client.working_directory
    assert client.requests == [
        ("workspace.write", {"path": "a.txt", "content": "x"})
    ]
    assert context.ask_handler.await_count == 2

    provide = ToolCall(
        "provide-1", "provide_file", {"root": "workspace", "path": "a.txt"}
    )
    context.tool_call = provide
    provided = await _tools(client.operations).execute(provide, context)
    payload = json.loads(provided.content)
    assert payload["sizeBytes"] == 1
    assert f"root_identity={client.root_id}" in payload["downloadUrl"]
    assert client.requests[-1] == ("workspace.stat", {"path": "a.txt"})


async def test_execution_workspace_grants_match_only_same_snapshot_descendants(
    tmp_path,
) -> None:
    call = ToolCall(
        "read-1", "read", {"root": "workspace", "path": "docs"}
    )
    client = FakeExecutionClient({}, {"workspace.stat", "workspace.read"})
    client.current_directory = None
    context = _context(tmp_path, call, client, decision=PermissionDecision.DENY)
    await _tools(client.operations).execute(call, context)
    granted_request = context.ask_handler.await_args_list[-1].args[0]
    grant = PermissionGrant(
        session_id=context.session.id,
        capability=granted_request.capability,
        resource_type=granted_request.resource_type,
        resource_scope=granted_request.resource_scope,
    )

    def request(path: str, **changes):
        scope = json.loads(granted_request.resource_scope) | {"path": path} | changes
        return replace(
            granted_request,
            resource_scope=json.dumps(
                scope,
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            ),
        )

    descendant = request("docs/readme.md")
    assert PermissionPolicy.grant_matches(grant, descendant, context.root_files)
    assert not PermissionPolicy.grant_matches(
        grant, request("docs-other/readme.md"), context.root_files
    )
    assert not PermissionPolicy.grant_matches(
        grant,
        replace(
            descendant,
            resource_scope=json.dumps(json.loads(descendant.resource_scope)),
        ),
        context.root_files,
    )
    for changed in (
        {"agentId": str(uuid4())},
        {"rootId": str(uuid4())},
        {"rootRevision": "b" * 64},
        {"workingDirectory": "other"},
    ):
        assert not PermissionPolicy.grant_matches(
            grant, request("docs/readme.md", **changed), context.root_files
        )

    db_result = Mock()
    db_result.all.return_value = [grant]
    db = AsyncMock(spec=AsyncSession)
    db.scalars.return_value = db_result
    assert (
        await PermissionPolicy.evaluate(
            db,
            context.session,
            descendant,
            context.root_files,
            ToolExecutionPolicy.ASK,
        )
        == PermissionDecision.ALLOW
    )


async def test_all_workspace_model_operations_use_the_execution_client(tmp_path) -> None:
    operations = {
        "workspace.stat",
        "workspace.list",
        "workspace.read",
        "workspace.glob",
        "workspace.grep",
        "workspace.mkdir",
        "workspace.delete",
        "workspace.copy",
        "workspace.move",
    }
    client = FakeExecutionClient(
        {
            "workspace.stat": lambda arguments: WorkspaceStatResult(
                path=arguments["path"],
                kind="directory" if arguments["path"] == "." else "file",
                size=None if arguments["path"] == "." else 1,
                modified_at=1.0,
            ),
            "workspace.list": WorkspaceListResult(
                entries=[], has_more=False, next_offset=None
            ),
            "workspace.read": WorkspaceReadResult(
                content="1: x",
                lines_read=1,
                has_more=False,
                next_offset=None,
                truncated_lines=[],
                sha256="a" * 64,
            ),
            "workspace.glob": WorkspaceGlobResult(
                paths=["a.txt"], truncated=False
            ),
            "workspace.grep": WorkspaceGrepResult(
                matches=[WorkspaceGrepMatch(path="a.txt", line=1, text="x")],
                truncated=False,
            ),
            "workspace.mkdir": WorkspacePathResult(path="new"),
            "workspace.delete": WorkspaceDeleteResult(),
            "workspace.copy": WorkspacePathResult(path="copy.txt"),
            "workspace.move": WorkspacePathResult(path="moved.txt"),
        },
        operations,
    )
    calls = [
        ToolCall("list", "read", {"root": "workspace", "path": "."}),
        ToolCall("read", "read", {"root": "workspace", "path": "a.txt"}),
        ToolCall("glob", "glob", {"root": "workspace", "pattern": "*.txt"}),
        ToolCall("grep", "grep", {"root": "workspace", "pattern": "x"}),
        ToolCall("mkdir", "create_directory", {"root": "workspace", "path": "new"}),
        ToolCall("delete", "delete_path", {"root": "workspace", "path": "old"}),
        ToolCall(
            "copy",
            "copy_path",
            {
                "source_root": "workspace",
                "source_path": "a.txt",
                "destination_root": "workspace",
                "destination_path": "copy.txt",
            },
        ),
        ToolCall(
            "move",
            "move_path",
            {
                "source_root": "workspace",
                "source_path": "copy.txt",
                "destination_root": "workspace",
                "destination_path": "moved.txt",
            },
        ),
    ]
    context = _context(tmp_path, calls[0], client)

    for call in calls:
        context.tool_call = call
        result = await _tools(operations).execute(call, context)
        assert not result.is_error

    assert [operation for operation, _ in client.requests] == [
        "workspace.stat",
        "workspace.list",
        "workspace.stat",
        "workspace.read",
        "workspace.glob",
        "workspace.grep",
        "workspace.mkdir",
        "workspace.delete",
        "workspace.copy",
        "workspace.stat",
        "workspace.move",
        "workspace.stat",
    ]


async def test_process_uses_current_directory_and_persists_streams(
    tmp_path, monkeypatch
) -> None:
    call = ToolCall("process-1", "process_exec", {"program": "git", "args": ["status"]})
    client = FakeExecutionClient(
        {
            "process.exec": ProcessResult(
                exit_code=0,
                timed_out=False,
                duration_ms=12,
                stdout_bytes=9,
                stderr_bytes=0,
                stdout_truncated=False,
                stderr_truncated=False,
            )
        },
        {"process.exec"},
    )
    context = _context(tmp_path, call, client)
    publish = AsyncMock()
    monkeypatch.setattr("mato_loop_backend.agent.tools.run_events.publish", publish)

    result = await _tools(client.operations).execute(call, context)

    permission = context.ask_handler.await_args.args[0]
    assert permission.risk_level == "high"
    assert permission.available_decisions == ("allow_once", "deny")
    assert "Betriebssystem-Sandbox" in permission.summary
    assert [
        (item.label, item.value, item.format) for item in permission.display_items
    ] == [
        ("Programm", "git", "code"),
        ("Parameter", ("status",), "list"),
    ]
    assert client.requests == [
        ("process.exec", {"program": "git", "args": ["status"]})
    ]
    assert publish.await_args.args[1] == {
        "type": "process.output",
        "toolCallId": "process-1",
        "output": {"stdout": "streamed\n", "stderr": ""},
    }
    payload = json.loads(result.content)
    assert payload["output"] == {
        "stdout": "streamed\n",
        "stderr": "",
        "truncated": False,
    }
    assert payload["exitCode"] == 0


async def test_process_approval_cannot_persist_a_policy_grant(tmp_path) -> None:
    call = ToolCall("process-2", "process_exec", {"program": "git", "args": ["status"]})
    client = FakeExecutionClient(
        {
            "process.exec": ProcessResult(
                exit_code=0,
                timed_out=False,
                duration_ms=1,
                stdout_bytes=0,
                stderr_bytes=0,
                stdout_truncated=False,
                stderr_truncated=False,
            )
        },
        {"process.exec"},
    )
    context = _context(tmp_path, call, client)
    await _tools(client.operations).execute(call, context)
    permission = context.ask_handler.await_args.args[0]
    assert permission.available_decisions == ("allow_once", "deny")
