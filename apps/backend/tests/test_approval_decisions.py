from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.agent.runtime import AgentRuntime
from mato_loop_backend.api.errors import ApiError
from mato_loop_backend.api.routes.approvals import ApprovalDecisionRequest, decide_approval
from mato_loop_backend.events import run_events
from mato_loop_backend.models import (
    ApprovalRequest,
    ChatSession,
    PermissionGrant,
    UserAccount,
)


def _approval() -> ApprovalRequest:
    return ApprovalRequest(
        id=uuid4(),
        session_id=uuid4(),
        run_id=uuid4(),
        action_kind="session_read",
        risk_level="medium",
        title="Session lesen?",
        summary="Andere Session lesen",
        capability="session.read",
        resource_type="session",
        resource_scope=str(uuid4()),
        payload={},
        available_decisions=["allow_once", "allow_session", "deny"],
        status="pending",
    )


async def test_allow_session_persists_grant_before_resuming(monkeypatch) -> None:
    approval = _approval()
    user = UserAccount(id=uuid4(), username="test", password_hash="hash")
    session = ChatSession(id=approval.session_id, user_id=user.id, title="Test")
    db = AsyncMock(spec=AsyncSession)
    db.get.side_effect = [approval, session]
    db.scalar.return_value = None
    claim = Mock(return_value=True)
    resolve = Mock(return_value=True)
    publish = AsyncMock()
    monkeypatch.setattr(AgentRuntime, "claim_approval_resolution", claim)
    monkeypatch.setattr(AgentRuntime, "resolve_approval", resolve)
    monkeypatch.setattr(run_events, "publish", publish)

    result = await decide_approval(
        approval.id, ApprovalDecisionRequest(decision="allow_session"), user, db
    )

    grant = db.add.call_args.args[0]
    assert isinstance(grant, PermissionGrant)
    assert grant.session_id == approval.session_id
    assert grant.resource_scope == approval.resource_scope
    assert approval.status == "approved"
    db.commit.assert_awaited_once()
    resolve.assert_called_once_with(approval.id, "allow_session")
    assert result == {"status": "approved", "decision": "allow_session"}


async def test_inactive_approval_expires_without_creating_grant(monkeypatch) -> None:
    approval = _approval()
    user = UserAccount(id=uuid4(), username="test", password_hash="hash")
    session = ChatSession(id=approval.session_id, user_id=user.id, title="Test")
    db = AsyncMock(spec=AsyncSession)
    db.get.side_effect = [approval, session]
    monkeypatch.setattr(
        AgentRuntime, "claim_approval_resolution", Mock(return_value=False)
    )

    with pytest.raises(ApiError) as exc_info:
        await decide_approval(
            approval.id, ApprovalDecisionRequest(decision="allow_session"), user, db
        )

    assert exc_info.value.code == "approval.runInactive"
    assert approval.status == "expired"
    db.add.assert_not_called()
