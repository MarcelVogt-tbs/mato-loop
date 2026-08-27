from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.agent.runtime import (
    AgentRuntime,
    ApprovalResolution,
    approval_payload,
)
from mato_loop_backend.api.errors import ApiError
from mato_loop_backend.db.session import get_db_session
from mato_loop_backend.events import run_events
from mato_loop_backend.memory.service import MemoryService
from mato_loop_backend.models import ApprovalRequest, ChatSession, PermissionGrant, UserAccount
from mato_loop_backend.security import require_user

router = APIRouter(prefix="/approvals", dependencies=[Depends(require_user)])


class ApprovalDecisionRequest(BaseModel):
    decision: Literal["allow_once", "allow_session", "approve", "deny"]
    expected_revision: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )


@router.get("/pending")
async def pending_approvals(
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> list[dict]:
    rows = (
        await db.execute(
            select(ApprovalRequest, ChatSession)
            .outerjoin(ChatSession, ChatSession.id == ApprovalRequest.session_id)
            .where(ApprovalRequest.status == "pending")
            .where(ApprovalRequest.user_id == user.id)
            .order_by(ApprovalRequest.created_at)
        )
    ).all()
    return [
        {
            **approval_payload(approval),
            "sourceSession": (
                {"id": str(session.id), "title": session.title} if session else None
            ),
        }
        for approval, session in rows
    ]


@router.post("/{approval_id}/decisions")
async def decide_approval(
    approval_id: UUID,
    payload: ApprovalDecisionRequest,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict[str, str]:
    approval = await db.get(ApprovalRequest, approval_id, with_for_update=True)
    if approval is None or approval.user_id != user.id:
        raise ApiError(404, "approval.notFound")
    if approval.status != "pending":
        raise ApiError(409, "approval.alreadyDecided")
    if payload.decision not in approval.available_decisions:
        raise ApiError(400, "approval.decisionUnavailable")
    if approval.request_type == "memory_change":
        if payload.decision not in {"approve", "deny"}:
            raise ApiError(400, "approval.decisionUnavailable")
        if payload.expected_revision is not None:
            raise ApiError(400, "validation.invalid", field="expected_revision")
        return await _decide_memory_change(approval, payload.decision, user.id, db)
    if approval.request_type != "agent_action":
        raise ApiError(400, "approval.decisionUnavailable")
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
    if (
        skill_draft_approval
        and payload.decision.startswith("allow")
        and payload.expected_revision is None
    ):
        raise ApiError(422, "validation.required", field="expected_revision")
    if not skill_draft_approval and payload.expected_revision is not None:
        raise ApiError(400, "validation.invalid", field="expected_revision")
    if not AgentRuntime.claim_approval_resolution(approval.id):
        approval.status = "expired"
        approval.resolved_at = datetime.now(UTC)
        await db.commit()
        raise ApiError(409, "approval.runInactive")
    approval.status = "approved" if payload.decision.startswith("allow") else "denied"
    approval.decision = payload.decision
    approval.resolved_at = datetime.now(UTC)
    if skill_draft_approval and payload.expected_revision is not None:
        approval.payload = {
            **approval.payload,
            "approvedRevision": payload.expected_revision,
        }
    created_grant: PermissionGrant | None = None
    if payload.decision == "allow_session":
        existing_grant = await db.scalar(
            select(PermissionGrant).where(
                PermissionGrant.session_id == approval.session_id,
                PermissionGrant.capability == approval.capability,
                PermissionGrant.resource_type == approval.resource_type,
                PermissionGrant.resource_scope == approval.resource_scope,
            )
        )
        if existing_grant is None:
            created_grant = PermissionGrant(
                session_id=approval.session_id,
                capability=approval.capability,
                resource_type=approval.resource_type,
                resource_scope=approval.resource_scope,
                source_approval_id=approval.id,
            )
            db.add(created_grant)
    try:
        await db.commit()
    except Exception:
        AgentRuntime.release_approval_resolution(approval.id)
        raise
    resolution = ApprovalResolution(payload.decision, payload.expected_revision)
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
        raise ApiError(409, "approval.runInactive")
    await run_events.publish(
        approval.run_id,
        {"type": "approval.resolved", "approvalId": str(approval.id), "decision": payload.decision},
    )
    return {"status": approval.status, "decision": payload.decision}


async def _decide_memory_change(
    approval: ApprovalRequest,
    decision: str,
    user_id: UUID,
    db: AsyncSession,
) -> dict[str, str]:
    now = datetime.now(UTC)
    if decision == "deny":
        approval.status = "denied"
        approval.decision = decision
        approval.resolved_at = now
        await db.commit()
        return {"status": approval.status, "decision": decision}

    resolver = getattr(MemoryService, "apply_approved_change", None)
    if resolver is None:
        raise ApiError(503, "memory.unavailable")
    outcome = await resolver(db, approval=approval, owner_id=user_id)
    error_code = {
        "target_changed": "approval.targetChanged",
        "policy_changed": "approval.policyChanged",
    }.get(outcome)
    if error_code is not None:
        approval.status = "expired"
        approval.resolved_at = now
        await db.commit()
        raise ApiError(409, error_code)
    if outcome != "applied":
        raise ApiError(503, "memory.unavailable")

    approval.status = "approved"
    approval.decision = decision
    approval.resolved_at = now
    await db.commit()
    return {"status": approval.status, "decision": decision}


@router.get("/sessions/{session_id}/permissions")
async def list_permissions(
    session_id: UUID,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> list[dict]:
    session = await db.get(ChatSession, session_id)
    if session is None or session.user_id != user.id:
        raise ApiError(404, "session.notFound")
    grants = list(
        (
            await db.scalars(
                select(PermissionGrant)
                .where(PermissionGrant.session_id == session_id)
                .order_by(PermissionGrant.created_at)
            )
        ).all()
    )
    payloads = []
    for item in grants:
        resource_display = None
        shell_details = {}
        if item.source_approval_id is not None:
            source = await db.get(ApprovalRequest, item.source_approval_id)
            if item.resource_type == "skill_collection":
                resource_display = "Persönliche Skills"
            elif source and isinstance(source.payload.get("resourceDisplay"), str):
                resource_display = source.payload["resourceDisplay"]
            if item.resource_type == "shell_command" and source is not None:
                shell_details = {
                    "shellClassification": source.payload.get("shellClassification"),
                    "shellPrefixes": source.payload.get("shellPrefixes"),
                }
        payloads.append(
            {
                "id": str(item.id),
                "capability": item.capability,
                "resourceType": item.resource_type,
                "resourceScope": item.resource_scope,
                "resourceDisplay": resource_display,
                **shell_details,
                "createdAt": item.created_at.isoformat(),
            }
        )
    return payloads


@router.delete(
    "/sessions/{session_id}/permissions/{grant_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def revoke_permission(
    session_id: UUID,
    grant_id: UUID,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> None:
    session = await db.get(ChatSession, session_id)
    if session is None or session.user_id != user.id:
        raise ApiError(404, "approval.notFound")
    grant = await db.get(PermissionGrant, grant_id)
    if grant is None or grant.session_id != session_id:
        raise ApiError(404, "approval.notFound")
    await db.delete(grant)
    await db.commit()
