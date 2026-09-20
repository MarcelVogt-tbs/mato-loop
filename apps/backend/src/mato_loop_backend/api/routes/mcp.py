from __future__ import annotations

import logging
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, status
from pydantic import AliasChoices, BaseModel, ConfigDict, Field, HttpUrl, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.api.errors import ApiError
from mato_loop_backend.api.routes.integrations import _integration
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
    UserIntegrationSettings,
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
