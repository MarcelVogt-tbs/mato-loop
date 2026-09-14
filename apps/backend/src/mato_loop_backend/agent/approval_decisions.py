from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.agent.permissions import (
    SessionGrantScope,
    session_grant_matches_request,
    session_grants_fingerprint,
)
from mato_loop_backend.agent.runtime import AgentRuntime, ApprovalResolution
from mato_loop_backend.events import run_events
from mato_loop_backend.execution import ExecutionClientError
from mato_loop_backend.models import ApprovalRequest, PermissionGrant


class ApprovalDecisionError(RuntimeError):
    def __init__(
        self, status_code: int, code: str, *, field: str | None = None
    ) -> None:
        super().__init__(code)
        self.status_code = status_code
        self.code = code
        self.field = field


async def decide_agent_action(
    db: AsyncSession,
    approval: ApprovalRequest,
    decision: str,
    *,
    expected_revision: str | None = None,
    idempotent_same_decision: bool = False,
    terminal_submitted: bool = False,
    terminal_cancelled: bool = False,
) -> dict[str, str]:
    async def decide() -> dict[str, str]:
        if "sharedDraft" not in (approval.payload or {}):
            await db.refresh(approval, with_for_update=True)
            return await _decide_agent_action(
                db, approval, decision, expected_revision=expected_revision,
                idempotent_same_decision=idempotent_same_decision,
            )
        from mato_loop_backend.agent.shared_shell import approval_command

        try:
            command = approval_command(approval)
            async with asyncio.timeout(10), command.lock:
                await db.refresh(approval)
                return await _decide_agent_action(
                    db, approval, decision, expected_revision=expected_revision,
                    idempotent_same_decision=idempotent_same_decision, shared_command=command,
                    terminal_submitted=terminal_submitted, terminal_cancelled=terminal_cancelled,
                )
        except ExecutionClientError as exc:
            raise ApprovalDecisionError(409, exc.code) from exc
        except TimeoutError as exc:
            raise ApprovalDecisionError(503, "terminal.unavailable") from exc

    task = asyncio.create_task(decide())
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        await task
        raise


async def _decide_agent_action(
    db, approval, decision, *, expected_revision=None, idempotent_same_decision=False,
    shared_command=None, terminal_submitted=False, terminal_cancelled=False,
) -> dict[str, str]:
    if approval.status != "pending":
        if (
            idempotent_same_decision
            and approval.request_type == "agent_action"
            and approval.action_kind != "terminal_command"
            and approval.status in {"approved", "denied"}
            and approval.decision == decision
        ):
            return {"status": approval.status, "decision": decision}
        raise ApprovalDecisionError(409, "approval.alreadyDecided")
    if approval.action_kind == "terminal_command":
        raise ApprovalDecisionError(404, "approval.notFound")
    if shared_command is None and decision not in approval.available_decisions:
        raise ApprovalDecisionError(400, "approval.decisionUnavailable")
    if approval.request_type != "agent_action":
        raise ApprovalDecisionError(400, "approval.decisionUnavailable")

    skill_draft_approval = (
        approval.capability == "skills.write"
        and (
            (
                approval.action_kind == "build_skill"
                and approval.resource_type == "skill_collection"
            )
            or (
                approval.action_kind == "edit_skill"
                and approval.resource_type == "skill"
            )
        )
    )
    if skill_draft_approval and decision.startswith("allow") and expected_revision is None:
        raise ApprovalDecisionError(
            422, "validation.required", field="expected_revision"
        )
    if not skill_draft_approval and shared_command is None and expected_revision is not None:
        raise ApprovalDecisionError(400, "validation.invalid", field="expected_revision")
    reviewed_request = None
    if shared_command is not None:
        from mato_loop_backend.agent.shared_shell import prepare_decision

        reviewed_request = await prepare_decision(
            db, approval, shared_command, decision, expected_revision, submitted=terminal_submitted,
        )
    scopes = (approval.payload or {}).get("sessionGrants")
    if not AgentRuntime.claim_approval_resolution(approval.id):
        approval.status = "expired"
        approval.resolved_at = datetime.now(UTC)
        await db.commit()
        raise ApprovalDecisionError(409, "approval.runInactive")
    if decision == "allow_session" and (
        not isinstance(scopes, list)
        or not 1 <= len(scopes) <= 257
        or (approval.payload or {}).get("sessionGrantsFingerprint")
        != session_grants_fingerprint(
            approval.capability,
            approval.resource_type,
            approval.resource_scope,
            scopes,
        )
        or any(
            not isinstance(scope, dict)
            or set(scope) != {
                "access", "resourceType", "resourceScope", "resourceDisplay"
            }
            or scope["access"] not in {"read", "write"}
            or not isinstance(scope["resourceType"], str)
            or not 1 <= len(scope["resourceType"]) <= 80
            or not isinstance(scope["resourceScope"], str)
            or len(scope["resourceScope"]) > 50_000
            or scope["resourceDisplay"] is not None
            and not isinstance(scope["resourceDisplay"], str)
            or not session_grant_matches_request(
                approval.capability,
                approval.resource_type,
                approval.resource_scope,
                SessionGrantScope(
                    scope["access"],
                    scope["resourceType"],
                    scope["resourceScope"],
                    scope["resourceDisplay"],
                ),
            )
            for scope in scopes
        )
    ):
        AgentRuntime.release_approval_resolution(approval.id)
        raise ApprovalDecisionError(409, "approval.targetChanged")

    approval.status = "approved" if decision.startswith("allow") else "denied"
    if terminal_cancelled:
        approval.status = "expired"
    approval.decision = decision
    approval.resolved_at = datetime.now(UTC)
    if skill_draft_approval and expected_revision is not None:
        approval.payload = {
            **approval.payload,
            "approvedRevision": expected_revision,
        }

    created_grants: list[PermissionGrant] = []
    if decision == "allow_session":
        profile_id = approval.payload.get("agentProfileId")
        trust_level = approval.payload.get("trustLevel")
        try:
            profile_id = UUID(profile_id) if profile_id is not None else None
        except (TypeError, ValueError):
            profile_id = None
        if trust_level not in {"private", "public"}:
            trust_level = None
        for scope in scopes:
            capability = f"resource.{scope['access']}"
            existing_grant = await db.scalar(
                select(PermissionGrant).where(
                    PermissionGrant.session_id == approval.session_id,
                    PermissionGrant.capability == capability,
                    PermissionGrant.resource_type == scope["resourceType"],
                    PermissionGrant.resource_scope == scope["resourceScope"],
                    PermissionGrant.agent_profile_id == profile_id,
                    PermissionGrant.trust_level == trust_level,
                )
            )
            if existing_grant is None:
                grant = PermissionGrant(
                    session_id=approval.session_id,
                    capability=capability,
                    resource_type=scope["resourceType"],
                    resource_scope=scope["resourceScope"],
                    agent_profile_id=profile_id,
                    trust_level=trust_level,
                    source_approval_id=approval.id,
                )
                created_grants.append(grant)
                db.add(grant)

    try:
        await db.commit()
    except (Exception, asyncio.CancelledError):
        AgentRuntime.release_approval_resolution(approval.id)
        if shared_command is not None and AgentRuntime.claim_approval_resolution(approval.id):
            AgentRuntime.resolve_approval(approval.id, ApprovalResolution("deny"))
        raise

    if shared_command is not None and reviewed_request is not None:
        shared_command.authorized = shared_command.reviewed
    resolution = ApprovalResolution(decision, expected_revision, reviewed_request)
    if not AgentRuntime.resolve_approval(approval.id, resolution):
        approval.status = "expired"
        approval.decision = None
        approval.payload = {
            key: value
            for key, value in approval.payload.items()
            if key != "approvedRevision"
        }
        for grant in created_grants:
            await db.delete(grant)
        await db.commit()
        raise ApprovalDecisionError(409, "approval.runInactive")

    await run_events.publish(
        approval.run_id,
        {
            "type": "approval.resolved",
            "approvalId": str(approval.id),
            "decision": decision,
        },
    )
    return {"status": approval.status, "decision": decision}
