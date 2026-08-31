from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from sqlalchemy import Text
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.agent.runtime import approval_payload
from mato_loop_backend.api.errors import ApiError
from mato_loop_backend.api.routes.approvals import (
    all_permissions,
    pending_approvals,
    revoke_permissions,
)
from mato_loop_backend.models import ApprovalRequest, ChatSession, PermissionGrant, UserAccount


def _approval(user_id, session_id, created_at: datetime, *, request_type="agent_action") -> ApprovalRequest:
    return ApprovalRequest(
        id=uuid4(),
        request_type=request_type,
        user_id=user_id,
        session_id=session_id,
        run_id=uuid4(),
        action_kind="session_read",
        risk_level="medium",
        title="Session lesen?",
        summary="Andere Session lesen",
        capability="session.read",
        resource_type="session",
        resource_scope=str(uuid4()),
        payload={"resourceDisplay": "Projekt"},
        available_decisions=["allow_once", "deny"],
        status="pending",
        created_at=created_at,
    )


async def test_pending_approval_payload_includes_request_and_source_session() -> None:
    user = UserAccount(id=uuid4(), username="test", password_hash="hash")
    session = ChatSession(id=uuid4(), user_id=user.id, title="Projekt Aurora")
    approval = _approval(user.id, session.id, datetime(2026, 8, 11, 9, 0, tzinfo=UTC))
    execution_result = MagicMock()
    execution_result.all.return_value = [(approval, session)]
    db = AsyncMock(spec=AsyncSession)
    db.execute.return_value = execution_result

    result = await pending_approvals(user, db)

    assert result == [
        {
            **approval_payload(approval),
            "sourceSession": {"id": str(session.id), "title": session.title},
        }
    ]


async def test_pending_approvals_query_filters_owner_and_orders_oldest_first() -> None:
    user = UserAccount(id=uuid4(), username="test", password_hash="hash")
    session = ChatSession(id=uuid4(), user_id=user.id, title="Eigene Session")
    oldest = _approval(user.id, session.id, datetime(2026, 8, 11, 8, 0, tzinfo=UTC))
    newest = _approval(user.id, session.id, datetime(2026, 8, 11, 10, 0, tzinfo=UTC))
    execution_result = MagicMock()
    execution_result.all.return_value = [(oldest, session), (newest, session)]
    db = AsyncMock(spec=AsyncSession)
    db.execute.return_value = execution_result

    result = await pending_approvals(user, db)

    assert [item["id"] for item in result] == [str(oldest.id), str(newest.id)]
    statement = db.execute.await_args.args[0]
    sql = str(statement)
    assert "LEFT OUTER JOIN chat_sessions ON chat_sessions.id = approval_requests.session_id" in sql
    assert "approval_requests.user_id =" in sql
    assert "approval_requests.action_kind !=" in sql
    assert "ORDER BY approval_requests.created_at" in sql
    assert {"pending", user.id}.issubset(set(statement.compile().params.values()))


async def test_pending_memory_approval_has_no_required_session() -> None:
    user = UserAccount(id=uuid4(), username="test", password_hash="hash")
    approval = _approval(
        user.id,
        None,
        datetime(2026, 8, 11, 9, 0, tzinfo=UTC),
        request_type="memory_change",
    )
    approval.run_id = None
    execution_result = MagicMock()
    execution_result.all.return_value = [(approval, None)]
    db = AsyncMock(spec=AsyncSession)
    db.execute.return_value = execution_result

    result = await pending_approvals(user, db)

    assert result[0]["requestType"] == "memory_change"
    assert result[0]["sessionId"] is None
    assert result[0]["runId"] is None
    assert result[0]["sourceSession"] is None


async def test_all_permissions_include_owned_session_context() -> None:
    user = UserAccount(id=uuid4(), username="test", password_hash="hash")
    first_session = ChatSession(id=uuid4(), user_id=user.id, title="Projekt Aurora")
    second_session = ChatSession(id=uuid4(), user_id=user.id, title="Recherche")
    created_at = datetime(2026, 8, 11, 9, 0, tzinfo=UTC)
    source = _approval(user.id, first_session.id, created_at)
    source.payload = {
        "resourceDisplay": "/home/test/projekt",
        "shellClassification": "read",
        "shellPrefixes": [["git", "status"]],
    }
    shell_grant = PermissionGrant(
        id=uuid4(),
        session_id=first_session.id,
        capability="shell.read",
        resource_type="shell_command",
        resource_scope="scope-1",
        source_approval_id=source.id,
        created_at=created_at,
    )
    session_grant = PermissionGrant(
        id=uuid4(),
        session_id=second_session.id,
        capability="session.search",
        resource_type="session_collection",
        resource_scope="*",
        created_at=created_at,
    )
    execution_result = MagicMock()
    execution_result.all.return_value = [
        (shell_grant, first_session, source),
        (session_grant, second_session, None),
    ]
    db = AsyncMock(spec=AsyncSession)
    db.execute.return_value = execution_result

    result = await all_permissions(user, db)

    assert [item["sessionId"] for item in result] == [
        str(first_session.id),
        str(second_session.id),
    ]
    assert result[0]["sourceSession"] == {
        "id": str(first_session.id),
        "title": "Projekt Aurora",
    }
    assert result[0]["resourceDisplay"] == "/home/test/projekt"
    assert result[0]["shellPrefixes"] == [["git", "status"]]
    assert result[1]["sourceSession"]["title"] == "Recherche"
    statement = db.execute.await_args.args[0]
    sql = str(statement)
    assert "JOIN chat_sessions ON chat_sessions.id = permission_grants.session_id" in sql
    assert "LEFT OUTER JOIN approval_requests" in sql
    assert "chat_sessions.user_id =" in sql
    assert "ORDER BY permission_grants.created_at" in sql
    assert user.id in statement.compile().params.values()


async def test_revoke_permissions_deletes_all_grants_for_owned_session() -> None:
    user = UserAccount(id=uuid4(), username="test", password_hash="hash")
    session = ChatSession(id=uuid4(), user_id=user.id, title="Projekt Aurora")
    db = AsyncMock(spec=AsyncSession)
    db.get.return_value = session

    await revoke_permissions(session.id, user, db)

    statement = db.execute.await_args.args[0]
    assert "DELETE FROM permission_grants" in str(statement)
    assert "permission_grants.session_id =" in str(statement)
    assert session.id in statement.compile().params.values()
    db.commit.assert_awaited_once()


async def test_revoke_permissions_rejects_foreign_session() -> None:
    user = UserAccount(id=uuid4(), username="test", password_hash="hash")
    session = ChatSession(id=uuid4(), user_id=uuid4(), title="Fremde Session")
    db = AsyncMock(spec=AsyncSession)
    db.get.return_value = session

    with pytest.raises(ApiError) as exc_info:
        await revoke_permissions(session.id, user, db)

    assert exc_info.value.code == "approval.notFound"
    db.execute.assert_not_awaited()
    db.commit.assert_not_awaited()


def test_approval_envelope_metadata_supports_owned_optional_sources() -> None:
    table = ApprovalRequest.__table__

    assert table.c.user_id.nullable is False
    assert table.c.user_id.index is True
    assert table.c.request_type.server_default is not None
    assert table.c.session_id.nullable is True
    assert next(iter(table.c.session_id.foreign_keys)).ondelete == "SET NULL"
    assert table.c.run_id.nullable is True
    assert next(iter(table.c.run_id.foreign_keys)).ondelete == "SET NULL"
    assert "ix_approval_requests_user_pending" in {index.name for index in table.indexes}


def test_resource_scope_columns_are_unbounded() -> None:
    assert isinstance(ApprovalRequest.__table__.c.resource_scope.type, Text)
    assert isinstance(PermissionGrant.__table__.c.resource_scope.type, Text)
