from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.agent.runtime import approval_payload
from mato_loop_backend.api.routes.approvals import pending_approvals
from mato_loop_backend.models import ApprovalRequest, ChatSession, UserAccount


def _approval(session_id, created_at: datetime) -> ApprovalRequest:
    return ApprovalRequest(
        id=uuid4(),
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
    approval = _approval(session.id, datetime(2026, 8, 11, 9, 0, tzinfo=UTC))
    execution_result = MagicMock()
    execution_result.all.return_value = [(approval, session)]
    db = AsyncMock(spec=AsyncSession)
    db.execute.return_value = execution_result

    result = await pending_approvals(user, db)

    assert result == [
        {
            **approval_payload(approval),
            "requestType": "agent_action",
            "sourceSession": {"id": str(session.id), "title": session.title},
        }
    ]


async def test_pending_approvals_query_filters_owner_and_orders_oldest_first() -> None:
    user = UserAccount(id=uuid4(), username="test", password_hash="hash")
    session = ChatSession(id=uuid4(), user_id=user.id, title="Eigene Session")
    oldest = _approval(session.id, datetime(2026, 8, 11, 8, 0, tzinfo=UTC))
    newest = _approval(session.id, datetime(2026, 8, 11, 10, 0, tzinfo=UTC))
    execution_result = MagicMock()
    execution_result.all.return_value = [(oldest, session), (newest, session)]
    db = AsyncMock(spec=AsyncSession)
    db.execute.return_value = execution_result

    result = await pending_approvals(user, db)

    assert [item["id"] for item in result] == [str(oldest.id), str(newest.id)]
    statement = db.execute.await_args.args[0]
    sql = str(statement)
    assert "JOIN chat_sessions ON chat_sessions.id = approval_requests.session_id" in sql
    assert "chat_sessions.user_id =" in sql
    assert "ORDER BY approval_requests.created_at" in sql
    assert {"pending", user.id}.issubset(set(statement.compile().params.values()))
