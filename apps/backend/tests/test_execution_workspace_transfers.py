import base64
import hashlib
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlparse
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.api.errors import ApiError
from mato_loop_backend.api.routes import workspace as workspace_route
from mato_loop_backend.api.routes.sessions import message_payload
from mato_loop_backend.execution.protocol import ResponseMessage, RootSnapshot
from mato_loop_backend.execution.transfers import (
    TransferError,
    TransferTicketStore,
    stage_file,
)
from mato_loop_backend.models import (
    ChatSession,
    ExecutionAgent,
    ExecutionAgentRoot,
    Message,
    UserAccount,
)


def _user() -> UserAccount:
    return UserAccount(id=uuid4(), username="test", password_hash="hash")


async def _chunks(*values: bytes):
    for value in values:
        yield value


async def test_transfer_tickets_are_owned_single_use_and_verify_streams() -> None:
    owner = uuid4()
    other = uuid4()
    data = b"bounded transfer"
    digest = hashlib.sha256(data).hexdigest()
    store = TransferTicketStore()

    grant, _ = store.create_download(owner, BytesIO(data), size=len(data), sha256=digest)
    with pytest.raises(TransferError, match="invalidTicket"):
        store.claim(grant.ticket_id, grant.token, "download", owner_id=other)
    ticket = store.claim(grant.ticket_id, grant.token, "download", owner_id=owner)
    assert ticket.content.read() == data
    with pytest.raises(TransferError, match="invalidTicket"):
        store.claim(grant.ticket_id, grant.token, "download", owner_id=owner)

    upload_grant, upload = store.create_upload(owner, size=len(data), sha256=digest)
    claimed = store.claim(upload_grant.ticket_id, upload_grant.token, "upload")
    await store.receive(
        claimed,
        _chunks(data[:4], data[4:]),
        content_length=len(data),
        digest_header=(
            "sha-256=" + base64.b64encode(bytes.fromhex(digest)).decode()
        ),
    )
    assert upload.content.read() == data
    assert upload.actual_sha256 == digest


def test_transfer_tickets_can_be_revoked_by_owner() -> None:
    owner = uuid4()
    other = uuid4()
    data = BytesIO(b"pending")
    digest = hashlib.sha256(b"pending").hexdigest()
    store = TransferTicketStore()
    owned, ticket = store.create_download(owner, data, size=7, sha256=digest)
    retained, _ = store.create_upload(other, size=0)

    store.revoke_owner(owner)

    assert data.closed is True
    with pytest.raises(TransferError, match="invalidTicket"):
        store.claim(owned.ticket_id, owned.token, "download", owner_id=owner)
    assert store.claim(retained.ticket_id, retained.token, "upload", owner_id=other)
    assert ticket.owner_id == owner


async def test_transfer_http_requires_agent_and_ticket_credentials() -> None:
    owner = uuid4()
    other = uuid4()
    data = b"agent transfer"
    digest = hashlib.sha256(data).hexdigest()
    grant, _ = workspace_route.transfer_tickets.create_download(
        owner, BytesIO(data), size=len(data), sha256=digest
    )
    unauthorized_db = AsyncMock(spec=AsyncSession)
    unauthorized_db.scalar.return_value = None
    with pytest.raises(ApiError) as exc_info:
        await workspace_route.get_workspace_transfer(
            grant.ticket_id,
            unauthorized_db,
            authorization="Bearer revoked-or-invalid",
            x_mato_transfer_token=grant.token,
        )
    assert exc_info.value.status_code == 401

    wrong_db = AsyncMock(spec=AsyncSession)
    wrong_db.scalar.return_value = other

    with pytest.raises(ApiError) as exc_info:
        await workspace_route.get_workspace_transfer(
            grant.ticket_id,
            wrong_db,
            authorization="Bearer registration-token",
            x_mato_transfer_token=grant.token,
        )
    assert exc_info.value.code == "transfer.invalidTicket"

    db = AsyncMock(spec=AsyncSession)
    db.scalar.return_value = owner
    with pytest.raises(ApiError) as exc_info:
        await workspace_route.get_workspace_transfer(
            grant.ticket_id,
            db,
            authorization="Bearer registration-token",
        )
    assert exc_info.value.code == "transfer.invalidTicket"

    response = await workspace_route.get_workspace_transfer(
        grant.ticket_id,
        db,
        authorization="Bearer registration-token",
        x_mato_transfer_token=grant.token,
    )
    assert b"".join([chunk async for chunk in response.body_iterator]) == data

    upload_grant, upload = workspace_route.transfer_tickets.create_upload(
        owner, size=len(data), sha256=digest
    )
    response = await workspace_route.put_workspace_transfer(
        upload_grant.ticket_id,
        SimpleNamespace(stream=lambda: _chunks(data)),
        db,
        authorization="Bearer registration-token",
        x_mato_transfer_token=upload_grant.token,
        content_length=len(data),
        digest="sha-256=" + base64.b64encode(bytes.fromhex(digest)).decode(),
    )
    assert response.status_code == 204
    assert upload.content.read() == data


def test_stage_file_rejects_content_past_the_bound() -> None:
    with pytest.raises(TransferError, match="tooLarge"):
        stage_file(BytesIO(b"1234"), max_bytes=3)


def test_transfer_url_uses_the_agent_connection_origin(monkeypatch) -> None:
    agent_id = uuid4()
    monkeypatch.setattr(
        workspace_route.execution_agent_connections,
        "_connections",
        {
            agent_id: SimpleNamespace(
                websocket=SimpleNamespace(
                    url="wss://backend.internal/api/execution-agents/connect"
                )
            )
        },
    )
    request = SimpleNamespace(
        url_for=lambda *_args, **_kwargs: SimpleNamespace(
            path="/api/workspace/transfers/ticket"
        )
    )

    spec = workspace_route._transfer_spec(request, agent_id, uuid4(), "secret")

    assert spec == {
        "url": "https://backend.internal/api/workspace/transfers/ticket",
        "token": "secret",
    }


async def test_file_context_uses_owned_session_root_snapshot(monkeypatch) -> None:
    user = _user()
    agent_id = uuid4()
    root_id = uuid4()
    revision = "a" * 64
    session = ChatSession(
        id=uuid4(),
        user_id=user.id,
        title="Test",
        preferred_execution_agent_id=agent_id,
        preferred_root_id=root_id,
        working_directory="project",
        workspace_access="read-only",
    )
    agent = ExecutionAgent(
        id=agent_id,
        user_id=user.id,
        name="Desktop",
        token_hash="x" * 64,
        roots=[
            ExecutionAgentRoot(
                execution_agent_id=agent_id,
                id=root_id,
                label="Projects",
                revision=revision,
            )
        ],
    )
    db = AsyncMock(spec=AsyncSession)
    db.get.return_value = session
    db.scalar.return_value = agent
    monkeypatch.setattr(
        workspace_route.execution_agent_connections,
        "snapshot",
        lambda selected: (
            frozenset({"workspace.list"}),
            (RootSnapshot(id=root_id, label="Projects", revision=revision),),
        )
        if selected == agent_id
        else None,
    )

    context = await workspace_route._file_context(
        workspace_route.FileScope.FILESYSTEM,
        session.id,
        user,
        db,
        root_identity=str(root_id),
    )

    assert context.agent_id == agent_id
    assert context.agent_root_id == root_id
    assert context.working_directory == "project"
    assert context.agent_writable is False


async def test_historical_file_action_url_ignores_changed_session_preferences(
    monkeypatch,
) -> None:
    user = _user()
    bound_agent_id = uuid4()
    bound_root_id = uuid4()
    revision = "c" * 64
    session = ChatSession(
        id=uuid4(),
        user_id=user.id,
        title="Test",
        preferred_execution_agent_id=bound_agent_id,
        preferred_root_id=bound_root_id,
        working_directory="persisted/path",
    )
    agent = ExecutionAgent(
        id=bound_agent_id,
        user_id=user.id,
        name="Bound",
        token_hash="x" * 64,
        roots=[
            ExecutionAgentRoot(
                execution_agent_id=bound_agent_id,
                id=bound_root_id,
                label="Bound root",
                revision=revision,
            )
        ],
    )
    db = AsyncMock(spec=AsyncSession)
    db.get.return_value = session
    db.scalar.return_value = agent
    monkeypatch.setattr(
        workspace_route.execution_agent_connections,
        "snapshot",
        lambda agent_id: (
            frozenset({"workspace.export", "workspace.stat"}),
            (RootSnapshot(id=bound_root_id, label="Bound root", revision=revision),),
        )
        if agent_id == bound_agent_id
        else None,
    )

    message = Message(
        id=uuid4(),
        session_id=session.id,
        role="tool",
        content="{}",
        provider_data={
            "_matoloop": {
                "fileActions": [
                    {
                        "operation": "write",
                        "root": "workspace",
                        "rootIdentity": str(bound_root_id),
                        "path": "report.txt",
                        "filename": "report.txt",
                        "sizeBytes": 10,
                        "executionAgentId": str(bound_agent_id),
                        "rootId": str(bound_root_id),
                        "rootRevision": revision,
                        "workingDirectory": "persisted/path",
                    }
                ]
            }
        },
    )
    params = parse_qs(
        urlparse(message_payload(message)["fileActions"][0]["downloadUrl"]).query
    )

    session.preferred_execution_agent_id = uuid4()
    session.preferred_root_id = uuid4()
    session.working_directory = "current"
    exported_context = None

    async def export(context, *_args, **_kwargs):
        nonlocal exported_context
        exported_context = context
        return BytesIO(b"historical"), 10, hashlib.sha256(b"historical").hexdigest()

    monkeypatch.setattr(workspace_route, "_agent_export", export)
    response = await workspace_route.download_file(
        user,
        db,
        params["path"][0],
        request=SimpleNamespace(),
        scope=workspace_route.FileScope(params["scope"][0]),
        session_id=UUID(params["session_id"][0]),
        root_identity=params["root_identity"][0],
        execution_agent_id=UUID(params["execution_agent_id"][0]),
        root_id=UUID(params["root_id"][0]),
        root_revision=params["root_revision"][0],
        working_directory=params["working_directory"][0],
    )
    body = b"".join([chunk async for chunk in response.body_iterator])

    assert body == b"historical"
    assert exported_context is not None
    assert exported_context.agent_id == bound_agent_id
    assert exported_context.agent_root_id == bound_root_id
    assert exported_context.agent_root_revision == revision
    assert exported_context.working_directory == "persisted/path"

    with pytest.raises(ApiError) as exc_info:
        await workspace_route._file_context(
            workspace_route.FileScope.FILESYSTEM,
            session.id,
            user,
            db,
            root_identity=str(bound_root_id),
            execution_agent_id=bound_agent_id,
            root_id=bound_root_id,
            root_revision="d" * 64,
            working_directory="persisted/path",
        )
    assert exc_info.value.code == "file.workspaceNotSelected"


async def test_agent_listing_rejects_paths_outside_requested_directory(monkeypatch) -> None:
    context = workspace_route._FileContext(
        agent_id=uuid4(),
        agent_root_id=uuid4(),
        agent_root_revision="a" * 64,
        working_directory=".",
    )

    async def file_context(*_args, **_kwargs):
        return context

    request = AsyncMock(
        return_value=ResponseMessage.model_validate(
            {
                "type": "response",
                "id": uuid4(),
                "ok": True,
                "result": {
                    "entries": [
                        {
                            "name": "escape.txt",
                            "path": "other/escape.txt",
                            "kind": "file",
                            "size": 1,
                            "modifiedAt": 1.0,
                        }
                    ],
                    "hasMore": False,
                    "nextOffset": None,
                },
            }
        )
    )
    monkeypatch.setattr(workspace_route, "_file_context", file_context)
    monkeypatch.setattr(workspace_route.execution_agent_connections, "request", request)

    with pytest.raises(ApiError) as exc_info:
        await workspace_route.list_entries(_user(), AsyncMock(spec=AsyncSession), path=".")

    assert exc_info.value.status_code == 502
    assert exc_info.value.code == "executionAgent.protocolError"


async def test_cross_root_agent_copy_uses_one_agent_operation(monkeypatch) -> None:
    agent_id = uuid4()
    source_root = uuid4()
    destination_root = uuid4()
    source = workspace_route._FileContext(
        agent_id=agent_id,
        agent_root_id=source_root,
        agent_root_revision="a" * 64,
        working_directory="source",
        agent_writable=False,
        agent_operations=frozenset({"workspace.copy", "workspace.stat"}),
    )
    destination = workspace_route._FileContext(
        agent_id=agent_id,
        agent_root_id=destination_root,
        agent_root_revision="b" * 64,
        working_directory="destination",
        agent_operations=frozenset({"workspace.copy", "workspace.stat"}),
    )
    contexts = iter((source, destination))

    async def file_context(*_args, **_kwargs):
        return next(contexts)

    response = ResponseMessage.model_validate(
        {
            "type": "response",
            "id": uuid4(),
            "ok": True,
            "result": {"path": "copied.txt"},
        }
    )
    remote_request = AsyncMock(return_value=response)
    monkeypatch.setattr(workspace_route, "_file_context", file_context)
    clear_grants = AsyncMock()
    monkeypatch.setattr(workspace_route, "_clear_context_grants", clear_grants)
    monkeypatch.setattr(
        workspace_route, "_agent_stat", AsyncMock(return_value={"path": "copied.txt"})
    )
    monkeypatch.setattr(
        workspace_route.execution_agent_connections, "request", remote_request
    )
    db = AsyncMock(spec=AsyncSession)

    result = await workspace_route._copy_or_move_entry(
        workspace_route.CopyMoveEntryRequest(
            path="source.txt",
            destination="copied.txt",
            destination_scope=workspace_route.FileScope.FILESYSTEM,
            destination_execution_agent_id=agent_id,
            destination_root_id=destination_root,
        ),
        _user(),
        db,
        SimpleNamespace(),
        move=False,
        scope=workspace_route.FileScope.FILESYSTEM,
        session_id=None,
        working_directory=None,
        execution_agent_id=agent_id,
        root_id=source_root,
    )

    assert result == {"entry": {"path": "copied.txt"}}
    assert remote_request.await_args.args[1:] == (
        "workspace.copy",
        {
            "rootId": str(source_root),
            "rootRevision": "a" * 64,
            "workingDirectory": "source",
            "path": "source.txt",
            "destination": "copied.txt",
            "destinationRootId": str(destination_root),
            "destinationRootRevision": "b" * 64,
            "destinationWorkingDirectory": "destination",
        },
    )
    clear_grants.assert_awaited_once()
    assert clear_grants.await_args.args[3] == ("copied.txt",)


async def test_cross_boundary_move_is_rejected_before_side_effects(monkeypatch) -> None:
    source = workspace_route._FileContext(
        agent_id=uuid4(),
        agent_root_id=uuid4(),
        agent_root_revision="a" * 64,
        agent_operations=frozenset({"workspace.export", "workspace.stat"}),
    )
    destination = workspace_route._FileContext(
        root=workspace_route.FileRoot.SESSION,
        root_files=SimpleNamespace(),
    )
    contexts = iter((source, destination))

    async def file_context(*_args, **_kwargs):
        return next(contexts)

    clear_grants = AsyncMock()
    monkeypatch.setattr(workspace_route, "_file_context", file_context)
    monkeypatch.setattr(workspace_route, "_clear_context_grants", clear_grants)

    with pytest.raises(ApiError) as exc_info:
        await workspace_route._copy_or_move_entry(
            workspace_route.CopyMoveEntryRequest(
                path="source.txt",
                destination="files/source.txt",
                destination_scope=workspace_route.FileScope.SESSION,
            ),
            _user(),
            AsyncMock(spec=AsyncSession),
            SimpleNamespace(),
            move=True,
            scope=workspace_route.FileScope.FILESYSTEM,
            session_id=uuid4(),
            working_directory=None,
            execution_agent_id=None,
            root_id=None,
        )

    assert exc_info.value.code == "file.crossBoundaryMoveUnavailable"
    clear_grants.assert_not_awaited()


async def test_direct_move_clears_source_and_destination_grants(monkeypatch) -> None:
    agent_id = uuid4()
    source = workspace_route._FileContext(
        agent_id=agent_id,
        agent_root_id=uuid4(),
        agent_root_revision="a" * 64,
        agent_operations=frozenset({"workspace.move", "workspace.stat"}),
    )
    destination = workspace_route._FileContext(
        agent_id=agent_id,
        agent_root_id=uuid4(),
        agent_root_revision="b" * 64,
        agent_operations=frozenset({"workspace.move", "workspace.stat"}),
    )
    contexts = iter((source, destination))

    async def file_context(*_args, **_kwargs):
        return next(contexts)

    clear_grants = AsyncMock()
    monkeypatch.setattr(workspace_route, "_file_context", file_context)
    monkeypatch.setattr(workspace_route, "_clear_context_grants", clear_grants)
    monkeypatch.setattr(
        workspace_route.execution_agent_connections,
        "request",
        AsyncMock(
            return_value=ResponseMessage.model_validate(
                {
                    "type": "response",
                    "id": uuid4(),
                    "ok": True,
                    "result": {"path": "new.txt"},
                }
            )
        ),
    )
    monkeypatch.setattr(
        workspace_route, "_agent_stat", AsyncMock(return_value={"path": "new.txt"})
    )

    await workspace_route._copy_or_move_entry(
        workspace_route.CopyMoveEntryRequest(
            path="old.txt",
            destination="new.txt",
            destination_scope=workspace_route.FileScope.FILESYSTEM,
            destination_execution_agent_id=agent_id,
            destination_root_id=destination.agent_root_id,
        ),
        _user(),
        AsyncMock(spec=AsyncSession),
        SimpleNamespace(),
        move=True,
        scope=workspace_route.FileScope.FILESYSTEM,
        session_id=None,
        working_directory=None,
        execution_agent_id=agent_id,
        root_id=source.agent_root_id,
    )

    assert [call.args[3] for call in clear_grants.await_args_list] == [
        ("old.txt",),
        ("new.txt",),
    ]
