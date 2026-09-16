from __future__ import annotations

import logging
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, status
from pydantic import AliasChoices, BaseModel, ConfigDict, Field, HttpUrl, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.api.errors import ApiError
from mato_loop_backend.api.routes.integrations import (
    IntegrationAccessRequest,
    _access_payload,
    _integration,
    _integration_resources,
    _load_scoped_integrations,
    _load_scoped_tools,
)
from mato_loop_backend.db.session import get_db_session
from mato_loop_backend.integrations.mcp import McpConfig
from mato_loop_backend.integrations.mcp_client import McpClientError
from mato_loop_backend.integrations.mcp_service import McpIntegrationService
from mato_loop_backend.integrations.service import IntegrationSettingsError
from mato_loop_backend.models import UserAccount
from mato_loop_backend.security import require_admin, require_user
from mato_loop_backend.settings import (
    IntegrationAdapterAvailability,
    IntegrationInstance,
    IntegrationSettings,
    SettingsStore,
    ToolExecutionPolicy,
    TrustLevel,
    UserIntegrationSettings,
    load_effective_tools,
)

router = APIRouter(prefix="/mcp", dependencies=[Depends(require_user)])
logger = logging.getLogger(__name__)
SettingsApiScope = Literal["instance", "personal"]


class McpServerRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    name: str = Field(min_length=1, max_length=160)
    endpoint: HttpUrl
    bearer_token: str | None = Field(
        default=None,
        min_length=1,
        max_length=10_000,
        validation_alias=AliasChoices("bearerToken", "bearer_token"),
    )
    remove_bearer_token: bool = Field(
        default=False,
        validation_alias=AliasChoices("removeBearerToken", "remove_bearer_token"),
    )
    enabled: bool = True

    @model_validator(mode="after")
    def validate_connection(self) -> McpServerRequest:
        if (
            self.endpoint.username is not None
            or self.endpoint.password is not None
            or self.endpoint.query is not None
            or self.endpoint.fragment is not None
            or (self.bearer_token is not None and self.remove_bearer_token)
            or (self.bearer_token is not None and self.endpoint.scheme != "https")
        ):
            raise ValueError("Invalid MCP connection settings")
        return self


class McpServerCreateRequest(McpServerRequest):
    remove_bearer_token: Literal[False] = False


class McpAccessRequest(IntegrationAccessRequest):
    global_policy: ToolExecutionPolicy | None = Field(
        default=None, validation_alias=AliasChoices("globalPolicy", "global_policy")
    )


class McpSettingsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    personal_allowed: bool = Field(
        validation_alias=AliasChoices("personalAllowed", "personal_allowed")
    )


async def _owner_id(
    db: AsyncSession, scope: SettingsApiScope, user: UserAccount
) -> UUID | None:
    if scope == "instance":
        if not user.is_admin:
            raise ApiError(403, "auth.adminRequired")
        return None
    global_settings = await SettingsStore.load(db, IntegrationSettings)
    if global_settings.availability_for("mcp_streamable_http").value != "full":
        raise ApiError(409, "integration.personalAdapterDisabled")
    return user.id


async def _settings(db: AsyncSession, owner_id: UUID | None):
    settings_type = UserIntegrationSettings if owner_id is not None else IntegrationSettings
    settings = await SettingsStore.load(db, settings_type, user_id=owner_id)
    for instance in settings.instances:
        instance.owner_id = owner_id
    return settings


def _payload(instance: IntegrationInstance) -> dict:
    config = McpConfig.model_validate(instance.config)
    return {
        "id": str(instance.id),
        "name": instance.name,
        "endpoint": str(config.endpoint),
        "enabled": instance.enabled,
        "hasBearerToken": instance.credential_id is not None,
        "tools": [
            {
                "name": tool.name,
                "title": tool.title,
                "description": tool.description,
                "inputSchema": tool.input_schema,
                "outputSchema": tool.output_schema,
                "annotations": tool.annotations.model_dump(),
            }
            for tool in config.tools
        ],
    }


def _mcp_instance(settings, integration_id: UUID) -> IntegrationInstance:
    instance = _integration(settings, integration_id)
    if instance is None or instance.integration_type != "mcp":
        raise ApiError(404, "mcp.notFound")
    return instance


def _mcp_access_payload(instance, resources, tools, configured_global) -> dict:
    payload = _access_payload(instance, resources, tools)
    effective_global = payload["actions"][0]["globalPolicy"]
    payload["globalPolicy"] = configured_global.value if configured_global else None
    payload["effectiveGlobalPolicy"] = effective_global
    return payload


def _api_error(exc: Exception) -> ApiError:
    code = getattr(exc, "code", "mcp.unavailable")
    status_code = 404 if code == "mcp.notFound" else 409 if code == "integration.changed" else 502
    return ApiError(status_code, code)


@router.get("/settings")
async def get_mcp_settings(
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict:
    settings = await SettingsStore.load(db, IntegrationSettings)
    return {
        "personalAllowed": settings.availability_for("mcp_streamable_http")
        == IntegrationAdapterAvailability.FULL
    }


@router.put("/settings")
async def update_mcp_settings(
    payload: McpSettingsRequest,
    _admin: Annotated[UserAccount, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict:
    settings = await SettingsStore.load(db, IntegrationSettings, for_update=True)
    settings.adapter_availability["mcp_streamable_http"] = (
        IntegrationAdapterAvailability.FULL
        if payload.personal_allowed
        else IntegrationAdapterAvailability.INSTANCE_ONLY
    )
    await SettingsStore.save(db, settings)
    await db.commit()
    return {"personalAllowed": payload.personal_allowed}


@router.get("/servers")
async def list_mcp_servers(
    db: Annotated[AsyncSession, Depends(get_db_session)],
    scope: SettingsApiScope = "instance",
    user: Annotated[UserAccount, Depends(require_user)] = None,
) -> dict:
    owner_id = await _owner_id(db, scope, user)
    settings = await _settings(db, owner_id)
    return {
        "servers": [
            _payload(instance)
            for instance in settings.instances
            if instance.integration_type == "mcp"
            and instance.adapter_type == "mcp_streamable_http"
        ]
    }


@router.post("/servers", status_code=status.HTTP_201_CREATED)
async def create_mcp_server(
    payload: McpServerCreateRequest,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    scope: SettingsApiScope = "instance",
    user: Annotated[UserAccount, Depends(require_user)] = None,
) -> dict:
    owner_id = await _owner_id(db, scope, user)
    try:
        instance = await McpIntegrationService.create(
            db,
            name=payload.name,
            endpoint=str(payload.endpoint),
            token=payload.bearer_token,
            enabled=payload.enabled,
            owner_id=owner_id,
        )
    except (McpClientError, IntegrationSettingsError) as exc:
        await db.rollback()
        logger.warning("MCP server creation failed with %s", type(exc).__name__)
        raise _api_error(exc) from exc
    return _payload(instance)


@router.put("/servers/{integration_id}")
async def update_mcp_server(
    integration_id: UUID,
    payload: McpServerRequest,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    scope: SettingsApiScope = "instance",
    user: Annotated[UserAccount, Depends(require_user)] = None,
) -> dict:
    owner_id = await _owner_id(db, scope, user)
    if (
        payload.endpoint.scheme != "https"
        and payload.bearer_token is None
        and not payload.remove_bearer_token
    ):
        existing = McpIntegrationService._find(
            await _settings(db, owner_id), integration_id
        )
        if existing is not None and existing.credential_id is not None:
            raise ApiError(422, "mcp.configurationInvalid")
    try:
        instance = await McpIntegrationService.update(
            db,
            integration_id,
            name=payload.name,
            endpoint=str(payload.endpoint),
            token=payload.bearer_token,
            remove_token=payload.remove_bearer_token,
            enabled=payload.enabled,
            owner_id=owner_id,
        )
    except (McpClientError, IntegrationSettingsError) as exc:
        await db.rollback()
        logger.warning("MCP server update failed with %s", type(exc).__name__)
        raise _api_error(exc) from exc
    return _payload(instance)


@router.post("/servers/{integration_id}/refresh")
async def refresh_mcp_server(
    integration_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    scope: SettingsApiScope = "instance",
    user: Annotated[UserAccount, Depends(require_user)] = None,
) -> dict:
    owner_id = await _owner_id(db, scope, user)
    try:
        return _payload(
            await McpIntegrationService.refresh(db, integration_id, owner_id=owner_id)
        )
    except (McpClientError, IntegrationSettingsError) as exc:
        await db.rollback()
        logger.warning("MCP server refresh failed with %s", type(exc).__name__)
        raise _api_error(exc) from exc


@router.delete("/servers/{integration_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_mcp_server(
    integration_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    scope: SettingsApiScope = "instance",
    user: Annotated[UserAccount, Depends(require_user)] = None,
) -> None:
    owner_id = await _owner_id(db, scope, user)
    try:
        await McpIntegrationService.delete(db, integration_id, owner_id=owner_id)
    except IntegrationSettingsError as exc:
        await db.rollback()
        raise _api_error(exc) from exc


@router.get("/servers/{integration_id}/access")
async def get_mcp_access(
    integration_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    trust: TrustLevel,
    scope: SettingsApiScope = "instance",
    user: Annotated[UserAccount, Depends(require_user)] = None,
) -> dict:
    integrations, owner_id = await _load_scoped_integrations(db, scope, user)
    instance = _mcp_instance(integrations, integration_id)
    resources = await _integration_resources(db, instance)
    stored_tools = await _load_scoped_tools(db, owner_id)
    stored_tools.select_trust(trust)
    effective_tools = (
        await load_effective_tools(db, owner_id, trust)
        if owner_id is not None
        else stored_tools
    )
    return _mcp_access_payload(
        instance,
        resources,
        effective_tools,
        (
            stored_tools.permissions.policies.get("mcp.call")
            if owner_id is None
            else None
        ),
    )


@router.put("/servers/{integration_id}/access")
async def update_mcp_access(
    integration_id: UUID,
    payload: McpAccessRequest,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    trust: TrustLevel,
    scope: SettingsApiScope = "instance",
    user: Annotated[UserAccount, Depends(require_user)] = None,
) -> dict:
    integrations, owner_id = await _load_scoped_integrations(db, scope, user)
    if owner_id is not None and "global_policy" in payload.model_fields_set:
        raise ApiError(422, "validation.invalidChoice", field="globalPolicy")
    instance = _mcp_instance(integrations, integration_id)
    resources = await _integration_resources(db, instance)
    revision = instance.model_dump(mode="json")
    resource_keys = {resource.key for resource in resources}
    if set(payload.resources) - resource_keys or any(
        set(policies) - {"mcp.call"} for policies in payload.resources.values()
    ):
        raise ApiError(422, "validation.invalidChoice", field="resources")
    if payload.policies is not None and set(payload.policies) - {"mcp.call"}:
        raise ApiError(422, "validation.invalidChoice", field="policies")

    current_integrations, current_owner_id = await _load_scoped_integrations(
        db, scope, user, for_update=True, mutation=True
    )
    current = _mcp_instance(current_integrations, integration_id)
    if current_owner_id != owner_id or current.model_dump(mode="json") != revision:
        raise ApiError(409, "integration.changed")
    tools = await _load_scoped_tools(db, owner_id, for_update=True)
    tools.select_trust(trust)
    if owner_id is None and "global_policy" in payload.model_fields_set:
        if payload.global_policy is None:
            tools.permissions.policies.pop("mcp.call", None)
        else:
            tools.permissions.policies["mcp.call"] = payload.global_policy
    if payload.policies is not None:
        if payload.policies:
            tools.permissions.integration_policies[str(integration_id)] = dict(
                payload.policies
            )
        else:
            tools.permissions.integration_policies.pop(str(integration_id), None)
    normalized = {
        resource_key: dict(policies)
        for resource_key, policies in payload.resources.items()
        if policies
    }
    if normalized:
        tools.permissions.resource_policies[str(integration_id)] = normalized
    else:
        tools.permissions.resource_policies.pop(str(integration_id), None)
    tools.resource_labels[str(integration_id)] = {
        resource.key: resource.label for resource in resources
    }
    await SettingsStore.save(db, tools, user_id=owner_id)
    await db.commit()
    response_tools = (
        await load_effective_tools(db, owner_id, trust)
        if owner_id is not None
        else tools
    )
    return _mcp_access_payload(
        current,
        resources,
        response_tools,
        tools.permissions.policies.get("mcp.call"),
    )
