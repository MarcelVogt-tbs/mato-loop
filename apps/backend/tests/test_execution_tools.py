import hashlib
import json
from dataclasses import replace
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from pydantic import ValidationError

from mato_loop_backend.agent import tools as tools_module
from mato_loop_backend.agent.permissions import (
    PermissionDecision,
    PermissionDisplayItem,
    PermissionPolicy,
    PermissionRequest,
    SessionGrantScope,
    execution_directory_permission,
    execution_directory_scope,
    session_grant_matches_request,
    shell_command_permission,
    shell_command_scope,
    shell_prefix_allowed,
)
from mato_loop_backend.agent.runtime import _tool_policy
from mato_loop_backend.agent.subagents import permission_snapshot
from mato_loop_backend.agent.tool_arguments import ShellRunArguments
from mato_loop_backend.agent.tool_registry import ToolContext, ToolResult
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
    WorkspacePatchResult,
    WorkspaceReadResult,
    WorkspaceWriteResult,
    transfer_tickets,
)
from mato_loop_backend.execution import (
    transfers as transfers_module,
)
from mato_loop_backend.execution.client import (
    WorkspaceExportResult,
    WorkspaceListResult,
    WorkspaceStatResult,
)
from mato_loop_backend.execution.protocol import ResponseMessage
from mato_loop_backend.execution.terminals import terminal_coordinator
from mato_loop_backend.execution.transfers import (
    EXPORT_TIMEOUT_SECONDS,
    MAX_TRANSFER_TTL_SECONDS,
    TRANSFER_TTL_SECONDS,
    TransferError,
    TransferTicketStore,
)
from mato_loop_backend.models import ChatSession, Credential, ExecutionAgent, PermissionGrant
from mato_loop_backend.providers.base import ToolCall
from mato_loop_backend.settings import (
    IntegrationSettings,
    ToolExecutionPolicy,
    ToolSettings,
    TrustLevel,
    build_profile,
    plan_profile,
)
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


def test_shell_tool_cannot_select_execution_targets() -> None:
    tools = _tools({"shell.run"})
    shell = next(item for item in tools.schemas() if item["name"] == "shell_run")
    assert set(shell["parameters"]["properties"]) == {
        "command",
        "environment",
        "timeout",
    }
    assert ShellRunArguments(command="pwd").timeout is None
    assert all(item["name"] != "process_exec" for item in tools.schemas())
    assert {
        action.capability: (
            action.label,
            action.private_default_policy,
            action.public_default_policy,
        )
        for action in tool_registry._operation_definitions["shell_run"].actions
    } == {
        "shell.read": (
            "Shell ohne erkannte Schreibaktionen",
            ToolExecutionPolicy.AUTO,
            ToolExecutionPolicy.ASK,
        ),
        "shell.write": (
            "Als schreibend eingestufte Shell",
            ToolExecutionPolicy.ASK,
            ToolExecutionPolicy.ASK,
        ),
    }
    assert tool_registry.action_default(
        "shell.read", TrustLevel.PRIVATE, IntegrationSettings()
    ) == ToolExecutionPolicy.AUTO


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


def test_upload_ticket_ttl_override_is_bounded_and_keeps_default(monkeypatch) -> None:
    monkeypatch.setattr(transfers_module.time, "monotonic", lambda: 100.0)
    store = TransferTicketStore()

    _, default_ticket = store.create_upload(uuid4(), size=1)
    _, export_ticket = store.create_upload(
        uuid4(), size=1, ttl_seconds=EXPORT_TIMEOUT_SECONDS + 30
    )

    assert default_ticket.expires_at == 100.0 + TRANSFER_TTL_SECONDS
    assert export_ticket.expires_at == 100.0 + EXPORT_TIMEOUT_SECONDS + 30
    with pytest.raises(TransferError):
        store.create_upload(uuid4(), size=1, ttl_seconds=MAX_TRANSFER_TTL_SECONDS + 1)


async def test_execution_export_uses_long_lived_upload_ticket(monkeypatch) -> None:
    data = b"export"
    digest = hashlib.sha256(data).hexdigest()
    captured = []
    create_upload = transfer_tickets.create_upload

    def capture_upload(*args, **kwargs):
        grant, ticket = create_upload(*args, **kwargs)
        ticket.content = BytesIO(data)
        ticket.actual_size = len(data)
        ticket.actual_sha256 = digest
        captured.append(ticket)
        return grant, ticket

    async def request(_self, operation, _arguments, _result_model, **kwargs):
        if operation == "filesystem.stat":
            return WorkspaceStatResult(
                path="/tmp/export.bin", kind="file", size=len(data), modified_at=0.0
            )
        assert operation == "filesystem.export"
        assert kwargs["timeout"] == EXPORT_TIMEOUT_SECONDS
        return WorkspaceExportResult(size=len(data), sha256=digest)

    monkeypatch.setattr(transfer_tickets, "create_upload", capture_upload)
    monkeypatch.setattr(ExecutionClient, "request", request)
    monkeypatch.setattr(
        "mato_loop_backend.execution.client.execution_agent_connections.transfer_url",
        lambda _agent_id, ticket_id: f"https://backend.test/transfers/{ticket_id}",
    )
    before = transfers_module.time.monotonic()

    with await ExecutionClient(uuid4(), frozenset()).open_export(
        "/tmp/export.bin", limit=len(data)
    ) as content:
        assert content.read() == data

    assert captured[0].expires_at >= before + EXPORT_TIMEOUT_SECONDS + 29


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
            "filesystem.stat": WorkspaceStatResult(
                path="/home/user/project/README.md",
                kind="file",
                size=5,
                modified_at=0.0,
            ),
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


async def test_workspace_directory_read_grants_only_that_directory(tmp_path) -> None:
    call = ToolCall(
        "call",
        "read",
        {"root": "workspace", "path": "/etc", "offset": 1, "limit": 20},
    )
    client = FakeExecutionClient(
        {
            "filesystem.stat": WorkspaceStatResult(
                path="/etc", kind="directory", size=None, modified_at=0.0
            ),
            "filesystem.list": WorkspaceListResult(
                entries=[], has_more=False, next_offset=None
            ),
        },
        {"filesystem.stat", "filesystem.list"},
    )
    context = _context(tmp_path, call, client)

    result = await _tools(client.operations).execute(call, context)

    assert not result.is_error
    request = context.ask_handler.await_args.args[0]
    assert request.effective_session_grants == (
        SessionGrantScope(
            "read",
            "execution_directory",
            execution_directory_scope(client.agent_id, "/etc"),
            "/etc",
        ),
    )


async def test_workspace_write_uses_compare_and_swap_operation(tmp_path) -> None:
    call = ToolCall(
        "write",
        "write_file",
        {
            "root": "workspace",
            "path": "README.md",
            "content": "updated\n",
            "expectedSha256": "a" * 64,
        },
    )
    client = FakeExecutionClient(
        {
            "filesystem.write": WorkspaceWriteResult(
                path="/home/user/project/README.md",
                sha256="b" * 64,
                size=8,
            )
        },
        {"filesystem.write"},
    )
    context = _context(tmp_path, call, client)

    result = await _tools(client.operations).execute(call, context)

    assert not result.is_error
    assert client.requests == [
        (
            "filesystem.write",
            {
                "path": "/home/user/project/README.md",
                "content": "updated\n",
                "expectedSha256": "a" * 64,
            },
        )
    ]


async def test_apply_patch_resolves_paths_and_checks_each_permission(tmp_path) -> None:
    patch = """*** Begin Patch
*** Update File: README.md
@@
-old
+new
*** Add File: src/new.py
+value = 1
*** End Patch
"""
    call = ToolCall(
        "patch",
        "apply_patch",
        {"patch": patch, "expectedSha256": {"README.md": "a" * 64}},
    )
    client = FakeExecutionClient(
        {
            "filesystem.patch": WorkspacePatchResult(
                files=[
                    {
                        "operation": "update",
                        "path": "/home/user/project/README.md",
                        "sha256": "b" * 64,
                        "size": 4,
                    },
                    {
                        "operation": "add",
                        "path": "/home/user/project/src/new.py",
                        "sha256": "c" * 64,
                        "size": 10,
                    },
                ]
            )
        },
        {"filesystem.patch"},
    )
    context = _context(tmp_path, call, client)

    result = await _tools(client.operations).execute(call, context)

    assert not result.is_error
    assert client.requests == [
        (
            "filesystem.patch",
            {
                "patch": patch.replace(
                    "README.md", "/home/user/project/README.md"
                ).replace("src/new.py", "/home/user/project/src/new.py"),
                "expectedSha256": {
                    "/home/user/project/README.md": "a" * 64
                },
            },
        )
    ]
    assert [
        request.args[0].capability for request in context.ask_handler.await_args_list
    ] == ["filesystem.write", "filesystem.write"]


async def test_incomplete_patch_error_propagates_remote_params_through_registry(
    tmp_path, monkeypatch,
) -> None:
    params = {
        "completedOperations": 1,
        "totalOperations": 2,
        "failedOperationIndex": 1,
        "path": "/home/user/project/source",
        "destination": "/home/user/project/destination",
    }
    client = ExecutionClient(uuid4(), frozenset({"filesystem.patch"}), "/home/user/project")
    request = AsyncMock(return_value=ResponseMessage.model_validate({
        "type": "response",
        "id": uuid4(),
        "ok": False,
        "error": {"code": "file.patchIncomplete", "params": params},
    }))
    monkeypatch.setattr(
        "mato_loop_backend.execution.client.execution_agent_connections.request", request
    )
    call = ToolCall("patch", "apply_patch", {
        "patch": "*** Begin Patch\n*** Add File: first\n+first\n"
        "*** Update File: source\n*** Move to: destination\n*** End Patch\n",
        "expectedSha256": {"source": "a" * 64},
    })

    result = await _tools(client.operations).execute(call, _context(tmp_path, call, client))

    assert result.is_error
    descriptor = json.loads(result.content)["error"]
    assert descriptor["code"] == "file.patchIncomplete"
    assert descriptor["params"] == params
    assert "Reread the affected paths before retrying" in descriptor["message"]
    request.assert_awaited_once()


async def test_execution_error_params_are_validated_before_tool_serialization(tmp_path) -> None:
    def fail(_arguments):
        raise ExecutionClientError("file.patchIncomplete", {"invalid": object()})

    client = FakeExecutionClient({"filesystem.patch": fail}, {"filesystem.patch"})
    call = ToolCall("patch", "apply_patch", {
        "patch": "*** Begin Patch\n*** Add File: first\n+first\n*** End Patch\n",
        "expectedSha256": {},
    })

    result = await _tools(client.operations).execute(call, _context(tmp_path, call, client))

    assert result.is_error
    descriptor = json.loads(result.content)["error"]
    assert descriptor["code"] == "file.patchIncomplete"
    assert "params" not in descriptor


def _shell_permission(
    client, classification="write", prefixes=None, directory=None, paths=None, analysis_complete=True
):
    grant_paths = paths or [directory or client.current_directory]
    return PermissionRequest(
        capability=f"shell.{classification}",
        resource_type="shell_command",
        resource_scope=shell_command_scope(
            client.agent_id,
            directory or client.current_directory,
            classification,
            prefixes or [],
            paths or [],
            analysis_complete,
        ),
        action_kind="shell_run",
        risk_level="high",
        title="Shell-Befehl ausführen?",
        summary="Test",
        session_grants=tuple(
            SessionGrantScope(
                classification,
                "execution_directory",
                execution_directory_scope(client.agent_id, path),
                path,
            )
            for path in grant_paths
        ),
    )


def test_shell_grant_matches_access_class_agent_and_directory(tmp_path) -> None:
    client = FakeExecutionClient({}, {"shell.inspect", "shell.run"})
    context = _context(tmp_path, ToolCall("shell", "shell_run", {}), client)
    granted = _shell_permission(client, prefixes=[["git", "status"]])
    matching = _shell_permission(client, prefixes=[["git", "status"]])
    wrong_class = _shell_permission(client, classification="read", prefixes=[["git", "status"]])
    wrong_directory = _shell_permission(
        client, prefixes=[["git", "status"]], directory="/home/user/other"
    )
    scope = granted.effective_session_grants[0]
    grant = PermissionGrant(
        session_id=context.session.id,
        capability=scope.capability,
        resource_type=scope.resource_type,
        resource_scope=scope.resource_scope,
    )

    assert PermissionPolicy.grant_matches(grant, matching, context.root_files)
    assert not PermissionPolicy.grant_matches(grant, wrong_class, context.root_files)
    assert not PermissionPolicy.grant_matches(grant, wrong_directory, context.root_files)


def test_shell_session_grants_include_cwd_and_every_target_directory() -> None:
    agent_id = uuid4()
    request = shell_command_permission(
        agent_id,
        "/work",
        "cat /outside/input.txt > /other/output.txt",
        ShellInspectResult(
            targets=[
                {"path": "/outside/input.txt", "kind": "input", "access": "read"},
                {"path": "/other/output.txt", "kind": "output", "access": "write"},
                {"path": "/generic/file.txt", "kind": "path", "access": "write"},
            ],
            analysis_complete=True,
            classification="write",
            read_only=False,
            commands=[],
            inspection_revision="a" * 64,
        ),
    )

    assert [
        (grant.access, json.loads(grant.resource_scope)["path"])
        for grant in request.effective_session_grants
    ] == [("write", "/work"), ("read", "/outside"), ("write", "/other"), ("write", "/generic")]
    assert json.loads(request.resource_scope)["targetAccesses"] == ["read", "write", "write"]
    assert all(
        session_grant_matches_request(
            request.capability, request.resource_type, request.resource_scope, grant
        )
        for grant in request.effective_session_grants
    )
    outside_read = request.effective_session_grants[1]
    assert not session_grant_matches_request(
        request.capability,
        request.resource_type,
        request.resource_scope,
        replace(outside_read, access="write"),
    )


def test_shell_permission_displays_confidential_paths() -> None:
    request = shell_command_permission(
        uuid4(),
        "/work",
        "cat .env.prod.example README.md",
        ShellInspectResult(
            targets=[
                {"path": "/work/.env.prod.example", "kind": "path", "access": "read"},
                {"path": "/work/README.md", "kind": "path", "access": "read"},
            ],
            analysis_complete=True,
            classification="read",
            read_only=True,
            commands=[],
            inspection_revision="a" * 64,
        ),
    )

    assert request.display_items[-1] == PermissionDisplayItem(
        "Besonders geschützte Pfade", ("/work/.env.prod.example",), "list"
    )
    assert "benötigen immer eine Freigabe" in request.summary
    assert request.available_decisions == ("allow_once", "deny")


def test_legacy_shell_targets_remain_one_shot_only() -> None:
    agent_id = uuid4()
    request = shell_command_permission(
        agent_id,
        "/work",
        "cat /outside/input.txt",
        ShellInspectResult(
            targets=[{"path": "/outside/input.txt", "kind": "input"}],
            analysis_complete=True, classification="read", read_only=True,
            commands=[], inspection_revision="a" * 64,
        ),
    )
    assert request.available_decisions == ("allow_once", "deny")
    assert json.loads(request.resource_scope)["targetAccesses"] == [None]


async def test_legacy_shell_target_is_not_authorized_by_existing_directory_grant(tmp_path) -> None:
    client = FakeExecutionClient({}, {"shell.inspect", "shell.run"})
    context = _context(tmp_path, ToolCall("shell", "shell_run", {}), client)
    context.session.current_directory = "/work"
    request = shell_command_permission(
        client.agent_id,
        "/work",
        "cat /outside/input.txt",
        ShellInspectResult(
            targets=[{"path": "/outside/input.txt", "kind": "input"}],
            analysis_complete=True, classification="read", read_only=True,
            commands=[], inspection_revision="a" * 64,
        ),
    )
    cwd = request.effective_session_grants[0]
    context.db.scalar.return_value = ExecutionAgent(
        id=client.agent_id, user_id=context.session.user_id,
        home_filesystem_policy="ask", external_filesystem_policy="ask",
        home_shell_enabled=True, external_shell_enabled=True,
    )
    context.db.scalars.return_value = Mock(all=Mock(return_value=[PermissionGrant(
        session_id=context.session.id,
        capability=cwd.capability,
        resource_type=cwd.resource_type,
        resource_scope=cwd.resource_scope,
    )]))

    assert await PermissionPolicy.evaluate(
        context.db, context.session, request, context.root_files, ToolExecutionPolicy.ASK,
    ) == PermissionDecision.ASK


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


@pytest.mark.parametrize("profile_factory", [plan_profile, build_profile])
@pytest.mark.parametrize("capability", ["filesystem.read", "shell.read"])
@pytest.mark.parametrize(
    ("path", "filesystem_policy", "expected"),
    [
        ("/home/user/project/README.md", "auto", PermissionDecision.ALLOW),
        ("/opt/lemonade/docker-compose.yaml", "auto", PermissionDecision.ASK),
        ("/home/user/project/.env", "auto", PermissionDecision.ASK),
        ("/home/user/project/README.md", "deny", PermissionDecision.DENY),
    ],
)
async def test_coding_profile_auto_reads_keep_path_boundaries(
    tmp_path, profile_factory, capability, path, filesystem_policy, expected
) -> None:
    client = FakeExecutionClient({}, {"shell.inspect", "shell.run"})
    context = _context(tmp_path, ToolCall("read", "read", {}), client)
    context.db.scalar.return_value = ExecutionAgent(
        id=client.agent_id,
        user_id=context.session.user_id,
        home_directory="/home/user",
        home_filesystem_policy=filesystem_policy,
        external_filesystem_policy="ask",
        home_shell_enabled=True,
        external_shell_enabled=True,
    )
    context.db.scalars.return_value = Mock(all=Mock(return_value=[]))
    profile = profile_factory()
    settings = ToolSettings().select_trust(TrustLevel.PUBLIC)
    settings._agent_permissions = profile.public_permissions
    if capability == "shell.read":
        request = _shell_permission(
            client, classification="read", prefixes=[["cat"]], paths=[path]
        )
    else:
        request = execution_directory_permission(client.agent_id, path, capability=capability)
    policy = _tool_policy(settings, IntegrationSettings(), request)

    assert policy == ToolExecutionPolicy.AUTO
    assert await PermissionPolicy.evaluate(
        context.db, context.session, request, context.root_files, policy,
        agent_profile_id=profile.id, trust_level=TrustLevel.PUBLIC,
    ) == expected


@pytest.mark.parametrize("profile_factory", [plan_profile, build_profile, None])
@pytest.mark.parametrize("trust", [TrustLevel.PUBLIC, TrustLevel.PRIVATE])
@pytest.mark.parametrize("classification", ["read", "write"])
@pytest.mark.parametrize(
    ("analysis_complete", "path", "agent_overrides", "expected"),
    [
        (True, "/workspace/harness-eval-astra/app/frontend", {}, PermissionDecision.ALLOW),
        (False, "/workspace/harness-eval-astra/app/frontend", {}, PermissionDecision.ALLOW),
        (False, None, {}, PermissionDecision.ALLOW),
        (False, "/etc/hosts", {}, PermissionDecision.ASK),
        (False, "/workspace/harness-eval-astra/app/.env", {}, PermissionDecision.ASK),
        (False, None, {"external_shell_enabled": False}, PermissionDecision.DENY),
        (False, None, {"home_shell_enabled": False}, PermissionDecision.DENY),
        (False, None, {"external_filesystem_policy": "deny"}, PermissionDecision.DENY),
        (False, None, {"home_filesystem_policy": "deny"}, PermissionDecision.DENY),
    ],
)
async def test_shell_policy_covers_incomplete_analysis(
    tmp_path, profile_factory, trust, classification, analysis_complete, path, agent_overrides, expected
) -> None:
    client = FakeExecutionClient(
        {}, {"shell.inspect", "shell.run"}, current_directory="/workspace/harness-eval-astra/app"
    )
    call = ToolCall("shell", "shell_run", {
        "command": 'for f in frontend/package.json frontend/vite.config.js; do cat "$f"; done'
    })
    context = _context(tmp_path, call, client)
    agent = ExecutionAgent(
        id=client.agent_id, user_id=context.session.user_id, home_directory="/workspace",
        home_filesystem_policy="auto", external_filesystem_policy="ask",
        home_shell_enabled=True, external_shell_enabled=True,
    )
    for key, value in agent_overrides.items():
        setattr(agent, key, value)
    context.db.scalar.return_value = agent
    context.db.scalars.return_value = Mock(all=Mock(return_value=[]))
    settings = ToolSettings().select_trust(trust)
    if profile_factory:
        profile = profile_factory()
        settings._agent_permissions = (
            profile.public_permissions if trust == TrustLevel.PUBLIC else profile.private_permissions
        )
    request = _shell_permission(
        client, classification=classification, prefixes=[["find"], ["find"], ["printf"], ["cat"]],
        paths=[path] if path else [], analysis_complete=analysis_complete,
    )
    policy = _tool_policy(
        settings, IntegrationSettings(), request,
        snapshot=permission_snapshot(settings, {"shell_run"}),
    )
    if profile_factory == plan_profile and classification == "write":
        assert policy == ToolExecutionPolicy.DISABLED
        expected = PermissionDecision.DENY
    elif profile_factory is None and (classification == "write" or trust == TrustLevel.PUBLIC):
        assert policy == ToolExecutionPolicy.ASK
        if expected == PermissionDecision.ALLOW:
            expected = PermissionDecision.ASK
    else:
        assert policy == ToolExecutionPolicy.AUTO
    assert await PermissionPolicy.evaluate(
        context.db, context.session, request, context.root_files, policy,
    ) == expected


@pytest.mark.parametrize(("command", "commands", "prefixes"), [
    (
        "cat README.md",
        [{"tokens": ["cat", "README.md"], "prefix": ["cat"], "classification": "read"}],
        [["cat"]],
    ),
    (
        "docker exec app cat /etc/hosts",
        [{"tokens": ["docker", "exec", "app", "cat", "/etc/hosts"],
          "prefix": None, "classification": "read"}],
        [],
    ),
    (
        "curl https://example.com; docker exec app cat /etc/hosts; cat README.md",
        [
            {"tokens": ["curl", "https://example.com"], "prefix": ["curl"], "classification": "read"},
            {"tokens": ["docker", "exec", "app", "cat", "/etc/hosts"],
             "prefix": None, "classification": "read"},
            {"tokens": ["cat", "README.md"], "prefix": ["cat"], "classification": "read"},
        ],
        [],
    ),
])
async def test_shell_dispatches_with_inspection_classification(
    tmp_path, command, commands, prefixes,
) -> None:
    call = ToolCall("shell", "shell_run", {"command": command})
    client = FakeExecutionClient(
        {
            "shell.inspect": ShellInspectResult(
                targets=[],
                analysis_complete=True,
                classification="read",
                read_only=True,
                commands=commands,
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
    assert client.requests[-1][1] == {
        "command": command,
        "currentDirectory": client.current_directory,
        "timeoutSeconds": None,
        "expectedInspectionRevision": "a" * 64,
    }
    request = context.ask_handler.await_args.args[0]
    assert request.capability == "shell.read"
    assert request.approval_details == {
        "shellClassification": "read",
        "shellAnalysisComplete": True,
        "shellPrefixes": prefixes,
    }
    assert json.loads(request.resource_scope)["prefixes"] == prefixes
    assert request.available_decisions == ("allow_once", "allow_session", "deny")
    allowed = [["docker"], ["curl"], ["cat"]]
    assert shell_prefix_allowed(ToolSettings(shell_prefixes={"read": allowed}), request) == bool(prefixes)
    granted = request
    scope = granted.effective_session_grants[0]
    grant = PermissionGrant(
        session_id=context.session.id,
        capability=scope.capability,
        resource_type=scope.resource_type,
        resource_scope=scope.resource_scope,
    )
    assert PermissionPolicy.grant_matches(grant, request, context.root_files)


async def test_shell_resolves_variable_reference_only_for_process_transport(
    tmp_path, monkeypatch
) -> None:
    reference = uuid4()
    call = ToolCall(
        "shell",
        "shell_run",
        {"command": "env", "environment": {"API_TOKEN": str(reference)}},
    )
    client = FakeExecutionClient(
        {
            "shell.inspect": ShellInspectResult(
                targets=[],
                analysis_complete=True,
                classification="read",
                read_only=True,
                commands=[
                    {
                        "tokens": ["env"],
                        "prefix": ["env"],
                        "classification": "read",
                    }
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
    request = client.request

    async def request_with_output(operation, arguments, result_model, **kwargs):
        if operation == "shell.run":
            await kwargs["on_event"](
                SimpleNamespace(event="stdout", data="token=host-secret\n")
            )
        return await request(operation, arguments, result_model, **kwargs)

    client.request = request_with_output
    context = _context(tmp_path, call, client)
    variable = Credential(
        id=reference,
        user_id=context.session.user_id,
        kind="personal_variable",
        label="TOKEN",
        encrypted_payload=b"encrypted",
        metadata_json={"secret": True},
    )
    monkeypatch.setattr(
        tools_module, "get_personal_variable", AsyncMock(return_value=variable)
    )
    monkeypatch.setattr(
        tools_module, "personal_variable_value", lambda _variable: "host-secret"
    )

    result = await _tools(client.operations).execute(call, context)

    assert not result.is_error
    assert client.requests[-1][1]["environment"] == {"API_TOKEN": "host-secret"}
    assert "host-secret" not in result.content
    assert "token=[redacted]" in result.content
    assert str(reference) in result.content
    assert context.ask_handler.await_args_list[0].args[0].capability == "shell.write"
    assert context.ask_handler.await_args_list[1].args[0].capability == "secrets.access"


@pytest.mark.parametrize("classification", ["read", "write"])
@pytest.mark.parametrize("analysis_complete", [False, True])
async def test_shell_copy_separates_classification_and_completeness(
    tmp_path, classification, analysis_complete,
) -> None:
    call = ToolCall("shell", "shell_run", {"command": "unknown-command --inspect"})
    client = FakeExecutionClient(
        {
            "shell.inspect": ShellInspectResult(
                targets=[],
                analysis_complete=analysis_complete,
                classification=classification,
                read_only=classification == "read",
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
    assert request.capability == f"shell.{classification}"
    assert request.risk_level == ("low" if classification == "read" else "high")
    assert request.title == (
        "Shell-Befehl ohne erkannte Schreibaktion ausführen?"
        if classification == "read"
        else "Als schreibend eingestuften Shell-Befehl ausführen?"
    )
    assert request.summary == (
        "Es wurden keine bekannten Schreibaktionen erkannt. Der Befehl kann dennoch Nebenwirkungen haben."
        if classification == "read"
        else "Der Befehl wurde als schreibend eingestuft und kann Dateien verändern, Programme starten und Netzwerkdienste verwenden."
    ) + (
        " Die Analyse ist unvollständig; nicht alle Befehle und Pfade konnten geprüft werden."
        if not analysis_complete else ""
    )
    assert request.approval_details == {
        "shellClassification": classification,
        "shellAnalysisComplete": analysis_complete,
        "shellPrefixes": [],
    }


@pytest.mark.parametrize("timeout", [None, 0.5, 3600])
async def test_attached_shell_routes_to_shared_runtime_without_isolated_transport(
    tmp_path, monkeypatch, timeout
) -> None:
    requested = {"command": "cat README.md", **({"timeout": timeout} if timeout else {})}
    call = ToolCall("shell", "shell_run", requested)

    from mato_loop_backend.agent import shared_shell

    client = FakeExecutionClient({}, {"shell.inspect", "shell.run"})
    context = _context(tmp_path, call, client)
    terminal = object()
    monkeypatch.setattr(terminal_coordinator, "attached_for", lambda **_kwargs: terminal)
    run = AsyncMock(return_value=ToolResult.json({"ok": True}))
    monkeypatch.setattr(shared_shell, "run_shared_shell", run)
    assert not (await _tools(client.operations).execute(call, context)).is_error
    assert client.requests == []
    run.assert_awaited_once()
    assert run.await_args.args[0] is context and run.await_args.args[2] is terminal
    assert run.await_args.args[1].model_dump(
        exclude_none=True, exclude_defaults=True
    ) == requested


async def test_attached_shell_rejects_personal_variable_environment(
    tmp_path, monkeypatch
) -> None:
    call = ToolCall(
        "shell",
        "shell_run",
        {"command": "env", "environment": {"TOKEN": str(uuid4())}},
    )
    client = FakeExecutionClient({}, {"shell.inspect", "shell.run"})
    context = _context(tmp_path, call, client)
    monkeypatch.setattr(
        terminal_coordinator, "attached_for", lambda **_kwargs: object()
    )

    result = await _tools(client.operations).execute(call, context)

    assert result.is_error
    assert json.loads(result.content)["error"]["code"] == (
        "variable.sharedTerminalUnsupported"
    )
    assert client.requests == []


@pytest.mark.parametrize("timeout", [0, -1, 3600.1, float("inf"), float("nan"), True, "60"])
def test_shell_timeout_rejects_invalid_values(timeout) -> None:
    with pytest.raises(ValidationError):
        ShellRunArguments(command="pwd", timeout=timeout)


@pytest.mark.parametrize("timeout", [None, 0.25, 3600])
@pytest.mark.parametrize(
    ("exit_code", "timed_out", "error_code"),
    [(0, False, None), (7, False, "process.commandFailed"), (-15, True, "process.timedOut"),
     (0, True, "process.timedOut")],
)
async def test_shell_timeout_and_process_evidence(
    tmp_path, timeout, exit_code, timed_out, error_code,
) -> None:
    call = ToolCall("shell", "shell_run", {"command": "printf evidence", "timeout": timeout})
    client = FakeExecutionClient({}, {"shell.inspect", "shell.run", "terminal.run"})
    process = ProcessResult(
        exit_code=exit_code, timed_out=timed_out, duration_ms=123, stdout_bytes=8,
        stderr_bytes=10, stdout_truncated=True, stderr_truncated=False,
    )
    inspection = ShellInspectResult(
        targets=[], analysis_complete=True, classification="read", read_only=True,
        commands=[{"tokens": ["printf", "evidence"], "prefix": ["printf"], "classification": "read"}],
        inspection_revision="a" * 64,
    )

    async def execute(*_args, **kwargs):
        await kwargs["on_event"](Mock(event="stdout", data="evidence"))
        await kwargs["on_event"](Mock(event="stderr", data="diagnostic"))
        return process

    async def request(operation, arguments, _model, **kwargs):
        if operation == "shell.inspect":
            assert arguments == {"command": "printf evidence", "currentDirectory": client.current_directory}
            return inspection
        assert arguments["timeoutSeconds"] == timeout
        assert arguments["expectedInspectionRevision"] == "a" * 64
        assert kwargs["timeout"] == 3610
        return await execute(**kwargs)

    client.request = AsyncMock(side_effect=request)
    context = _context(tmp_path, call, client)
    result = await _tools(client.operations).execute(call, context)
    payload = json.loads(result.content)

    assert result.is_error == (error_code is not None)
    assert result.metadata.get("errorCode") == error_code
    assert payload["exitCode"] == exit_code
    assert payload["timedOut"] == timed_out
    assert payload["durationMs"] == 123
    assert payload["stdoutBytes"] == 8
    assert payload["stderrBytes"] == 10
    assert payload["output"] == {"stdout": "evidence", "stderr": "diagnostic", "truncated": True}
    assert context.ask_handler.await_count == 1
    assert client.request.await_count == 2


@pytest.mark.parametrize("deny", [False, True])
async def test_shell_timeout_does_not_bypass_permission_or_inspection_revision(tmp_path, deny) -> None:
    call = ToolCall("shell", "shell_run", {"command": "cat README.md", "timeout": 3600})

    def changed(arguments):
        assert arguments["expectedInspectionRevision"] == "a" * 64
        assert arguments["timeoutSeconds"] == 3600
        raise ExecutionClientError("process.inspectionChanged")

    client = FakeExecutionClient(
        {
            "shell.inspect": ShellInspectResult(
                targets=[], analysis_complete=True, classification="read", read_only=True,
                commands=[{"tokens": ["cat", "README.md"], "prefix": ["cat"], "classification": "read"}],
                inspection_revision="a" * 64,
            ),
            "shell.run": changed,
        },
        {"shell.inspect", "shell.run"},
    )
    context = _context(
        tmp_path, call, client, decision=PermissionDecision.DENY if deny else PermissionDecision.ALLOW,
    )
    result = await _tools(client.operations).execute(call, context)
    assert result.is_error
    assert result.metadata["errorCode"] == ("permission.denied" if deny else "process.inspectionChanged")
    assert len(client.requests) == (1 if deny else 2)
    assert context.ask_handler.await_count == 1


@pytest.mark.parametrize("profile_factory", [plan_profile, build_profile, None])
@pytest.mark.parametrize("trust", [TrustLevel.PUBLIC, TrustLevel.PRIVATE])
@pytest.mark.parametrize("classification", ["read", "write"])
@pytest.mark.parametrize("analysis_complete", [False, True])
async def test_shell_metadata_only_paths_preserve_profile_and_trust_policies(
    tmp_path, profile_factory, trust, classification, analysis_complete,
) -> None:
    command = "command -v python3; ls /usr/bin /usr/local/bin 2>/dev/null"
    if classification == "write":
        command += "; touch result.txt"
    call = ToolCall("shell", "shell_run", {"command": command})
    targets = [
        {"path": "/usr/bin", "kind": "toolchain_metadata"},
        {"path": "/usr/local/bin", "kind": "toolchain_metadata"},
        {"path": "/usr/bin/python3", "kind": "toolchain_metadata"},
        {"path": "/dev/null", "kind": "discard"},
        {"path": "/home/user/project/result.txt", "kind": "path", "access": classification},
    ]
    inspection = ShellInspectResult.model_validate({
        "targets": targets, "analysisComplete": analysis_complete,
        "classification": classification, "readOnly": classification == "read",
        "commands": [
            {"tokens": ["command", "-v", "python3"], "prefix": ["command"], "classification": "read"},
            {"tokens": ["ls", "/usr/bin", "/usr/local/bin"], "prefix": ["ls"], "classification": "read"},
            *([{"tokens": ["touch", "result.txt"], "prefix": ["touch"], "classification": "write"}]
              if classification == "write" else []),
        ],
        "inspectionRevision": "a" * 64,
    })
    client = FakeExecutionClient({
        "shell.inspect": inspection,
        "shell.run": ProcessResult(
            exit_code=0, timed_out=False, duration_ms=1, stdout_bytes=0, stderr_bytes=0,
            stdout_truncated=False, stderr_truncated=False,
        ),
    }, {"shell.inspect", "shell.run"})
    context = _context(tmp_path, call, client)
    context.db.scalar.return_value = ExecutionAgent(
        id=client.agent_id, user_id=context.session.user_id, home_directory="/home/user",
        home_filesystem_policy="auto", external_filesystem_policy="ask",
        home_shell_enabled=True, external_shell_enabled=True,
    )
    context.db.scalars.return_value = Mock(all=Mock(return_value=[]))
    settings = ToolSettings().select_trust(trust)
    profile = profile_factory() if profile_factory else None
    if profile:
        settings._agent_permissions = (
            profile.public_permissions if trust == TrustLevel.PUBLIC else profile.private_permissions
        )
    expected_policy = (
        ToolExecutionPolicy.DISABLED
        if profile_factory == plan_profile and classification == "write"
        else ToolExecutionPolicy.ASK
        if profile is None and (classification == "write" or trust == TrustLevel.PUBLIC)
        else ToolExecutionPolicy.AUTO
    )

    async def authorize(request):
        policy = _tool_policy(
            settings, IntegrationSettings(), request,
            snapshot=permission_snapshot(settings, {"shell_run"}),
        )
        assert policy == expected_policy
        return await PermissionPolicy.evaluate(
            context.db, context.session, request, context.root_files, policy,
            agent_profile_id=profile.id if profile else None, trust_level=trust,
        )

    context.ask_handler = AsyncMock(side_effect=authorize)
    result = await _tools(client.operations).execute(call, context)
    assert result.is_error == (expected_policy != ToolExecutionPolicy.AUTO)
    request = context.ask_handler.await_args.args[0]
    assert request.shell_metadata_paths == frozenset(target["path"] for target in targets[:-1])
    assert json.loads(request.resource_scope) == {
        "agentId": str(client.agent_id), "currentDirectory": client.current_directory,
        "classification": classification, "analysisComplete": analysis_complete,
        "prefixes": [unit.prefix for unit in inspection.commands],
        "paths": [target["path"] for target in targets],
        "targetAccesses": ["exempt", "exempt", "exempt", "exempt", classification],
    }
    assert set(request.approval_details) == {
        "shellClassification", "shellAnalysisComplete", "shellPrefixes",
    }
    assert [operation for operation, _ in client.requests] == (
        ["shell.inspect", "shell.run"] if not result.is_error else ["shell.inspect"]
    )
    if not result.is_error:
        assert client.requests[-1][1]["expectedInspectionRevision"] == "a" * 64


@pytest.mark.parametrize("classification", ["read", "write"])
@pytest.mark.parametrize("kind", ["path", "input", "output", "executable", "script", "source", "directory"])
@pytest.mark.parametrize("conflicting_path", ["/usr/bin/python3", "/dev/null"])
async def test_shell_ordinary_target_wins_over_metadata_for_the_same_path(
    tmp_path, classification, kind, conflicting_path,
) -> None:
    call = ToolCall("shell", "shell_run", {"command": "command -v python3; cat /usr/bin/python3"})
    target_access = (
        "write" if kind == "output"
        else "read" if kind in {"input", "executable", "script", "source"}
        else classification
    )
    effective_classification = "write" if target_access == "write" else classification
    client = FakeExecutionClient({
        "shell.inspect": ShellInspectResult(
            targets=[
                {"path": "/usr/bin/python3", "kind": "toolchain_metadata"},
                {"path": "/dev/null", "kind": "discard"},
                {"path": conflicting_path, "kind": kind, "access": target_access},
            ],
            analysis_complete=False, classification=effective_classification,
            read_only=effective_classification == "read",
            commands=[], inspection_revision="a" * 64,
        ),
    }, {"shell.inspect", "shell.run"})
    context = _context(tmp_path, call, client, decision=PermissionDecision.ASK)
    result = await _tools(client.operations).execute(call, context)
    request = context.ask_handler.await_args.args[0]
    assert result.is_error
    assert request.shell_metadata_paths == frozenset({"/usr/bin/python3", "/dev/null"}) - {conflicting_path}
    assert json.loads(request.resource_scope)["paths"] == [
        "/usr/bin/python3", "/dev/null", conflicting_path,
    ]
    context.db.scalar.return_value = ExecutionAgent(
        id=client.agent_id, user_id=context.session.user_id, home_directory="/home/user",
        home_filesystem_policy="auto", external_filesystem_policy="ask",
        home_shell_enabled=True, external_shell_enabled=True,
    )
    context.db.scalars.return_value = Mock(all=Mock(return_value=[]))
    assert await PermissionPolicy.evaluate(
        context.db, context.session, request, context.root_files, ToolExecutionPolicy.AUTO,
    ) == PermissionDecision.ASK


@pytest.mark.parametrize("classification", ["read", "write"])
@pytest.mark.parametrize("analysis_complete", [False, True])
@pytest.mark.parametrize(("path", "overrides", "expected"), [
    ("/usr/bin", {}, PermissionDecision.ALLOW),
    ("/dev/null", {}, PermissionDecision.ALLOW),
    ("/usr/bin", {"external_shell_enabled": False}, PermissionDecision.DENY),
    ("/dev/null", {"external_shell_enabled": False}, PermissionDecision.DENY),
    ("/usr/bin", {"external_filesystem_policy": "deny"}, PermissionDecision.DENY),
    ("/dev/null", {"external_filesystem_policy": "deny"}, PermissionDecision.DENY),
    ("/usr/bin", {"home_shell_enabled": False}, PermissionDecision.DENY),
    ("/usr/bin", {"home_filesystem_policy": "deny"}, PermissionDecision.DENY),
    ("/usr/bin/.env", {}, PermissionDecision.ASK),
    ("/usr/bin", {"unavailable": True}, PermissionDecision.DENY),
])
async def test_shell_metadata_paths_do_not_bypass_hard_checks(
    tmp_path, classification, analysis_complete, path, overrides, expected,
) -> None:
    client = FakeExecutionClient({}, {"shell.inspect", "shell.run"})
    context = _context(tmp_path, ToolCall("shell", "shell_run", {}), client)
    agent = ExecutionAgent(
        id=client.agent_id, user_id=context.session.user_id, home_directory="/home/user",
        home_filesystem_policy="auto", external_filesystem_policy="ask",
        home_shell_enabled=True, external_shell_enabled=True,
    )
    for key, value in overrides.items():
        if key != "unavailable":
            setattr(agent, key, value)
    context.db.scalar.return_value = None if overrides.get("unavailable") else agent
    context.db.scalars.return_value = Mock(all=Mock(return_value=[]))
    request = replace(
        _shell_permission(client, classification=classification, paths=[path], analysis_complete=analysis_complete),
        shell_metadata_paths=frozenset({path}),
    )
    assert await PermissionPolicy.evaluate(
        context.db, context.session, request, context.root_files, ToolExecutionPolicy.AUTO,
    ) == expected
    statement = context.db.scalar.await_args.args[0]
    assert statement.get_execution_options()["populate_existing"] is True
    assert "revoked_at IS NULL" in str(statement)
    assert context.session.user_id in statement.compile().params.values()
    assert client.agent_id in statement.compile().params.values()


async def test_shell_metadata_must_be_current_typed_scope_subset(tmp_path) -> None:
    client = FakeExecutionClient({}, {"shell.inspect", "shell.run"})
    context = _context(tmp_path, ToolCall("shell", "shell_run", {}), client)
    context.db.scalar.return_value = ExecutionAgent(
        id=client.agent_id, user_id=context.session.user_id, home_directory="/home/user",
        home_filesystem_policy="auto", external_filesystem_policy="ask",
        home_shell_enabled=True, external_shell_enabled=True,
    )
    context.db.scalars.return_value = Mock(all=Mock(return_value=[]))
    request = _shell_permission(client, classification="read", paths=["/etc/hosts"])
    assert request.shell_metadata_paths == frozenset()
    for details in ({"shell_metadata_paths": ["/etc/hosts"]}, {"shellMetadataPaths": ["/etc/hosts"]}):
        assert await PermissionPolicy.evaluate(
            context.db, context.session, replace(request, approval_details=details),
            context.root_files, ToolExecutionPolicy.AUTO,
        ) == PermissionDecision.ASK
        with pytest.raises(ValidationError):
            ShellRunArguments(command="cat /etc/hosts", **details)
    assert await PermissionPolicy.evaluate(
        context.db, context.session,
        replace(request, shell_metadata_paths=frozenset({"/usr/bin"})),
        context.root_files, ToolExecutionPolicy.AUTO,
    ) == PermissionDecision.DENY
    # Typed filesystem reads never use the shell-only exemption.
    direct_read = execution_directory_permission(client.agent_id, "/usr/bin", capability="filesystem.read")
    assert await PermissionPolicy.evaluate(
        context.db, context.session,
        replace(direct_read, shell_metadata_paths=frozenset({"/usr/bin"})),
        context.root_files, ToolExecutionPolicy.AUTO,
    ) == PermissionDecision.ASK


async def test_shell_reinspection_keeps_existing_grants_class_bound(tmp_path) -> None:
    call = ToolCall("shell", "shell_run", {"command": "command -v python3"})
    inspection = ShellInspectResult(
        targets=[{"path": "/usr/bin/python3", "kind": "toolchain_metadata"}],
        analysis_complete=False, classification="read", read_only=True,
        commands=[{"tokens": ["command", "-v", "python3"], "prefix": ["command"], "classification": "read"}],
        inspection_revision="a" * 64,
    )
    client = FakeExecutionClient({"shell.inspect": inspection}, {"shell.inspect", "shell.run"})
    context = _context(tmp_path, call, client)
    context.db.scalar.return_value = ExecutionAgent(
        id=client.agent_id, user_id=context.session.user_id, home_directory="/home/user",
        home_filesystem_policy="auto", external_filesystem_policy="ask",
        home_shell_enabled=True, external_shell_enabled=True,
    )
    grants = []
    context.db.scalars.return_value = Mock(all=Mock(side_effect=lambda: grants))

    async def authorize(request):
        return await PermissionPolicy.evaluate(
            context.db, context.session, request, context.root_files, ToolExecutionPolicy.ASK,
        )

    context.ask_handler = AsyncMock(side_effect=authorize)
    assert (await _tools(client.operations).execute(call, context)).is_error
    pending = context.ask_handler.await_args.args[0]
    scope = pending.effective_session_grants[0]
    grants.append(PermissionGrant(
        session_id=context.session.id, capability=scope.capability,
        resource_type=scope.resource_type, resource_scope=scope.resource_scope,
    ))
    client.responses["shell.inspect"] = ShellInspectResult.model_validate({
        **inspection.model_dump(), "classification": "write", "read_only": False,
        "inspection_revision": "b" * 64,
    })
    assert (await _tools(client.operations).execute(call, context)).is_error
    reinspected = context.ask_handler.await_args.args[0]
    assert reinspected.capability == "shell.write"
    assert not PermissionPolicy.grant_matches(grants[0], reinspected, context.root_files)
    assert [operation for operation, _ in client.requests] == ["shell.inspect", "shell.inspect"]
    assert await authorize(pending) == PermissionDecision.ALLOW
    assert await PermissionPolicy.evaluate(
        context.db, context.session, pending, context.root_files, ToolExecutionPolicy.DISABLED,
    ) == PermissionDecision.DENY


async def test_terminal_shell_failure_never_falls_back_to_shell_transport(tmp_path, monkeypatch) -> None:
    from mato_loop_backend.agent import shared_shell

    call = ToolCall("shell", "shell_run", {"command": "pwd"})
    client = FakeExecutionClient({
        "shell.inspect": ShellInspectResult(
            targets=[], analysis_complete=False, classification="read", read_only=True,
            commands=[], inspection_revision="a" * 64,
        ),
    }, {"shell.inspect", "shell.run", "terminal.run"})
    context = _context(tmp_path, call, client)
    monkeypatch.setattr(terminal_coordinator, "attached_for", lambda **_kwargs: object())
    run = AsyncMock(side_effect=ExecutionClientError("process.inspectionChanged"))
    monkeypatch.setattr(shared_shell, "run_shared_shell", run)
    result = await _tools(client.operations).execute(call, context)
    assert result.is_error
    assert result.metadata["errorCode"] == "process.inspectionChanged"
    assert client.requests == []
    run.assert_awaited_once()
