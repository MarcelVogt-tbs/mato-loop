from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.integrations import canonicalize_url, sanitize_url_for_display
from mato_loop_backend.models import ChatSession, PermissionGrant
from mato_loop_backend.workspace import WorkspaceService


class PermissionDecision(StrEnum):
    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"


@dataclass(frozen=True, slots=True)
class PermissionRequest:
    capability: str
    resource_type: str
    resource_scope: str
    action_kind: str
    risk_level: str
    title: str
    summary: str
    resource_display: str | None = None


def path_permission(
    *,
    capability: str,
    relative_path: str,
    action_kind: str,
    action_label: str,
) -> PermissionRequest:
    return PermissionRequest(
        capability=capability,
        resource_type="path",
        resource_scope=relative_path,
        action_kind=action_kind,
        risk_level="high" if capability == "filesystem.delete" else "medium",
        title=f"{action_label} erlauben?",
        summary=f"Der Agent möchte {relative_path} verwenden.",
    )


def session_permission(target_session_id: UUID) -> PermissionRequest:
    return PermissionRequest(
        capability="session.read",
        resource_type="session",
        resource_scope=str(target_session_id),
        action_kind="session_read",
        risk_level="medium",
        title="Andere Session lesen?",
        summary="Der Agent möchte den Gesprächsverlauf einer anderen Session lesen.",
    )


def session_search_permission() -> PermissionRequest:
    return PermissionRequest(
        capability="session.search",
        resource_type="session_collection",
        resource_scope="all",
        action_kind="session_search",
        risk_level="medium",
        title="Sessions durchsuchen?",
        summary="Der Agent möchte Titel und Inhalte anderer Sessions durchsuchen.",
    )


def webfetch_permission(url: str) -> PermissionRequest:
    canonical_url = canonicalize_url(url)
    return PermissionRequest(
        capability="network.fetch",
        resource_type="url",
        resource_scope=hashlib.sha256(canonical_url.encode("utf-8")).hexdigest(),
        action_kind="webfetch",
        risk_level="medium",
        title="Webseite abrufen?",
        summary=f"Der Agent möchte {sanitize_url_for_display(canonical_url)} abrufen.",
        resource_display=sanitize_url_for_display(canonical_url),
    )


def _path_is_within(resource: Path, scope: Path) -> bool:
    try:
        resource.relative_to(scope)
        return True
    except ValueError:
        return False


class PermissionPolicy:
    @staticmethod
    async def evaluate(
        db: AsyncSession,
        chat_session: ChatSession,
        request: PermissionRequest,
        workspace: WorkspaceService,
    ) -> PermissionDecision:
        if request.resource_type == "session" and request.capability == "session.read":
            try:
                if UUID(request.resource_scope) == chat_session.id:
                    return PermissionDecision.ALLOW
            except ValueError:
                return PermissionDecision.DENY

        if request.resource_type == "path":
            try:
                resource = workspace.resolve(request.resource_scope)
            except ValueError:
                return PermissionDecision.DENY
            if chat_session.working_directory:
                workdir = workspace.resolve(chat_session.working_directory)
                if _path_is_within(resource, workdir):
                    if request.capability == "filesystem.read":
                        return PermissionDecision.ALLOW
                    if chat_session.workspace_access == "read-write":
                        return PermissionDecision.ALLOW

        grants = list(
            (
                await db.scalars(
                    select(PermissionGrant).where(
                        PermissionGrant.session_id == chat_session.id,
                        PermissionGrant.capability == request.capability,
                        PermissionGrant.resource_type == request.resource_type,
                    )
                )
            ).all()
        )
        for grant in grants:
            if PermissionPolicy.grant_matches(grant, request, workspace):
                return PermissionDecision.ALLOW
        return PermissionDecision.ASK

    @staticmethod
    def grant_matches(
        grant: PermissionGrant,
        request: PermissionRequest,
        workspace: WorkspaceService,
    ) -> bool:
        if grant.capability != request.capability or grant.resource_type != request.resource_type:
            return False
        if request.resource_type == "path":
            try:
                return _path_is_within(
                    workspace.resolve(request.resource_scope),
                    workspace.resolve(grant.resource_scope),
                )
            except ValueError:
                return False
        if request.resource_type == "session":
            try:
                return UUID(request.resource_scope) == UUID(grant.resource_scope)
            except ValueError:
                return False
        if request.resource_type == "session_collection":
            return request.resource_scope == grant.resource_scope == "all"
        if request.resource_type == "url":
            return request.resource_scope == grant.resource_scope
        return False
