import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from fastapi import Response
from pydantic import ValidationError
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.api.errors import ApiError
from mato_loop_backend.api.routes import execution_agents as execution_agents_route
from mato_loop_backend.api.routes.execution_agents import (
    ExecutionAgentRequest,
    PairExecutionAgentRequest,
    PairingTokenRequest,
    _agent_payload,
    _register_snapshot,
    create_execution_agent,
    create_execution_agent_pairing_token,
    execution_agent_pairing_policy,
    pair_execution_agent,
    unpair_execution_agent,
)
from mato_loop_backend.execution import HelloMessage
from mato_loop_backend.models import ExecutionAgent, ExecutionAgentRoot, UserAccount
from mato_loop_backend.security import hash_token


def _user() -> UserAccount:
    return UserAccount(id=uuid4(), username="test", password_hash="hash")


def _agent(**values) -> ExecutionAgent:
    now = datetime.now(UTC)
    defaults = {
        "id": uuid4(),
        "user_id": uuid4(),
        "name": "Desktop",
        "platform": None,
        "token_hash": None,
        "token_is_legacy": None,
        "registration_token_hash": None,
        "registration_expires_at": None,
        "protocol_version": None,
        "operations": [],
        "roots": [],
        "revision": 1,
        "last_connected_at": None,
        "revoked_at": None,
        "created_at": now,
        "updated_at": now,
    }
    return ExecutionAgent(**(defaults | values))


class _SessionContext:
    def __init__(self, db) -> None:
        self.db = db

    async def __aenter__(self):
        return self.db

    async def __aexit__(self, *_args) -> None:
        return None


def test_execution_agent_models_store_only_hashes_and_relational_roots() -> None:
    agent = _agent(
        platform="linux",
        token_hash=hash_token("durable access key"),
        token_is_legacy=False,
    )
    root = ExecutionAgentRoot(
        execution_agent_id=agent.id,
        id=uuid4(),
        label="Projects",
        revision="a" * 64,
    )

    assert agent.token_hash != "durable access key"
    assert ExecutionAgent.__table__.c.token_hash.nullable is True
    assert ExecutionAgent.__table__.c.registration_token_hash.unique is True
    assert {
        constraint.name for constraint in ExecutionAgent.__table__.constraints
    } >= {
        "ck_execution_agents_registration_complete",
        "ck_execution_agents_durable_token_classified",
        "ck_execution_agents_credential_state_exclusive",
    }
    assert root.execution_agent_id == agent.id
    assert ExecutionAgent.__table__.c.operations.type.compile(
        dialect=postgresql.dialect()
    ) == "JSONB"


async def test_create_returns_hash_only_pending_pairing_envelope() -> None:
    user = _user()
    db = AsyncMock(spec=AsyncSession)
    response_headers = Response()

    async def assign_server_values() -> None:
        agent = db.add.call_args.args[0]
        agent.id = uuid4()
        agent.revision = 1
        agent.operations = []
        agent.roots = []
        agent.created_at = datetime.now(UTC)
        agent.updated_at = datetime.now(UTC)

    db.flush.side_effect = assign_server_values

    result = await create_execution_agent(
        ExecutionAgentRequest(name=" Desktop "), user, db, response_headers
    )
    stored = db.add.call_args.args[0]

    assert result["agent"]["name"] == "Desktop"
    assert result["agent"]["pairingState"] == "pending"
    assert result["pairingToken"]
    assert "token" not in result
    assert result["websocketUrl"] == "/api/execution-agents/connect"
    assert stored.token_hash is None
    assert stored.token_is_legacy is None
    assert stored.registration_token_hash == hash_token(result["pairingToken"])
    assert result["pairingTokenExpiresAt"] == stored.registration_expires_at
    assert response_headers.headers["cache-control"] == "no-store"
    db.commit.assert_awaited_once()


async def test_pairing_policy_and_duration_validation() -> None:
    assert await execution_agent_pairing_policy(_user()) == {
        "defaultDurationSeconds": 1800,
        "maximumDurationSeconds": 86400,
    }
    assert ExecutionAgentRequest(name="Agent").pairing_token_duration_seconds == 1800
    assert PairingTokenRequest(pairingTokenDurationSeconds=86400)
    with pytest.raises(ValidationError):
        ExecutionAgentRequest(name="Agent", pairingTokenDurationSeconds=1799)
    with pytest.raises(ValidationError):
        PairingTokenRequest(pairingTokenDurationSeconds=86401)
    with pytest.raises(ValidationError):
        PairExecutionAgentRequest(agentId=uuid4(), accessKeyHash="A" * 64)
    with pytest.raises(ValidationError):
        PairExecutionAgentRequest(
            agentId=uuid4(), accessKeyHash="a" * 64, unexpected=True
        )


async def test_pair_once_and_idempotent_retry() -> None:
    pairing_token = "pair-once"
    access_key_hash = hash_token("locally generated access key")
    agent = _agent(
        registration_token_hash=hash_token(pairing_token),
        registration_expires_at=datetime.now(UTC) + timedelta(minutes=30),
    )
    db = AsyncMock(spec=AsyncSession)
    db.scalar.return_value = agent
    payload = PairExecutionAgentRequest(agentId=agent.id, accessKeyHash=access_key_hash)

    response = await pair_execution_agent(payload, db, f"Bearer {pairing_token}")

    assert response.status_code == 204
    assert agent.token_hash == access_key_hash
    assert agent.token_is_legacy is False
    assert agent.registration_token_hash is None
    assert agent.registration_expires_at is None
    assert agent.revision == 2

    response = await pair_execution_agent(payload, db, "Bearer consumed-token")

    assert response.status_code == 204
    assert agent.revision == 2
    assert db.commit.await_count == 2


async def test_concurrent_pairing_allows_only_one_access_key() -> None:
    pairing_token = "concurrent-token"
    agent = _agent(
        registration_token_hash=hash_token(pairing_token),
        registration_expires_at=datetime.now(UTC) + timedelta(minutes=30),
    )
    lock = asyncio.Lock()

    class LockedDb:
        async def scalar(self, _statement):
            await lock.acquire()
            return agent

        async def commit(self):
            lock.release()

    results = await asyncio.gather(
        pair_execution_agent(
            PairExecutionAgentRequest(agentId=agent.id, accessKeyHash="a" * 64),
            LockedDb(),
            f"Bearer {pairing_token}",
        ),
        pair_execution_agent(
            PairExecutionAgentRequest(agentId=agent.id, accessKeyHash="b" * 64),
            LockedDb(),
            f"Bearer {pairing_token}",
        ),
        return_exceptions=True,
    )

    assert sum(isinstance(result, Response) for result in results) == 1
    errors = [result for result in results if isinstance(result, ApiError)]
    assert len(errors) == 1
    assert errors[0].code == "executionAgent.alreadyPaired"
    assert agent.token_hash in {"a" * 64, "b" * 64}
    assert agent.revision == 2


async def test_pair_rejects_expired_invalid_and_different_paired_keys() -> None:
    token = "expired-token"
    agent = _agent(
        registration_token_hash=hash_token(token),
        registration_expires_at=datetime.now(UTC) - timedelta(seconds=1),
    )
    db = AsyncMock(spec=AsyncSession)
    db.scalar.return_value = agent
    payload = PairExecutionAgentRequest(agentId=agent.id, accessKeyHash="a" * 64)

    with pytest.raises(ApiError) as exc_info:
        await pair_execution_agent(payload, db, f"Bearer {token}")
    assert (exc_info.value.status_code, exc_info.value.code) == (
        410,
        "executionAgent.pairingTokenExpired",
    )

    with pytest.raises(ApiError) as exc_info:
        await pair_execution_agent(payload, db, "Bearer wrong")
    assert (exc_info.value.status_code, exc_info.value.code) == (
        401,
        "executionAgent.pairingTokenInvalid",
    )

    agent.token_hash = "b" * 64
    agent.token_is_legacy = False
    with pytest.raises(ApiError) as exc_info:
        await pair_execution_agent(payload, db, "Bearer wrong")
    assert (exc_info.value.status_code, exc_info.value.code) == (
        409,
        "executionAgent.alreadyPaired",
    )


async def test_normal_paired_key_cannot_rotate_itself() -> None:
    durable_key = "normal-durable-key"
    agent = _agent(
        token_hash=hash_token(durable_key), token_is_legacy=False, revision=8
    )
    db = AsyncMock(spec=AsyncSession)
    db.scalar.return_value = agent

    with pytest.raises(ApiError) as exc_info:
        await pair_execution_agent(
            PairExecutionAgentRequest(agentId=agent.id, accessKeyHash="c" * 64),
            db,
            f"Bearer {durable_key}",
        )

    assert exc_info.value.code == "executionAgent.alreadyPaired"
    assert agent.token_hash == hash_token(durable_key)
    assert agent.revision == 8
    db.commit.assert_not_awaited()


async def test_pair_migrates_legacy_key_then_revokes_live_capabilities(
    monkeypatch,
) -> None:
    legacy_token = "legacy-durable-key"
    agent = _agent(
        token_hash=hash_token(legacy_token), token_is_legacy=True, revision=8
    )
    db = AsyncMock(spec=AsyncSession)
    db.scalar.return_value = agent
    calls = []
    db.commit.side_effect = lambda: calls.append("commit")
    close = AsyncMock(side_effect=lambda _agent_id: calls.append("close"))
    revoke_owner = Mock(side_effect=lambda _agent_id: calls.append("tickets"))
    monkeypatch.setattr(execution_agents_route.execution_agent_connections, "close", close)
    monkeypatch.setattr(execution_agents_route.transfer_tickets, "revoke_owner", revoke_owner)

    await pair_execution_agent(
        PairExecutionAgentRequest(agentId=agent.id, accessKeyHash="c" * 64),
        db,
        f"Bearer {legacy_token}",
    )

    assert agent.token_hash == "c" * 64
    assert agent.token_is_legacy is False
    assert agent.revision == 9
    db.commit.assert_awaited_once()
    assert calls == ["commit", "close", "tickets"]


async def test_reissue_replaces_pending_token_and_rejects_paired_agent() -> None:
    user = _user()
    old_token_hash = hash_token("old")
    agent = _agent(
        user_id=user.id,
        registration_token_hash=old_token_hash,
        registration_expires_at=datetime.now(UTC) - timedelta(seconds=1),
    )
    db = AsyncMock(spec=AsyncSession)
    db.scalar.return_value = agent
    response_headers = Response()

    result = await create_execution_agent_pairing_token(
        agent.id,
        user,
        db,
        response_headers,
        PairingTokenRequest(pairingTokenDurationSeconds=86400),
    )

    assert agent.registration_token_hash != old_token_hash
    assert agent.registration_token_hash == hash_token(result["pairingToken"])
    assert result["agent"]["pairingState"] == "pending"
    assert response_headers.headers["cache-control"] == "no-store"
    assert agent.revision == 2

    agent.token_hash = "d" * 64
    agent.token_is_legacy = False
    with pytest.raises(ApiError) as exc_info:
        await create_execution_agent_pairing_token(
            agent.id, user, db, Response(), None
        )
    assert exc_info.value.code == "executionAgent.alreadyPaired"


async def test_unpair_is_idempotent_and_revokes_live_capabilities(monkeypatch) -> None:
    user = _user()
    agent = _agent(
        user_id=user.id,
        token_hash="e" * 64,
        token_is_legacy=False,
        revision=4,
    )
    db = AsyncMock(spec=AsyncSession)
    db.scalar.return_value = agent
    calls = []
    db.commit.side_effect = lambda: calls.append("commit")
    close = AsyncMock(side_effect=lambda _agent_id: calls.append("close"))
    revoke_owner = Mock(side_effect=lambda _agent_id: calls.append("tickets"))
    monkeypatch.setattr(execution_agents_route.execution_agent_connections, "close", close)
    monkeypatch.setattr(execution_agents_route.transfer_tickets, "revoke_owner", revoke_owner)

    result = await unpair_execution_agent(agent.id, user, db)

    assert result["pairingState"] == "unpaired"
    assert result["connected"] is False
    assert agent.token_hash is None
    assert agent.token_is_legacy is None
    assert agent.registration_token_hash is None
    assert agent.revision == 5
    assert calls == ["commit", "close", "tickets"]

    await unpair_execution_agent(agent.id, user, db)
    assert agent.revision == 5


async def test_unpair_cleanup_completes_when_request_is_cancelled(monkeypatch) -> None:
    user = _user()
    agent = _agent(user_id=user.id, token_hash="e" * 64, token_is_legacy=False)
    db = AsyncMock(spec=AsyncSession)
    db.scalar.return_value = agent
    commit_started = asyncio.Event()
    allow_commit = asyncio.Event()
    calls = []

    async def commit() -> None:
        calls.append("commit-start")
        commit_started.set()
        await allow_commit.wait()
        calls.append("commit-end")

    db.commit.side_effect = commit
    monkeypatch.setattr(
        execution_agents_route.execution_agent_connections,
        "close",
        AsyncMock(side_effect=lambda _agent_id: calls.append("close")),
    )
    monkeypatch.setattr(
        execution_agents_route.transfer_tickets,
        "revoke_owner",
        lambda _agent_id: calls.append("tickets"),
    )

    task = asyncio.create_task(unpair_execution_agent(agent.id, user, db))
    await commit_started.wait()
    task.cancel()
    await asyncio.sleep(0)
    assert task.done() is False
    allow_commit.set()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert calls == ["commit-start", "commit-end", "close", "tickets"]


def test_pairing_migration_preserves_rows_on_downgrade() -> None:
    migrations = list(
        (Path(__file__).parents[1] / "alembic" / "versions").glob(
            "*_add_execution_agent_pairing.py"
        )
    )
    assert len(migrations) == 1
    source = migrations[0].read_text(encoding="utf-8")
    assert "DELETE FROM execution_agents" not in source
    assert "SET token_is_legacy = true" in source
    assert "'00000000000000000000000000000000'" in source
    assert "replace(id::text, '-', '')" in source


def test_payload_maps_expired_pending_to_unpaired_and_connects_only_paired(
    monkeypatch,
) -> None:
    agent = _agent(
        registration_token_hash=hash_token("expired"),
        registration_expires_at=datetime.now(UTC) - timedelta(seconds=1),
    )
    monkeypatch.setattr(
        execution_agents_route.execution_agent_connections,
        "is_connected",
        lambda _agent_id: True,
    )

    payload = _agent_payload(agent)
    assert payload["pairingState"] == "unpaired"
    assert payload["connected"] is False
    assert "pairingTokenExpiresAt" not in payload

    agent.token_hash = "f" * 64
    agent.token_is_legacy = False
    assert _agent_payload(agent)["connected"] is True


async def test_ws_snapshot_rejects_stale_authenticated_hash(monkeypatch) -> None:
    agent_id = uuid4()
    db = AsyncMock(spec=AsyncSession)
    db.scalar.return_value = None
    register = AsyncMock()
    monkeypatch.setattr(
        execution_agents_route, "AsyncSessionFactory", lambda: _SessionContext(db)
    )
    monkeypatch.setattr(execution_agents_route.execution_agent_connections, "register", register)
    hello = HelloMessage.model_validate(
        {
            "type": "hello",
            "protocolVersion": 1,
            "agentId": agent_id,
            "name": "Desktop",
            "platform": "linux",
            "operations": [],
            "roots": [],
        }
    )

    accepted = await _register_snapshot(
        agent_id, hash_token("stale access key"), hello, SimpleNamespace()
    )

    assert accepted is False
    statement = db.scalar.await_args.args[0]
    assert "execution_agents.token_hash" in str(statement)
    register.assert_not_awaited()
