import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
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
    DefaultExecutionAgentRequest,
    ExecutionAgentRequest,
    ExecutionAgentUpdateRequest,
    MaintenanceCompletion,
    MaintenanceRequest,
    PairExecutionAgentRequest,
    PairingTokenRequest,
    _agent_payload,
    _register_snapshot,
    complete_execution_agent_maintenance,
    create_execution_agent,
    create_execution_agent_pairing_token,
    execution_agent_pairing_policy,
    pair_execution_agent,
    set_default_execution_agent,
    start_execution_agent_maintenance,
    unpair_execution_agent,
    update_execution_agent,
)
from mato_loop_backend.execution import ExecutionAgentError, HelloMessage
from mato_loop_backend.models import ExecutionAgent, UserAccount
from mato_loop_backend.security import hash_token
from mato_loop_backend.settings import SettingsStore, UserPreferences


def _user() -> UserAccount:
    return UserAccount(id=uuid4(), username="test", password_hash="hash")


def _agent(**values) -> ExecutionAgent:
    now = datetime.now(UTC)
    defaults = {
        "id": uuid4(),
        "user_id": uuid4(),
        "alias": "Desktop",
        "computer_name": None,
        "agent_version": None,
        "platform": None,
        "token_hash": None,
        "token_is_legacy": None,
        "registration_token_hash": None,
        "registration_expires_at": None,
        "operations": [],
        "home_directory": None,
        "home_filesystem_policy": "auto",
        "external_filesystem_policy": "ask",
        "home_shell_enabled": True,
        "external_shell_enabled": True,
        "revision": 1,
        "last_connected_at": None,
        "revoked_at": None,
        "created_at": now,
        "updated_at": now,
    }
    return ExecutionAgent(**(defaults | values))


def test_payload_exposes_computer_metadata_and_policies() -> None:
    agent = _agent(home_directory="/home/user", computer_name="host", agent_version="1")

    payload = _agent_payload(agent)

    assert payload["homeDirectory"] == "/home/user"
    assert payload["computerName"] == "host"
    assert payload["agentVersion"] == "1"
    assert payload["homeFilesystemPolicy"] == "auto"
    assert payload["externalFilesystemPolicy"] == "ask"
    assert payload["homeShellEnabled"] is True
    assert payload["externalShellEnabled"] is True


class _SessionContext:
    def __init__(self, db) -> None:
        self.db = db

    async def __aenter__(self):
        return self.db

    async def __aexit__(self, *_args) -> None:
        return None


def test_execution_agent_model_has_policy_constraints() -> None:
    assert ExecutionAgent.__table__.c.token_hash.nullable is True
    assert ExecutionAgent.__table__.c.registration_token_hash.unique is True
    assert {
        constraint.name for constraint in ExecutionAgent.__table__.constraints
    } >= {
        "ck_execution_agents_registration_complete",
        "ck_execution_agents_durable_token_classified",
        "ck_execution_agents_credential_state_exclusive",
    }
    assert "alias" in ExecutionAgent.__table__.c
    assert {
        "ck_execution_agents_home_filesystem_policy_valid",
        "ck_execution_agents_external_filesystem_policy_valid",
    } <= {constraint.name for constraint in ExecutionAgent.__table__.constraints}
    assert ExecutionAgent.__table__.c.home_shell_enabled.default.arg is True
    assert ExecutionAgent.__table__.c.external_shell_enabled.default.arg is True
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
        agent.created_at = datetime.now(UTC)
        agent.updated_at = datetime.now(UTC)

    db.flush.side_effect = assign_server_values

    result = await create_execution_agent(
        ExecutionAgentRequest(alias=" Desktop "), user, db, response_headers
    )
    stored = db.add.call_args.args[0]

    assert result["agent"]["alias"] == "Desktop"
    assert result["agent"]["pairingState"] == "pending"
    assert result["pairingToken"]
    assert "token" not in result
    assert result["websocketUrl"] == "/api/execution-agents/connect"
    assert stored.token_hash is None
    assert stored.token_is_legacy is None
    assert stored.home_shell_enabled is True
    assert stored.external_shell_enabled is True
    assert stored.registration_token_hash == hash_token(result["pairingToken"])
    assert result["pairingTokenExpiresAt"] == stored.registration_expires_at
    assert response_headers.headers["cache-control"] == "no-store"
    db.commit.assert_awaited_once()


async def test_update_changes_alias_policies_and_shell_switches() -> None:
    user = _user()
    agent = _agent(user_id=user.id, token_hash="a" * 64, token_is_legacy=False)
    db = AsyncMock(spec=AsyncSession)
    db.scalar.return_value = agent

    result = await update_execution_agent(
        agent.id,
        ExecutionAgentUpdateRequest(
            alias="Laptop",
            externalFilesystemPolicy="deny",
            externalShellEnabled=False,
        ),
        user,
        db,
    )

    assert result["alias"] == "Laptop"
    assert result["externalFilesystemPolicy"] == "deny"
    assert result["externalShellEnabled"] is False
    db.commit.assert_awaited_once()


async def test_pairing_policy_and_duration_validation() -> None:
    assert await execution_agent_pairing_policy(_user()) == {
        "defaultDurationSeconds": 1800,
        "maximumDurationSeconds": 86400,
    }
    assert ExecutionAgentRequest(alias="Agent").pairing_token_duration_seconds == 1800
    assert ExecutionAgentRequest(alias="Agent").home_shell_enabled is True
    assert ExecutionAgentRequest(alias="Agent").external_shell_enabled is True
    assert PairingTokenRequest(pairingTokenDurationSeconds=86400)
    with pytest.raises(ValidationError):
        ExecutionAgentRequest(alias="Agent", pairingTokenDurationSeconds=1799)
    with pytest.raises(ValidationError):
        PairingTokenRequest(pairingTokenDurationSeconds=86401)
    with pytest.raises(ValidationError):
        PairExecutionAgentRequest(agentId=uuid4(), accessKeyHash="A" * 64)
    with pytest.raises(ValidationError):
        PairExecutionAgentRequest(
            agentId=uuid4(), accessKeyHash="a" * 64, unexpected=True
        )
    with pytest.raises(ValidationError):
        ExecutionAgentRequest(alias="Agent", homeShellEnabled="deny")


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
    preferences = UserPreferences(default_execution_agent_id=agent.id)
    monkeypatch.setattr(SettingsStore, "load", AsyncMock(return_value=preferences))
    save_preferences = AsyncMock()
    monkeypatch.setattr(SettingsStore, "save", save_preferences)
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
    assert preferences.default_execution_agent_id is None
    save_preferences.assert_awaited_once_with(db, preferences, user_id=user.id)
    assert calls == ["commit", "close", "tickets"]

    await unpair_execution_agent(agent.id, user, db)
    assert agent.revision == 5


async def test_default_execution_agent_can_be_set_and_cleared(monkeypatch) -> None:
    user = _user()
    agent = _agent(user_id=user.id, token_hash="a" * 64, token_is_legacy=False)
    preferences = UserPreferences()
    db = AsyncMock(spec=AsyncSession)
    db.scalar.return_value = agent
    monkeypatch.setattr(SettingsStore, "load", AsyncMock(return_value=preferences))
    monkeypatch.setattr(SettingsStore, "save", AsyncMock())

    result = await set_default_execution_agent(
        DefaultExecutionAgentRequest(defaultExecutionAgentId=agent.id), user, db
    )
    assert result == {"defaultExecutionAgentId": str(agent.id)}

    result = await set_default_execution_agent(
        DefaultExecutionAgentRequest(defaultExecutionAgentId=None), user, db
    )
    assert result == {"defaultExecutionAgentId": None}


async def test_unpair_cleanup_completes_when_request_is_cancelled(monkeypatch) -> None:
    user = _user()
    agent = _agent(user_id=user.id, token_hash="e" * 64, token_is_legacy=False)
    db = AsyncMock(spec=AsyncSession)
    db.scalar.return_value = agent
    monkeypatch.setattr(
        SettingsStore, "load", AsyncMock(return_value=UserPreferences())
    )
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


def test_shell_switch_migration_preserves_explicit_denies() -> None:
    migrations = list(
        (Path(__file__).parents[1] / "alembic" / "versions").glob(
            "*_replace_shell_policies_with_hard_.py"
        )
    )
    assert len(migrations) == 1
    source = migrations[0].read_text(encoding="utf-8")
    assert "home_shell_enabled = home_shell_policy <> 'deny'" in source
    assert "external_shell_enabled = external_shell_policy <> 'deny'" in source


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
    monkeypatch.setattr(
        execution_agents_route, "AsyncSessionFactory", lambda: _SessionContext(db)
    )
    hello = HelloMessage.model_validate(
        {
            "type": "hello",
            "protocolVersion": 1,
            "agentId": agent_id,
            "agentVersion": "1.0.0",
            "computerName": "desktop",
            "platform": "linux",
            "operations": [],
            "homeDirectory": "/home/user",
        }
    )

    accepted = await _register_snapshot(agent_id, hash_token("stale access key"), hello)

    assert accepted is False
    statement = db.scalar.await_args.args[0]
    assert "execution_agents.token_hash" in str(statement)


async def test_maintenance_password_is_forwarded_once_and_never_persisted(monkeypatch) -> None:
    user = _user()
    agent = _agent(user_id=user.id, operations=["maintenance.start"])
    db = AsyncMock(spec=AsyncSession)
    db.scalar.return_value = agent
    db.get.return_value = agent
    monkeypatch.setattr(execution_agents_route.execution_agent_connections, "is_connected", lambda _: True)
    request = AsyncMock(return_value=Mock(ok=True))
    monkeypatch.setattr(execution_agents_route.execution_agent_connections, "request", request)

    result = await start_execution_agent_maintenance(
        agent.id, MaintenanceRequest(action="update", sudoPassword="private"), user, db, Response()
    )

    assert result["maintenance"]["status"] == "running"
    assert "private" not in str(agent.maintenance)
    assert request.await_args.args[2]["sudoPassword"] == "private"
    assert db.commit.await_count == 1


async def test_maintenance_auth_prompt_and_callback_owner_check(monkeypatch) -> None:
    user = _user()
    agent = _agent(user_id=user.id, operations=["maintenance.start"], token_hash=hash_token("agent-key"))
    db = AsyncMock(spec=AsyncSession)
    db.scalar.return_value = agent
    db.get.return_value = agent
    monkeypatch.setattr(execution_agents_route.execution_agent_connections, "is_connected", lambda _: True)
    monkeypatch.setattr(execution_agents_route.execution_agent_connections, "request", AsyncMock(return_value=Mock(ok=False, error=Mock(code="process.sudoAuthRequired"))))

    with pytest.raises(ApiError) as error:
        await start_execution_agent_maintenance(
            agent.id, MaintenanceRequest(action="uninstall"), user, db, Response()
        )
    assert error.value.code == "process.sudoAuthRequired"
    assert agent.maintenance is None

    agent.maintenance = {"jobId": str(uuid4()), "action": "uninstall", "status": "running", "startedAt": datetime.now(UTC).isoformat()}
    with pytest.raises(ApiError) as error:
        await complete_execution_agent_maintenance(
            agent.id, uuid4(), MaintenanceCompletion(status="succeeded"), db, "Bearer agent-key"
        )
    assert error.value.code == "maintenance.jobUnavailable"
    with pytest.raises(ApiError) as error:
        await complete_execution_agent_maintenance(
            agent.id, uuid4(), MaintenanceCompletion(status="succeeded"), db, "Bearer wrong"
        )
    assert error.value.status_code == 401

    await complete_execution_agent_maintenance(
        agent.id, execution_agents_route.UUID(agent.maintenance["jobId"]),
        MaintenanceCompletion(status="succeeded"), db, "Bearer agent-key"
    )
    assert agent.maintenance["status"] == "succeeded"


async def test_disconnected_maintenance_is_unknown_not_success(monkeypatch) -> None:
    user = _user()
    agent = _agent(user_id=user.id, operations=["maintenance.start"])
    db = AsyncMock(spec=AsyncSession)
    db.scalar.return_value = agent
    db.get.return_value = agent
    monkeypatch.setattr(execution_agents_route.execution_agent_connections, "is_connected", lambda _: True)
    monkeypatch.setattr(
        execution_agents_route.execution_agent_connections, "request",
        AsyncMock(side_effect=ExecutionAgentError("executionAgent.outcomeUnknown")),
    )

    with pytest.raises(ApiError) as error:
        await start_execution_agent_maintenance(
            agent.id, MaintenanceRequest(action="update"), user, db, Response()
        )

    assert error.value.code == "executionAgent.outcomeUnknown"
    assert agent.maintenance["status"] == "unknown"
    with pytest.raises(ApiError) as second:
        await start_execution_agent_maintenance(
            agent.id, MaintenanceRequest(action="update"), user, db, Response()
        )
    assert second.value.code == "maintenance.busy"
