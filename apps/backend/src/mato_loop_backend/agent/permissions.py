from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Any, Literal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.integrations import (
    canonicalize_url,
    canonicalize_youtube_video_url,
    sanitize_url_for_display,
)
from mato_loop_backend.models import ChatSession, ExecutionAgent, PermissionGrant
from mato_loop_backend.settings import (
    PLAN_AGENT_PROFILE_ID,
    ToolExecutionPolicy,
    ToolSettings,
    TrustLevel,
)
from mato_loop_backend.workspace import FileRoot, RootFileError, RootFileService

if TYPE_CHECKING:
    from mato_loop_backend.execution.client import ShellInspectResult


class PermissionDecision(StrEnum):
    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"
    USER_DENIED = "user_denied"


@dataclass(frozen=True, slots=True)
class PermissionDisplayItem:
    label: str
    value: str | tuple[str, ...]
    format: Literal["text", "code", "list"] = "text"


@dataclass(frozen=True, slots=True)
class SessionGrantScope:
    access: Literal["read", "write"]
    resource_type: str
    resource_scope: str
    resource_display: str | None = None

    @property
    def capability(self) -> str:
        return f"resource.{self.access}"


_READ_CAPABILITIES = frozenset({
    "automation.read", "browser.read", "coding.analyze", "coding.plan.read", "file.provide",
    "filesystem.read", "knowledge.read", "mail.read", "meal_plan.read",
    "media.analyze.video", "media.transcribe.audio",
    "mealie.accounts.read", "memory.read", "network.fetch", "network.search",
    "recipes.read", "session.files.read", "session.read", "session.search",
    "session.tool_result.read",
    "skills.load", "system.info", "tasks.read", "calendar.read",
})
_WRITE_CAPABILITIES = frozenset({
    "automation.create", "automation.delete", "automation.run", "automation.update",
    "browser.code", "browser.control", "browser.release", "browser.write",
    "cad.render", "calendar.delete", "calendar.write", "coding.execute.enqueue",
    "coding.plan.create", "filesystem.delete", "filesystem.write",
    "knowledge.delete", "knowledge.write", "mail.delete", "mail.drafts",
    "mail.send", "meal_plan.delete", "meal_plan.write", "memory.write",
    "media.edit.image", "media.generate.audio", "media.generate.image",
    "media.generate.speech",
    "session.files.write", "settings.agent_identity.update",
    "settings.user_profile.update", "skills.write", "tasks.delete", "tasks.write",
})


def _access_for_capability(capability: str) -> Literal["read", "write"] | None:
    if capability in _READ_CAPABILITIES or capability == "shell.read":
        return "read"
    if capability in _WRITE_CAPABILITIES or capability == "shell.write":
        return "write"
    return None


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
    use_integration_policy: bool = True
    policy_integration_id: UUID | None = None
    policy_resource_key: str | None = None
    policy_hard_unavailable_capabilities: frozenset[str] = frozenset()
    approval_details: dict[str, Any] | None = None
    display_items: tuple[PermissionDisplayItem, ...] = ()
    force_ask: bool = False
    available_decisions: tuple[str, ...] = (
        "allow_once",
        "allow_session",
        "deny",
    )
    # Derived from the current shell inspection, never from approval payloads or grants.
    shell_metadata_paths: frozenset[str] = frozenset()
    shared_command_id: UUID | None = None
    session_grants: tuple[SessionGrantScope, ...] = ()

    @property
    def grant_capabilities(self) -> tuple[str, ...]:
        grants = self.effective_session_grants
        return (
            tuple(dict.fromkeys(grant.capability for grant in grants))
            if grants else (self.capability,)
        )

    @property
    def effective_session_grants(self) -> tuple[SessionGrantScope, ...]:
        if self.session_grants:
            return self.session_grants
        access = _access_for_capability(self.capability)
        return (
            SessionGrantScope(
                access,
                self.resource_type,
                self.resource_scope,
                self.resource_display,
            ),
        ) if access is not None else ()


def session_grants_payload(request: PermissionRequest) -> list[dict[str, str | None]]:
    return [
        {
            "access": grant.access,
            "resourceType": grant.resource_type,
            "resourceScope": grant.resource_scope,
            "resourceDisplay": grant.resource_display,
        }
        for grant in request.effective_session_grants
    ]


def session_grants_fingerprint(
    capability: str,
    resource_type: str,
    resource_scope: str,
    scopes: list[dict[str, Any]],
) -> str:
    return hashlib.sha256(json.dumps(
        {
            "capability": capability,
            "resourceScope": resource_scope,
            "resourceType": resource_type,
            "sessionGrants": scopes,
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()).hexdigest()


def session_grants_display(request: PermissionRequest) -> list[str]:
    result = []
    for grant in request.effective_session_grants:
        subtree = grant.resource_type in {"execution_directory", "root_path"}
        if grant.resource_type == "execution_directory":
            target = "Datei- und Shell-Aktionen"
        elif grant.resource_type == "root_path":
            target = "Dateiaktionen"
        else:
            target = "Aktionen"
        action = f"Alle passenden {'lesenden' if grant.access == 'read' else 'schreibenden'} {target}"
        area = grant.resource_display or "Angezeigter Bereich"
        result.append(f"{action}: {area}{' und Unterordner' if subtree else ''}")
    return result


_INTEGRATION_OPERATION_KEY = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_INTEGRATION_RESOURCE_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,499}$")
_CONFIDENTIAL_PATH_PARTS = frozenset({".ssh", ".gnupg", ".aws", ".azure", ".kube"})
_CONFIDENTIAL_FILENAMES = frozenset({".env", ".netrc", ".npmrc", ".pypirc"})


def confidential_execution_path(path: str) -> bool:
    parsed = PurePosixPath(path)
    return bool(
        _CONFIDENTIAL_PATH_PARTS.intersection(parsed.parts)
        or parsed.name in _CONFIDENTIAL_FILENAMES
        or parsed.name.startswith(".env.")
    )


def integration_operation_scope(
    integration_id: UUID,
    binding_revision: str,
    operation_key: str,
    *,
    resource_key: str | None = None,
) -> str:
    if len(binding_revision) > 50_000:
        raise ValueError("binding revision is too large")
    try:
        revision_value = json.loads(
            binding_revision,
            parse_constant=lambda _value: None,
        )
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError("binding revision must be canonical JSON") from exc
    if not isinstance(revision_value, dict):
        raise ValueError("binding revision must be a canonical object")
    if json.dumps(
        revision_value,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ) != binding_revision:
        raise ValueError("binding revision must be canonical JSON")
    if _INTEGRATION_OPERATION_KEY.fullmatch(operation_key) is None:
        raise ValueError("operation key is invalid")
    if resource_key is not None and _INTEGRATION_RESOURCE_KEY.fullmatch(resource_key) is None:
        raise ValueError("resource key is invalid")
    return json.dumps(
        {
            "bindingRevision": hashlib.sha256(binding_revision.encode()).hexdigest(),
            "integrationId": str(integration_id),
            "operationKey": operation_key,
            **({"resourceKey": resource_key} if resource_key is not None else {}),
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


def integration_resource_scope(
    integration_id: UUID,
    binding_revision: str,
    resource_key: str = "account",
    capability: str | None = None,
) -> str:
    operation = json.loads(
        integration_operation_scope(
            integration_id,
            binding_revision,
            "resource",
            resource_key=resource_key,
        )
    )
    operation.pop("operationKey")
    if capability is not None:
        operation["capability"] = capability
    return json.dumps(
        operation, ensure_ascii=True, separators=(",", ":"), sort_keys=True
    )


def integration_operation_permission(
    *,
    capability: str,
    integration_id: UUID,
    binding_revision: str,
    operation_key: str,
    title: str,
    summary: str,
    resource_key: str | None = None,
    grant_resource_key: str | None = None,
    resource_display: str | None = None,
    use_integration_policy: bool = True,
    action_kind: str = "integration_operation",
    risk_level: str = "medium",
    display_items: tuple[PermissionDisplayItem, ...] = (),
    force_ask: bool = False,
    available_decisions: tuple[str, ...] = (
        "allow_once",
        "allow_session",
        "deny",
    ),
) -> PermissionRequest:
    access = _access_for_capability(capability)
    return PermissionRequest(
        capability=capability,
        resource_type="integration_operation",
        resource_scope=integration_operation_scope(
            integration_id,
            binding_revision,
            operation_key,
            resource_key=resource_key,
        ),
        action_kind=action_kind,
        risk_level=risk_level,
        title=title,
        summary=summary,
        resource_display=resource_display,
        use_integration_policy=use_integration_policy,
        policy_integration_id=integration_id if use_integration_policy else None,
        policy_resource_key=resource_key,
        display_items=display_items,
        force_ask=force_ask,
        available_decisions=available_decisions,
        session_grants=(
            SessionGrantScope(
                access,
                "integration_resource",
                integration_resource_scope(
                    integration_id,
                    binding_revision,
                    grant_resource_key or resource_key or "account",
                    capability,
                ),
                resource_display,
            ),
        ) if access is not None else (),
    )


def _integration_operation_scope(resource_scope: str) -> dict[str, str] | None:
    try:
        value = json.loads(resource_scope)
        if not isinstance(value, dict) or set(value) not in (
            {"bindingRevision", "integrationId", "operationKey"},
            {"bindingRevision", "integrationId", "operationKey", "resourceKey"},
        ):
            return None
        integration_id = UUID(value["integrationId"])
        if str(integration_id) != value["integrationId"]:
            return None
        if re.fullmatch(r"[0-9a-f]{64}", value["bindingRevision"]) is None:
            return None
        if _INTEGRATION_OPERATION_KEY.fullmatch(value["operationKey"]) is None:
            return None
        resource_key = value.get("resourceKey")
        if resource_key is not None and _INTEGRATION_RESOURCE_KEY.fullmatch(resource_key) is None:
            return None
        canonical = json.dumps(
            value,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        return value if canonical == resource_scope else None
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None


def _integration_resource_scope(resource_scope: str) -> dict[str, str] | None:
    try:
        value = json.loads(resource_scope)
        if not isinstance(value, dict) or set(value) != {
            "bindingRevision", "capability", "integrationId", "resourceKey"
        }:
            return None
        integration_id = UUID(value["integrationId"])
        if (
            str(integration_id) != value["integrationId"]
            or re.fullmatch(r"[0-9a-f]{64}", value["bindingRevision"]) is None
            or _INTEGRATION_RESOURCE_KEY.fullmatch(value["resourceKey"]) is None
            or _access_for_capability(value["capability"]) is None
        ):
            return None
        canonical = json.dumps(
            value, ensure_ascii=True, separators=(",", ":"), sort_keys=True
        )
        return value if canonical == resource_scope else None
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None


def path_permission(
    *,
    root_files: RootFileService,
    root: FileRoot | str,
    capability: str,
    relative_path: str,
    action_kind: str,
    action_label: str,
    grant_path: str | None = None,
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
    grant_scope = scope if grant_path is None else json.dumps(
        {
            "identity": root_identity,
            "path": root_files.normalize_relative_path(grant_path),
            "root": selected_root.value,
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    access = _access_for_capability(capability)
    return PermissionRequest(
        capability=(
            "session.files.read"
            if selected_root == FileRoot.SESSION and capability == "filesystem.read"
            else capability
        ),
        resource_type="root_path",
        resource_scope=scope,
        action_kind=action_kind,
        risk_level="high" if capability == "filesystem.delete" else "medium",
        title=f"{action_label} erlauben?",
        summary=(
            f"Der Agent möchte {selected_root.value}:{normalized_path} verwenden."
        ),
        resource_display=f"{selected_root.value}:{normalized_path}",
        session_grants=(
            SessionGrantScope(
                access,
                "root_path",
                grant_scope,
                f"{selected_root.value}:{root_files.normalize_relative_path(grant_path)}",
            ),
        ) if access is not None and grant_path is not None else (),
    )


def project_session_path_permission(
    *,
    project_id: UUID,
    session_id: UUID,
    relative_path: str,
    action_kind: str,
    action_label: str,
    capability: str = "session.files.read",
) -> PermissionRequest:
    scope = json.dumps(
        {
            "path": RootFileService(UUID(int=0)).normalize_relative_path(relative_path),
            "projectId": str(project_id),
            "sessionId": str(session_id),
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return PermissionRequest(
        capability=capability,
        resource_type="project_session_path",
        resource_scope=scope,
        action_kind=action_kind,
        risk_level="medium",
        title=f"{action_label} erlauben?",
        summary="Der Agent möchte Dateien einer Session im aktuellen Projekt lesen.",
        resource_display=f"Projekt-Session:{relative_path}",
    )


def _project_session_path_scope(
    resource_scope: str,
) -> tuple[UUID, UUID, str] | None:
    try:
        value = json.loads(resource_scope)
        if not isinstance(value, dict) or set(value) != {
            "path",
            "projectId",
            "sessionId",
        }:
            return None
        project_id = UUID(value["projectId"])
        session_id = UUID(value["sessionId"])
        path = RootFileService(UUID(int=0)).normalize_relative_path(value["path"])
        canonical = json.dumps(
            value,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        if (
            canonical != resource_scope
            or str(project_id) != value["projectId"]
            or str(session_id) != value["sessionId"]
            or path != value["path"]
        ):
            return None
        return project_id, session_id, path
    except (KeyError, TypeError, ValueError, json.JSONDecodeError, RootFileError):
        return None


def execution_directory_scope(agent_id: UUID, path: str) -> str:
    parsed = PurePosixPath(path)
    if (
        not parsed.is_absolute()
        or str(parsed) != path
        or "\\" in path
        or (path != "/" and any(part in {"", ".", ".."} for part in path.split("/")[1:]))
        or any(ord(character) < 32 or 127 <= ord(character) <= 159 for character in path)
    ):
        raise ValueError("invalid execution directory")
    return json.dumps(
        {
            "agentId": str(agent_id),
            "path": path,
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


def execution_directory_permission(
    agent_id: UUID,
    path: str,
    *,
    capability: str = "execution.directory",
    action_kind: str = "change_directory",
    title: str = "Verzeichnis verwenden?",
    summary: str | None = None,
    grant_path: str | None = None,
) -> PermissionRequest:
    access = _access_for_capability(capability)
    return PermissionRequest(
        capability=capability,
        resource_type="execution_directory",
        resource_scope=execution_directory_scope(agent_id, path),
        action_kind=action_kind,
        risk_level="medium",
        title=title,
        summary=summary or f"Der Agent möchte das Verzeichnis {path} verwenden.",
        resource_display=path,
        use_integration_policy=False,
        available_decisions=(
            ("allow_once", "allow_session", "deny")
            if access is not None
            and (capability == "file.provide" or not confidential_execution_path(path))
            else ("allow_once", "deny")
        ),
        session_grants=(
            SessionGrantScope(
                access,
                "execution_directory",
                execution_directory_scope(agent_id, grant_path),
                grant_path,
            ),
        ) if access is not None and grant_path is not None else (),
    )


def system_info_permission(agent_id: UUID) -> PermissionRequest:
    return PermissionRequest(
        capability="system.info",
        resource_type="execution_agent",
        resource_scope=str(agent_id),
        action_kind="get_system_info",
        risk_level="low",
        title="Systeminformationen lesen?",
        summary="Der Agent möchte grundlegende Informationen des ausgewählten Computers lesen.",
        resource_display="Ausgewählter Computer",
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


def automation_collection_scope(user_id: UUID, session_id: UUID) -> str:
    return json.dumps(
        {"sessionId": str(session_id), "userId": str(user_id)},
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


def automation_scope(
    automation_id: UUID, revision: int, user_id: UUID, session_id: UUID
) -> str:
    if revision < 1:
        raise ValueError("automation revision must be positive")
    return json.dumps(
        {
            "automationId": str(automation_id),
            "revision": revision,
            "sessionId": str(session_id),
            "userId": str(user_id),
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


def _automation_collection_scope(resource_scope: str) -> tuple[UUID, UUID] | None:
    try:
        value = json.loads(resource_scope)
        if set(value) != {"sessionId", "userId"}:
            return None
        session_id = UUID(value["sessionId"])
        user_id = UUID(value["userId"])
        canonical = automation_collection_scope(user_id, session_id)
        return (user_id, session_id) if canonical == resource_scope else None
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None


def _automation_scope(
    resource_scope: str,
) -> tuple[UUID, int, UUID, UUID] | None:
    try:
        value = json.loads(resource_scope)
        if set(value) != {"automationId", "revision", "sessionId", "userId"}:
            return None
        automation_id = UUID(value["automationId"])
        revision = value["revision"]
        session_id = UUID(value["sessionId"])
        user_id = UUID(value["userId"])
        if isinstance(revision, bool) or not isinstance(revision, int):
            return None
        canonical = automation_scope(automation_id, revision, user_id, session_id)
        return (
            (automation_id, revision, user_id, session_id)
            if canonical == resource_scope
            else None
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None


def memory_write_permission(
    session_id: UUID, action: str, *, pinned: bool = False
) -> PermissionRequest:
    updating = action == "update"
    forgetting = action == "forget"
    return PermissionRequest(
        capability="memory.write",
        resource_type="memory_collection" if forgetting else "session",
        resource_scope=f"forget:{session_id}" if forgetting else str(session_id),
        action_kind="memory_write",
        risk_level="medium",
        title=(
            "Memory vergessen?"
            if forgetting
            else "Memory aktualisieren?"
            if updating
            else "Memory speichern?"
        ),
        summary=(
            "Der Agent möchte ein bestehendes Memory endgültig löschen."
            if forgetting
            else "Der Agent möchte ein bestehendes Memory aktualisieren."
            if updating
            else "Der Agent möchte ein neues Memory speichern."
        ),
        resource_display="Memory der aktuellen Session",
        display_items=(
            (PermissionDisplayItem("Berücksichtigung", "Immer"),) if pinned else ()
        ),
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


def transcript_permission(url: str) -> PermissionRequest:
    canonical_url = canonicalize_youtube_video_url(url)
    return PermissionRequest(
        capability="network.fetch",
        resource_type="url",
        resource_scope=hashlib.sha256(canonical_url.encode("utf-8")).hexdigest(),
        action_kind="fetch_transcript",
        risk_level="medium",
        title="Untertitel abrufen?",
        summary=f"Der Agent möchte veröffentlichte Untertitel von {canonical_url} abrufen.",
        resource_display=canonical_url,
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


def media_permission(
    session_id: UUID,
    capability: str,
    tool_name: str,
    *,
    provider_id: UUID | None = None,
    model_id: UUID | None = None,
    model_name: str | None = None,
    trust_level: str | None = None,
    source_attachment_ids: tuple[UUID, ...] = (),
) -> PermissionRequest:
    labels = {
        "media.generate.image": ("Bild erzeugen?", "ein Bild erzeugen"),
        "media.edit.image": ("Bild bearbeiten?", "Bilder bearbeiten"),
        "media.generate.speech": ("Sprache erzeugen?", "eine Sprachaufnahme erzeugen"),
        "media.generate.audio": ("Audio erzeugen?", "eine Audiodatei erzeugen"),
        "media.transcribe.audio": ("Audio transkribieren?", "einen Audioanhang transkribieren"),
        "media.analyze.video": ("Video analysieren?", "ein Video analysieren"),
    }
    title, action = labels.get(capability, ("Medienaktion erlauben?", "Medien verarbeiten"))
    targeted = provider_id is not None and model_id is not None
    return PermissionRequest(
        capability=capability,
        resource_type="media_model" if targeted else "session",
        resource_scope=(
            json.dumps(
                {
                    "sessionId": str(session_id),
                    "providerId": str(provider_id),
                    "modelId": str(model_id),
                    "trustLevel": trust_level,
                    **(
                        {
                            "sourceAttachmentIds": sorted(
                                str(attachment_id)
                                for attachment_id in source_attachment_ids
                            )
                        }
                        if source_attachment_ids
                        else {}
                    ),
                },
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            )
            if targeted
            else str(session_id)
        ),
        action_kind=tool_name,
        risk_level="medium",
        title=title,
        summary=(
            f"Der Agent möchte mit {model_name} {action}."
            if targeted and model_name
            else f"Der Agent möchte in der aktuellen Session {action}."
        ),
        resource_display=model_name if targeted else None,
    )


def agent_identity_permission(
    user_id: UUID,
    *,
    changes_name: bool,
    changes_instructions: bool,
    persona_name: str | None = None,
) -> PermissionRequest:
    if changes_name and changes_instructions:
        summary = f"Der Agent möchte seinen Namen und die Persona „{persona_name}“ ändern."
    elif changes_name:
        summary = "Der Agent möchte seinen Namen ändern."
    else:
        summary = f"Der Agent möchte die Persona „{persona_name}“ ändern."
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


def user_profile_permission(
    user_id: UUID, *, field_labels: list[str], model_scope: str
) -> PermissionRequest:
    scopes = {
        "private_models": "private Modelle",
        "public_models": "öffentliche Modelle",
        "both_models": "private und öffentliche Modelle",
    }
    fields = ", ".join(field_labels)
    return PermissionRequest(
        capability="settings.user_profile.update",
        resource_type="user_profile",
        resource_scope=json.dumps(
            {"modelScope": model_scope, "userId": str(user_id)},
            separators=(",", ":"), sort_keys=True,
        ),
        action_kind="update_user_profile",
        risk_level="medium",
        title="Persönliches Profil ändern?",
        summary=(
            f"Der Agent möchte {fields} im persönlichen Profil für "
            f"{scopes[model_scope]} ändern."
        ),
        resource_display=f"Eigenes persönliches Profil ({scopes[model_scope]})",
    )


def _user_profile_scope(resource_scope: str) -> tuple[UUID, str] | None:
    try:
        value = json.loads(resource_scope)
        if not isinstance(value, dict) or set(value) != {"modelScope", "userId"}:
            return None
        if not isinstance(value["userId"], str) or value["modelScope"] not in (
            "private_models", "public_models", "both_models"
        ):
            return None
        user_id = UUID(value["userId"])
        if str(user_id) != value["userId"] or json.dumps(
            value, separators=(",", ":"), sort_keys=True
        ) != resource_scope:
            return None
        return user_id, value["modelScope"]
    except (TypeError, ValueError):
        return None


def skill_load_permission(user_id: UUID, skill_name: str) -> PermissionRequest:
    return PermissionRequest(
        capability="skills.load",
        resource_type="skill_collection",
        resource_scope=str(user_id),
        action_kind="load_skill",
        risk_level="low",
        title="Skill laden?",
        summary=f'Der Agent möchte den persönlichen Skill „{skill_name}“ verwenden.',
        resource_display=skill_name,
    )


def skill_write_permission(
    user_id: UUID,
    skill_name: str,
    *,
    approval_details: dict[str, Any] | None = None,
) -> PermissionRequest:
    return PermissionRequest(
        capability="skills.write",
        resource_type="skill_collection",
        resource_scope=str(user_id),
        action_kind="build_skill",
        risk_level="medium",
        title="Skill speichern?",
        summary=f'Der Agent möchte den persönlichen Skill „{skill_name}“ speichern.',
        resource_display=skill_name,
        approval_details=approval_details,
    )


def skill_edit_permission(
    skill_id: UUID,
    skill_name: str,
    *,
    approval_details: dict[str, Any] | None = None,
) -> PermissionRequest:
    return PermissionRequest(
        capability="skills.write",
        resource_type="skill",
        resource_scope=str(skill_id),
        action_kind="edit_skill",
        risk_level="medium",
        title="Skill ändern?",
        summary=f'Der Agent möchte den persönlichen Skill „{skill_name}“ ändern.',
        resource_display=skill_name,
        approval_details=approval_details,
    )


def _root_path_scope(resource_scope: str) -> tuple[FileRoot, str, PurePosixPath] | None:
    try:
        value = json.loads(resource_scope)
        if not isinstance(value, dict) or set(value) != {"identity", "path", "root"}:
            return None
        root = FileRoot(value["root"])
        if root is not FileRoot.SESSION:
            return None
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
        if json.dumps(
            value, ensure_ascii=True, separators=(",", ":"), sort_keys=True
        ) != resource_scope:
            return None
        return root, identity, parsed
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None


def _execution_directory_scope(
    resource_scope: str,
) -> tuple[UUID, PurePosixPath] | None:
    try:
        value = json.loads(resource_scope)
        if set(value) != {"agentId", "path"}:
            return None
        agent_id = UUID(value["agentId"])
        path = value["path"]
        canonical = execution_directory_scope(agent_id, path)
        return (
            agent_id,
            PurePosixPath(path),
        ) if canonical == resource_scope else None
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None


def _path_is_within(resource: PurePosixPath, scope: PurePosixPath) -> bool:
    return resource == scope or scope == PurePosixPath(".") or scope in resource.parents


def shell_command_scope(
    agent_id: UUID,
    current_directory: str,
    classification: Literal["read", "write"],
    prefixes: list[list[str]],
    paths: list[str],
    analysis_complete: bool,
    target_accesses: list[Literal["read", "write", "exempt"] | None] | None = None,
) -> str:
    return json.dumps(
        {
            "agentId": str(agent_id),
            "analysisComplete": analysis_complete,
            "classification": classification,
            "currentDirectory": current_directory,
            "paths": paths,
            "prefixes": prefixes,
            "targetAccesses": target_accesses if target_accesses is not None else [None] * len(paths),
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


def shell_command_permission(
    agent_id: UUID, current_directory: str, command_line: str, inspection: ShellInspectResult,
) -> PermissionRequest:
    prefixes = (
        [command.prefix for command in inspection.commands if command.prefix is not None]
        if inspection.commands
        and all(command.prefix is not None for command in inspection.commands)
        else []
    )
    metadata_kinds = {"toolchain_metadata", "discard"}
    metadata_paths = frozenset(
        target.path for target in inspection.targets if target.kind in metadata_kinds
    ) - {
        target.path for target in inspection.targets if target.kind not in metadata_kinds
    }
    grant_paths = list(dict.fromkeys([
        (inspection.classification, current_directory),
        *(
            (
                target.access,
                str(PurePosixPath(target.path).parent)
                if target.kind in {"input", "output", "source", "script", "executable", "path"}
                else target.path,
            )
            for target in inspection.targets
            if target.path not in metadata_paths
        ),
    ]))
    reusable = all(
        target.access is not None
        for target in inspection.targets
        if target.kind not in metadata_kinds
    )
    return PermissionRequest(
        capability=f"shell.{inspection.classification}",
        resource_type="shell_command",
        resource_scope=shell_command_scope(
            agent_id, current_directory, inspection.classification, prefixes,
            [target.path for target in inspection.targets], inspection.analysis_complete,
            [
                "exempt" if target.kind in metadata_kinds else target.access
                for target in inspection.targets
            ],
        ),
        action_kind="shell_run",
        risk_level="low" if inspection.classification == "read" else "high",
        title=(
            "Shell-Befehl ohne erkannte Schreibaktion ausführen?"
            if inspection.classification == "read"
            else "Als schreibend eingestuften Shell-Befehl ausführen?"
        ),
        summary=(
            "Es wurden keine bekannten Schreibaktionen erkannt. Der Befehl kann dennoch Nebenwirkungen haben."
            if inspection.classification == "read"
            else "Der Befehl wurde als schreibend eingestuft und kann Dateien verändern, Programme starten und Netzwerkdienste verwenden."
        ) + (
            " Die Analyse ist unvollständig; nicht alle Befehle und Pfade konnten geprüft werden."
            if not inspection.analysis_complete else ""
        ),
        resource_display=current_directory,
        shell_metadata_paths=metadata_paths,
        session_grants=tuple(
            SessionGrantScope(
                access,
                "execution_directory",
                execution_directory_scope(agent_id, path),
                path,
            )
            for access, path in grant_paths
            if access is not None
        ),
        approval_details={
            "shellClassification": inspection.classification,
            "shellAnalysisComplete": inspection.analysis_complete,
            "shellPrefixes": prefixes,
        },
        display_items=(PermissionDisplayItem("Shell-Befehl", command_line, "code"),),
        available_decisions=(
            ("allow_once", "allow_session", "deny")
            if reusable and not any(
                confidential_execution_path(path)
                for path in [current_directory, *(target.path for target in inspection.targets)]
            )
            else ("allow_once", "deny")
        ),
    )


def _shell_command_scope(resource_scope: str) -> dict[str, Any] | None:
    try:
        value = json.loads(resource_scope)
        if set(value) != {
            "agentId",
            "analysisComplete",
            "classification",
            "currentDirectory",
            "paths",
            "prefixes",
            "targetAccesses",
        }:
            return None
        agent_id = UUID(value["agentId"])
        current_directory = value["currentDirectory"]
        classification = value["classification"]
        prefixes = value["prefixes"]
        paths = value["paths"]
        target_accesses = value["targetAccesses"]
        analysis_complete = value["analysisComplete"]
        if (
            not isinstance(current_directory, str)
            or not PurePosixPath(current_directory).is_absolute()
            or PurePosixPath(current_directory).as_posix() != current_directory
            or classification not in {"read", "write"}
            or not isinstance(analysis_complete, bool)
            or not isinstance(prefixes, list)
            or len(prefixes) > 64
            or any(
                not isinstance(prefix, list)
                or not 1 <= len(prefix) <= 8
                or any(
                    not isinstance(token, str)
                    or not token
                    or len(token) > 256
                    or any(
                        ord(character) < 32 or 127 <= ord(character) <= 159
                        for character in token
                    )
                    for token in prefix
                )
                for prefix in prefixes
            )
            or not isinstance(paths, list)
            or len(paths) > 256
            or not isinstance(target_accesses, list)
            or len(target_accesses) != len(paths)
            or any(access not in {None, "read", "write", "exempt"} for access in target_accesses)
            or any(
                not isinstance(path, str)
                or not PurePosixPath(path).is_absolute()
                or PurePosixPath(path).as_posix() != path
                for path in paths
            )
        ):
            return None
        canonical = shell_command_scope(
            agent_id,
            current_directory,
            classification,
            prefixes,
            paths,
            analysis_complete,
            target_accesses,
        )
        return value if canonical == resource_scope else None
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None


def shell_prefix_allowed(settings: ToolSettings, request: PermissionRequest) -> bool:
    scope = _shell_command_scope(request.resource_scope)
    if scope is None:
        return False
    prefixes = scope["prefixes"]
    allowed = getattr(settings.shell_prefixes, scope["classification"])
    return bool(prefixes) and all(
        any(prefix[: len(candidate)] == candidate for candidate in allowed)
        for prefix in prefixes
    )


def session_grant_matches_request(
    capability: str,
    resource_type: str,
    resource_scope: str,
    grant: SessionGrantScope,
) -> bool:
    if resource_type != "shell_command" and grant.access != _access_for_capability(capability):
        return False
    if resource_type == "root_path":
        requested = _root_path_scope(resource_scope)
        granted = _root_path_scope(grant.resource_scope)
        return bool(
            grant.resource_type == "root_path"
            and requested
            and granted
            and requested[:2] == granted[:2]
            and _path_is_within(requested[2], granted[2])
        )
    if resource_type == "execution_directory":
        requested = _execution_directory_scope(resource_scope)
        granted = _execution_directory_scope(grant.resource_scope)
        return bool(
            grant.resource_type == "execution_directory"
            and requested
            and granted
            and requested[0] == granted[0]
            and _path_is_within(requested[1], granted[1])
        )
    if resource_type == "shell_command":
        requested = _shell_command_scope(resource_scope)
        granted = _execution_directory_scope(grant.resource_scope)
        if grant.resource_type != "execution_directory" or requested is None or granted is None:
            return False
        if None in requested["targetAccesses"]:
            return False
        paths = {
            (requested["classification"], PurePosixPath(requested["currentDirectory"]))
        }
        for path, access in zip(requested["paths"], requested["targetAccesses"], strict=True):
            if access == "exempt":
                continue
            parsed = PurePosixPath(path)
            paths.update(((access, parsed), (access, parsed.parent)))
        return (
            str(granted[0]) == requested["agentId"]
            and (grant.access, granted[1]) in paths
        )
    if resource_type == "integration_operation":
        requested = _integration_operation_scope(resource_scope)
        granted = _integration_resource_scope(grant.resource_scope)
        return bool(
            grant.resource_type == "integration_resource"
            and requested
            and granted
            and requested["integrationId"] == granted["integrationId"]
            and requested["bindingRevision"] == granted["bindingRevision"]
            and capability == granted["capability"]
        )
    if resource_type == "project":
        try:
            expected = json.dumps(
                {"capability": capability, "projectId": str(UUID(resource_scope))},
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            )
        except ValueError:
            return False
        return grant.resource_type == "project" and grant.resource_scope == expected
    return grant.resource_type == resource_type and grant.resource_scope == resource_scope


class PermissionPolicy:
    @staticmethod
    async def evaluate(
        db: AsyncSession,
        chat_session: ChatSession,
        request: PermissionRequest,
        root_files: RootFileService,
        policy: ToolExecutionPolicy | None = None,
        *,
        agent_profile_id: UUID | None = None,
        trust_level: TrustLevel | None = None,
        permission_session_id: UUID | None = None,
    ) -> PermissionDecision:
        grant_session_id = permission_session_id or chat_session.id
        if grant_session_id != chat_session.id:
            if chat_session.parent_session_id != grant_session_id:
                return PermissionDecision.DENY
            parent = await db.get(ChatSession, grant_session_id, populate_existing=True)
            if parent is None or parent.user_id != chat_session.user_id:
                return PermissionDecision.DENY

        def grant_binding_conditions() -> tuple[Any, ...]:
            if trust_level is None:
                return ()
            profile_condition = (
                PermissionGrant.agent_profile_id.is_(None)
                if agent_profile_id is None
                else PermissionGrant.agent_profile_id == agent_profile_id
            )
            return (profile_condition, PermissionGrant.trust_level == trust_level.value)

        if request.resource_type == "user_profile":
            scope = _user_profile_scope(request.resource_scope)
            if (
                scope is None
                or scope[0] != chat_session.user_id
                or request.capability != "settings.user_profile.update"
            ):
                return PermissionDecision.DENY

        if request.resource_type in {"automation_collection", "automation"}:
            scope = (
                _automation_collection_scope(request.resource_scope)
                if request.resource_type == "automation_collection"
                else _automation_scope(request.resource_scope)
            )
            user_id, session_id = (
                scope if request.resource_type == "automation_collection" else scope[2:]
            ) if scope is not None else (None, None)
            valid_capability = request.capability in (
                {"automation.read", "automation.create"}
                if request.resource_type == "automation_collection"
                else {
                    "automation.read",
                    "automation.update",
                    "automation.run",
                    "automation.delete",
                }
            )
            if (
                not valid_capability
                or user_id != chat_session.user_id
                or session_id != chat_session.id
            ):
                return PermissionDecision.DENY

        if request.resource_type == "session" and request.capability == "session.read":
            try:
                target_id = UUID(request.resource_scope)
                target = (
                    chat_session
                    if target_id == chat_session.id
                    else await db.get(ChatSession, target_id)
                )
                if target is None or target.user_id != chat_session.user_id or (
                    target is not chat_session
                    and target.parent_session_id is not None
                    and target.parent_session_id != chat_session.id
                ):
                    return PermissionDecision.DENY
                if target is chat_session or (
                    target.parent_session_id == chat_session.id
                    or (
                        chat_session.project_id is not None
                        and target.project_id == chat_session.project_id
                        and target.session_type == "chat"
                    )
                ):
                    if policy == ToolExecutionPolicy.DISABLED:
                        return PermissionDecision.DENY
                    if policy == ToolExecutionPolicy.AUTO:
                        return PermissionDecision.ALLOW
            except ValueError:
                return PermissionDecision.DENY

        if request.resource_type == "session_collection":
            if request.resource_scope != "all":
                return PermissionDecision.DENY

        if request.resource_type == "project":
            try:
                project_id = UUID(request.resource_scope)
            except ValueError:
                return PermissionDecision.DENY
            if (
                project_id != chat_session.project_id
                or not request.capability.startswith("coding.")
            ):
                return PermissionDecision.DENY

        if request.resource_type == "project_session_path":
            scope = _project_session_path_scope(request.resource_scope)
            if scope is None or request.capability not in {
                "file.provide", "filesystem.read", "session.files.read"
            }:
                return PermissionDecision.DENY
            project_id, target_id, _path = scope
            target = await db.get(ChatSession, target_id)
            if (
                chat_session.project_id is None
                or project_id != chat_session.project_id
                or target is None
                or target.user_id != chat_session.user_id
                or target.project_id != project_id
                or target.session_type != "chat"
                or (target.parent_session_id is not None and target.parent_session_id != chat_session.id)
            ):
                return PermissionDecision.DENY
            if policy == ToolExecutionPolicy.AUTO:
                return PermissionDecision.ALLOW

        if request.resource_type == "execution_directory":
            scope = _execution_directory_scope(request.resource_scope)
            if scope is None:
                return PermissionDecision.DENY
            agent = await db.scalar(
                select(ExecutionAgent).where(
                    ExecutionAgent.id == scope[0],
                    ExecutionAgent.user_id == chat_session.user_id,
                    ExecutionAgent.revoked_at.is_(None),
                )
            )
            if agent is None:
                return PermissionDecision.DENY
            if policy == ToolExecutionPolicy.DISABLED:
                return PermissionDecision.DENY
            path = scope[1]
            home = PurePosixPath(agent.home_directory) if agent.home_directory else None
            selected_policy = (
                agent.home_filesystem_policy
                if home is not None and _path_is_within(path, home)
                else agent.external_filesystem_policy
            )
            if selected_policy == "deny":
                return PermissionDecision.DENY
            workspace = (
                PurePosixPath(chat_session.current_directory)
                if chat_session.current_directory is not None
                else None
            )
            inside_workspace = workspace is not None and _path_is_within(path, workspace)
            if request.capability != "file.provide" and confidential_execution_path(str(path)):
                return PermissionDecision.ASK
            if policy == ToolExecutionPolicy.AUTO and inside_workspace:
                return PermissionDecision.ALLOW
            if workspace is None and selected_policy == "auto":
                return PermissionDecision.ALLOW
            grants = list(
                (
                    await db.scalars(
                        select(PermissionGrant).where(
                            PermissionGrant.session_id == grant_session_id,
                            PermissionGrant.capability.in_(request.grant_capabilities),
                            *grant_binding_conditions(),
                        )
                    )
                ).all()
            )
            return (
                PermissionDecision.ALLOW
                if any(PermissionPolicy.grant_matches(item, request, root_files) for item in grants)
                else PermissionDecision.ASK
            )

        if request.resource_type == "shell_command":
            scope = _shell_command_scope(request.resource_scope)
            if (
                scope is None
                or request.capability != f"shell.{scope['classification']}"
                or not request.shell_metadata_paths.issubset(scope["paths"])
            ):
                return PermissionDecision.DENY
            agent = await db.scalar(
                select(ExecutionAgent)
                .where(
                    ExecutionAgent.id == UUID(scope["agentId"]),
                    ExecutionAgent.user_id == chat_session.user_id,
                    ExecutionAgent.revoked_at.is_(None),
                )
                .execution_options(populate_existing=True)
            )
            if agent is None:
                return PermissionDecision.DENY
            home = PurePosixPath(agent.home_directory) if agent.home_directory else None
            paths = [scope["currentDirectory"], *scope["paths"]]
            zones = [
                "home"
                if home is not None and _path_is_within(PurePosixPath(path), home)
                else "external"
                for path in paths
            ]
            if not scope["analysisComplete"]:
                zones.append("external")
            if any(not getattr(agent, f"{zone}_shell_enabled") for zone in zones):
                return PermissionDecision.DENY
            filesystem_policies = [
                getattr(agent, f"{zone}_filesystem_policy") for zone in zones
            ]
            if policy == ToolExecutionPolicy.DISABLED or "deny" in filesystem_policies:
                return PermissionDecision.DENY
            workspace = (
                PurePosixPath(chat_session.current_directory)
                if chat_session.current_directory is not None else None
            )
            target_paths = [PurePosixPath(path) for path in paths]
            # Verified metadata-only targets still participate in every hard path check.
            inside_workspace = workspace is not None and all(
                _path_is_within(path, workspace)
                for path in target_paths
                if str(path) not in request.shell_metadata_paths
            )
            confidential = any(
                confidential_execution_path(str(path)) for path in target_paths
            )
            if confidential:
                return PermissionDecision.ASK
            if policy == ToolExecutionPolicy.AUTO and inside_workspace:
                return PermissionDecision.ALLOW
            if None in scope["targetAccesses"]:
                return PermissionDecision.ASK
            grants = list(
                (
                    await db.scalars(
                        select(PermissionGrant).where(
                            PermissionGrant.session_id == grant_session_id,
                            PermissionGrant.capability.in_(request.grant_capabilities),
                            *grant_binding_conditions(),
                        )
                    )
                ).all()
            )
            required = [
                item for item in request.effective_session_grants
                if not (
                    policy == ToolExecutionPolicy.AUTO
                    and workspace is not None
                    and item.resource_type == "execution_directory"
                    and (item_scope := _execution_directory_scope(item.resource_scope)) is not None
                    and _path_is_within(item_scope[1], workspace)
                )
            ]
            return PermissionDecision.ALLOW if required and all(
                any(PermissionPolicy.scope_matches(grant, item, root_files) for grant in grants)
                for item in required
            ) else PermissionDecision.ASK

        if request.resource_type == "root_path":
            scope = _root_path_scope(request.resource_scope)
            if scope is None or root_files.session_id != chat_session.id:
                return PermissionDecision.DENY
            root, identity, path = scope
            try:
                current_identity = root_files.root_identity(root)
            except RootFileError:
                return PermissionDecision.DENY
            if identity != current_identity:
                return PermissionDecision.DENY
            if identity != str(chat_session.id):
                return PermissionDecision.DENY
            if request.capability not in {
                "filesystem.read",
                "filesystem.write",
                "filesystem.delete",
                "file.provide",
                "session.files.read",
                "session.files.write",
            }:
                return PermissionDecision.DENY
            if policy == ToolExecutionPolicy.DISABLED:
                return PermissionDecision.DENY
            if request.capability == "filesystem.delete":
                policy = ToolExecutionPolicy.ASK
            if (
                agent_profile_id == PLAN_AGENT_PROFILE_ID
                and request.capability == "session.files.write"
                and (
                    root != FileRoot.SESSION
                    or path != PurePosixPath("plan.md")
                )
            ):
                policy = ToolExecutionPolicy.DISABLED
            if policy in {None, ToolExecutionPolicy.AUTO}:
                return PermissionDecision.ALLOW

        elif request.resource_type != "project_session_path" and request.capability.startswith(
            ("filesystem.", "session.files.")
        ):
            return PermissionDecision.DENY

        if policy == ToolExecutionPolicy.DISABLED:
            return PermissionDecision.DENY
        if request.force_ask:
            return PermissionDecision.ASK
        if policy == ToolExecutionPolicy.AUTO:
            return PermissionDecision.ALLOW

        grants = list(
            (
                await db.scalars(
                        select(PermissionGrant).where(
                            PermissionGrant.session_id == grant_session_id,
                            PermissionGrant.capability.in_(request.grant_capabilities),
                            *grant_binding_conditions(),
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
        if grant.capability not in request.grant_capabilities:
            return False
        if grant.capability in {"resource.read", "resource.write"}:
            return any(
                PermissionPolicy.scope_matches(grant, scope, root_files)
                for scope in request.effective_session_grants
            )
        if grant.resource_type != request.resource_type:
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
        if request.resource_type == "execution_directory":
            resource = _execution_directory_scope(request.resource_scope)
            granted = _execution_directory_scope(grant.resource_scope)
            return bool(
                resource
                and granted
                and resource[0] == granted[0]
                and _path_is_within(resource[1], granted[1])
            )
        if request.resource_type == "session":
            try:
                return UUID(request.resource_scope) == UUID(grant.resource_scope)
            except ValueError:
                return False
        if request.resource_type == "execution_agent":
            try:
                return UUID(request.resource_scope) == UUID(grant.resource_scope)
            except ValueError:
                return False
        if request.resource_type == "session_collection":
            return request.resource_scope == grant.resource_scope == "all"
        if request.resource_type == "project":
            try:
                return UUID(request.resource_scope) == UUID(grant.resource_scope)
            except ValueError:
                return False
        if request.resource_type == "project_session_path":
            return (
                _project_session_path_scope(request.resource_scope) is not None
                and request.resource_scope == grant.resource_scope
            )
        if request.resource_type == "user_profile":
            return (
                _user_profile_scope(request.resource_scope) is not None
                and request.resource_scope == grant.resource_scope
            )
        if request.resource_type == "url":
            return request.resource_scope == grant.resource_scope
        if request.resource_type == "shell_command":
            resource = _shell_command_scope(request.resource_scope)
            granted = _shell_command_scope(grant.resource_scope)
            return bool(
                resource
                and granted
                and resource["agentId"] == granted["agentId"]
                and resource["currentDirectory"] == granted["currentDirectory"]
                and resource["classification"] == granted["classification"]
                and resource["prefixes"]
                and all(
                    any(
                        prefix[: len(granted_prefix)] == granted_prefix
                        for granted_prefix in granted["prefixes"]
                    )
                    for prefix in resource["prefixes"]
                )
            )
        if request.resource_type == "integration_operation":
            return (
                _integration_operation_scope(request.resource_scope) is not None
                and _integration_operation_scope(grant.resource_scope) is not None
                and request.resource_scope == grant.resource_scope
            )
        if request.resource_type == "automation_collection":
            return (
                _automation_collection_scope(request.resource_scope) is not None
                and request.resource_scope == grant.resource_scope
            )
        if request.resource_type == "automation":
            return (
                _automation_scope(request.resource_scope) is not None
                and request.resource_scope == grant.resource_scope
            )
        if request.resource_type in {
            "network_service",
            "memory_collection",
            "media_model",
        }:
            return request.resource_scope == grant.resource_scope
        if request.resource_type in {"agent_identity", "skill"}:
            try:
                return UUID(request.resource_scope) == UUID(grant.resource_scope)
            except ValueError:
                return False
        if request.resource_type == "skill_collection":
            try:
                return UUID(request.resource_scope) == UUID(grant.resource_scope)
            except ValueError:
                return False
        return False

    @staticmethod
    def scope_matches(
        grant: PermissionGrant,
        requested: SessionGrantScope,
        root_files: RootFileService,
    ) -> bool:
        if (
            grant.capability != requested.capability
            or grant.resource_type != requested.resource_type
        ):
            return False
        if requested.resource_type == "root_path":
            resource = _root_path_scope(requested.resource_scope)
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
        if requested.resource_type == "execution_directory":
            resource = _execution_directory_scope(requested.resource_scope)
            granted = _execution_directory_scope(grant.resource_scope)
            return bool(
                resource and granted and resource[0] == granted[0]
                and _path_is_within(resource[1], granted[1])
            )
        if requested.resource_type == "integration_resource":
            return (
                _integration_resource_scope(requested.resource_scope) is not None
                and _integration_resource_scope(grant.resource_scope) is not None
                and requested.resource_scope == grant.resource_scope
            )
        return requested.resource_scope == grant.resource_scope
