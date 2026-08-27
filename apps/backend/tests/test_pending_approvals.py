from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from sqlalchemy import Text
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.agent.runtime import approval_payload
from mato_loop_backend.api.routes.approvals import pending_approvals
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
