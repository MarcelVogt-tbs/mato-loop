import hashlib
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.api.errors import ApiError
from mato_loop_backend.api.routes import workspace as workspace_route
from mato_loop_backend.api.workspace_service import (
    FileScope,
    _agent_context,
    _agent_request,
    _file_context,
    _FileContext,
    _normalize_agent_path,
)
from mato_loop_backend.execution.protocol import ResponseMessage
from mato_loop_backend.models import ChatSession, ExecutionAgent, UserAccount
from mato_loop_backend.settings import InstanceLimits
from mato_loop_backend.workspace import FileRoot, RootFileService


def _user():
    return UserAccount(id=uuid4(), username="test", password_hash="hash")


async def test_owner_context_accepts_arbitrary_absolute_agent_paths(monkeypatch) -> None:
    user = _user()
    agent = ExecutionAgent(id=uuid4(), user_id=user.id, alias="Desktop")
    db = AsyncMock(spec=AsyncSession)
    db.scalar.return_value = agent
    monkeypatch.setattr(
        "mato_loop_backend.api.workspace_service.execution_agent_connections.snapshot",
        lambda _agent_id: frozenset({"filesystem.read"}),
    )

    context = await _agent_context(db, user, agent.id, current_directory="/home/user")

    assert context.agent_id == agent.id
    assert context.current_directory == "/home/user"
    assert context.agent_operations == {"filesystem.read"}


async def test_owner_request_injects_no_root_or_working_directory(monkeypatch) -> None:
    agent_id = uuid4()
    request = AsyncMock(
        return_value=ResponseMessage.model_validate(
            {"type": "response", "id": uuid4(), "ok": True, "result": {}}
        )
    )
    monkeypatch.setattr(
        "mato_loop_backend.api.workspace_service.execution_agent_connections.request",
        request,
    )
    context = _FileContext(
        agent_id=agent_id,
        agent_operations=frozenset({"filesystem.read"}),
    )

    await _agent_request(context, "filesystem.read", {"path": "/etc/hosts"})

    request.assert_awaited_once_with(
        agent_id, "filesystem.read", {"path": "/etc/hosts"}
    )


async def test_session_filesystem_context_uses_selected_agent(monkeypatch) -> None:
    user = _user()
    agent_id = uuid4()
    session = ChatSession(
        id=uuid4(),
        user_id=user.id,
        title="Test",
        preferred_execution_agent_id=agent_id,
        current_directory="/home/user/project",
    )
    db = AsyncMock(spec=AsyncSession)
    db.get.return_value = session
    selected = _FileContext(agent_id=agent_id)
    resolve = AsyncMock(return_value=selected)
    monkeypatch.setattr("mato_loop_backend.api.workspace_service._agent_context", resolve)

    context = await _file_context(
        FileScope.FILESYSTEM,
        session.id,
        user,
        db,
        instance_limits=InstanceLimits(),
    )

    assert context.agent_id == agent_id
    resolve.assert_awaited_once_with(
        db,
        user,
        agent_id,
        current_directory="/home/user/project",
    )


async def test_filesystem_context_rejects_historical_root_identity() -> None:
    with pytest.raises(ApiError) as error:
        await _file_context(
            FileScope.FILESYSTEM,
            None,
            _user(),
            AsyncMock(spec=AsyncSession),
            root_identity=str(uuid4()),
            execution_agent_id=uuid4(),
            instance_limits=InstanceLimits(),
        )
    assert error.value.code == "file.invalidRoot"


def test_agent_path_normalizer_accepts_the_filesystem_root() -> None:
    assert _normalize_agent_path("/") == "/"


async def test_copy_normalizes_managed_source_and_agent_destination_independently(
    monkeypatch,
) -> None:
    agent_id = uuid4()
    source = _FileContext(
        root_files=RootFileService(uuid4()),
        root=FileRoot.SESSION,
    )
    destination = _FileContext(
        agent_id=agent_id,
        agent_operations=frozenset({"filesystem.import", "filesystem.stat"}),
    )
    contexts = iter((source, destination))
    monkeypatch.setattr(
        workspace_route,
        "_file_context",
        AsyncMock(side_effect=lambda *_args, **_kwargs: next(contexts)),
    )
    monkeypatch.setattr(workspace_route, "_clear_context_grants", AsyncMock())
    opened = Mock(return_value=BytesIO(b"content"))
    monkeypatch.setattr(workspace_route, "_open_context", opened)
    imported = AsyncMock(return_value={"path": "/tmp/file.txt"})
    monkeypatch.setattr(workspace_route, "_import_agent_file", imported)

    result = await workspace_route._copy_or_move_entry(
        workspace_route.CopyMoveEntryRequest(
            path="docs/file.txt",
            destination="/tmp/file.txt",
            destination_scope=FileScope.FILESYSTEM,
            destination_execution_agent_id=agent_id,
        ),
        _user(),
        AsyncMock(spec=AsyncSession),
        SimpleNamespace(),
        move=False,
        scope=FileScope.SESSION,
        session_id=uuid4(),
        current_directory=None,
        execution_agent_id=None,
    )

    assert result == {"entry": {"path": "/tmp/file.txt"}}
    opened.assert_called_once_with(source, "docs/file.txt")
    assert imported.await_args.args[1] == "/tmp/file.txt"
    assert imported.await_args.args[3] == len(b"content")
    assert imported.await_args.args[4] == hashlib.sha256(b"content").hexdigest()
