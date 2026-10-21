import asyncio
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from sqlalchemy import CheckConstraint, ForeignKeyConstraint, UniqueConstraint
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.agent.runtime import AgentRuntime
from mato_loop_backend.api.errors import ApiError
from mato_loop_backend.api.routes import sessions as routes
from mato_loop_backend.chat_history import ChatHistoryError, ChatHistoryService
from mato_loop_backend.files.storage import SessionStorage
from mato_loop_backend.memory.episode_indexing import EpisodeIndexService, _eligible_filters
from mato_loop_backend.memory.episode_retrieval import _candidate_filters
from mato_loop_backend.models import AgentRun, ChatSession, Message, UserAccount
from mato_loop_backend.sessions import cleanup, service
from mato_loop_backend.settings import TrustLevel


def _child(user_id):
    return ChatSession(
        id=uuid4(), user_id=user_id, title="Child", session_type="chat",
        parent_session_id=uuid4(), spawned_by_run_id=uuid4(),
        spawned_by_tool_call_id="call-1", spawn_index=0,
    )


def test_child_metadata_contract() -> None:
    table = ChatSession.__table__
    fields = ("parent_session_id", "spawned_by_run_id", "spawned_by_tool_call_id", "spawn_index")
    assert all(table.c[field].nullable for field in fields)
    assert table.c.spawned_by_tool_call_id.type.length == 200
    constraints = table.constraints
    checks = {
        constraint.name: str(constraint.sqltext)
        for constraint in constraints if isinstance(constraint, CheckConstraint)
    }
    shape = checks["ck_chat_sessions_spawn_shape_valid"]
    assert all(f"{field} IS NULL" in shape and f"{field} IS NOT NULL" in shape for field in fields)
    assert "parent_session_id <> id" in checks["ck_chat_sessions_parent_not_self"]
    assert "spawn_index >= 0" in checks["ck_chat_sessions_spawn_index_nonnegative"]
    assert any(
        isinstance(constraint, UniqueConstraint)
        and list(constraint.columns.keys()) == list(fields[1:])
        for constraint in constraints
    )
    parent_fk = next(
        constraint for constraint in constraints
        if isinstance(constraint, ForeignKeyConstraint)
        and constraint.name == "fk_chat_sessions_parent_owner"
    )
    assert parent_fk.ondelete == "CASCADE"
    assert [element.target_fullname for element in parent_fk.elements] == [
        "chat_sessions.id", "chat_sessions.user_id",
    ]


async def test_child_owner_read_and_mutation_guard() -> None:
    child = _child(uuid4())
    db = AsyncMock(spec=AsyncSession)
    db.get.return_value = child
    db.scalar.return_value = child
    assert await service.owned_session(db, child.id, child.user_id) is child
    assert await service.owned_session(db, child.id, uuid4()) is None
    for locked in (False, True):
        with pytest.raises(service.SessionServiceError) as error:
            await service.get_owned_session(db, child.id, child.user_id, for_update=locked)
        assert (error.value.status_code, error.value.code) == (403, "session.readOnly")
    payload = routes.session_payload(child)
    assert {key: payload[key] for key in (
        "parentSessionId", "spawnedByRunId", "spawnedByToolCallId", "spawnIndex", "readOnly",
    )} == {
        "parentSessionId": str(child.parent_session_id),
        "spawnedByRunId": str(child.spawned_by_run_id),
        "spawnedByToolCallId": "call-1", "spawnIndex": 0, "readOnly": True,
    }
    parent = ChatSession(id=uuid4(), user_id=child.user_id)
    assert routes.session_payload(parent)["readOnly"] is False


@pytest.mark.parametrize("operation", ["send", "update", "delete", "undo", "retry", "cancel", "read"])
async def test_child_mutation_routes_reject_before_side_effects(operation) -> None:
    user = UserAccount(id=uuid4())
    child = _child(user.id)
    db = AsyncMock(spec=AsyncSession)
    db.get.return_value = child
    db.scalar.return_value = child
    message_id, run_id = uuid4(), uuid4()
    with pytest.raises(ApiError) as error:
        if operation == "send":
            await routes.send_message(child.id, routes.SendMessageRequest(content="change"), user, db)
        elif operation == "update":
            await routes.update_session(child.id, routes.SessionUpdateRequest(title="change"), user, db)
        elif operation == "delete":
            await routes.delete_session(child.id, user, db)
        elif operation == "undo":
            await routes.undo_user_message(child.id, message_id, user, db)
        elif operation == "retry":
            await routes.retry_failed_message(child.id, message_id, user, db)
        elif operation == "cancel":
            await routes.cancel_run(child.id, run_id, user, db)
        else:
            await routes.mark_session_read(
                child.id, routes.SessionReadRequest(throughMessageId=message_id), user, db
            )
    assert (error.value.status_code, error.value.code) == (403, "session.readOnly")
    db.commit.assert_not_awaited()
    db.delete.assert_not_awaited()
    assert all(call.args[0].is_select for call in db.execute.await_args_list)


async def test_child_history_and_duplicate_submission_cannot_bypass_guard() -> None:
    child = _child(uuid4())
    db = AsyncMock(spec=AsyncSession)
    db.scalar.return_value = child
    with pytest.raises(ChatHistoryError, match="session.readOnly"):
        await ChatHistoryService._owned_session(db, child.id, child.user_id)
    run = AgentRun(id=uuid4(), session_id=child.id, status="queued")
    db.scalar.return_value = run
    db.get.return_value = child
    with pytest.raises(service.SessionServiceError, match="session.readOnly"):
        await service.duplicate_submission_response(
            db, run.id, child.user_id,
            session_serializer=AsyncMock(), message_serializer=MagicMock(),
        )


async def test_children_endpoint_returns_latest_normal_run_and_owner_bound_order() -> None:
    user = UserAccount(id=uuid4())
    child = _child(user.id)
    parent = ChatSession(id=child.parent_session_id, user_id=user.id)
    second = _child(user.id)
    second.parent_session_id = parent.id
    second.spawn_index = 1
    run = AgentRun(id=uuid4(), session_id=child.id, status="running")
    db = AsyncMock(spec=AsyncSession)
    db.get.return_value = parent
    db.execute.return_value = MagicMock(all=lambda: [(child, run), (second, None)])
    result = await routes.get_session_children(parent.id, user, db)
    assert result[0] == {
        "id": str(child.id), "title": child.title,
        "parentSessionId": str(parent.id), "spawnedByRunId": str(child.spawned_by_run_id),
        "spawnedByToolCallId": "call-1", "spawnIndex": 0, "run": routes.run_payload(run),
    }
    assert result[1]["run"] is None
    query = db.execute.await_args.args[0].compile(dialect=postgresql.dialect())
    assert user.id in query.params.values() and parent.id in query.params.values()
    assert "agent_runs.parent_run_id IS NULL" in str(query)
    assert "ORDER BY chat_sessions.created_at, chat_sessions.spawn_index, chat_sessions.id" in str(query)
    db.get.return_value = None
    with pytest.raises(ApiError) as error:
        await routes.get_session_children(parent.id, user, db)
    assert error.value.status_code == 404


async def test_child_normal_run_get_and_sse_remain_readable() -> None:
    user = UserAccount(id=uuid4())
    child = _child(user.id)
    run = AgentRun(id=uuid4(), session_id=child.id, status="completed")
    db = AsyncMock(spec=AsyncSession)
    db.get.side_effect = [child, run, child, run]
    assert (await routes.get_run(child.id, run.id, user, db))["id"] == str(run.id)
    response = await routes.stream_run_events(child.id, run.id, user, db)
    events = [event async for event in response.body_iterator]
    assert len(events) == 1 and "event: run.completed" in events[0]


async def test_children_excluded_from_list_retrieval_and_indexing() -> None:
    db = AsyncMock(spec=AsyncSession)
    db.execute.return_value = MagicMock(all=lambda: [])
    await service.list_sessions(
        db, uuid4(), page_size=20, cursor=None, project_id=None,
        standalone=False, session_serializer=AsyncMock(),
    )
    assert "chat_sessions.parent_session_id IS NULL" in str(db.execute.await_args.args[0])
    assert any("parent_session_id IS NULL" in str(item) for item in _eligible_filters())
    assert any("parent_session_id IS NULL" in str(item) for item in _candidate_filters(
        owner_id=uuid4(), current_session_id=None, readable_by=TrustLevel.PRIVATE,
    ))
    child = _child(uuid4())
    db.scalar.return_value = child.parent_session_id
    message = Message(
        id=uuid4(), session_id=child.id, role="assistant", content="Do not index",
        generation_status="completed",
    )
    assert await EpisodeIndexService.enqueue(db, message) == 0
    db.add_all.assert_not_called()


@pytest.mark.parametrize("commit_fails", [False, True])
async def test_child_storage_is_purged_or_restored_with_transaction(tmp_path, monkeypatch, commit_fails) -> None:
    child_id = uuid4()
    storage = SessionStorage(tmp_path / "sessions")
    path = storage.provision(child_id)
    monkeypatch.setattr(cleanup, "SessionStorage", lambda: storage)
    monkeypatch.setattr(cleanup, "descendant_sessions", AsyncMock(return_value=[(child_id, None)]))
    db = AsyncMock(spec=AsyncSession)
    db.scalars.return_value = MagicMock(all=lambda: [child_id])
    if commit_fails:
        db.commit.side_effect = RuntimeError("commit failed")
    try:
        async with cleanup.deleting_child_sessions(db, uuid4()):
            assert not path.exists()
            await db.commit()
    except RuntimeError:
        assert commit_fails
    assert path.exists() is commit_fails
    assert list(storage.trash_dir.iterdir()) == []
    assert "DELETE FROM chat_sessions" in str(db.execute.await_args.args[0])


async def test_parent_delete_cancels_descendants_before_trashing_all_storage(tmp_path, monkeypatch) -> None:
    user = UserAccount(id=uuid4())
    parent = ChatSession(id=uuid4(), user_id=user.id, title="Parent")
    child = _child(user.id)
    child.parent_session_id = parent.id
    storage = SessionStorage(tmp_path / "sessions")
    paths = [storage.provision(item.id, user_id=user.id) for item in (parent, child)]
    db = AsyncMock(spec=AsyncSession)
    db.get.return_value = parent
    db.scalar.side_effect = [parent, 0, parent, 0]
    db.scalars.return_value = MagicMock(all=lambda: [])
    db.execute.return_value = MagicMock(all=lambda: [(child.id, None)])

    async def cancel_descendants(session_id):
        assert session_id == parent.id
        assert all(path.exists() for path in paths)
        assert db.rollback.await_count > 0

    cancel = AsyncMock(side_effect=cancel_descendants)
    monkeypatch.setattr(AgentRuntime, "cancel_descendants", cancel, raising=False)
    monkeypatch.setattr(routes, "SessionStorage", lambda: storage)
    await routes.delete_session(parent.id, user, db)
    cancel.assert_awaited_once_with(parent.id)
    assert all(not path.exists() for path in paths)
    assert storage._owners == {}
    db.commit.assert_awaited_once()


async def test_descendant_query_recurses_only_from_selected_spawn_runs() -> None:
    db = AsyncMock(spec=AsyncSession)
    db.execute.return_value = MagicMock(all=lambda: [])
    parent_id, run_id = uuid4(), uuid4()
    await cleanup.descendant_sessions(db, [parent_id], spawned_by_run_ids=[run_id])
    query = db.execute.await_args.args[0].compile(dialect=postgresql.dialect())
    assert "WITH RECURSIVE descendant_sessions" in str(query)
    assert "chat_sessions.spawned_by_run_id IN" in str(query)
    assert "chat_sessions.parent_session_id = descendant_sessions.id" in str(query)
    assert [parent_id] in query.params.values() and [run_id] in query.params.values()


@pytest.mark.parametrize("first_message", [False, True])
async def test_undo_cleans_child_storage_for_only_removed_turns(tmp_path, monkeypatch, first_message) -> None:
    parent = ChatSession(id=uuid4(), user_id=uuid4())
    run_id, child_id, earlier_child_id = uuid4(), uuid4(), uuid4()
    messages = [
        Message(id=uuid4(), session_id=parent.id, role="user", content="First"),
        Message(id=uuid4(), session_id=parent.id, role="assistant", content="Answer"),
        Message(id=uuid4(), session_id=parent.id, role="user", content="Second"),
    ]
    storage = SessionStorage(tmp_path / "sessions")
    paths = {item_id: storage.provision(item_id) for item_id in (parent.id, child_id, earlier_child_id)}
    monkeypatch.setattr(cleanup, "SessionStorage", lambda: storage)
    descendants = AsyncMock(return_value=[(child_id, None)] + ([(earlier_child_id, None)] if first_message else []))
    monkeypatch.setattr(cleanup, "descendant_sessions", descendants)
    monkeypatch.setattr(ChatHistoryService, "_lock_after_cancelling_active_run", AsyncMock(return_value=parent))
    monkeypatch.setattr(ChatHistoryService, "_messages", AsyncMock(return_value=messages))
    db = AsyncMock(spec=AsyncSession)
    db.scalars.return_value = MagicMock(all=lambda: [run_id])
    target = messages[0 if first_message else 2]
    result = await ChatHistoryService.undo_user_message(
        db, session_id=parent.id, user_id=parent.user_id, message_id=target.id,
    )
    assert result.content == target.content
    descendants.assert_awaited_once_with(
        db, [parent.id], spawned_by_run_ids=None if first_message else [run_id],
    )
    assert not paths[child_id].exists()
    assert paths[earlier_child_id].exists() is not first_message
    assert paths[parent.id].exists()
    assert list(storage.trash_dir.iterdir()) == []


async def test_cancellation_after_commit_does_not_restore_deleted_child_storage(tmp_path, monkeypatch) -> None:
    child_id = uuid4()
    storage = SessionStorage(tmp_path / "sessions")
    path = storage.provision(child_id)
    monkeypatch.setattr(cleanup, "SessionStorage", lambda: storage)
    monkeypatch.setattr(cleanup, "descendant_sessions", AsyncMock(return_value=[(child_id, None)]))
    db = AsyncMock(spec=AsyncSession)
    db.scalars.return_value = MagicMock(all=lambda: [])
    with pytest.raises(asyncio.CancelledError):
        async with cleanup.deleting_child_sessions(db, uuid4()):
            await db.commit()
            raise asyncio.CancelledError
    assert not path.exists()
    assert list(storage.trash_dir.iterdir()) == []
