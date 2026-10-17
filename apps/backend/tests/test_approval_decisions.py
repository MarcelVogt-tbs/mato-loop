import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

import mato_loop_backend.permissions.requests as mock_permissions_requests
import mato_loop_backend.tools.coding as tools_coding
from mato_loop_backend.agent.run_contracts import ApprovalResolution
from mato_loop_backend.agent.run_support import _run_action_scope
from mato_loop_backend.agent.runtime import AgentRuntime
from mato_loop_backend.api.errors import ApiError
from mato_loop_backend.api.routes.approvals import ApprovalDecisionRequest, decide_approval
from mato_loop_backend.events import run_events
from mato_loop_backend.execution import ShellInspectResult
from mato_loop_backend.models import (
    AgentRun,
    ApprovalRequest,
    ChatSession,
    Message,
    PermissionGrant,
    UserAccount,
)
from mato_loop_backend.permissions.decisions import ApprovalDecisionError, decide_agent_action
from mato_loop_backend.permissions.policy import (
    PermissionDecision,
    PermissionRequest,
    administrator_permission,
    execution_directory_permission,
    execution_directory_scope,
    integration_operation_permission,
    session_grant_options,
    session_grant_options_payload,
    session_grants_fingerprint,
    session_grants_payload,
    shell_action_scope,
    shell_command_permissions,
    shell_command_scope,
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


@pytest.mark.parametrize("decision", ["allow_once", "allow_session"])
@pytest.mark.parametrize("kind", ["exact_shell", "navigation", "resources", "computer", "parent", "coding"])
async def test_allow_persists_scoped_grant_before_resuming(monkeypatch, decision, kind) -> None:
    approval = _approval()
    profile_id = uuid4()
    agent_id = uuid4()
    approval.capability = "shell.write"
    approval.resource_type = "shell_command"
    action = shell_action_scope(agent_id, "/source", "touch /target/file")
    approval.resource_scope = shell_command_scope(
        agent_id, "/source", "write", [], ["/target/file"], True, ["write"], action
    )
    approval.payload = {
        "agentProfileId": str(profile_id),
        "trustLevel": "public",
        "sessionGrants": [
            {
                "category": "write",
                "resourceType": "shell_action",
                "resourceScope": action,
                "resourceDisplay": "touch /target/file",
            },
        ],
    }
    expected = [("category.write", "shell_action", action)]
    if kind == "navigation":
        request = execution_directory_permission(agent_id, "/source")
        approval.capability, approval.resource_type, approval.resource_scope = request.capability, request.resource_type, request.resource_scope
        approval.payload["sessionGrants"] = session_grants_payload(request)
        action = request.resource_scope
        expected = [("execution.directory", "execution_directory", action)]
    elif kind in {"resources", "computer"}:
        requests = shell_command_permissions(agent_id, "/source", "cat /input/file > /target/file", ShellInspectResult(
            required_categories=["read", "write"],
            targets=[{"path": "/input/file", "kind": "input", "access": "read"}, {"path": "/target/file", "kind": "output", "access": "write"}],
            classification="write", read_only=False, analysis_complete=True, commands=[], inspection_revision="a" * 64,
        ))
        request = next(request for request in requests if request.capability == "shell.write")
        approval.resource_scope = request.resource_scope
        approval.payload["sessionGrants"] = session_grants_payload(request)
        approval.payload["grantOptions"] = session_grant_options_payload(request)
        expected = [("category.write", "shell_computer", str(agent_id))] if kind == "computer" else [
            ("category.write", "shell_action", request.effective_session_grants[0].resource_scope),
        ]
    elif kind == "parent":
        request = execution_directory_permission(agent_id, "/source/file.txt", capability="filesystem.write")
        approval.capability, approval.resource_type, approval.resource_scope = request.capability, request.resource_type, request.resource_scope
        approval.payload["sessionGrants"] = session_grants_payload(request)
        approval.payload["grantOptions"] = session_grant_options_payload(request)
        expected = [("category.write", "execution_path", session_grant_options(request)["parent"].effective_session_grants[0].resource_scope)]
    elif kind == "coding":
        project = SimpleNamespace(id=uuid4(), name="Project", coding_integration_id=uuid4())
        monkeypatch.setattr(tools_coding, "_coding_project", AsyncMock(return_value=project))
        context = SimpleNamespace(ask=AsyncMock(return_value=PermissionDecision.ALLOW))
        await tools_coding._authorize_coding(context, project, "coding.analyze", "Analyze?", "Analyze project.")
        request = context.ask.await_args.args[0]
        approval.capability, approval.resource_type, approval.resource_scope = request.capability, request.resource_type, request.resource_scope
        approval.payload["sessionGrants"] = session_grants_payload(request)
        expected = [("category.read", "project", str(project.id))]
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
        approval.id, ApprovalDecisionRequest(decision=decision, grant_option=kind if kind in {"computer", "parent"} else None), user, db
    )

    grants = [call.args[0] for call in db.add.call_args_list]
    assert all(isinstance(grant, PermissionGrant) for grant in grants)
    assert [(grant.capability, grant.resource_type, grant.resource_scope) for grant in grants] == expected
    assert all(grant.session_id == approval.session_id for grant in grants)
    assert all(grant.run_id == (approval.run_id if decision == "allow_once" else None) for grant in grants)
    assert all(grant.agent_profile_id == profile_id for grant in grants)
    assert all(grant.trust_level == "public" for grant in grants)
    assert approval.status == "approved"
    db.commit.assert_awaited_once()
    db.refresh.assert_awaited_once_with(approval, with_for_update=True)
    resolve.assert_called_once_with(
        approval.id, ApprovalResolution(decision=decision)
    )
    assert result == {"status": "approved", "decision": decision}


@pytest.mark.parametrize("decision, confidential", [("allow_session", False), ("allow_once", False), ("allow_once", True)])
async def test_administrator_decision_creates_scoped_grants(monkeypatch, decision, confidential):
    approval = _approval()
    path = "/root/.ssh/config" if confidential else "/etc/demo.conf"
    request = administrator_permission(uuid4(), "/work", capability="filesystem.write", path=path)
    approval.resource_type, approval.resource_scope, approval.capability = request.resource_type, request.resource_scope, request.capability
    approval.available_decisions = ["allow_once", "deny"] if confidential else list(request.available_decisions)
    approval.payload = {
        **request.approval_details, "sessionGrants": session_grants_payload(request),
        "runActionOnly": "allow_session" not in approval.available_decisions,
        "runActionScope": _run_action_scope(request, ToolCall("edit", "edit_file", {"path": path})),
    }
    _fingerprint(approval)
    db = AsyncMock(spec=AsyncSession)
    db.get.return_value = approval
    db.scalar.return_value = None
    resolve = Mock(return_value=True)
    monkeypatch.setattr(AgentRuntime, "claim_approval_resolution", Mock(return_value=True))
    monkeypatch.setattr(AgentRuntime, "resolve_approval", resolve)
    monkeypatch.setattr(run_events, "publish", AsyncMock())
    user = UserAccount(id=approval.user_id, username="test", password_hash="hash")
    await decide_approval(approval.id, ApprovalDecisionRequest(decision=decision), user, db)
    assert [(call.args[0].resource_type, call.args[0].run_id) for call in db.add.call_args_list] == (
        [("run_action", approval.run_id)] if confidential else
        [("administrator_path", approval.run_id if decision == "allow_once" else None)]
    )


async def test_mcp_allow_session_persists_tool_grant(monkeypatch) -> None:
    approval = _approval()
    request = integration_operation_permission(
        capability="mcp.call",
        integration_id=uuid4(),
        binding_revision="{}",
        operation_key="call:lookup",
        resource_key="tool:lookup",
        title="MCP-Tool aufrufen?",
        summary="Tool aufrufen.",
    )
    approval.capability = request.capability
    approval.resource_type = request.resource_type
    approval.resource_scope = request.resource_scope
    approval.payload = {"sessionGrants": session_grants_payload(request)}
    _fingerprint(approval)
    db = AsyncMock(spec=AsyncSession)
    db.get.return_value = approval
    db.scalar.return_value = None
    monkeypatch.setattr(AgentRuntime, "claim_approval_resolution", Mock(return_value=True))
    monkeypatch.setattr(AgentRuntime, "resolve_approval", Mock(return_value=True))
    monkeypatch.setattr(run_events, "publish", AsyncMock())

    result = await decide_approval(
        approval.id,
        ApprovalDecisionRequest(decision="allow_session"),
        UserAccount(id=approval.user_id, username="test", password_hash="hash"),
        db,
    )

    assert result == {"status": "approved", "decision": "allow_session"}
    grant = db.add.call_args.args[0]
    assert grant.capability == "category.write"
    assert grant.resource_type == "integration_resource"
    assert grant.resource_scope == request.effective_session_grants[0].resource_scope


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


@pytest.mark.parametrize("selection,offered", [("parent", False), ("computer", True), ("/arbitrary", True), ("parent", True)])
async def test_scope_selection_rejects_unoffered_or_changed_options(monkeypatch, selection, offered):
    approval = _approval()
    request = execution_directory_permission(uuid4(), "/work/file", capability="filesystem.write")
    approval.capability, approval.resource_type, approval.resource_scope = request.capability, request.resource_type, request.resource_scope
    approval.payload = {"sessionGrants": session_grants_payload(request), "grantOptions": session_grant_options_payload(request) if offered else []}
    _fingerprint(approval)
    if selection == "parent" and offered:
        approval.payload["grantOptions"][1]["fingerprint"] = "0" * 64
    db = AsyncMock(spec=AsyncSession)
    db.get.return_value = approval
    monkeypatch.setattr(AgentRuntime, "claim_approval_resolution", Mock(return_value=True))
    release = Mock()
    monkeypatch.setattr(AgentRuntime, "release_approval_resolution", release)
    with pytest.raises(ApiError) as exc:
        await decide_approval(approval.id, ApprovalDecisionRequest(decision="allow_session", grant_option=selection),
                             UserAccount(id=approval.user_id), db)
    assert (exc.value.code, db.add.called, release.call_args.args) == ("approval.targetChanged", False, (approval.id,))


@pytest.mark.parametrize("count", [1, 257, 258])
async def test_allow_session_rejects_legacy_shell_directory_scopes(monkeypatch, count) -> None:
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

    approval.payload = {"sessionGrants": scopes(count)}
    _fingerprint(approval)
    with pytest.raises(ApiError) as exc_info:
        await decide_approval(
            approval.id, ApprovalDecisionRequest(decision="allow_session"), user, db
        )
    assert exc_info.value.code == "approval.targetChanged"
    release.assert_called_once_with(approval.id)
    db.add.assert_not_called()

@pytest.mark.parametrize("decision", ["allow_once", "allow_session"])
async def test_allow_rejects_a_changed_scope_fingerprint(monkeypatch, decision) -> None:
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
            approval.id, ApprovalDecisionRequest(decision=decision), user, db
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


@pytest.mark.parametrize("reusable", [True, False])
async def test_approval_wait_has_no_inactivity_timeout(monkeypatch, reusable) -> None:
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

    async def deliver(*_args, **_kwargs) -> None:
        assert db.commit.await_count == 1

    db.flush.side_effect = flush
    monkeypatch.setattr(run_events, "publish", publish)
    push = AsyncMock(side_effect=deliver)
    notification_id = uuid4()
    monkeypatch.setattr(mock_permissions_requests.NotificationService, "create_for_approval", AsyncMock(return_value=notification_id))
    monkeypatch.setattr(mock_permissions_requests.NotificationService, "deliver_after_commit", push)
    enqueue = AsyncMock()
    _mock_value = enqueue
    for _mock_target in (mock_permissions_requests,):
        monkeypatch.setattr(_mock_target, 'enqueue_approval_prompt', _mock_value)
    wait_for = AsyncMock(side_effect=AssertionError("approval wait must not time out"))
    monkeypatch.setattr(asyncio, "wait_for", wait_for)

    task = asyncio.create_task(
        AgentRuntime._request_approval(
            db,
            run,
            session,
            ToolCall(
                id="call-1",
                name="session_read",
                arguments={"_reason": "Die andere Session enthält den benötigten Kontext."},
            ),
            PermissionRequest(
                capability="session.read" if reusable else "unclassified.call",
                resource_type="session",
                resource_scope=str(target_session_id),
                action_kind="session_read",
                risk_level="medium",
                title="Session lesen?",
                summary="Andere Session lesen",
                resource_display=None if reusable else "Ziel-Session",
            ),
        )
    )
    async with asyncio.timeout(1):
        await created.wait()
    assert not task.done()
    persisted = db.add.call_args.args[0]
    assert persisted.request_type == "agent_action"
    assert persisted.user_id == session.user_id
    assert persisted.reason == "Die andere Session enthält den benötigten Kontext."
    assert persisted.payload["resourceDisplay"] == "Ziel-Session"
    assert persisted.payload["arguments"]["_reason"] == persisted.reason
    assert persisted.available_decisions == (
        ["allow_once", "allow_session", "deny"] if reusable else ["allow_once", "deny"]
    )
    assert bool(persisted.payload["sessionGrants"]) is reusable
    enqueue.assert_awaited_once_with(db, persisted)
    if reusable:
        statement = db.scalar.await_args.args[0]
        assert "chat_sessions.user_id =" in str(statement)
        assert {target_session_id, session.user_id, "chat"}.issubset(
            set(statement.compile().params.values())
        )

    assert AgentRuntime.claim_approval_resolution(approval_id) is True
    assert AgentRuntime.resolve_approval(approval_id, "allow_once") is True
    async with asyncio.timeout(1):
        assert await task == "allow_once"
    push.assert_awaited_once_with(notification_id)
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
