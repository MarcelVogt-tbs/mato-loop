from __future__ import annotations

import asyncio
import re
from datetime import UTC, date, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.agent.run_contracts import ApprovalResolution
from mato_loop_backend.agent.runtime import AgentRuntime
from mato_loop_backend.events import run_events
from mato_loop_backend.models import ApprovalRequest, PermissionGrant
from mato_loop_backend.permissions.policy import (
    SessionGrantScope,
    session_grant_matches_request,
    session_grants_fingerprint,
)


class ApprovalDecisionError(RuntimeError):
    def __init__(
        self,
        status_code: int,
        code: str,
        *,
        field: str | None = None,
        params: dict | None = None,
    ) -> None:
        super().__init__(code)
        self.status_code = status_code
        self.code = code
        self.field = field
        self.params = params


def _validated_argument_edits(
    approval: ApprovalRequest,
    edits: tuple[tuple[tuple[str, ...], str], ...],
    revision: str | None,
) -> tuple[tuple[tuple[str, ...], str], ...]:
    edit_payload = (approval.payload or {}).get("argumentEdit")
    if not isinstance(edit_payload, dict) or revision != edit_payload.get("revision"):
        raise ApprovalDecisionError(409, "approval.requestChanged")
    raw_fields = edit_payload.get("fields")
    if not isinstance(raw_fields, list):
        raise ApprovalDecisionError(409, "approval.requestChanged")
    fields = {
        tuple(field["path"]): field
        for field in raw_fields
        if isinstance(field, dict)
        and isinstance(field.get("path"), list)
        and all(isinstance(segment, str) for segment in field["path"])
    }
    paths = [path for path, _value in edits]
    if len(paths) != len(set(paths)) or any(path not in fields for path in paths):
        raise ApprovalDecisionError(400, "validation.invalid", field="argument_edits")
    temporal: dict[str, date | datetime] = {}
    for path, value in edits:
        descriptor = fields[path]
        field_name = ".".join(path)
        if descriptor.get("required") is True and not value.strip():
            raise ApprovalDecisionError(422, "validation.required", field=field_name)
        max_length = descriptor.get("maxLength")
        if not isinstance(max_length, int) or max_length < 1:
            raise ApprovalDecisionError(409, "approval.requestChanged")
        if len(value) > max_length:
            raise ApprovalDecisionError(
                422,
                "validation.tooLong",
                field=field_name,
                params={"maxLength": max_length},
            )
        input_type = descriptor.get("inputType")
        try:
            if input_type == "date":
                parsed: date | datetime = date.fromisoformat(value)
            elif input_type == "datetime-local":
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
                if parsed.tzinfo is None or parsed.utcoffset() is None:
                    raise ValueError
            else:
                continue
        except ValueError as exc:
            raise ApprovalDecisionError(
                422, "validation.invalidFormat", field=field_name
            ) from exc
        temporal[path[-1]] = parsed
    start, end = temporal.get("start"), temporal.get("end")
    if start is not None and end is not None and (
        type(start) is not type(end) or end <= start
    ):
        raise ApprovalDecisionError(422, "validation.invalid", field="timing.end")
    return edits


async def decide_agent_action(
    db: AsyncSession,
    approval: ApprovalRequest,
    decision: str,
    *,
    expected_revision: str | None = None,
    argument_edits: tuple[tuple[tuple[str, ...], str], ...] = (),
    argument_revision: str | None = None,
    idempotent_same_decision: bool = False,
) -> dict[str, str]:
    async def decide() -> dict[str, str]:
        await db.refresh(approval, with_for_update=True)
        if "sharedDraft" in (approval.payload or {}) and approval.status == "pending":
            approval.status = "expired"
            approval.resolved_at = datetime.now(UTC)
            await db.commit()
            if AgentRuntime.claim_approval_resolution(approval.id):
                AgentRuntime.resolve_approval(approval.id, ApprovalResolution("deny"))
            raise ApprovalDecisionError(409, "approval.runInactive")
        return await _decide_agent_action(
            db,
            approval,
            decision,
            expected_revision=expected_revision,
            argument_edits=argument_edits,
            argument_revision=argument_revision,
            idempotent_same_decision=idempotent_same_decision,
        )

    task = asyncio.create_task(decide())
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        await task
        raise


async def _decide_agent_action(
    db,
    approval,
    decision,
    *,
    expected_revision=None,
    argument_edits=(),
    argument_revision=None,
    idempotent_same_decision=False,
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
    if argument_edits:
        if decision != "allow_once" or expected_revision is not None:
            raise ApprovalDecisionError(400, "approval.decisionUnavailable")
        edits = _validated_argument_edits(
            approval, argument_edits, argument_revision
        )
        if not AgentRuntime.claim_approval_resolution(approval.id):
            approval.status = "expired"
            approval.resolved_at = datetime.now(UTC)
            await db.commit()
            raise ApprovalDecisionError(409, "approval.runInactive")
        approval.status = "approved"
        approval.decision = decision
        approval.resolved_at = datetime.now(UTC)
        approval.payload = {
            **approval.payload,
            "ownerArgumentEdits": [
                {"path": list(path), "value": value} for path, value in edits
            ],
        }
        try:
            await db.commit()
        except (Exception, asyncio.CancelledError):
            AgentRuntime.release_approval_resolution(approval.id)
            raise
        resolution = ApprovalResolution(decision, argument_edits=edits)
        if not AgentRuntime.resolve_approval(approval.id, resolution):
            approval.status = "expired"
            approval.decision = None
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
    if argument_revision is not None:
        raise ApprovalDecisionError(400, "validation.invalid", field="argument_revision")

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
    scopes = (approval.payload or {}).get("sessionGrants")
    if decision == "allow_once" and approval.run_id is None:
        raise ApprovalDecisionError(409, "approval.runInactive")
    reusable = decision == "allow_session" or (
        decision == "allow_once" and "allow_session" in approval.available_decisions
        and bool(scopes) and not approval.payload.get("runActionOnly")
    )
    if not AgentRuntime.claim_approval_resolution(approval.id):
        approval.status = "expired"
        approval.resolved_at = datetime.now(UTC)
        await db.commit()
        raise ApprovalDecisionError(409, "approval.runInactive")
    if reusable and (
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
            or len(scope["resourceScope"]) > 1_048_576
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
    approval.decision = decision
    approval.resolved_at = datetime.now(UTC)
    if skill_draft_approval and expected_revision is not None:
        approval.payload = {
            **approval.payload,
            "approvedRevision": expected_revision,
        }

    created_grants: list[PermissionGrant] = []
    if decision.startswith("allow"):
        profile_id = approval.payload.get("agentProfileId")
        trust_level = approval.payload.get("trustLevel")
        try:
            profile_id = UUID(profile_id) if profile_id is not None else None
        except (TypeError, ValueError):
            profile_id = None
        if trust_level not in {"private", "public"}:
            trust_level = None
    if reusable:
        grant_run_id = approval.run_id if decision == "allow_once" else None
        for scope in scopes:
            capability = f"resource.{scope['access']}"
            existing_grant = await db.scalar(
                select(PermissionGrant).where(
                    PermissionGrant.session_id == approval.session_id,
                    PermissionGrant.run_id == grant_run_id,
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
                    run_id=grant_run_id,
                    capability=capability,
                    resource_type=scope["resourceType"],
                    resource_scope=scope["resourceScope"],
                    agent_profile_id=profile_id,
                    trust_level=trust_level,
                    source_approval_id=approval.id,
                )
                created_grants.append(grant)
                db.add(grant)

    elif decision == "allow_once" and isinstance(
        action_scope := (approval.payload or {}).get("runActionScope"), str
    ) and re.fullmatch(r"[a-f0-9]{64}", action_scope):
        grant = PermissionGrant(
            session_id=approval.session_id, run_id=approval.run_id,
            capability=approval.capability, resource_type="run_action",
            resource_scope=action_scope,
            agent_profile_id=profile_id, trust_level=trust_level,
            source_approval_id=approval.id,
        )
        created_grants.append(grant)
        db.add(grant)

    try:
        await db.commit()
    except (Exception, asyncio.CancelledError):
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
