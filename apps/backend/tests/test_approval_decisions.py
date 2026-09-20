import asyncio
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.agent import runtime as runtime_module
from mato_loop_backend.agent.approval_decisions import (
    ApprovalDecisionError,
    decide_agent_action,
)
from mato_loop_backend.agent.permissions import (
    PermissionRequest,
    execution_directory_scope,
    session_grants_fingerprint,
    shell_command_scope,
)
from mato_loop_backend.agent.runtime import AgentRuntime, ApprovalResolution
from mato_loop_backend.api.errors import ApiError
from mato_loop_backend.api.routes.approvals import ApprovalDecisionRequest, decide_approval
from mato_loop_backend.events import run_events
from mato_loop_backend.models import (
    AgentRun,
    ApprovalRequest,
    ChatSession,
    Message,
    PermissionGrant,
    UserAccount,
)
from mato_loop_backend.providers.base import ToolCall


def _approval() -> ApprovalRequest:
    return ApprovalRequest(
        id=uuid4(),
        request_type="agent_action",
        user_id=uuid4(),
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


def _fingerprint(approval: ApprovalRequest) -> None:
    approval.payload["sessionGrantsFingerprint"] = session_grants_fingerprint(
        approval.capability,
        approval.resource_type,
        approval.resource_scope,
        approval.payload["sessionGrants"],
    )


async def test_allow_session_persists_grant_before_resuming(monkeypatch) -> None:
    approval = _approval()
    profile_id = uuid4()
    agent_id = uuid4()
    approval.capability = "shell.write"
    approval.resource_type = "shell_command"
    approval.resource_scope = shell_command_scope(
        agent_id, "/source", "write", [], ["/target/file"], True, ["write"]
    )
    approval.payload = {
        "agentProfileId": str(profile_id),
        "trustLevel": "public",
        "sessionGrants": [
            {
                "access": "write",
                "resourceType": "execution_directory",
                "resourceScope": execution_directory_scope(agent_id, "/source"),
                "resourceDisplay": "/source",
            },
            {
                "access": "write",
                "resourceType": "execution_directory",
                "resourceScope": execution_directory_scope(agent_id, "/target"),
                "resourceDisplay": "/target",
            },
        ],
    }
    _fingerprint(approval)
    user = UserAccount(id=approval.user_id, username="test", password_hash="hash")
    db = AsyncMock(spec=AsyncSession)
    db.get.return_value = approval
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

    grants = [call.args[0] for call in db.add.call_args_list]
    assert all(isinstance(grant, PermissionGrant) for grant in grants)
    assert [grant.capability for grant in grants] == ["resource.write", "resource.write"]
    assert [grant.resource_scope for grant in grants] == [
        execution_directory_scope(agent_id, "/source"),
        execution_directory_scope(agent_id, "/target"),
    ]
    assert all(grant.session_id == approval.session_id for grant in grants)
    assert all(grant.agent_profile_id == profile_id for grant in grants)
    assert all(grant.trust_level == "public" for grant in grants)
    assert approval.status == "approved"
    db.commit.assert_awaited_once()
    db.refresh.assert_awaited_once_with(approval, with_for_update=True)
    resolve.assert_called_once_with(
        approval.id, ApprovalResolution(decision="allow_session")
    )
    assert result == {"status": "approved", "decision": "allow_session"}


async def test_argument_edit_approves_exact_values_once(monkeypatch) -> None:
    approval = _approval()
    approval.payload = {
        "argumentEdit": {
            "revision": "a" * 64,
            "fields": [
                {
                    "path": ["command"],
                    "label": "Befehl",
                    "inputType": "textarea",
                    "required": True,
                    "maxLength": 100,
                    "value": "ls",
                }
            ],
        }
    }
    user = UserAccount(id=approval.user_id, username="test", password_hash="hash")
    db = AsyncMock(spec=AsyncSession)
    db.get.return_value = approval
    monkeypatch.setattr(AgentRuntime, "claim_approval_resolution", Mock(return_value=True))
    resolve = Mock(return_value=True)
    monkeypatch.setattr(AgentRuntime, "resolve_approval", resolve)
    monkeypatch.setattr(run_events, "publish", AsyncMock())

    result = await decide_approval(
        approval.id,
        ApprovalDecisionRequest(
            decision="allow_once",
            argument_revision="a" * 64,
            argument_edits=[{"path": ["command"], "value": "pwd"}],
        ),
        user,
        db,
    )

    assert result == {"status": "approved", "decision": "allow_once"}
    assert approval.payload["ownerArgumentEdits"] == [
        {"path": ["command"], "value": "pwd"}
    ]
    resolve.assert_called_once_with(
        approval.id,
        ApprovalResolution(
            "allow_once", argument_edits=((('command',), "pwd"),)
        ),
    )


@pytest.mark.parametrize(
    ("revision", "value", "code"),
    [
        ("b" * 64, "pwd", "approval.requestChanged"),
        ("a" * 64, "", "validation.required"),
        ("a" * 64, "x" * 101, "validation.tooLong"),
    ],
)
async def test_argument_edit_rejects_stale_or_invalid_values(
    monkeypatch, revision, value, code
) -> None:
    approval = _approval()
    approval.payload = {
        "argumentEdit": {
            "revision": "a" * 64,
            "fields": [
                {
                    "path": ["command"],
                    "label": "Befehl",
                    "inputType": "textarea",
                    "required": True,
                    "maxLength": 100,
                    "value": "ls",
                }
            ],
        }
    }
    user = UserAccount(id=approval.user_id, username="test", password_hash="hash")
    db = AsyncMock(spec=AsyncSession)
    db.get.return_value = approval
    claim = Mock(return_value=True)
    monkeypatch.setattr(AgentRuntime, "claim_approval_resolution", claim)

    with pytest.raises(ApiError) as exc_info:
        await decide_approval(
            approval.id,
            ApprovalDecisionRequest(
                decision="allow_once",
                argument_revision=revision,
                argument_edits=[{"path": ["command"], "value": value}],
            ),
            user,
            db,
        )

    assert exc_info.value.code == code
    assert approval.status == "pending"
    claim.assert_not_called()


async def test_allow_session_rejects_entire_malformed_grant_set(monkeypatch) -> None:
    approval = _approval()
    approval.payload = {
        "sessionGrants": [
            {
                "access": "read",
                "resourceType": "execution_directory",
                "resourceScope": "source",
                "resourceDisplay": "/source",
            },
            {
                "access": "execute",
                "resourceType": "execution_directory",
                "resourceScope": "target",
                "resourceDisplay": "/target",
            },
        ]
    }
    _fingerprint(approval)
    user = UserAccount(id=approval.user_id, username="test", password_hash="hash")
    db = AsyncMock(spec=AsyncSession)
    db.get.return_value = approval
    claim = Mock(return_value=True)
    release = Mock()
    monkeypatch.setattr(AgentRuntime, "claim_approval_resolution", claim)
    monkeypatch.setattr(AgentRuntime, "release_approval_resolution", release)

    with pytest.raises(ApiError) as exc_info:
        await decide_approval(
            approval.id, ApprovalDecisionRequest(decision="allow_session"), user, db
        )

    assert exc_info.value.code == "approval.targetChanged"
    assert approval.status == "pending"
    db.add.assert_not_called()
    db.commit.assert_not_awaited()
    release.assert_called_once_with(approval.id)


async def test_allow_session_accepts_257_scopes_and_rejects_258(monkeypatch) -> None:
    approval = _approval()
    agent_id = uuid4()
    paths = [f"/target/{index}" for index in range(256)]
    approval.capability = "shell.read"
    approval.resource_type = "shell_command"
    approval.resource_scope = shell_command_scope(
        agent_id, "/workspace", "read", [], paths, True, ["read"] * len(paths)
    )
    user = UserAccount(id=approval.user_id, username="test", password_hash="hash")
    db = AsyncMock(spec=AsyncSession)
    db.get.return_value = approval
    db.scalar.return_value = None
    monkeypatch.setattr(
        AgentRuntime, "claim_approval_resolution", Mock(return_value=True)
    )
    release = Mock()
    monkeypatch.setattr(AgentRuntime, "release_approval_resolution", release)
    monkeypatch.setattr(AgentRuntime, "resolve_approval", Mock(return_value=True))
    monkeypatch.setattr(run_events, "publish", AsyncMock())

    def scopes(count):
        values = ["/workspace", *paths]
        return [
            {
                "access": "read",
                "resourceType": "execution_directory",
                "resourceScope": execution_directory_scope(
                    agent_id, values[index % len(values)]
                ),
                "resourceDisplay": None,
            }
            for index in range(count)
        ]

    approval.payload = {"sessionGrants": scopes(258)}
    _fingerprint(approval)
    with pytest.raises(ApiError) as exc_info:
        await decide_approval(
            approval.id, ApprovalDecisionRequest(decision="allow_session"), user, db
        )
    assert exc_info.value.code == "approval.targetChanged"
    release.assert_called_once_with(approval.id)
    db.add.assert_not_called()

    approval.payload = {"sessionGrants": scopes(257)}
    _fingerprint(approval)
    await decide_approval(
        approval.id, ApprovalDecisionRequest(decision="allow_session"), user, db
    )
    assert db.add.call_count == 257


async def test_allow_session_rejects_a_changed_scope_fingerprint(monkeypatch) -> None:
    approval = _approval()
    agent_id = uuid4()
    approval.capability = "filesystem.read"
    approval.resource_type = "execution_directory"
    approval.resource_scope = execution_directory_scope(agent_id, "/source/file")
    approval.payload = {
        "sessionGrants": [{
            "access": "read",
            "resourceType": "execution_directory",
            "resourceScope": execution_directory_scope(agent_id, "/source"),
            "resourceDisplay": "/source",
        }]
    }
    _fingerprint(approval)
    approval.payload["sessionGrants"][0]["resourceScope"] = execution_directory_scope(
        agent_id, "/"
    )
    user = UserAccount(id=approval.user_id, username="test", password_hash="hash")
    db = AsyncMock(spec=AsyncSession)
    db.get.return_value = approval
    monkeypatch.setattr(
        AgentRuntime, "claim_approval_resolution", Mock(return_value=True)
    )
    release = Mock()
    monkeypatch.setattr(AgentRuntime, "release_approval_resolution", release)

    with pytest.raises(ApiError) as exc_info:
        await decide_approval(
            approval.id, ApprovalDecisionRequest(decision="allow_session"), user, db
        )

    assert exc_info.value.code == "approval.targetChanged"
    release.assert_called_once_with(approval.id)
    db.add.assert_not_called()


async def test_deny_is_persisted_before_resuming_without_a_grant(monkeypatch) -> None:
    approval = _approval()
    user = UserAccount(id=approval.user_id, username="test", password_hash="hash")
    db = AsyncMock(spec=AsyncSession)
    db.get.return_value = approval

    def resolve(*_args) -> bool:
        assert db.commit.await_count == 1
        return True

    monkeypatch.setattr(
        AgentRuntime, "claim_approval_resolution", Mock(return_value=True)
    )
    resolver = Mock(side_effect=resolve)
    monkeypatch.setattr(AgentRuntime, "resolve_approval", resolver)
    monkeypatch.setattr(run_events, "publish", AsyncMock())

    result = await decide_approval(
        approval.id, ApprovalDecisionRequest(decision="deny"), user, db
    )

    assert approval.status == "denied"
    assert approval.decision == "deny"
    assert approval.resolved_at is not None
    resolver.assert_called_once_with(approval.id, ApprovalResolution(decision="deny"))
    db.add.assert_not_called()
    assert result == {"status": "denied", "decision": "deny"}


async def test_matching_committed_decision_can_be_retried_without_effects(
    monkeypatch,
) -> None:
    approval = _approval()
    approval.status = "approved"
    approval.decision = "allow_session"
    db = AsyncMock(spec=AsyncSession)
    claim = Mock()
    publish = AsyncMock()
    monkeypatch.setattr(AgentRuntime, "claim_approval_resolution", claim)
    monkeypatch.setattr(run_events, "publish", publish)

    result = await decide_agent_action(
        db,
        approval,
        "allow_session",
        idempotent_same_decision=True,
    )

    assert result == {"status": "approved", "decision": "allow_session"}
    claim.assert_not_called()
    db.scalar.assert_not_awaited()
    db.add.assert_not_called()
    db.commit.assert_not_awaited()
    publish.assert_not_awaited()


async def test_idempotent_retry_rejects_a_different_decision() -> None:
    approval = _approval()
    approval.status = "approved"
    approval.decision = "allow_once"
    db = AsyncMock(spec=AsyncSession)

    with pytest.raises(ApprovalDecisionError) as exc_info:
        await decide_agent_action(
            db,
            approval,
            "deny",
            idempotent_same_decision=True,
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.code == "approval.alreadyDecided"
    db.commit.assert_not_awaited()


async def test_legacy_shared_draft_decision_expires_without_executing(monkeypatch) -> None:
    approval = _approval()
    approval.payload = {"sharedDraft": {"command": "rm old.txt"}}
    db = AsyncMock(spec=AsyncSession)
    claim = Mock(return_value=True)
    resolve = Mock(return_value=True)
    monkeypatch.setattr(AgentRuntime, "claim_approval_resolution", claim)
    monkeypatch.setattr(AgentRuntime, "resolve_approval", resolve)

    with pytest.raises(ApprovalDecisionError, match="approval.runInactive"):
        await decide_agent_action(db, approval, "allow_once")

    assert approval.status == "expired"
    assert approval.resolved_at is not None
    db.commit.assert_awaited_once()
    resolve.assert_called_once_with(approval.id, ApprovalResolution("deny"))


async def test_resolved_legacy_shared_draft_remains_idempotently_readable() -> None:
    approval = _approval()
    approval.payload = {"sharedDraft": {"command": "old"}}
    approval.status = "denied"
    approval.decision = "deny"
    db = AsyncMock(spec=AsyncSession)

    result = await decide_agent_action(
        db, approval, "deny", idempotent_same_decision=True,
    )

    assert result == {"status": "denied", "decision": "deny"}
    db.commit.assert_not_awaited()


async def test_http_retry_of_matching_decision_remains_non_idempotent() -> None:
    approval = _approval()
    approval.status = "denied"
    approval.decision = "deny"
    user = UserAccount(id=approval.user_id, username="test", password_hash="hash")
    db = AsyncMock(spec=AsyncSession)
    db.get.return_value = approval

    with pytest.raises(ApiError) as exc_info:
        await decide_approval(
            approval.id, ApprovalDecisionRequest(decision="deny"), user, db
        )

    assert exc_info.value.code == "approval.alreadyDecided"
    db.commit.assert_not_awaited()


async def test_approval_without_live_waiter_expires_without_creating_grant(monkeypatch) -> None:
    approval = _approval()
    approval.payload = {
        "sessionGrants": [{
            "access": "read",
            "resourceType": "session",
            "resourceScope": approval.resource_scope,
            "resourceDisplay": None,
        }]
    }
    user = UserAccount(id=approval.user_id, username="test", password_hash="hash")
    db = AsyncMock(spec=AsyncSession)
    db.get.return_value = approval
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
    user = UserAccount(id=approval.user_id, username="test", password_hash="hash")
    db = AsyncMock(spec=AsyncSession)
    db.get.return_value = approval

    with pytest.raises(ApiError) as exc_info:
        await decide_approval(
            approval.id, ApprovalDecisionRequest(decision="allow_once"), user, db
        )
    assert exc_info.value.code == "validation.required"

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
    target_session_id = uuid4()
    session = ChatSession(id=uuid4(), user_id=uuid4(), title="Test")
    run = AgentRun(id=uuid4(), session_id=session.id, input_message_id=uuid4())
    db = AsyncMock(spec=AsyncSession)
    db.scalar.return_value = "Ziel-Session"
    db.get.return_value = Message(
        id=run.input_message_id,
        session_id=session.id,
        role="user",
        content="Test",
        source_type="endpoint",
        source_provider="telegram",
        source_endpoint_id=uuid4(),
    )
    created = asyncio.Event()

    async def flush() -> None:
        db.add.call_args.args[0].id = approval_id

    async def publish(*_args) -> None:
        created.set()

    async def deliver(*_args) -> None:
        assert db.commit.await_count == 1

    db.flush.side_effect = flush
    monkeypatch.setattr(run_events, "publish", publish)
    push = AsyncMock(side_effect=deliver)
    monkeypatch.setattr(runtime_module.WebPushService, "deliver_after_commit", push)
    enqueue = AsyncMock()
    monkeypatch.setattr(runtime_module, "enqueue_approval_prompt", enqueue)
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
                resource_scope=str(target_session_id),
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
    persisted = db.add.call_args.args[0]
    assert persisted.request_type == "agent_action"
    assert persisted.user_id == session.user_id
    assert persisted.payload["resourceDisplay"] == "Ziel-Session"
    enqueue.assert_awaited_once_with(db, persisted)
    statement = db.scalar.await_args.args[0]
    assert "chat_sessions.user_id =" in str(statement)
    assert {target_session_id, session.user_id, "chat"}.issubset(
        set(statement.compile().params.values())
    )

    assert AgentRuntime.claim_approval_resolution(approval_id) is True
    assert AgentRuntime.resolve_approval(approval_id, "allow_once") is True
    async with asyncio.timeout(1):
        assert await task == "allow_once"
    assert push.await_args.args[0] == session.user_id
    assert push.await_args.args[1].notification.target.type == "actions"
    wait_for.assert_not_awaited()


async def test_approval_ownership_is_checked_directly() -> None:
    approval = _approval()
    db = AsyncMock(spec=AsyncSession)
    db.get.return_value = approval
    other_user = UserAccount(id=uuid4(), username="other", password_hash="hash")

    with pytest.raises(ApiError) as exc_info:
        await decide_approval(
            approval.id, ApprovalDecisionRequest(decision="deny"), other_user, db
        )

    assert exc_info.value.code == "approval.notFound"
    assert db.get.await_count == 1


async def test_memory_deny_resolves_without_calling_memory_service(monkeypatch) -> None:
    approval = _approval()
    approval.request_type = "memory_change"
    approval.session_id = None
    approval.run_id = None
    approval.available_decisions = ["approve", "deny"]
    user = UserAccount(id=approval.user_id, username="test", password_hash="hash")
    db = AsyncMock(spec=AsyncSession)
    db.get.return_value = approval
    resolver = AsyncMock()
    monkeypatch.setattr(
        "mato_loop_backend.api.routes.approvals.MemoryService.apply_approved_change",
        resolver,
        raising=False,
    )

    result = await decide_approval(
        approval.id, ApprovalDecisionRequest(decision="deny"), user, db
    )

    assert result == {"status": "denied", "decision": "deny"}
    assert approval.resolved_at is not None
    db.commit.assert_awaited_once()
    resolver.assert_not_awaited()


@pytest.mark.parametrize(
    ("outcome", "expected_code", "expected_status"),
    [
        ("applied", None, "approved"),
        ("target_changed", "approval.targetChanged", "expired"),
        ("policy_changed", "approval.policyChanged", "expired"),
    ],
)
async def test_memory_approve_dispatches_service_outcomes(
    monkeypatch, outcome, expected_code, expected_status
) -> None:
    approval = _approval()
    approval.request_type = "memory_change"
    approval.session_id = None
    approval.run_id = None
    approval.available_decisions = ["approve", "deny"]
    user = UserAccount(id=approval.user_id, username="test", password_hash="hash")
    db = AsyncMock(spec=AsyncSession)
    db.get.return_value = approval
    resolver = AsyncMock(return_value=outcome)
    monkeypatch.setattr(
        "mato_loop_backend.api.routes.approvals.MemoryService.apply_approved_change",
        resolver,
        raising=False,
    )

    if expected_code is None:
        result = await decide_approval(
            approval.id, ApprovalDecisionRequest(decision="approve"), user, db
        )
        assert result == {"status": "approved", "decision": "approve"}
    else:
        with pytest.raises(ApiError) as exc_info:
            await decide_approval(
                approval.id, ApprovalDecisionRequest(decision="approve"), user, db
            )
        assert exc_info.value.code == expected_code

    resolver.assert_awaited_once_with(
        db, approval=approval, owner_id=user.id, memory_edit=None
    )
    assert approval.status == expected_status
    assert approval.resolved_at is not None
    db.commit.assert_awaited_once()
