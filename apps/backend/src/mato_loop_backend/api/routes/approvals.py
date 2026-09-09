from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.agent.approval_decisions import (
    ApprovalDecisionError,
    decide_agent_action,
)
from mato_loop_backend.agent.runtime import approval_payload
from mato_loop_backend.api.errors import ApiError
from mato_loop_backend.db.session import get_db_session
from mato_loop_backend.memory.service import MemoryApprovalEdit, MemoryService
from mato_loop_backend.models import ApprovalRequest, ChatSession, PermissionGrant, UserAccount
from mato_loop_backend.security import require_user
from mato_loop_backend.sessions.service import SessionServiceError, get_owned_session

router = APIRouter(prefix="/approvals", dependencies=[Depends(require_user)])


class ApprovalDecisionRequest(BaseModel):
    decision: Literal["allow_once", "allow_session", "approve", "deny"]
    memory_edit: MemoryApprovalEdit | None = None
    expected_revision: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )


def _permission_payload(
    grant: PermissionGrant, source: ApprovalRequest | None = None
) -> dict:
    resource_display = None
    shell_details = {}
    if grant.source_approval_id is not None:
        if grant.resource_type == "skill_collection":
            resource_display = "Persönliche Skills"
        elif source and isinstance(source.payload.get("resourceDisplay"), str):
            resource_display = source.payload["resourceDisplay"]
        if grant.resource_type == "shell_command" and source is not None:
            shell_details = {
                "shellClassification": source.payload.get("shellClassification"),
                "shellPrefixes": source.payload.get("shellPrefixes"),
            }
    return {
        "id": str(grant.id),
        "capability": grant.capability,
        "resourceType": grant.resource_type,
        "resourceScope": grant.resource_scope,
        "resourceDisplay": resource_display,
        "agentProfileId": (
            str(grant.agent_profile_id)
            if grant.agent_profile_id is not None
            else None
        ),
        "trustLevel": grant.trust_level,
        **shell_details,
        "createdAt": grant.created_at.isoformat(),
    }


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
            .where(ApprovalRequest.action_kind != "terminal_command")
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
    approval = await db.get(ApprovalRequest, approval_id)
    if approval is None or approval.user_id != user.id:
        raise ApiError(404, "approval.notFound")
    if payload.memory_edit is not None and (
        payload.decision != "approve"
        or approval.request_type != "memory_change"
        or approval.action_kind not in {"memory_update", "memory_merge"}
    ):
        raise ApiError(400, "validation.invalid", field="memory_edit")
    if approval.request_type == "memory_change":
        await db.refresh(approval, with_for_update=True)
        if approval.status != "pending":
            raise ApiError(409, "approval.alreadyDecided")
        if approval.action_kind == "terminal_command":
            raise ApiError(404, "approval.notFound")
        if payload.decision not in approval.available_decisions:
            raise ApiError(400, "approval.decisionUnavailable")
        if payload.decision not in {"approve", "deny"}:
            raise ApiError(400, "approval.decisionUnavailable")
        if payload.expected_revision is not None:
            raise ApiError(400, "validation.invalid", field="expected_revision")
        return await _decide_memory_change(
            approval, payload.decision, user.id, db, memory_edit=payload.memory_edit
        )

    try:
        return await decide_agent_action(
            db,
            approval,
            payload.decision,
            expected_revision=payload.expected_revision,
        )
    except ApprovalDecisionError as exc:
        raise ApiError(exc.status_code, exc.code, field=exc.field) from exc


async def _decide_memory_change(
    approval: ApprovalRequest,
    decision: str,
    user_id: UUID,
    db: AsyncSession,
    *,
    memory_edit: MemoryApprovalEdit | None = None,
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
    outcome = await resolver(
        db, approval=approval, owner_id=user_id, memory_edit=memory_edit
    )
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

    if memory_edit is not None:
        approval.payload = {
            **approval.payload,
            "ownerEdit": memory_edit.model_dump(mode="json"),
        }
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
        source = (
            await db.get(ApprovalRequest, item.source_approval_id)
            if item.source_approval_id is not None
            else None
        )
        payloads.append(_permission_payload(item, source))
    return payloads


@router.get("/permissions")
async def all_permissions(
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> list[dict]:
    rows = (
        await db.execute(
            select(PermissionGrant, ChatSession, ApprovalRequest)
            .join(ChatSession, ChatSession.id == PermissionGrant.session_id)
            .outerjoin(
                ApprovalRequest,
                ApprovalRequest.id == PermissionGrant.source_approval_id,
            )
            .where(ChatSession.user_id == user.id, ChatSession.parent_session_id.is_(None))
            .order_by(PermissionGrant.created_at)
        )
    ).all()
    return [
        {
            **_permission_payload(grant, source),
            "sessionId": str(session.id),
            "sourceSession": {"id": str(session.id), "title": session.title},
        }
        for grant, session, source in rows
    ]


@router.delete(
    "/sessions/{session_id}/permissions", status_code=status.HTTP_204_NO_CONTENT
)
async def revoke_permissions(
    session_id: UUID,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> None:
    try:
        session = await get_owned_session(db, session_id, user.id)
    except SessionServiceError as exc:
        raise ApiError(exc.status_code, exc.code) from exc
    if session is None or session.user_id != user.id:
        raise ApiError(404, "approval.notFound")
    await db.execute(
        delete(PermissionGrant).where(PermissionGrant.session_id == session_id)
    )
    await db.commit()


@router.delete(
    "/sessions/{session_id}/permissions/{grant_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def revoke_permission(
    session_id: UUID,
    grant_id: UUID,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> None:
    try:
        session = await get_owned_session(db, session_id, user.id)
    except SessionServiceError as exc:
        raise ApiError(exc.status_code, exc.code) from exc
    if session is None or session.user_id != user.id:
        raise ApiError(404, "approval.notFound")
    grant = await db.get(PermissionGrant, grant_id)
    if grant is None or grant.session_id != session_id:
        raise ApiError(404, "approval.notFound")
    await db.delete(grant)
    await db.commit()
