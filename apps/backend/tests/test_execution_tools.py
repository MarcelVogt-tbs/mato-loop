import hashlib
import json
from dataclasses import replace
from io import BytesIO
from unittest.mock import AsyncMock, Mock
from uuid import UUID, uuid4

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
    transfer_tickets,
)
from mato_loop_backend.execution.client import ExecutionResult, WorkspaceGrepMatch
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

    async def read_bytes(self, path, *, limit):
        self.requests.append(("workspace.export", {"path": path, "limit": limit}))
        response = self.responses["workspace.export"]
        return response(path) if callable(response) else response

    async def write_bytes(self, path, source, *, limit):
        data = source.read(limit + 1)
        self.requests.append(
            ("workspace.import", {"path": path, "content": data, "limit": limit})
        )
        response = self.responses["workspace.import"]
        return response(path, data) if callable(response) else response


def _context(tmp_path, call, client, *, decision=PermissionDecision.ALLOW):
    session = ChatSession(
        id=uuid4(),
        user_id=uuid4(),
        title="Test",
        memory_mode="disabled",
        web_search_policy="available",
        working_directory="project",
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
        ),
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


def test_process_tools_are_gated_and_cannot_select_execution_targets() -> None:
    only_exec = _tools({"process.exec"})
    assert "process_exec" in only_exec.tools
    assert "shell_run" not in only_exec.tools

    schema = next(item for item in only_exec.schemas() if item["name"] == "process_exec")
    assert set(schema["parameters"]["properties"]) == {"program", "args"}
    assert not {"root", "rootId", "workingDirectory", "executable"} & set(
        schema["parameters"]["properties"]
    )

    shell_only = _tools({"process.exec", "shell.run"})
    assert "process_exec" in shell_only.tools
    assert "shell_run" in shell_only.tools


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
    assert exc_info.value.params == {"secret": "value"}
    assert "secret" not in str(exc_info.value)


async def test_execution_client_imports_fixed_bytes_with_transfer_ticket(monkeypatch) -> None:
    agent_id = uuid4()
    root_id = uuid4()
    data = b"managed snapshot"
    digest = hashlib.sha256(data).hexdigest()
    client = ExecutionClient(
        agent_id,
        root_id,
        "a" * 64,
        "project",
        frozenset({"workspace.import"}),
    )

    monkeypatch.setattr(
        "mato_loop_backend.execution.client.execution_agent_connections.transfer_url",
        lambda _agent_id, ticket_id: f"https://backend.test/transfers/{ticket_id}",
    )

    async def request(selected_agent_id, operation, arguments, **_kwargs):
        ticket_id = UUID(arguments["transfer"]["url"].rsplit("/", 1)[-1])
        ticket = transfer_tickets.claim(
            ticket_id,
            arguments["transfer"]["token"],
            "download",
            owner_id=selected_agent_id,
        )
        assert ticket.content is not None
        with ticket.content:
            assert ticket.content.read() == data
        return ResponseMessage.model_validate(
            {
                "type": "response",
                "id": uuid4(),
                "ok": True,
                "result": {
                    "path": "snapshot.bin",
                    "size": len(data),
                    "sha256": digest,
                },
            }
        )

    monkeypatch.setattr(
        "mato_loop_backend.execution.client.execution_agent_connections.request",
        request,
    )

    result = await client.write_bytes(
        "snapshot.bin", BytesIO(data), limit=len(data)
    )

    assert result.path == "snapshot.bin"
    assert result.sha256 == digest


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


@pytest.mark.parametrize(
    "operation", ["integration.openscad.render", "integration.openscad.render6"]
)
async def test_execution_client_sends_archive_render_without_workspace_metadata(
    monkeypatch,
    operation,
) -> None:
    agent_id = uuid4()
    client = ExecutionClient(
        agent_id,
        None,
        None,
        None,
        frozenset({operation}),
    )
    request = AsyncMock(
        return_value=ResponseMessage.model_validate(
            {
                "type": "response",
                "id": uuid4(),
                "ok": True,
                "result": {},
            }
        )
    )
    monkeypatch.setattr(
        "mato_loop_backend.execution.client.execution_agent_connections.request", request
    )
    arguments = {"source": {"type": "archive"}}

    await client.request(operation, arguments, ExecutionResult)

    assert request.await_args.args[:3] == (
        agent_id,
        operation,
        arguments,
    )


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


async def test_read_only_shell_executes_only_host_verified_read_commands(tmp_path) -> None:
    call = ToolCall("shell", "shell_run", {"command": "cat README.md"})
    client = FakeExecutionClient(
        {
            "shell.inspect": ShellInspectResult(
                targets=[],
                analysis_complete=True,
                read_only=True,
                inspection_revision="c" * 64,
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
    assert [request[0] for request in client.requests] == ["shell.inspect", "shell.run"]
    permission = context.ask_handler.await_args.args[0]
    assert permission.resource_type == "shell_command"
    assert permission.available_decisions == ("allow_once", "allow_session", "deny")
    grant = PermissionGrant(
        session_id=context.session.id,
        capability=permission.capability,
        resource_type=permission.resource_type,
        resource_scope=permission.resource_scope,
    )
    assert PermissionPolicy.grant_matches(grant, permission, context.root_files)
    assert not PermissionPolicy.grant_matches(
        grant,
        replace(permission, resource_scope=permission.resource_scope.replace("cat", "head")),
        context.root_files,
    )

    client.responses["shell.inspect"] = ShellInspectResult(
        targets=[],
        analysis_complete=True,
        read_only=False,
        inspection_revision="d" * 64,
    )
    context.tool_call = ToolCall("write", "shell_run", {"command": "rm README.md"})
    result = await _tools(client.operations).execute(context.tool_call, context)

    assert not result.is_error
    assert [request[0] for request in client.requests] == [
        "shell.inspect",
        "shell.run",
        "shell.inspect",
        "shell.run",
    ]


async def test_workspace_tools_delegate_and_derive_canonical_results(tmp_path) -> None:
    call = ToolCall(
        "write-1",
        "write_file",
        {
            "root": "computer",
            "path": "a.txt",
            "content": "x",
        },
    )
    client = FakeExecutionClient(
        {
            "workspace.upsert": WorkspaceWriteResult(
                path="a.txt", sha256="a" * 64, size=1
            ),
            "workspace.stat": WorkspaceStatResult(
                path="a.txt", kind="file", size=1, modified_at=1.0
            ),
            "workspace.export": b"x",
        },
        {
            "workspace.upsert",
            "workspace.edit",
            "workspace.stat",
            "workspace.export",
        },
    )
    context = _context(tmp_path, call, client)
    context.ask_handler = AsyncMock(return_value=PermissionDecision.ALLOW)

    written = await _tools(client.operations).execute(call, context)
    assert json.loads(written.content) == {
        "root": "computer",
        "written": "a.txt",
        "sizeBytes": 1,
    }
    action = written.file_actions[0]
    assert action.root_identity == str(client.root_id)
    assert action.execution_agent_id == str(client.agent_id)
    assert action.root_id == str(client.root_id)
    assert action.root_revision == client.root_revision
    assert action.working_directory == client.working_directory
    assert client.requests == [
        (
            "workspace.upsert",
            {"path": "a.txt", "content": "x"},
        )
    ]
    assert context.ask_handler.await_count == 2

    client.responses["workspace.edit"] = WorkspaceWriteResult(
        path="a.txt", sha256="c" * 64, size=1
    )
    edit = ToolCall(
        "edit-1",
        "edit_file",
        {
            "root": "computer",
            "path": "a.txt",
            "oldText": "x",
            "newText": "y",
        },
    )
    context.tool_call = edit
    edited = await _tools(client.operations).execute(edit, context)
    assert json.loads(edited.content) == {
        "root": "computer",
        "edited": "a.txt",
        "sizeBytes": 1,
    }
    assert client.requests[-1] == (
        "workspace.edit",
        {"path": "a.txt", "oldText": "x", "newText": "y", "replaceAll": False},
    )

    provide = ToolCall(
        "provide-1", "provide_file", {"root": "computer", "path": "a.txt"}
    )
    context.tool_call = provide
    provided = await _tools(client.operations).execute(provide, context)
    payload = json.loads(provided.content)
    assert payload["sizeBytes"] == 1
    assert payload["root"] == "computer"
    assert payload["snapshotImported"] is True
    assert provided.attachments[0].data == b"x"
    assert "downloadUrl" not in payload


async def test_managed_read_and_provide_use_exact_revisions(tmp_path) -> None:
    call = ToolCall("read", "read", {"root": "files", "path": "note.txt", "revision": 1})
    context = _context(tmp_path, call, FakeExecutionClient({}, set()))
    context.root_files.upsert_text_file("session", "note.txt", "one")
    context.root_files.upsert_text_file("session", "note.txt", "two")

    read = await _tools(set()).execute(call, context)

    assert read.content == "1: one"
    assert read.metadata["revision"] == 1

    provide_call = ToolCall(
        "provide", "provide_file", {"root": "files", "path": "note.txt"}
    )
    context.tool_call = provide_call
    provided = await _tools(set()).execute(provide_call, context)
    payload = json.loads(provided.content)

    assert payload["root"] == "files"
    assert payload["revision"] == 2
    assert "/files/revisions/2/download?" in payload["downloadUrl"]
    assert "scope=" not in payload["downloadUrl"]
    assert provided.attachments[0].file_id is not None
    assert provided.attachments[0].file_revision == 2
    assert provided.attachments[0].file_path == "note.txt"


async def test_computer_revision_is_rejected_without_permission(tmp_path) -> None:
    call = ToolCall(
        "read",
        "read",
        {"root": "computer", "path": "note.txt", "revision": 1},
    )
    context = _context(tmp_path, call, FakeExecutionClient({}, {"workspace.read"}))

    result = await _tools({"workspace.read"}).execute(call, context)

    assert result.metadata == {"errorCode": "tool.invalidArguments"}
    context.ask_handler.assert_not_awaited()


async def test_copy_path_transfers_files_between_managed_and_computer_roots(tmp_path) -> None:
    client = FakeExecutionClient(
        {
            "workspace.export": b"from computer",
            "workspace.import": WorkspaceWriteResult(
                path="on-computer.txt",
                sha256="a" * 64,
                size=len(b"from files"),
            ),
        },
        {"workspace.stat", "workspace.export", "workspace.import"},
    )
    first = ToolCall(
        "to-computer",
        "copy_path",
        {
            "source_root": "files",
            "source_path": "from-files.txt",
            "destination_root": "computer",
            "destination_path": "on-computer.txt",
        },
    )
    context = _context(tmp_path, first, client)
    context.root_files.upsert_text_file("session", "from-files.txt", "from files")

    to_computer = await _tools(client.operations).execute(first, context)

    assert json.loads(to_computer.content) == {
        "root": "computer",
        "copied": "on-computer.txt",
    }
    assert client.requests[-1][0] == "workspace.import"

    second = ToolCall(
        "to-files",
        "copy_path",
        {
            "source_root": "computer",
            "source_path": "from-computer.txt",
            "destination_root": "files",
            "destination_path": "on-files.txt",
        },
    )
    context.tool_call = second
    to_files = await _tools(client.operations).execute(second, context)

    assert json.loads(to_files.content) == {
        "root": "files",
        "copied": "on-files.txt",
    }
    assert context.root_files.read_bytes("session", "on-files.txt", limit=100) == b"from computer"


async def test_cross_root_copy_and_move_validate_before_permissions(tmp_path) -> None:
    copy = ToolCall(
        "copy",
        "copy_path",
        {
            "source_root": "files",
            "source_path": "a.txt",
            "destination_root": "computer",
            "destination_path": "a.txt",
        },
    )
    context = _context(tmp_path, copy, FakeExecutionClient({}, set()))

    unsupported = await _tools(set()).execute(copy, context)

    assert unsupported.metadata == {"errorCode": "executionAgent.operationUnavailable"}
    context.ask_handler.assert_not_awaited()

    move = ToolCall(
        "move",
        "move_path",
        {
            "source_root": "files",
            "source_path": "a.txt",
            "destination_root": "computer",
            "destination_path": "a.txt",
        },
    )
    context.tool_call = move
    rejected = await _tools(set()).execute(move, context)

    assert rejected.metadata == {"errorCode": "file.crossBoundaryMoveUnavailable"}
    context.ask_handler.assert_not_awaited()


async def test_write_file_blocks_matching_dirty_active_file(tmp_path) -> None:
    call = ToolCall(
        "write-1",
        "write_file",
        {"root": "computer", "path": "a.txt", "content": "x"},
    )
    client = FakeExecutionClient({}, {"workspace.upsert"})
    context = _context(tmp_path, call, client)
    context.db.scalar.return_value = Mock(
        provider_data={
            "_matoloop": {
                "activeFile": {
                    "root": "workspace",
                    "rootIdentity": str(client.root_id),
                    "path": "a.txt",
                    "sha256": "a" * 64,
                    "dirty": True,
                }
            }
        }
    )

    result = await _tools(client.operations).execute(call, context)

    assert result.is_error
    assert json.loads(result.content)["error"]["code"] == "file.activeDirty"
    assert client.requests == []
    context.ask_handler.assert_not_awaited()


async def test_execution_workspace_grants_match_only_same_snapshot_descendants(
    tmp_path,
) -> None:
    call = ToolCall(
        "read-1", "read", {"root": "computer", "path": "docs"}
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
        ToolCall("list", "read", {"root": "computer", "path": "."}),
        ToolCall("read", "read", {"root": "computer", "path": "a.txt"}),
        ToolCall("glob", "glob", {"root": "computer", "pattern": "*.txt"}),
        ToolCall("grep", "grep", {"root": "computer", "pattern": "x"}),
        ToolCall("mkdir", "create_directory", {"root": "computer", "path": "new"}),
        ToolCall("delete", "delete_path", {"root": "computer", "path": "old"}),
        ToolCall(
            "copy",
            "copy_path",
            {
                "source_root": "computer",
                "source_path": "a.txt",
                "destination_root": "computer",
                "destination_path": "copy.txt",
            },
        ),
        ToolCall(
            "move",
            "move_path",
            {
                "source_root": "computer",
                "source_path": "copy.txt",
                "destination_root": "computer",
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
