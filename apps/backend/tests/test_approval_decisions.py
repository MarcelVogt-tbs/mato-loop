import asyncio
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.agent.permissions import PermissionRequest
from mato_loop_backend.agent.runtime import AgentRuntime, ApprovalResolution
from mato_loop_backend.api.errors import ApiError
from mato_loop_backend.api.routes.approvals import ApprovalDecisionRequest, decide_approval
from mato_loop_backend.events import run_events
from mato_loop_backend.models import (
    AgentRun,
    ApprovalRequest,
    ChatSession,
    PermissionGrant,
    UserAccount,
)
from mato_loop_backend.providers.base import ToolCall


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
    resolve.assert_called_once_with(
        approval.id, ApprovalResolution(decision="allow_session")
    )
    assert result == {"status": "approved", "decision": "allow_session"}


async def test_approval_without_live_waiter_expires_without_creating_grant(monkeypatch) -> None:
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


@pytest.mark.parametrize(
    ("action_kind", "resource_type"),
    [("build_skill", "skill_collection"), ("edit_skill", "skill")],
)
async def test_skill_approval_requires_and_forwards_exact_revision(
    monkeypatch, action_kind, resource_type
) -> None:
    approval = _approval()
    approval.action_kind = action_kind
    approval.capability = "skills.write"
    approval.resource_type = resource_type
    user = UserAccount(id=uuid4(), username="test", password_hash="hash")
    session = ChatSession(id=approval.session_id, user_id=user.id, title="Test")
    db = AsyncMock(spec=AsyncSession)
    db.get.side_effect = [approval, session]

    with pytest.raises(ApiError) as exc_info:
        await decide_approval(
            approval.id, ApprovalDecisionRequest(decision="allow_once"), user, db
        )
    assert exc_info.value.code == "validation.required"

    db.get.side_effect = [approval, session]
    monkeypatch.setattr(
        AgentRuntime, "claim_approval_resolution", Mock(return_value=True)
    )
    resolve = Mock(return_value=True)
    monkeypatch.setattr(AgentRuntime, "resolve_approval", resolve)
    monkeypatch.setattr(run_events, "publish", AsyncMock())
    revision = "a" * 64

    await decide_approval(
        approval.id,
        ApprovalDecisionRequest(
            decision="allow_once", expected_revision=revision
        ),
        user,
        db,
    )

    resolve.assert_called_once_with(
        approval.id,
        ApprovalResolution(decision="allow_once", expected_revision=revision),
    )
    assert approval.payload["approvedRevision"] == revision


async def test_approval_wait_has_no_inactivity_timeout(monkeypatch) -> None:
    approval_id = uuid4()
    session = ChatSession(id=uuid4(), user_id=uuid4(), title="Test")
    run = AgentRun(id=uuid4(), session_id=session.id)
    db = AsyncMock(spec=AsyncSession)
    created = asyncio.Event()

    async def flush() -> None:
        db.add.call_args.args[0].id = approval_id

    async def publish(*_args) -> None:
        created.set()

    db.flush.side_effect = flush
    monkeypatch.setattr(run_events, "publish", publish)
    wait_for = AsyncMock(side_effect=AssertionError("approval wait must not time out"))
    monkeypatch.setattr(asyncio, "wait_for", wait_for)

    task = asyncio.create_task(
        AgentRuntime._request_approval(
            db,
            run,
            session,
            ToolCall(id="call-1", name="session_read", arguments={}),
            PermissionRequest(
                capability="session.read",
                resource_type="session",
                resource_scope=str(uuid4()),
                action_kind="session_read",
                risk_level="medium",
                title="Session lesen?",
                summary="Andere Session lesen",
            ),
        )
    )
    async with asyncio.timeout(1):
        await created.wait()
    assert not task.done()

    assert AgentRuntime.claim_approval_resolution(approval_id) is True
    assert AgentRuntime.resolve_approval(approval_id, "allow_once") is True
    async with asyncio.timeout(1):
        assert await task == "allow_once"
    wait_for.assert_not_awaited()
