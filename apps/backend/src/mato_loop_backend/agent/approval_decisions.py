from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.agent.runtime import AgentRuntime, ApprovalResolution
from mato_loop_backend.events import run_events
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
    if decision not in approval.available_decisions:
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
    if not skill_draft_approval and expected_revision is not None:
        raise ApprovalDecisionError(400, "validation.invalid", field="expected_revision")
    if not AgentRuntime.claim_approval_resolution(approval.id):
        approval.status = "expired"
        approval.resolved_at = datetime.now(UTC)
        await db.commit()
        raise ApprovalDecisionError(409, "approval.runInactive")

    approval.status = "approved" if decision.startswith("allow") else "denied"
    approval.decision = decision
    approval.resolved_at = datetime.now(UTC)
    if skill_draft_approval and expected_revision is not None:
        approval.payload = {
            **approval.payload,
            "approvedRevision": expected_revision,
        }

    created_grant: PermissionGrant | None = None
    if decision == "allow_session":
        profile_id = approval.payload.get("agentProfileId")
        trust_level = approval.payload.get("trustLevel")
        try:
            profile_id = UUID(profile_id) if profile_id is not None else None
        except (TypeError, ValueError):
            profile_id = None
        if trust_level not in {"private", "public"}:
            trust_level = None
        existing_grant = await db.scalar(
            select(PermissionGrant).where(
                PermissionGrant.session_id == approval.session_id,
                PermissionGrant.capability == approval.capability,
                PermissionGrant.resource_type == approval.resource_type,
                PermissionGrant.resource_scope == approval.resource_scope,
                PermissionGrant.agent_profile_id == profile_id,
                PermissionGrant.trust_level == trust_level,
            )
        )
        if existing_grant is None:
            created_grant = PermissionGrant(
                session_id=approval.session_id,
                capability=approval.capability,
                resource_type=approval.resource_type,
                resource_scope=approval.resource_scope,
                agent_profile_id=profile_id,
                trust_level=trust_level,
                source_approval_id=approval.id,
            )
            db.add(created_grant)

    try:
        await db.commit()
    except Exception:
        AgentRuntime.release_approval_resolution(approval.id)
        raise

    resolution = ApprovalResolution(decision, expected_revision)
    if not AgentRuntime.resolve_approval(approval.id, resolution):
        approval.status = "expired"
        approval.decision = None
        approval.payload = {
            key: value
            for key, value in approval.payload.items()
            if key != "approvedRevision"
        }
        if created_grant is not None:
            await db.delete(created_grant)
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
