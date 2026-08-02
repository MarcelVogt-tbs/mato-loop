from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import StrEnum
from pathlib import PurePosixPath
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.integrations import canonicalize_url, sanitize_url_for_display
from mato_loop_backend.models import ChatSession, PermissionGrant
from mato_loop_backend.settings import ToolExecutionPolicy
from mato_loop_backend.workspace import FileRoot, RootFileError, RootFileService


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
    policy_integration_id: UUID | None = None
    policy_resource_key: str | None = None


def path_permission(
    *,
    root_files: RootFileService,
    root: FileRoot | str,
    capability: str,
    relative_path: str,
    action_kind: str,
    action_label: str,
) -> PermissionRequest:
    selected_root = FileRoot(root)
    normalized_path = root_files.normalize_relative_path(relative_path)
    root_identity = root_files.root_identity(selected_root)
    scope = json.dumps(
        {
            "identity": root_identity,
            "path": normalized_path,
            "root": selected_root.value,
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return PermissionRequest(
        capability=capability,
        resource_type="root_path",
        resource_scope=scope,
        action_kind=action_kind,
        risk_level="high" if capability == "filesystem.delete" else "medium",
        title=f"{action_label} erlauben?",
        summary=(
            f"Der Agent möchte {selected_root.value}:{normalized_path} verwenden."
        ),
        resource_display=f"{selected_root.value}:{normalized_path}",
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


def session_browse_permission() -> PermissionRequest:
    return PermissionRequest(
        capability="session.search",
        resource_type="session_collection",
        resource_scope="all",
        action_kind="session_browse",
        risk_level="medium",
        title="Sessions anzeigen?",
        summary="Der Agent möchte kürzlich aktive Sessions anzeigen.",
    )


def memory_write_permission(session_id: UUID, action: str) -> PermissionRequest:
    updating = action == "update"
    return PermissionRequest(
        capability="memory.write",
        resource_type="session",
        resource_scope=str(session_id),
        action_kind="memory_write",
        risk_level="medium",
        title="Memory aktualisieren?" if updating else "Memory speichern?",
        summary=(
            "Der Agent möchte ein bestehendes Memory aktualisieren."
            if updating
            else "Der Agent möchte ein neues Memory speichern."
        ),
        resource_display="Memory der aktuellen Session",
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


def web_search_permission() -> PermissionRequest:
    return PermissionRequest(
        capability="network.search",
        resource_type="network_service",
        resource_scope="web_search",
        action_kind="web_search",
        risk_level="medium",
        title="Websuche ausführen?",
        summary="Der Agent möchte einen konfigurierten Websuchdienst verwenden.",
        resource_display="Konfigurierte Websuche",
    )


def memory_read_permission(user_id: UUID) -> PermissionRequest:
    return PermissionRequest(
        capability="memory.read",
        resource_type="memory_collection",
        resource_scope=str(user_id),
        action_kind="memory_retrieve",
        risk_level="medium",
        title="Memories lesen?",
        summary="Der Agent möchte gespeicherte Memories lesen.",
        resource_display="Eigene Memories",
    )


def media_permission(session_id: UUID, capability: str, tool_name: str) -> PermissionRequest:
    labels = {
        "media.generate.image": ("Bild erzeugen?", "ein Bild erzeugen"),
        "media.generate.speech": ("Sprache erzeugen?", "eine Sprachaufnahme erzeugen"),
        "media.generate.audio": ("Audio erzeugen?", "eine Audiodatei erzeugen"),
        "media.transcribe.audio": ("Audio transkribieren?", "einen Audioanhang transkribieren"),
    }
    title, action = labels.get(capability, ("Medienaktion erlauben?", "Medien verarbeiten"))
    return PermissionRequest(
        capability=capability,
        resource_type="session",
        resource_scope=str(session_id),
        action_kind=tool_name,
        risk_level="medium",
        title=title,
        summary=f"Der Agent möchte in der aktuellen Session {action}.",
    )


def agent_identity_permission(
    user_id: UUID, *, changes_name: bool, changes_instructions: bool
) -> PermissionRequest:
    if changes_name and changes_instructions:
        summary = "Der Agent möchte seinen Namen und seine Persona-Anweisungen ändern."
    elif changes_name:
        summary = "Der Agent möchte seinen Namen ändern."
    else:
        summary = "Der Agent möchte seine Persona-Anweisungen ändern."
    return PermissionRequest(
        capability="settings.agent_identity.update",
        resource_type="agent_identity",
        resource_scope=str(user_id),
        action_kind="update_agent_identity",
        risk_level="medium",
        title="Agent-Persona ändern?",
        summary=summary,
        resource_display="Eigene Agent-Persona",
    )


def _root_path_scope(resource_scope: str) -> tuple[FileRoot, str, PurePosixPath] | None:
    try:
        value = json.loads(resource_scope)
        root = FileRoot(value["root"])
        identity = value["identity"]
        path = value["path"]
        if not isinstance(identity, str) or not isinstance(path, str):
            return None
        parsed = PurePosixPath(path)
        if path != "." and (
            parsed.is_absolute()
            or any(part in {"", ".", ".."} for part in path.split("/"))
        ):
            return None
        return root, identity, parsed
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None


def _path_is_within(resource: PurePosixPath, scope: PurePosixPath) -> bool:
    return resource == scope or scope == PurePosixPath(".") or scope in resource.parents


class PermissionPolicy:
    @staticmethod
    async def evaluate(
        db: AsyncSession,
        chat_session: ChatSession,
        request: PermissionRequest,
        root_files: RootFileService,
        policy: ToolExecutionPolicy | None = None,
    ) -> PermissionDecision:
        if request.resource_type == "session" and request.capability == "session.read":
            try:
                if UUID(request.resource_scope) == chat_session.id:
                    return (
                        PermissionDecision.DENY
                        if policy == ToolExecutionPolicy.DISABLED
                        else PermissionDecision.ALLOW
                    )
            except ValueError:
                return PermissionDecision.DENY

        if request.resource_type == "root_path":
            scope = _root_path_scope(request.resource_scope)
            if scope is None or root_files.session_id != chat_session.id:
                return PermissionDecision.DENY
            root, identity, _path = scope
            try:
                current_identity = root_files.root_identity(root)
            except RootFileError:
                return PermissionDecision.DENY
            if identity != current_identity:
                return PermissionDecision.DENY
            if root is FileRoot.SESSION and identity != str(chat_session.id):
                return PermissionDecision.DENY
            if root is FileRoot.WORKSPACE and (
                chat_session.working_directory is None
                or root_files.working_directory != chat_session.working_directory
            ):
                return PermissionDecision.DENY
            if request.capability not in {
                "filesystem.read",
                "filesystem.write",
                "filesystem.delete",
            }:
                return PermissionDecision.DENY
            if (
                request.capability in {"filesystem.write", "filesystem.delete"}
                and chat_session.workspace_access != "read-write"
            ):
                return PermissionDecision.DENY
            if policy == ToolExecutionPolicy.DISABLED:
                return PermissionDecision.DENY
            if policy in {None, ToolExecutionPolicy.AUTO}:
                return PermissionDecision.ALLOW

        elif request.capability.startswith("filesystem."):
            return PermissionDecision.DENY

        if policy == ToolExecutionPolicy.DISABLED:
            return PermissionDecision.DENY
        if policy == ToolExecutionPolicy.AUTO:
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
            if PermissionPolicy.grant_matches(grant, request, root_files):
                return PermissionDecision.ALLOW
        return PermissionDecision.ASK

    @staticmethod
    def grant_matches(
        grant: PermissionGrant,
        request: PermissionRequest,
        root_files: RootFileService,
    ) -> bool:
        if grant.capability != request.capability or grant.resource_type != request.resource_type:
            return False
        if request.resource_type == "root_path":
            resource = _root_path_scope(request.resource_scope)
            granted = _root_path_scope(grant.resource_scope)
            if resource is None or granted is None:
                return False
            resource_root, resource_identity, resource_path = resource
            grant_root, grant_identity, grant_path = granted
            try:
                current_identity = root_files.root_identity(resource_root)
            except RootFileError:
                return False
            return (
                resource_root is grant_root
                and resource_identity == grant_identity == current_identity
                and _path_is_within(resource_path, grant_path)
            )
        if request.resource_type == "session":
            try:
                return UUID(request.resource_scope) == UUID(grant.resource_scope)
            except ValueError:
                return False
        if request.resource_type == "session_collection":
            return request.resource_scope == grant.resource_scope == "all"
        if request.resource_type == "url":
            return request.resource_scope == grant.resource_scope
        if request.resource_type in {"network_service", "memory_collection"}:
            return request.resource_scope == grant.resource_scope
        if request.resource_type == "agent_identity":
            try:
                return UUID(request.resource_scope) == UUID(grant.resource_scope)
            except ValueError:
                return False
        return False
