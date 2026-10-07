import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from mato_loop_backend.config import get_runtime_config
from mato_loop_backend.execution import terminals
from mato_loop_backend.execution.terminals import (
    REPLAY_BYTES,
    ShellOutput,
    ShellOutputReplay,
    agent_snapshot,
)
from mato_loop_backend.models import ChatSession, Message, UserAccount


def test_agent_replay_combines_persisted_results_and_live_output_without_duplicates():
    replay = ShellOutputReplay()
    started = datetime.now(UTC)
    replay.append(ShellOutput("completed", started), b"old live copy")
    replay.append(ShellOutput("running", started + timedelta(seconds=1)), b"new live output")
    assert agent_snapshot([ShellOutput("completed", started, b"stored result\n")], replay) == (
        b"stored result\nnew live output", False,
    )


def test_agent_replay_is_bounded_across_calls():
    replay = ShellOutputReplay()
    started = datetime.now(UTC)
    replay.append(ShellOutput("first", started), b"x" * REPLAY_BYTES)
    replay.append(ShellOutput("second", started + timedelta(seconds=1)), b"end")
    assert agent_snapshot([], replay) == (b"x" * (REPLAY_BYTES - 3) + b"end", True)


def test_old_cached_calls_do_not_follow_the_latest_fifty_results():
    replay = ShellOutputReplay()
    started = datetime.now(UTC)
    for index in range(51):
        replay.append(ShellOutput(str(index), started + timedelta(seconds=index)), f"command-{index}\n".encode())
    history = list(replay.calls.values())[1:]
    assert agent_snapshot(history, replay) == (b"".join(item.data for item in history), True)


async def test_persisted_shell_history_is_owner_session_and_computer_bound(request, monkeypatch):
    if not request.config.getoption("--memory-db"):
        pytest.skip("requires the migrated development PostgreSQL database")
    engine = create_async_engine(get_runtime_config().database_url, poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            await connection.begin()
            factory = async_sessionmaker(connection, expire_on_commit=False, join_transaction_mode="create_savepoint")
            monkeypatch.setattr(terminals, "AsyncSessionFactory", factory)
            async with factory() as db:
                owner = UserAccount(id=uuid4(), username=f"terminal-{uuid4()}", password_hash="hash")
                other = UserAccount(id=uuid4(), username=f"terminal-{uuid4()}", password_hash="hash")
                db.add_all([owner, other])
                await db.flush()
                session = ChatSession(id=uuid4(), user_id=owner.id, title="Terminal")
                sibling = ChatSession(id=uuid4(), user_id=owner.id, title="Sibling")
                foreign = ChatSession(id=uuid4(), user_id=other.id, title="Foreign")
                db.add_all([session, sibling, foreign])
                await db.flush()
                agent_id = uuid4()
                messages = []
                for index, (target, computer) in enumerate(((session, agent_id), (session, uuid4()), (sibling, agent_id), (foreign, agent_id))):
                    message = Message(
                        session_id=target.id, role="tool", tool_call_id=f"call-{index}",
                        content=json.dumps({"executedArguments": {"command": "pwd"}, "output": {"stdout": f"result-{index}\n"}}),
                        provider_data={"_matoloop": {"toolName": "shell_run", "executionAgentId": str(computer)}},
                    )
                    db.add(message)
                    messages.append(message)
                await db.commit()
            assert (
                [(item.id, item.data) for item in await terminals.shell_history(owner.id, session.id, agent_id)],
                await terminals.shell_history(other.id, session.id, agent_id),
            ) == ([(str(messages[0].id), b"\r\n$ pwd\r\nresult-0\r\n")], [])
            await connection.rollback()
    finally:
        await engine.dispose()
