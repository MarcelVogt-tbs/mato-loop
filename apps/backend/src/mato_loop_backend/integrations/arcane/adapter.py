from __future__ import annotations

import hashlib
import json
from contextlib import asynccontextmanager
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from mato_loop_backend.integrations.arcane.client import (
    ArcaneClient,
    ArcaneConfig,
    ArcaneError,
    environment_id,
    segment,
)
from mato_loop_backend.integrations.contracts import (
    IntegrationAdapterDefinition,
    IntegrationContributions,
    IntegrationResource,
    ToolContribution,
)
from mato_loop_backend.models import Credential
from mato_loop_backend.security import load_credential, read_credential
from mato_loop_backend.settings import IntegrationInstance, ToolExecutionPolicy
from mato_loop_backend.tools.actions import ToolAction
from mato_loop_backend.tools.snapshots import ToolSnapshotPolicy

ARCANE_CREDENTIAL_KIND = "arcane.api_key"

_ACCOUNTS = ToolAction("arcane.accounts.read", "Arcane-Instanzen auflisten", ToolExecutionPolicy.AUTO, ToolExecutionPolicy.ASK, category="read")
_READ = ToolAction("arcane.read", "Docker-Ressourcen lesen", ToolExecutionPolicy.AUTO, ToolExecutionPolicy.ASK, category="read")
_MANAGE = ToolAction("arcane.manage", "Docker-Ressourcen verwalten", ToolExecutionPolicy.ASK, ToolExecutionPolicy.ASK, category="write", mutates=True)
_DELETE = ToolAction("arcane.delete", "Docker-Ressourcen löschen", ToolExecutionPolicy.ASK, ToolExecutionPolicy.ASK, category="delete", mutates=True)

Kind = Literal["containers", "projects", "images", "volumes", "networks"]


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ArcaneAccounts(_Args):
    pass


class ArcaneInstance(_Args):
    integration_id: UUID


class ArcaneEnvironment(ArcaneInstance):
    environment_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,80}$")


class ArcaneList(ArcaneEnvironment):
    kind: Kind
    search: str | None = Field(default=None, max_length=160)
    start: int = Field(default=0, ge=0, le=100_000)
    limit: int = Field(default=20, ge=1, le=50)


class ArcaneGet(ArcaneEnvironment):
    kind: Kind
    resource_id: str = Field(min_length=1, max_length=256)
    view: Literal["summary", "runtime", "updates"] = "summary"

    @model_validator(mode="after")
    def validate_view(self) -> ArcaneGet:
        if self.kind != "projects" and self.view != "summary":
            raise ValueError("runtime and updates views require a project")
        return self


class ArcaneLogs(ArcaneEnvironment):
    container_id: str = Field(min_length=1, max_length=256)


class ArcaneAction(ArcaneEnvironment):
    action: Literal[
        "start", "stop", "restart", "pause", "unpause", "redeploy",
        "up", "down", "archive", "unarchive", "pull", "build", "update_services",
        "create_container", "create_volume", "create_network", "pull_image", "tag_image",
        "rename_volume", "connect_network", "disconnect_network", "auto_update",
    ]
    resource_id: str | None = Field(default=None, min_length=1, max_length=256)
    name: str | None = Field(default=None, min_length=1, max_length=256)
    tag: str | None = Field(default=None, min_length=1, max_length=128)
    image: str | None = Field(default=None, min_length=1, max_length=256)
    enabled: bool | None = None

    @model_validator(mode="after")
    def validate_action(self) -> ArcaneAction:
        needs_id = self.action not in {"create_container", "create_volume", "create_network", "pull_image"}
        if needs_id != (self.resource_id is not None):
            raise ValueError("resource_id must match the selected Arcane action")
        if (self.action in {"create_container", "create_volume", "create_network", "pull_image", "tag_image", "rename_volume", "connect_network", "disconnect_network"}) != (self.name is not None):
            raise ValueError("name must match the selected Arcane action")
        if (self.action == "tag_image") != (self.tag is not None):
            raise ValueError("tag must match the selected Arcane action")
        if (self.action == "create_container") != (self.image is not None):
            raise ValueError("image must match the selected Arcane action")
        if (self.action == "auto_update") != (self.enabled is not None):
            raise ValueError("enabled must match the selected Arcane action")
        return self


class ArcaneDelete(ArcaneGet):
    kind: Literal["containers", "projects", "images", "volumes", "networks"]


def _credential(record: Credential | None) -> str:
    if record is None or record.kind != ARCANE_CREDENTIAL_KIND:
        raise ArcaneError("integration.authenticationFailed")
    try:
        payload = read_credential(record)
    except Exception as exc:
        raise ArcaneError("integration.authenticationFailed") from exc
    if not isinstance(payload, dict) or set(payload) != {"api_key"} or not isinstance(payload["api_key"], str):
        raise ArcaneError("integration.authenticationFailed")
    return payload["api_key"]


def _binding(context: Any, integration_id: UUID) -> tuple[IntegrationInstance, str]:
    revision = context.bound_options.get("binding_revision")
    if context.bound_options.get("integration_id") != str(integration_id) or not isinstance(revision, str):
        raise ValueError("Arcane integration is not bound")
    instance = next((item for item in context.integration_settings.instances if item.id == integration_id
                     and item.enabled and item.adapter_type == "arcane" and item.integration_type == "infrastructure"), None)
    if instance is None or json.dumps(instance.binding_data(), ensure_ascii=True, separators=(",", ":"), sort_keys=True) != revision:
        raise ValueError("Arcane integration binding is stale")
    return instance, revision


@asynccontextmanager
async def _client(arguments: ArcaneInstance, context: Any):
    instance, _revision = _binding(context, arguments.integration_id)
    record = await load_credential(context.db, instance.credential_id, owner_id=instance.owner_id) if instance.credential_id else None
    config = ArcaneConfig.model_validate(instance.config)
    async with ArcaneClient(str(config.base_url), _credential(record)) as client:
        yield client


async def _environments(client: ArcaneClient) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for start in range(0, 500, 50):
        page, _pagination = await client.request("GET", "/environments", params={"start": start, "limit": 50})
        if page is None:
            page = []
        if not isinstance(page, list) or len(page) > 50 or not all(isinstance(item, dict) for item in page):
            raise ArcaneError("integration.invalidResponse")
        result.extend(page)
        if len(page) < 50:
            return result
    raise ArcaneError("integration.responseTooLarge")


async def load_arcane_resources(instance: IntegrationInstance, credential: dict[str, Any] | None) -> tuple[IntegrationResource, ...]:
    if not isinstance(credential, dict) or set(credential) != {"api_key"}:
        raise ArcaneError("integration.authenticationFailed")
    config = ArcaneConfig.model_validate(instance.config)
    async with ArcaneClient(str(config.base_url), credential["api_key"]) as client:
        environments = await _environments(client)
    resources = []
    for item in environments:
        key = environment_id(item.get("id"))
        resources.append(IntegrationResource(key=key, label=str(item.get("name") or key)[:160]))
    return tuple(resources)


def _public(item: Any, kind: str) -> dict[str, Any]:
    if not isinstance(item, dict):
        raise ArcaneError("integration.invalidResponse")
    fields = {
        "environments": ("id", "name", "status", "enabled", "isEdge", "connected"),
        "containers": ("id", "name", "image", "state", "status", "created", "ports", "activityId"),
        "projects": ("id", "name", "status", "statusReason", "isArchived", "createdAt", "updatedAt", "runningCount", "serviceCount", "runtimeServices", "activityId"),
        "images": ("id", "repoTags", "size", "created", "inUse", "activityId"),
        "volumes": ("id", "name", "driver", "scope", "size", "inUse", "containers", "activityId"),
        "networks": ("id", "name", "driver", "scope", "inUse", "isDefault", "containersList", "activityId"),
    }[kind]
    result = {}
    for key in fields:
        if key not in item:
            continue
        value = item[key]
        if isinstance(value, str):
            result[key] = value[:500]
        elif isinstance(value, bool | int | float) or value is None:
            result[key] = value
        elif isinstance(value, list):
            result[key] = [entry[:500] if isinstance(entry, str) else
                           {field: data[:500] if isinstance(data, str) else data
                            for field, data in entry.items() if field in {"id", "name", "image", "status", "health", "containerId", "containerName", "ipv4Address", "ipv6Address", "privatePort", "publicPort", "type"}}
                           for entry in value[:40] if isinstance(entry, str | dict)]
    return result


def _result(value: Any) -> Any:
    from mato_loop_backend.tools.contracts import ToolResult

    return ToolResult.json({"data": value, "contentIsUntrusted": True})


def _error(code: str) -> Any:
    from mato_loop_backend.tools.contracts import ToolResult

    return ToolResult.error(code)


async def arcane_accounts(_args: ArcaneAccounts, context: Any) -> Any:
    ids = set(context.bound_options.get("integration_ids", ()))
    return _result([{"integrationId": str(item.id), "name": item.name[:160]} for item in context.integration_settings.instances
                    if str(item.id) in ids and item.enabled and item.adapter_type == "arcane"])


async def arcane_environments(args: ArcaneInstance, context: Any) -> Any:
    try:
        async with _client(args, context) as client:
            items = await _environments(client)
        return _result([_public(item, "environments") for item in items])
    except ValueError:
        return _error("integration.staleReference")
    except ArcaneError as exc:
        return _error(exc.code)


async def arcane_list(args: ArcaneList, context: Any) -> Any:
    try:
        async with _client(args, context) as client:
            data, pagination = await client.request("GET", f"/environments/{args.environment_id}/{args.kind}",
                                                    params={"start": args.start, "limit": args.limit, **({"search": args.search} if args.search else {})})
        if data is None:
            data = []
        if not isinstance(data, list) or len(data) > args.limit:
            raise ArcaneError("integration.invalidResponse")
        return _result({"items": [_public(item, args.kind) for item in data], "pagination": pagination})
    except ValueError:
        return _error("integration.staleReference")
    except ArcaneError as exc:
        return _error(exc.code)


async def arcane_get(args: ArcaneGet, context: Any) -> Any:
    try:
        async with _client(args, context) as client:
            path = f"/environments/{args.environment_id}/{args.kind}/{segment(args.resource_id)}"
            data, _ = await client.request("GET", path + (f"/{args.view}" if args.view != "summary" else ""))
        safe = _public(data, args.kind)
        if args.view == "updates" and isinstance(data.get("updateInfo"), dict):
            info = data["updateInfo"]
            safe["updateInfo"] = {key: info[key] for key in ("status", "hasUpdate", "imageCount", "imagesWithUpdates", "imagesNotPulled", "errorCount")
                                  if key in info and isinstance(info[key], str | bool | int)}
        return _result(safe)
    except ValueError:
        return _error("integration.staleReference")
    except ArcaneError as exc:
        return _error(exc.code)


async def arcane_logs(args: ArcaneLogs, context: Any) -> Any:
    try:
        async with _client(args, context) as client:
            logs = await client.request("GET", f"/environments/{args.environment_id}/containers/{segment(args.container_id)}/logs/download", text=True)
        return _result({"containerId": args.container_id, "logs": logs})
    except ValueError:
        return _error("integration.staleReference")
    except ArcaneError as exc:
        return _error(exc.code)


_ACTION_ROUTES = {
    "start": ("containers", "start", False), "stop": ("containers", "stop", False),
    "restart": ("containers", "restart", False), "pause": ("containers", "pause", False),
    "unpause": ("containers", "unpause", False), "redeploy": ("containers", "redeploy", False),
    "up": ("projects", "up", True), "down": ("projects", "down", False),
    "archive": ("projects", "archive", False), "unarchive": ("projects", "unarchive", False),
    "pull": ("projects", "pull", True), "build": ("projects", "build", True),
    "update_services": ("projects", "update-services", False),
}


async def arcane_action(args: ArcaneAction, context: Any) -> Any:
    try:
        env = f"/environments/{args.environment_id}"
        body = None
        streamed = False
        if args.action in _ACTION_ROUTES:
            kind, action, streamed = _ACTION_ROUTES[args.action]
            path = f"{env}/{kind}/{segment(args.resource_id or '')}/{action}"
        elif args.action == "create_container":
            # ponytail: basic containers only; add typed mounts and ports when a real workflow needs them.
            path, body = f"{env}/containers", {"name": args.name, "image": args.image}
        elif args.action == "create_volume":
            path, body = f"{env}/volumes", {"name": args.name}
        elif args.action == "create_network":
            path, body = f"{env}/networks", {"name": args.name, "options": {"driver": "bridge", "checkDuplicate": True}}
        elif args.action == "pull_image":
            path, body, streamed = f"{env}/images/pull", {"imageName": args.name}, True
        elif args.action == "rename_volume":
            path, body = f"{env}/volumes/{segment(args.resource_id or '')}/rename", {"name": args.name}
        elif args.action in {"connect_network", "disconnect_network"}:
            suffix = "connect" if args.action == "connect_network" else "disconnect"
            path, body = f"{env}/networks/{segment(args.resource_id or '')}/{suffix}", {"containerId": args.name}
        elif args.action == "auto_update":
            path, body = f"{env}/containers/{segment(args.resource_id or '')}/auto-update", {"enabled": args.enabled}
        else:
            path, body = f"{env}/images/{segment(args.resource_id or '')}/tag", {"repository": args.name, "tag": args.tag}
        async with _client(args, context) as client:
            response = await client.request("PUT" if args.action == "auto_update" else "POST", path, body=body, streamed=streamed)
        return _result(response if streamed else {"completed": True, "activityId": (response[0] or {}).get("activityId") if isinstance(response[0], dict) else None})
    except ValueError:
        return _error("integration.staleReference")
    except ArcaneError as exc:
        return _error(exc.code)


async def arcane_delete(args: ArcaneDelete, context: Any) -> Any:
    try:
        path = f"/environments/{args.environment_id}/{args.kind}/{segment(args.resource_id)}"
        if args.kind == "projects":
            path += "/destroy"
        async with _client(args, context) as client:
            await client.request("DELETE", path, body={"removeFiles": True, "removeVolumes": False} if args.kind == "projects" else None)
        return _result({"deleted": True})
    except ValueError:
        return _error("integration.staleReference")
    except ArcaneError as exc:
        return _error(exc.code)


def _accounts_permission(_args: ArcaneAccounts, _context: Any) -> tuple[Any, ...]:
    from mato_loop_backend.permissions.policy import PermissionRequest

    return (PermissionRequest(capability=_ACCOUNTS.capability, resource_type="network_service", resource_scope="arcane_accounts",
                              action_kind="arcane_accounts", risk_level="medium", title="Arcane-Instanzen auflisten?",
                              summary="Der Agent möchte die Arcane-Instanzen lesen.", resource_display="Arcane-Instanzen"),)


def _permission(args: ArcaneInstance, context: Any, capability: str, operation: str, target: str) -> tuple[Any, ...]:
    from mato_loop_backend.permissions.policy import (
        PermissionDisplayItem,
        integration_operation_permission,
    )

    instance, revision = _binding(context, args.integration_id)
    material = args.model_dump_json(exclude_unset=True)
    target_key = hashlib.sha256(target.encode()).hexdigest()[:24]
    label = {
        "start": "Container starten", "stop": "Container stoppen", "restart": "Container neu starten",
        "pause": "Container pausieren", "unpause": "Container fortsetzen", "redeploy": "Container erneut bereitstellen",
        "up": "Projekt starten", "down": "Projekt stoppen", "archive": "Projekt archivieren",
        "unarchive": "Projekt wiederherstellen", "pull": "Projektabbilder laden", "build": "Projektabbilder bauen",
        "update_services": "Projektdienste aktualisieren", "create_container": "Container erstellen",
        "create_volume": "Volume erstellen", "create_network": "Netzwerk erstellen",
        "pull_image": "Abbild laden", "tag_image": "Abbild markieren", "rename_volume": "Volume umbenennen",
        "connect_network": "Container mit Netzwerk verbinden", "disconnect_network": "Container vom Netzwerk trennen",
        "auto_update": "Automatische Containerupdates ändern",
    }.get(operation, operation)
    return (integration_operation_permission(
        capability=capability, integration_id=args.integration_id, binding_revision=revision,
        operation_key=f"arcane:{hashlib.sha256(material.encode()).hexdigest()}",
        resource_key=getattr(args, "environment_id", None),
        grant_resource_key=f"{getattr(args, 'environment_id', 'account')}:{target_key}",
        resource_display=f'Arcane "{instance.name[:100]}" · {getattr(args, "environment_id", "Instanz")}',
        title=f"Arcane: {label}?", summary=f"Der Agent möchte {label.lower()}: {target[:160]}.",
        display_items=(PermissionDisplayItem("Ziel", target[:160]),),
    ),)


def _environments_permission(args: ArcaneInstance, context: Any) -> tuple[Any, ...]:
    return _permission(args, context, _READ.capability, "Umgebungen lesen", "Umgebungen")


def _read_permission(args: ArcaneList | ArcaneGet, context: Any) -> tuple[Any, ...]:
    return _permission(args, context, _READ.capability, "Ressourcen lesen", f"{args.kind}: {getattr(args, 'resource_id', 'Liste')}")


def _logs_permission(args: ArcaneLogs, context: Any) -> tuple[Any, ...]:
    return _permission(args, context, _READ.capability, "Protokoll lesen", f"Container: {args.container_id}")


def _manage_permission(args: ArcaneAction, context: Any) -> tuple[Any, ...]:
    target = f"{args.resource_id or args.name}"
    if args.resource_id and args.name:
        target += f" → {args.name}"
    if args.image:
        target += f" · {args.image}"
    if args.tag:
        target += f":{args.tag}"
    if args.enabled is not None:
        target += f" · {'einschalten' if args.enabled else 'ausschalten'}"
    return _permission(args, context, _MANAGE.capability, args.action, target)


def _delete_permission(args: ArcaneDelete, context: Any) -> tuple[Any, ...]:
    return _permission(args, context, _DELETE.capability, "Löschen", f"{args.kind}: {args.resource_id}")


ARCANE_ADAPTER = IntegrationAdapterDefinition(
    integration_type="infrastructure", adapter_type="arcane", config_model=ArcaneConfig,
    contribution_factory=lambda _instance: IntegrationContributions(),
    resource_loader=load_arcane_resources,
    resource_actions=(_READ, _MANAGE, _DELETE),
    shared_tools=(
        ToolContribution(name="arcane_accounts", description="List configured Arcane instances.", arguments_model=ArcaneAccounts,
                         snapshot_policy=ToolSnapshotPolicy("data", ("integrationId", "name"), max_age_seconds=None),
                         handler=arcane_accounts, label="Arcane-Instanzen auflisten", icon="dns", actions=(_ACCOUNTS,),
                         authorization_factory=_accounts_permission, releases_private_data=True),
        ToolContribution(name="arcane_environments", description="List accessible Docker environments in one Arcane instance.",
                         snapshot_policy=ToolSnapshotPolicy("data", ("id", "name")),
                         arguments_model=ArcaneInstance, handler=arcane_environments, label="Arcane-Umgebungen auflisten", icon="dns",
                         actions=(_READ,), instance_argument="integration_id", authorization_factory=_environments_permission, releases_private_data=True),
        ToolContribution(name="arcane_list", description="List containers, projects, images, volumes, or networks in one environment.",
                         arguments_model=ArcaneList, handler=arcane_list, label="Docker-Ressourcen auflisten", icon="list",
                         actions=(_READ,), instance_argument="integration_id", resource_argument="environment_id",
                         authorization_factory=_read_permission, releases_private_data=True),
        ToolContribution(name="arcane_get", description="Read one Docker resource. Projects also support runtime and image-update views.",
                         arguments_model=ArcaneGet, handler=arcane_get, label="Docker-Ressource lesen", icon="info",
                         actions=(_READ,), instance_argument="integration_id", resource_argument="environment_id",
                         authorization_factory=_read_permission, releases_private_data=True),
        ToolContribution(name="arcane_logs", description="Read up to 32 KiB of retained container logs. Larger logs fail without truncation.",
                         arguments_model=ArcaneLogs, handler=arcane_logs, label="Containerprotokoll lesen", icon="article",
                         actions=(_READ,), instance_argument="integration_id", resource_argument="environment_id",
                         authorization_factory=_logs_permission, releases_private_data=True),
        ToolContribution(name="arcane_action", description="Run one named Docker lifecycle action or create a basic container, volume, or network.",
                         arguments_model=ArcaneAction, handler=arcane_action, label="Docker-Ressource verwalten", icon="settings",
                         actions=(_MANAGE,), instance_argument="integration_id", resource_argument="environment_id",
                         authorization_factory=_manage_permission, releases_private_data=True),
        ToolContribution(name="arcane_delete", description="Delete a container, project, image, volume, or network.",
                         arguments_model=ArcaneDelete, handler=arcane_delete, label="Docker-Ressource löschen", icon="delete",
                         actions=(_DELETE,), instance_argument="integration_id", resource_argument="environment_id",
                         authorization_factory=_delete_permission, releases_private_data=True),
    ),
)
