from __future__ import annotations

import logging
import re
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, status
from pydantic import AliasChoices, BaseModel, ConfigDict, Field, HttpUrl, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.agent.tools import (
    resolve_tool_execution_policy,
    tool_registry,
)
from mato_loop_backend.api.errors import ApiError
from mato_loop_backend.db.session import get_db_session
from mato_loop_backend.execution import ExecutionClient, ExecutionClientError
from mato_loop_backend.execution.manager import execution_agent_connections
from mato_loop_backend.integrations import (
    BOOKSTACK_CREDENTIAL_KIND,
    CALDAV_CREDENTIAL_KIND,
    BookStackIntegrationService,
    CalDavIntegrationService,
    GitIntegrationService,
    IntegrationSettingsError,
    OpenScadIntegrationService,
    SearchIntegrationError,
    SearchIntegrationService,
    bookstack_error_code,
    caldav_error_code,
    load_bookstack_resources,
    load_caldav_resources,
    search_with_provider,
)
from mato_loop_backend.integrations.bookstack_client import BookStackError
from mato_loop_backend.integrations.caldav_client import CalDavError
from mato_loop_backend.integrations.contracts import IntegrationResource
from mato_loop_backend.integrations.openscad import (
    OpenScadConfig,
    OpenScadDiscoverResult,
    OpenScadTestResult,
)
from mato_loop_backend.integrations.registry import integration_adapter
from mato_loop_backend.models import ExecutionAgent, UserAccount
from mato_loop_backend.security import load_credential, read_credential, require_user
from mato_loop_backend.settings import (
    DefaultCalendarSettings,
    DefaultTaskListSettings,
    IntegrationInstance,
    IntegrationSettings,
    SettingsStore,
    ToolExecutionPolicy,
    ToolSettings,
    UserIntegrationSettings,
    UserToolSettings,
)

router = APIRouter(prefix="/integrations", dependencies=[Depends(require_user)])
logger = logging.getLogger(__name__)
_RESOURCE_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,499}$")
SettingsApiScope = Literal["instance", "personal"]
IntegrationSettingsDocument = IntegrationSettings | UserIntegrationSettings
ToolSettingsDocument = ToolSettings | UserToolSettings


async def _load_scoped_integrations(
    db: AsyncSession,
    scope: SettingsApiScope,
    user: UserAccount | None,
    *,
    for_update: bool = False,
    mutation: bool = False,
    adapter_type: str | None = None,
) -> tuple[IntegrationSettingsDocument, UUID | None]:
    if scope == "instance":
        if user is not None and not getattr(user, "is_admin", False):
            raise ApiError(403, "auth.adminRequired")
        settings = await SettingsStore.load(
            db, IntegrationSettings, for_update=for_update
        )
        return settings, None
    if user is None:
        raise ApiError(401, "auth.notAuthenticated")
    instance = await SettingsStore.load(db, IntegrationSettings)
    if not instance.personal_integrations_enabled:
        raise ApiError(409, "integration.personalDisabled")
    if (
        adapter_type is not None
        and adapter_type not in instance.allowed_personal_adapter_types
    ):
        raise ApiError(409, "integration.personalAdapterDisabled")
    settings = await SettingsStore.load(
        db, UserIntegrationSettings, user_id=user.id, for_update=for_update
    )
    for item in settings.instances:
        item.owner_id = user.id
    return settings, user.id


async def _load_scoped_tools(
    db: AsyncSession,
    owner_id: UUID | None,
    *,
    for_update: bool = False,
) -> ToolSettingsDocument:
    if owner_id is None:
        return await SettingsStore.load(db, ToolSettings, for_update=for_update)
    personal = await SettingsStore.load_optional(
        db, UserToolSettings, user_id=owner_id, for_update=for_update
    )
    if personal is not None:
        return personal
    instance = await SettingsStore.load(db, ToolSettings)
    return UserToolSettings.model_validate(
        instance.model_dump(exclude={"personal_settings_enabled"})
    )


class PersonalIntegrationSettingsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    enabled: bool
    allowed_adapter_types: set[Literal["searxng", "caldav", "bookstack", "git", "openscad"]] = Field(
        validation_alias=AliasChoices("allowedAdapterTypes", "allowed_adapter_types")
    )


class SearchIntegrationRequest(BaseModel):
    integration_type: Literal["search"] = "search"
    provider_type: Literal["searxng"] = "searxng"
    name: str = Field(min_length=1, max_length=160)
    base_url: HttpUrl
    enabled: bool = True


class CalendarIntegrationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    integration_type: Literal["calendar"] = "calendar"
    provider_type: Literal["caldav"] = "caldav"
    name: str = Field(min_length=1, max_length=160)
    base_url: HttpUrl
    username: str = Field(min_length=1, max_length=320)
    password: str | None = Field(default=None, max_length=10_000)
    enabled: bool = True

    @model_validator(mode="after")
    def require_secure_url(self) -> CalendarIntegrationRequest:
        if (
            self.base_url.scheme != "https"
            or self.base_url.username is not None
            or self.base_url.password is not None
            or self.base_url.query is not None
            or self.base_url.fragment is not None
        ):
            raise ValueError("CalDAV URL must be a secure endpoint without credentials")
        return self


class CalendarIntegrationCreateRequest(CalendarIntegrationRequest):
    password: str = Field(min_length=1, max_length=10_000)


class KnowledgeIntegrationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    integration_type: Literal["knowledge"] = "knowledge"
    provider_type: Literal["bookstack"] = "bookstack"
    name: str = Field(min_length=1, max_length=160)
    base_url: HttpUrl
    token_id: str | None = Field(default=None, max_length=10_000)
    token_secret: str | None = Field(default=None, max_length=10_000)
    enabled: bool = True

    @model_validator(mode="after")
    def require_secure_url(self) -> KnowledgeIntegrationRequest:
        if (
            self.base_url.scheme != "https"
            or self.base_url.username is not None
            or self.base_url.password is not None
            or self.base_url.query is not None
            or self.base_url.fragment is not None
        ):
            raise ValueError(
                "BookStack URL must be a secure endpoint without credentials"
            )
        return self


class KnowledgeIntegrationCreateRequest(KnowledgeIntegrationRequest):
    token_id: str = Field(min_length=1, max_length=10_000)
    token_secret: str = Field(min_length=1, max_length=10_000)


class GitIntegrationConfigRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    allow_network: bool = Field(
        default=False,
        validation_alias=AliasChoices("allow_network", "allowNetwork"),
    )


class GitIntegrationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    execution_agent_id: UUID = Field(
        validation_alias=AliasChoices("execution_agent_id", "executionAgentId")
    )
    name: str = Field(default="Git", min_length=1, max_length=160)
    config: GitIntegrationConfigRequest = Field(default_factory=GitIntegrationConfigRequest)
    enabled: bool = True


class OpenScadIntegrationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    execution_agent_id: UUID = Field(
        validation_alias=AliasChoices("execution_agent_id", "executionAgentId")
    )
    name: str = Field(default="OpenSCAD", min_length=1, max_length=160)
    config: OpenScadConfig
    enabled: bool = True


class OpenScadDiscoverRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    execution_agent_id: UUID = Field(
        validation_alias=AliasChoices("execution_agent_id", "executionAgentId")
    )


class IntegrationAccessRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    policies: dict[str, ToolExecutionPolicy] | None = None
    resources: dict[str, dict[str, ToolExecutionPolicy]] = Field(default_factory=dict)
    default_resource_key: str | None = Field(
        default=None,
        max_length=500,
        validation_alias=AliasChoices("defaultResourceKey", "default_resource_key"),
    )
    default_task_list_key: str | None = Field(
        default=None,
        max_length=500,
        validation_alias=AliasChoices(
            "defaultTaskListKey", "default_task_list_key"
        ),
    )


def _provider_payload(provider: IntegrationInstance) -> dict:
    adapter = integration_adapter(provider.integration_type, provider.adapter_type)
    resource_actions = tool_registry.integration_resource_actions(provider)
    return {
        "id": str(provider.id),
        "name": provider.name,
        "integrationType": provider.integration_type,
        "providerType": provider.adapter_type,
        "enabled": provider.enabled,
        "hasCredential": provider.credential_id is not None,
        "supportsResourcePolicies": bool(
            adapter and resource_actions and adapter.resource_loader
        ),
        "config": {
            "baseUrl": provider.config.get("base_url"),
            **(
                {"username": provider.config.get("username")}
                if "username" in provider.config
                else {}
            ),
        },
    }


def _integration(
    settings: IntegrationSettings, integration_id: UUID
) -> IntegrationInstance | None:
    return next((item for item in settings.instances if item.id == integration_id), None)


async def _integration_resources(
    db: AsyncSession, instance: IntegrationInstance
) -> tuple:
    adapter = integration_adapter(instance.integration_type, instance.adapter_type)
    if adapter is None or not adapter.resource_actions or adapter.resource_loader is None:
        raise ApiError(409, "integration.resourcePoliciesUnsupported")
    if not tool_registry.integration_resource_actions(instance):
        raise ApiError(409, "integration.resourcePoliciesUnsupported")
    credential = None
    try:
        if instance.credential_id is not None:
            record = await load_credential(
                db,
                instance.credential_id,
                owner_id=instance.owner_id,
                populate_existing=True,
            )
            if record is None:
                raise ValueError("missing integration credential")
            if (
                instance.integration_type == "calendar"
                and instance.adapter_type == "caldav"
                and record.kind != CALDAV_CREDENTIAL_KIND
            ):
                raise ValueError("invalid integration credential kind")
            if (
                instance.integration_type == "knowledge"
                and instance.adapter_type == "bookstack"
                and record.kind != BOOKSTACK_CREDENTIAL_KIND
            ):
                raise ValueError("invalid integration credential kind")
            credential = read_credential(record)
        await db.rollback()
        resources = await adapter.resources(instance, credential)
    except Exception as exc:
        await db.rollback()
        logger.warning(
            "Resource discovery for integration %s failed with %s",
            instance.id,
            type(exc).__name__,
        )
        raise ApiError(502, "integration.resourceDiscoveryFailed") from exc
    keys = set()
    for resource in resources:
        if (
            not isinstance(resource, IntegrationResource)
            or not isinstance(resource.key, str)
            or _RESOURCE_KEY.fullmatch(resource.key) is None
            or not isinstance(resource.label, str)
            or not 1 <= len(resource.label) <= 160
            or resource.label != resource.label.strip()
            or (
                resource.group_key is not None
                and (
                    not isinstance(resource.group_key, str)
                    or _RESOURCE_KEY.fullmatch(resource.group_key) is None
                )
            )
            or resource.key in keys
        ):
            raise ApiError(502, "integration.resourceDiscoveryFailed")
        keys.add(resource.key)
    return resources


def _access_payload(
    instance: IntegrationInstance,
    resources: tuple,
    tools: ToolSettings,
) -> dict:
    adapter = integration_adapter(instance.integration_type, instance.adapter_type)
    if adapter is None:
        raise ApiError(409, "integration.resourcePoliciesUnsupported")
    integration_policies = tools.integration_policies.get(str(instance.id), {})
    resource_policies = tools.resource_policies.get(str(instance.id), {})
    resource_actions = tool_registry.integration_resource_actions(instance)
    if not resource_actions:
        raise ApiError(409, "integration.resourcePoliciesUnsupported")
    capabilities = {action.capability for action in resource_actions}

    def applicable_capabilities(resource: IntegrationResource) -> set[str]:
        return (
            set(resource.applicable_capabilities)
            if resource.applicable_capabilities is not None
            else capabilities
        )

    actions = [
        {
            "capability": action.capability,
            "label": action.label,
            "allowedPolicies": [policy.value for policy in action.allowed_policies],
            "globalPolicy": resolve_tool_execution_policy(
                tools,
                action.capability,
                action.default_policy,
                allowed_policies=action.allowed_policies,
            ).policy.value,
            "integrationPolicy": (
                integration_policies[action.capability].value
                if action.capability in integration_policies
                else None
            ),
            "effectiveIntegrationPolicy": resolve_tool_execution_policy(
                tools,
                action.capability,
                action.default_policy,
                allowed_policies=action.allowed_policies,
                integration_id=instance.id,
            ).policy.value,
        }
        for action in resource_actions
    ]
    payload = {
        "integrationId": str(instance.id),
        "actions": actions,
        "resources": [
            {
                "key": resource.key,
                "groupKey": resource.group_key,
                "label": resource.label.strip(),
                "hardUnavailableCapabilities": sorted(
                    resource.hard_unavailable_capabilities
                ),
                "applicableCapabilities": sorted(applicable_capabilities(resource)),
                "policies": {
                    capability: policy.value
                    for capability, policy in resource_policies.get(
                        resource.key, {}
                    ).items()
                    if capability in applicable_capabilities(resource)
                },
                "effectivePolicies": {
                    action.capability: resolve_tool_execution_policy(
                        tools,
                        action.capability,
                        action.default_policy,
                        allowed_policies=action.allowed_policies,
                        integration_id=instance.id,
                        resource_key=resource.key,
                        hard_unavailable_capabilities=(
                            resource.hard_unavailable_capabilities
                        ),
                    ).policy.value
                    for action in resource_actions
                    if action.capability in applicable_capabilities(resource)
                },
            }
            for resource in resources
        ],
    }
    if instance.integration_type == "calendar" and instance.adapter_type == "caldav":
        default_calendar = tools.default_calendar
        payload["defaultResourceKey"] = (
            default_calendar.calendar_key
            if default_calendar is not None
            and default_calendar.integration_id == instance.id
            and any(resource.key == default_calendar.calendar_key for resource in resources)
            else None
        )
        default_task_list = tools.default_task_list
        payload["defaultTaskListKey"] = (
            default_task_list.task_list_key
            if default_task_list is not None
            and default_task_list.integration_id == instance.id
            and any(
                resource.key == default_task_list.task_list_key
                for resource in resources
            )
            else None
        )
    return payload


def search_settings_payload(settings: IntegrationSettings) -> dict:
    providers = [
        instance for instance in settings.instances if instance.integration_type == "search"
    ]
    return {
        **(
            {
                "personalIntegrationsEnabled": settings.personal_integrations_enabled,
                "allowedPersonalAdapterTypes": sorted(
                    settings.allowed_personal_adapter_types
                ),
            }
            if isinstance(settings, IntegrationSettings)
            else {}
        ),
        "configured": bool(providers),
        "enabled": True,
        "effectiveEnabled": True,
        "unavailableReason": None,
        "providers": [_provider_payload(provider) for provider in providers],
    }


def calendar_settings_payload(settings: IntegrationSettings) -> dict:
    accounts = [
        instance
        for instance in settings.instances
        if instance.integration_type == "calendar" and instance.adapter_type == "caldav"
    ]
    return {
        **(
            {
                "personalIntegrationsEnabled": settings.personal_integrations_enabled,
                "allowedPersonalAdapterTypes": sorted(
                    settings.allowed_personal_adapter_types
                ),
            }
            if isinstance(settings, IntegrationSettings)
            else {}
        ),
        "configured": bool(accounts),
        "accounts": [_provider_payload(account) for account in accounts],
    }


def knowledge_settings_payload(settings: IntegrationSettings) -> dict:
    bases = [
        instance
        for instance in settings.instances
        if instance.integration_type == "knowledge"
        and instance.adapter_type == "bookstack"
    ]
    return {
        **(
            {
                "personalIntegrationsEnabled": settings.personal_integrations_enabled,
                "allowedPersonalAdapterTypes": sorted(
                    settings.allowed_personal_adapter_types
                ),
            }
            if isinstance(settings, IntegrationSettings)
            else {}
        ),
        "configured": bool(bases),
        "bases": [_provider_payload(base) for base in bases],
    }


def git_settings_payload(settings: IntegrationSettings) -> dict:
    integrations = [
        instance
        for instance in settings.instances
        if instance.integration_type == "git" and instance.adapter_type == "git"
    ]
    return {
        "configured": bool(integrations),
        "instances": [
            {
                "id": str(instance.id),
                "name": instance.name,
                "integrationType": "git",
                "providerType": "git",
                "executionLocation": "agent",
                "executionKind": "git",
                "executionAgentId": str(instance.execution_agent_id),
                "enabled": instance.enabled,
                "hasCredential": False,
                "config": {
                    "allowNetwork": bool(instance.config.get("allow_network", False))
                },
            }
            for instance in integrations
        ],
    }


def openscad_settings_payload(
    settings: IntegrationSettings, execution_agent_ids: set[UUID]
) -> dict:
    integrations = [
        instance
        for instance in settings.instances
        if instance.integration_type == "openscad"
        and instance.adapter_type == "openscad"
        and instance.execution_agent_id in execution_agent_ids
    ]
    return {
        "configured": bool(integrations),
        "instances": [
            {
                "id": str(instance.id),
                "name": instance.name,
                "integrationType": "openscad",
                "providerType": "openscad",
                "executionLocation": "agent",
                "executionKind": "openscad",
                "executionAgentId": str(instance.execution_agent_id),
                "enabled": instance.enabled,
                "hasCredential": False,
                "config": {
                    "binaryPath": instance.config.get("binary_path"),
                },
            }
            for instance in integrations
        ],
    }


async def _owned_execution_agent_ids(db: AsyncSession, user_id: UUID) -> set[UUID]:
    result = await db.scalars(
        select(ExecutionAgent.id).where(
            ExecutionAgent.user_id == user_id,
            ExecutionAgent.revoked_at.is_(None),
        )
    )
    return set(result.all())


def _openscad_api_error(exc: ExecutionClientError) -> ApiError:
    return ApiError(
        503 if exc.code.startswith("executionAgent.") else 502,
        exc.code,
    )


def _connected_client(agent_id: UUID) -> ExecutionClient:
    snapshot = execution_agent_connections.snapshot(agent_id)
    if snapshot is None:
        raise ApiError(503, "executionAgent.unavailable")
    return ExecutionClient(agent_id, None, None, None, snapshot[0])


@router.get("/search")
async def get_search_integrations(
    db: Annotated[AsyncSession, Depends(get_db_session)],
    scope: SettingsApiScope = "instance",
    user: Annotated[UserAccount | None, Depends(require_user)] = None,
) -> dict:
    settings, _owner_id = await _load_scoped_integrations(db, scope, user)
    return search_settings_payload(settings)


@router.get("/git")
async def get_git_integrations(
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
    scope: SettingsApiScope = "instance",
) -> dict:
    settings, _owner_id = await _load_scoped_integrations(db, scope, user)
    try:
        await GitIntegrationService.check_ownership(db, settings, user_id=user.id)
    except IntegrationSettingsError as exc:
        raise ApiError(404, exc.code) from exc
    return git_settings_payload(settings)


@router.post("/git", status_code=status.HTTP_201_CREATED)
async def create_git_integration(
    payload: GitIntegrationRequest,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
    scope: SettingsApiScope = "personal",
) -> dict:
    if scope != "personal":
        raise ApiError(409, "integration.agentPersonalOnly")
    await _load_scoped_integrations(
        db, scope, user, mutation=True, adapter_type="git"
    )
    try:
        settings = await GitIntegrationService.create(
            db,
            user_id=user.id,
            execution_agent_id=payload.execution_agent_id,
            name=payload.name,
            allow_network=payload.config.allow_network,
            enabled=payload.enabled,
            settings_owner_id=user.id,
        )
    except IntegrationSettingsError as exc:
        status_code = 409 if exc.code == "git.alreadyConfigured" else 404
        raise ApiError(status_code, exc.code, field="executionAgentId") from exc
    return git_settings_payload(settings)


@router.put("/git/{integration_id}")
async def update_git_integration(
    integration_id: UUID,
    payload: GitIntegrationRequest,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
    scope: SettingsApiScope = "personal",
) -> dict:
    _settings, owner_id = await _load_scoped_integrations(
        db, scope, user, mutation=True, adapter_type="git"
    )
    try:
        settings = await GitIntegrationService.update(
            db,
            integration_id,
            user_id=user.id,
            execution_agent_id=payload.execution_agent_id,
            name=payload.name,
            allow_network=payload.config.allow_network,
            enabled=payload.enabled,
            settings_owner_id=owner_id,
        )
    except IntegrationSettingsError as exc:
        status_code = (
            409
            if exc.code in {"git.alreadyConfigured", "integration.changed"}
            else 404
        )
        field = "executionAgentId" if exc.code != "integration.notFound" else None
        raise ApiError(status_code, exc.code, field=field) from exc
    return git_settings_payload(settings)


@router.delete("/git/{integration_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_git_integration(
    integration_id: UUID,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
    scope: SettingsApiScope = "personal",
) -> None:
    _settings, owner_id = await _load_scoped_integrations(
        db, scope, user, mutation=True, adapter_type="git"
    )
    try:
        await GitIntegrationService.delete(
            db,
            integration_id,
            user_id=user.id,
            settings_owner_id=owner_id,
        )
    except IntegrationSettingsError as exc:
        raise ApiError(409 if exc.code == "integration.changed" else 404, exc.code) from exc


@router.get("/openscad")
async def get_openscad_integrations(
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
    scope: SettingsApiScope = "instance",
) -> dict:
    settings, _owner_id = await _load_scoped_integrations(db, scope, user)
    return openscad_settings_payload(
        settings, await _owned_execution_agent_ids(db, user.id)
    )


@router.post("/openscad", status_code=status.HTTP_201_CREATED)
async def create_openscad_integration(
    payload: OpenScadIntegrationRequest,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
    scope: SettingsApiScope = "personal",
) -> dict:
    if scope != "personal":
        raise ApiError(409, "integration.agentPersonalOnly")
    await _load_scoped_integrations(
        db, scope, user, mutation=True, adapter_type="openscad"
    )
    try:
        settings = await OpenScadIntegrationService.create(
            db,
            user_id=user.id,
            execution_agent_id=payload.execution_agent_id,
            name=payload.name,
            binary_path=payload.config.binary_path,
            enabled=payload.enabled,
            settings_owner_id=user.id,
        )
    except IntegrationSettingsError as exc:
        status_code = 409 if exc.code == "openscad.alreadyConfigured" else 404
        raise ApiError(status_code, exc.code, field="executionAgentId") from exc
    return openscad_settings_payload(
        settings, await _owned_execution_agent_ids(db, user.id)
    )


@router.put("/openscad/{integration_id}")
async def update_openscad_integration(
    integration_id: UUID,
    payload: OpenScadIntegrationRequest,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
    scope: SettingsApiScope = "personal",
) -> dict:
    _settings, owner_id = await _load_scoped_integrations(
        db, scope, user, mutation=True, adapter_type="openscad"
    )
    try:
        settings = await OpenScadIntegrationService.update(
            db,
            integration_id,
            user_id=user.id,
            execution_agent_id=payload.execution_agent_id,
            name=payload.name,
            binary_path=payload.config.binary_path,
            enabled=payload.enabled,
            settings_owner_id=owner_id,
        )
    except IntegrationSettingsError as exc:
        status_code = (
            409
            if exc.code in {"openscad.alreadyConfigured", "integration.changed"}
            else 404
        )
        field = "executionAgentId" if exc.code != "integration.notFound" else None
        raise ApiError(status_code, exc.code, field=field) from exc
    return openscad_settings_payload(
        settings, await _owned_execution_agent_ids(db, user.id)
    )


@router.delete("/openscad/{integration_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_openscad_integration(
    integration_id: UUID,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
    scope: SettingsApiScope = "personal",
) -> None:
    _settings, owner_id = await _load_scoped_integrations(
        db, scope, user, mutation=True, adapter_type="openscad"
    )
    try:
        await OpenScadIntegrationService.delete(
            db,
            integration_id,
            user_id=user.id,
            settings_owner_id=owner_id,
        )
    except IntegrationSettingsError as exc:
        raise ApiError(409 if exc.code == "integration.changed" else 404, exc.code) from exc


@router.post("/openscad/discover")
async def discover_openscad(
    payload: OpenScadDiscoverRequest,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
    scope: SettingsApiScope = "personal",
) -> dict:
    if scope != "personal":
        raise ApiError(409, "integration.agentPersonalOnly")
    await _load_scoped_integrations(
        db, scope, user, mutation=True, adapter_type="openscad"
    )
    try:
        await OpenScadIntegrationService._owned_agent(
            db, user.id, payload.execution_agent_id, for_update=False
        )
        result = await _connected_client(payload.execution_agent_id).request(
            "integration.openscad.discover", {}, OpenScadDiscoverResult
        )
    except IntegrationSettingsError as exc:
        raise ApiError(404, exc.code, field="executionAgentId") from exc
    except ExecutionClientError as exc:
        raise _openscad_api_error(exc) from exc
    assert isinstance(result, OpenScadDiscoverResult)
    return result.model_dump(mode="json", by_alias=True, exclude_none=True)


@router.post("/openscad/{integration_id}/test")
async def test_openscad_integration(
    integration_id: UUID,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
    scope: SettingsApiScope = "personal",
) -> dict:
    settings, _owner_id = await _load_scoped_integrations(
        db, scope, user, adapter_type="openscad"
    )
    instance = OpenScadIntegrationService._find(settings, integration_id)
    if instance is None:
        raise ApiError(404, "integration.notFound")
    if instance.execution_agent_id is None:
        raise ApiError(404, "executionAgent.notFound")
    try:
        await OpenScadIntegrationService._owned_agent(
            db, user.id, instance.execution_agent_id, for_update=False
        )
        config = OpenScadConfig.model_validate(instance.config)
        result = await _connected_client(instance.execution_agent_id).request(
            "integration.openscad.test",
            {"executablePath": config.binary_path},
            OpenScadTestResult,
        )
    except IntegrationSettingsError as exc:
        raise ApiError(404, exc.code) from exc
    except ExecutionClientError as exc:
        logger.warning("OpenSCAD integration test failed: %s", exc.code)
        raise _openscad_api_error(exc) from exc
    assert isinstance(result, OpenScadTestResult)
    return result.model_dump(mode="json", by_alias=True)


@router.get("/calendar")
async def get_calendar_integrations(
    db: Annotated[AsyncSession, Depends(get_db_session)],
    scope: SettingsApiScope = "instance",
    user: Annotated[UserAccount | None, Depends(require_user)] = None,
) -> dict:
    settings, _owner_id = await _load_scoped_integrations(db, scope, user)
    return calendar_settings_payload(settings)


@router.post("/calendar/accounts", status_code=status.HTTP_201_CREATED)
async def create_calendar_integration(
    payload: CalendarIntegrationCreateRequest,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    scope: SettingsApiScope = "instance",
    user: Annotated[UserAccount | None, Depends(require_user)] = None,
) -> dict:
    _settings, owner_id = await _load_scoped_integrations(
        db, scope, user, mutation=True, adapter_type="caldav"
    )
    settings = await CalDavIntegrationService.create(
        db,
        name=payload.name,
        base_url=str(payload.base_url),
        username=payload.username,
        password=payload.password,
        enabled=payload.enabled,
        owner_id=owner_id,
    )
    return calendar_settings_payload(settings)


@router.put("/calendar/accounts/{integration_id}")
async def update_calendar_integration(
    integration_id: UUID,
    payload: CalendarIntegrationRequest,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    scope: SettingsApiScope = "instance",
    user: Annotated[UserAccount | None, Depends(require_user)] = None,
) -> dict:
    _settings, owner_id = await _load_scoped_integrations(
        db, scope, user, mutation=True, adapter_type="caldav"
    )
    try:
        settings = await CalDavIntegrationService.update(
            db,
            integration_id,
            name=payload.name,
            base_url=str(payload.base_url),
            username=payload.username,
            password=payload.password,
            enabled=payload.enabled,
            owner_id=owner_id,
        )
    except IntegrationSettingsError as exc:
        status_code = 404 if exc.code == "integration.notFound" else 422
        raise ApiError(status_code, exc.code, field="password") from exc
    return calendar_settings_payload(settings)


@router.delete(
    "/calendar/accounts/{integration_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_calendar_integration(
    integration_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    scope: SettingsApiScope = "instance",
    user: Annotated[UserAccount | None, Depends(require_user)] = None,
) -> None:
    _settings, owner_id = await _load_scoped_integrations(
        db, scope, user, mutation=True, adapter_type="caldav"
    )
    try:
        await CalDavIntegrationService.delete(db, integration_id, owner_id=owner_id)
    except IntegrationSettingsError as exc:
        raise ApiError(404, exc.code) from exc


@router.post("/calendar/accounts/{integration_id}/test")
async def test_calendar_integration(
    integration_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    scope: SettingsApiScope = "instance",
    user: Annotated[UserAccount | None, Depends(require_user)] = None,
) -> dict[str, str]:
    settings, _owner_id = await _load_scoped_integrations(
        db, scope, user, adapter_type="caldav"
    )
    account = next(
        (
            item
            for item in settings.instances
            if item.id == integration_id
            and item.integration_type == "calendar"
            and item.adapter_type == "caldav"
        ),
        None,
    )
    if account is None:
        raise ApiError(404, "integration.notFound")
    try:
        if account.credential_id is None:
            raise CalDavError("caldav.authenticationFailed")
        record = await load_credential(
            db,
            account.credential_id,
            owner_id=account.owner_id,
            populate_existing=True,
        )
        if record is None or record.kind != CALDAV_CREDENTIAL_KIND:
            raise CalDavError("caldav.authenticationFailed")
        try:
            credential = read_credential(record)
        except Exception as exc:
            raise CalDavError("caldav.authenticationFailed") from exc
        await db.rollback()
        await load_caldav_resources(account, credential)
    except CalDavError as exc:
        await db.rollback()
        code = caldav_error_code(exc)
        logger.warning("CalDAV integration test failed: %s", code)
        raise ApiError(502, code) from exc
    except Exception as exc:
        await db.rollback()
        logger.warning(
            "CalDAV integration test failed with %s", type(exc).__name__
        )
        raise ApiError(502, "integration.unavailable") from exc
    return {"status": "ok"}


@router.get("/knowledge")
async def get_knowledge_integrations(
    db: Annotated[AsyncSession, Depends(get_db_session)],
    scope: SettingsApiScope = "instance",
    user: Annotated[UserAccount | None, Depends(require_user)] = None,
) -> dict:
    settings, _owner_id = await _load_scoped_integrations(db, scope, user)
    return knowledge_settings_payload(settings)


@router.post("/knowledge/bases", status_code=status.HTTP_201_CREATED)
async def create_knowledge_integration(
    payload: KnowledgeIntegrationCreateRequest,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    scope: SettingsApiScope = "instance",
    user: Annotated[UserAccount | None, Depends(require_user)] = None,
) -> dict:
    _settings, owner_id = await _load_scoped_integrations(
        db, scope, user, mutation=True, adapter_type="bookstack"
    )
    settings = await BookStackIntegrationService.create(
        db,
        name=payload.name,
        base_url=str(payload.base_url),
        token_id=payload.token_id,
        token_secret=payload.token_secret,
        enabled=payload.enabled,
        owner_id=owner_id,
    )
    return knowledge_settings_payload(settings)


@router.put("/knowledge/bases/{integration_id}")
async def update_knowledge_integration(
    integration_id: UUID,
    payload: KnowledgeIntegrationRequest,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    scope: SettingsApiScope = "instance",
    user: Annotated[UserAccount | None, Depends(require_user)] = None,
) -> dict:
    _settings, owner_id = await _load_scoped_integrations(
        db, scope, user, mutation=True, adapter_type="bookstack"
    )
    try:
        settings = await BookStackIntegrationService.update(
            db,
            integration_id,
            name=payload.name,
            base_url=str(payload.base_url),
            token_id=payload.token_id,
            token_secret=payload.token_secret,
            enabled=payload.enabled,
            owner_id=owner_id,
        )
    except IntegrationSettingsError as exc:
        status_code = 404 if exc.code == "integration.notFound" else 422
        raise ApiError(status_code, exc.code, field="token_secret") from exc
    return knowledge_settings_payload(settings)


@router.delete(
    "/knowledge/bases/{integration_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_knowledge_integration(
    integration_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    scope: SettingsApiScope = "instance",
    user: Annotated[UserAccount | None, Depends(require_user)] = None,
) -> None:
    _settings, owner_id = await _load_scoped_integrations(
        db, scope, user, mutation=True, adapter_type="bookstack"
    )
    try:
        await BookStackIntegrationService.delete(db, integration_id, owner_id=owner_id)
    except IntegrationSettingsError as exc:
        raise ApiError(404, exc.code) from exc


@router.post("/knowledge/bases/{integration_id}/test")
async def test_knowledge_integration(
    integration_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    scope: SettingsApiScope = "instance",
    user: Annotated[UserAccount | None, Depends(require_user)] = None,
) -> dict[str, str]:
    settings, _owner_id = await _load_scoped_integrations(
        db, scope, user, adapter_type="bookstack"
    )
    base = next(
        (
            item
            for item in settings.instances
            if item.id == integration_id
            and item.integration_type == "knowledge"
            and item.adapter_type == "bookstack"
        ),
        None,
    )
    if base is None:
        raise ApiError(404, "integration.notFound")
    try:
        if base.credential_id is None:
            raise BookStackError("bookstack.authenticationFailed")
        record = await load_credential(
            db,
            base.credential_id,
            owner_id=base.owner_id,
            populate_existing=True,
        )
        if record is None or record.kind != BOOKSTACK_CREDENTIAL_KIND:
            raise BookStackError("bookstack.authenticationFailed")
        try:
            credential = read_credential(record)
        except Exception as exc:
            raise BookStackError("bookstack.authenticationFailed") from exc
        await db.rollback()
        await load_bookstack_resources(base, credential)
    except BookStackError as exc:
        await db.rollback()
        code = bookstack_error_code(exc)
        logger.warning("BookStack integration test failed: %s", code)
        raise ApiError(502, code) from exc
    except Exception as exc:
        await db.rollback()
        logger.warning("BookStack integration test failed with %s", type(exc).__name__)
        raise ApiError(502, "integration.unavailable") from exc
    return {"status": "ok"}


@router.get("/{integration_id}/access")
async def get_integration_access(
    integration_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    scope: SettingsApiScope = "instance",
    user: Annotated[UserAccount | None, Depends(require_user)] = None,
) -> dict:
    integrations, owner_id = await _load_scoped_integrations(db, scope, user)
    instance = _integration(integrations, integration_id)
    if instance is None:
        raise ApiError(404, "integration.notFound")
    if scope == "personal":
        await _load_scoped_integrations(
            db, scope, user, adapter_type=instance.adapter_type
        )
    resources = await _integration_resources(db, instance)
    tools = await _load_scoped_tools(db, owner_id)
    return _access_payload(instance, resources, tools)


@router.put("/{integration_id}/access")
async def update_integration_access(
    integration_id: UUID,
    payload: IntegrationAccessRequest,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    scope: SettingsApiScope = "instance",
    user: Annotated[UserAccount | None, Depends(require_user)] = None,
) -> dict:
    integrations, owner_id = await _load_scoped_integrations(
        db, scope, user, mutation=True
    )
    instance = _integration(integrations, integration_id)
    if instance is None:
        raise ApiError(404, "integration.notFound")
    if scope == "personal":
        await _load_scoped_integrations(
            db, scope, user, mutation=True, adapter_type=instance.adapter_type
        )
    resources = await _integration_resources(db, instance)
    revision = instance.model_dump(mode="json")
    adapter = integration_adapter(instance.integration_type, instance.adapter_type)
    if adapter is None:
        raise ApiError(409, "integration.resourcePoliciesUnsupported")
    resource_actions = tool_registry.integration_resource_actions(instance)
    if not resource_actions:
        raise ApiError(409, "integration.resourcePoliciesUnsupported")
    resource_keys = {resource.key for resource in resources}
    resources_by_key = {resource.key: resource for resource in resources}
    capabilities = {action.capability for action in resource_actions}
    actions = {action.capability: action for action in resource_actions}
    if set(payload.resources) - resource_keys:
        raise ApiError(422, "validation.invalidChoice", field="resources")
    if any(set(policies) - capabilities for policies in payload.resources.values()):
        raise ApiError(422, "validation.invalidChoice", field="resources")
    if any(
        resource_key in resources_by_key
        and resources_by_key[resource_key].applicable_capabilities is not None
        and not set(policies)
        <= resources_by_key[resource_key].applicable_capabilities
        for resource_key, policies in payload.resources.items()
    ):
        raise ApiError(422, "validation.invalidChoice", field="resources")
    if payload.policies is not None and set(payload.policies) - capabilities:
        raise ApiError(422, "validation.invalidChoice", field="policies")
    default_resource_changed = "default_resource_key" in payload.model_fields_set
    default_task_list_changed = "default_task_list_key" in payload.model_fields_set
    if (default_resource_changed or default_task_list_changed) and (
        instance.integration_type != "calendar" or instance.adapter_type != "caldav"
    ):
        field = (
            "default_resource_key"
            if default_resource_changed
            else "default_task_list_key"
        )
        raise ApiError(422, "validation.invalidChoice", field=field)
    if (
        default_resource_changed
        and payload.default_resource_key is not None
        and (
            payload.default_resource_key not in resource_keys
            or not payload.default_resource_key.startswith("calendar:")
        )
    ):
        raise ApiError(422, "validation.invalidChoice", field="default_resource_key")
    if (
        default_task_list_changed
        and payload.default_task_list_key is not None
        and (
            payload.default_task_list_key not in resource_keys
            or not payload.default_task_list_key.startswith("tasklist:")
        )
    ):
        raise ApiError(422, "validation.invalidChoice", field="default_task_list_key")
    if payload.policies is not None and any(
        policy not in actions[capability].allowed_policies
        for capability, policy in payload.policies.items()
    ):
        raise ApiError(422, "validation.invalidChoice", field="policies")
    if any(
        policy not in actions[capability].allowed_policies
        for policies in payload.resources.values()
        for capability, policy in policies.items()
    ):
        raise ApiError(422, "validation.invalidChoice", field="resources")

    current_integrations, current_owner_id = await _load_scoped_integrations(
        db, scope, user, for_update=True, mutation=True
    )
    if current_owner_id != owner_id:
        raise ApiError(409, "integration.changed")
    current = _integration(current_integrations, integration_id)
    if current is None:
        raise ApiError(404, "integration.notFound")
    if current.model_dump(mode="json") != revision:
        raise ApiError(409, "integration.changed")
    tools = await _load_scoped_tools(db, owner_id, for_update=True)
    if payload.policies is not None:
        normalized_policies = dict(payload.policies)
        if normalized_policies:
            tools.integration_policies[str(integration_id)] = normalized_policies
        else:
            tools.integration_policies.pop(str(integration_id), None)
    normalized = {
        resource_key: dict(policies)
        for resource_key, policies in payload.resources.items()
        if policies
    }
    if normalized:
        tools.resource_policies[str(integration_id)] = normalized
    else:
        tools.resource_policies.pop(str(integration_id), None)
    labels = {resource.key: resource.label for resource in resources}
    if labels:
        tools.resource_labels[str(integration_id)] = labels
    else:
        tools.resource_labels.pop(str(integration_id), None)
    if default_resource_changed:
        if payload.default_resource_key is not None:
            tools.default_calendar = DefaultCalendarSettings(
                integration_id=integration_id,
                calendar_key=payload.default_resource_key,
            )
        elif (
            tools.default_calendar is not None
            and tools.default_calendar.integration_id == integration_id
        ):
            tools.default_calendar = None
    if default_task_list_changed:
        if payload.default_task_list_key is not None:
            tools.default_task_list = DefaultTaskListSettings(
                integration_id=integration_id,
                task_list_key=payload.default_task_list_key,
            )
        elif (
            tools.default_task_list is not None
            and tools.default_task_list.integration_id == integration_id
        ):
            tools.default_task_list = None
    await SettingsStore.save(db, tools, user_id=owner_id)
    await db.commit()
    return _access_payload(current, resources, tools)


@router.put("/personal-settings")
async def update_personal_integration_settings(
    payload: PersonalIntegrationSettingsRequest,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    user: Annotated[UserAccount | None, Depends(require_user)] = None,
) -> dict:
    settings, _owner_id = await _load_scoped_integrations(
        db, "instance", user, for_update=True, mutation=True
    )
    assert isinstance(settings, IntegrationSettings)
    settings.personal_integrations_enabled = payload.enabled
    settings.allowed_personal_adapter_types = set(payload.allowed_adapter_types)
    await SettingsStore.save(db, settings)
    await db.commit()
    return {
        "enabled": settings.personal_integrations_enabled,
        "allowedAdapterTypes": sorted(settings.allowed_personal_adapter_types),
    }


@router.get("/personal-settings")
async def get_personal_integration_settings(
    _user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict:
    settings = await SettingsStore.load(db, IntegrationSettings)
    return {
        "enabled": settings.personal_integrations_enabled,
        "allowedAdapterTypes": sorted(settings.allowed_personal_adapter_types),
    }


@router.post("/search/providers", status_code=status.HTTP_201_CREATED)
async def create_search_integration(
    payload: SearchIntegrationRequest,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    scope: SettingsApiScope = "instance",
    user: Annotated[UserAccount | None, Depends(require_user)] = None,
) -> dict:
    _settings, owner_id = await _load_scoped_integrations(
        db, scope, user, mutation=True, adapter_type="searxng"
    )
    settings = await SearchIntegrationService.create(
        db,
        name=payload.name,
        provider_type=payload.provider_type,
        base_url=str(payload.base_url),
        enabled=payload.enabled,
        owner_id=owner_id,
    )
    return search_settings_payload(settings)


@router.put("/search/providers/{integration_id}")
async def update_search_integration(
    integration_id: UUID,
    payload: SearchIntegrationRequest,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    scope: SettingsApiScope = "instance",
    user: Annotated[UserAccount | None, Depends(require_user)] = None,
) -> dict:
    _settings, owner_id = await _load_scoped_integrations(
        db, scope, user, mutation=True, adapter_type="searxng"
    )
    try:
        settings = await SearchIntegrationService.update(
            db,
            integration_id,
            name=payload.name,
            provider_type=payload.provider_type,
            base_url=str(payload.base_url),
            enabled=payload.enabled,
            owner_id=owner_id,
        )
    except IntegrationSettingsError as exc:
        raise ApiError(404, exc.code) from exc
    return search_settings_payload(settings)


@router.delete(
    "/search/providers/{integration_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_search_integration(
    integration_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    scope: SettingsApiScope = "instance",
    user: Annotated[UserAccount | None, Depends(require_user)] = None,
) -> None:
    _settings, owner_id = await _load_scoped_integrations(
        db, scope, user, mutation=True, adapter_type="searxng"
    )
    try:
        await SearchIntegrationService.delete(db, integration_id, owner_id=owner_id)
    except IntegrationSettingsError as exc:
        raise ApiError(404, exc.code) from exc


@router.post("/search/providers/{integration_id}/test")
async def test_search_integration(
    integration_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    scope: SettingsApiScope = "instance",
    user: Annotated[UserAccount | None, Depends(require_user)] = None,
) -> dict[str, str]:
    settings, _owner_id = await _load_scoped_integrations(db, scope, user)
    provider = next(
        (
            item
            for item in settings.instances
            if item.id == integration_id and item.integration_type == "search"
        ),
        None,
    )
    if provider is None:
        raise ApiError(404, "integration.notFound")
    if scope == "personal":
        await _load_scoped_integrations(
            db, scope, user, adapter_type=provider.adapter_type
        )
    try:
        await search_with_provider(provider, "MatoLoop Verbindungstest")
    except SearchIntegrationError as exc:
        logger.warning("Search integration test failed: %s", exc.code)
        raise ApiError(502, exc.code) from exc
    return {"status": "ok"}
