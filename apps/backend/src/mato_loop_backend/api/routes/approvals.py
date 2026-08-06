from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.agent.runtime import AgentRuntime, approval_payload
from mato_loop_backend.api.errors import ApiError
from mato_loop_backend.db.session import get_db_session
from mato_loop_backend.events import run_events
from mato_loop_backend.models import ApprovalRequest, ChatSession, PermissionGrant, UserAccount
from mato_loop_backend.security import require_user

router = APIRouter(prefix="/approvals", dependencies=[Depends(require_user)])


class ApprovalDecisionRequest(BaseModel):
    decision: Literal["allow_once", "allow_session", "deny"]


@router.get("/pending")
async def pending_approvals(
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> list[dict]:
    items = list(
        (
            await db.scalars(
                select(ApprovalRequest)
                .join(ChatSession, ChatSession.id == ApprovalRequest.session_id)
                .where(ApprovalRequest.status == "pending")
                .where(ChatSession.user_id == user.id)
                .order_by(ApprovalRequest.created_at)
            )
        ).all()
    )
    return [approval_payload(item) for item in items]


@router.post("/{approval_id}/decisions")
async def decide_approval(
    approval_id: UUID,
    payload: ApprovalDecisionRequest,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict[str, str]:
    approval = await db.get(ApprovalRequest, approval_id, with_for_update=True)
    if approval is None:
        raise ApiError(404, "approval.notFound")
    session = await db.get(ChatSession, approval.session_id)
    if session is None or session.user_id != user.id:
        raise ApiError(404, "approval.notFound")
    if approval.status != "pending":
        raise ApiError(409, "approval.alreadyDecided")
    if payload.decision not in approval.available_decisions:
        raise ApiError(400, "approval.decisionUnavailable")
    if not AgentRuntime.claim_approval_resolution(approval.id):
        approval.status = "expired"
        approval.resolved_at = datetime.now(UTC)
        await db.commit()
        raise ApiError(409, "approval.runInactive")
    approval.status = "approved" if payload.decision.startswith("allow") else "denied"
    approval.decision = payload.decision
    approval.resolved_at = datetime.now(UTC)
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
    if not AgentRuntime.resolve_approval(approval.id, payload.decision):
        approval.status = "expired"
        approval.decision = None
        if created_grant is not None:
            await db.delete(created_grant)
        await db.commit()
        raise ApiError(409, "approval.runInactive")
    await run_events.publish(
        approval.run_id,
        {"type": "approval.resolved", "approvalId": str(approval.id), "decision": payload.decision},
    )
    return {"status": approval.status, "decision": payload.decision}


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
        if item.source_approval_id is not None:
            source = await db.get(ApprovalRequest, item.source_approval_id)
            if source and isinstance(source.payload.get("resourceDisplay"), str):
                resource_display = source.payload["resourceDisplay"]
        payloads.append(
            {
            "id": str(item.id),
            "capability": item.capability,
            "resourceType": item.resource_type,
            "resourceScope": item.resource_scope,
            "resourceDisplay": resource_display,
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
