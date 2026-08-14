from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Any
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
    use_integration_policy: bool = True
    policy_integration_id: UUID | None = None
    policy_resource_key: str | None = None
    policy_hard_unavailable_capabilities: frozenset[str] = frozenset()
    approval_details: dict[str, Any] | None = None
    force_ask: bool = False
    available_decisions: tuple[str, ...] = (
        "allow_once",
        "allow_session",
        "deny",
    )


_INTEGRATION_OPERATION_KEY = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_INTEGRATION_RESOURCE_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,499}$")


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


def integration_operation_permission(
    *,
    capability: str,
    integration_id: UUID,
    binding_revision: str,
    operation_key: str,
    title: str,
    summary: str,
    resource_key: str | None = None,
    resource_display: str | None = None,
    use_integration_policy: bool = True,
    action_kind: str = "integration_operation",
    risk_level: str = "medium",
    available_decisions: tuple[str, ...] = (
        "allow_once",
        "allow_session",
        "deny",
    ),
) -> PermissionRequest:
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
        available_decisions=available_decisions,
    )


def private_integration_data_release_permission(session_id: UUID) -> PermissionRequest:
    return PermissionRequest(
        capability="integration.private_data.release",
        resource_type="session",
        resource_scope=str(session_id),
        action_kind="integration_private_data_release",
        risk_level="high",
        title="Private Integrationsdaten freigeben?",
        summary=(
            "Private Daten aus Integrationen dürfen in dieser Session an das "
            "öffentliche Modell übermittelt werden."
        ),
        resource_display="Aktuelle Session",
        available_decisions=("allow_session", "deny"),
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


def execution_directory_scope(
    agent_id: UUID, path: str, root_id: UUID, root_revision: str
) -> str:
    parsed = PurePosixPath(path)
    if (
        not parsed.is_absolute()
        or str(parsed) != path
        or "\\" in path
        or (path != "/" and any(part in {"", ".", ".."} for part in path.split("/")[1:]))
        or any(ord(character) < 32 or 127 <= ord(character) <= 159 for character in path)
        or re.fullmatch(r"[0-9a-f]{64}", root_revision) is None
    ):
        raise ValueError("invalid execution directory")
    return json.dumps(
        {
            "agentId": str(agent_id),
            "path": path,
            "rootId": str(root_id),
            "rootRevision": root_revision,
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


def execution_directory_permission(
    agent_id: UUID,
    path: str,
    root_id: UUID,
    root_revision: str,
    *,
    action_kind: str = "change_directory",
    title: str = "Verzeichnis verwenden?",
    summary: str | None = None,
) -> PermissionRequest:
    return PermissionRequest(
        capability="execution.directory",
        resource_type="execution_directory",
        resource_scope=execution_directory_scope(agent_id, path, root_id, root_revision),
        action_kind=action_kind,
        risk_level="medium",
        title=title,
        summary=summary or f"Der Agent möchte das Verzeichnis {path} verwenden.",
        resource_display=path,
        use_integration_policy=False,
        available_decisions=("allow_session", "deny"),
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


def _execution_root_path_scope(
    resource_scope: str,
) -> tuple[UUID, UUID, str, PurePosixPath, PurePosixPath] | None:
    try:
        value = json.loads(resource_scope)
        if set(value) != {
            "agentId",
            "path",
            "rootId",
            "rootRevision",
            "workingDirectory",
        }:
            return None
        agent_id = UUID(value["agentId"])
        root_id = UUID(value["rootId"])
        revision = value["rootRevision"]
        working_directory = value["workingDirectory"]
        path = value["path"]
        if (
            str(agent_id) != value["agentId"]
            or str(root_id) != value["rootId"]
            or not isinstance(revision, str)
            or re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", revision) is None
            or not isinstance(working_directory, str)
            or not isinstance(path, str)
        ):
            return None

        def relative(value: str) -> PurePosixPath | None:
            parsed = PurePosixPath(value)
            if value == ".":
                return parsed
            if (
                not value
                or "\\" in value
                or parsed.is_absolute()
                or any(part in {"", ".", ".."} for part in value.split("/"))
                or any(ord(character) < 32 or 127 <= ord(character) <= 159 for character in value)
            ):
                return None
            return parsed

        working_path = relative(working_directory)
        resource_path = relative(path)
        canonical = json.dumps(
            value,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        if working_path is None or resource_path is None or canonical != resource_scope:
            return None
        return agent_id, root_id, revision, working_path, resource_path
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None


def _execution_directory_scope(
    resource_scope: str,
) -> tuple[UUID, UUID, str, PurePosixPath] | None:
    try:
        value = json.loads(resource_scope)
        if set(value) != {"agentId", "path", "rootId", "rootRevision"}:
            return None
        agent_id = UUID(value["agentId"])
        root_id = UUID(value["rootId"])
        path = value["path"]
        revision = value["rootRevision"]
        canonical = execution_directory_scope(agent_id, path, root_id, revision)
        return (
            agent_id,
            root_id,
            revision,
            PurePosixPath(path),
        ) if canonical == resource_scope else None
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

        if request.resource_type == "execution_directory":
            if _execution_directory_scope(request.resource_scope) is None:
                return PermissionDecision.DENY
            if policy == ToolExecutionPolicy.DISABLED:
                return PermissionDecision.DENY
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
            return (
                PermissionDecision.ALLOW
                if any(PermissionPolicy.grant_matches(item, request, root_files) for item in grants)
                else PermissionDecision.ASK
            )

        if request.resource_type in {"root_path", "execution_root_path"}:
            scope = (
                _root_path_scope(request.resource_scope)
                if request.resource_type == "root_path"
                else _execution_root_path_scope(request.resource_scope)
            )
            if scope is None or root_files.session_id != chat_session.id:
                return PermissionDecision.DENY
            if request.resource_type == "root_path":
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
        if request.resource_type == "execution_root_path":
            resource = _execution_root_path_scope(request.resource_scope)
            granted = _execution_root_path_scope(grant.resource_scope)
            if resource is None or granted is None:
                return False
            return resource[:4] == granted[:4] and _path_is_within(
                resource[4], granted[4]
            )
        if request.resource_type == "execution_directory":
            resource = _execution_directory_scope(request.resource_scope)
            granted = _execution_directory_scope(grant.resource_scope)
            return bool(
                resource
                and granted
                and resource[:3] == granted[:3]
                and _path_is_within(resource[3], granted[3])
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
        if request.resource_type == "url":
            return request.resource_scope == grant.resource_scope
        if request.resource_type == "integration_operation":
            return (
                _integration_operation_scope(request.resource_scope) is not None
                and _integration_operation_scope(grant.resource_scope) is not None
                and request.resource_scope == grant.resource_scope
            )
        if request.resource_type in {
            "network_service",
            "memory_collection",
            "media_model",
        }:
            return request.resource_scope == grant.resource_scope
        if request.resource_type == "agent_identity":
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
