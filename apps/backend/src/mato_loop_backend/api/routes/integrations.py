from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, status
from pydantic import AliasChoices, BaseModel, ConfigDict, Field, HttpUrl, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import mato_loop_backend.api.integration_access as api_integration_access
import mato_loop_backend.api.integration_resources as api_integration_resources
from mato_loop_backend.api.errors import ApiError
from mato_loop_backend.api.integration_access import IntegrationSettingsDocument, SettingsApiScope
from mato_loop_backend.api.integration_resources import ToolSettingsDocument, logger
from mato_loop_backend.db.session import get_db_session
from mato_loop_backend.execution import ExecutionClient, ExecutionClientError
from mato_loop_backend.execution.manager import execution_agent_connections
from mato_loop_backend.integrations.bookstack.adapter import (
    BOOKSTACK_CREDENTIAL_KIND,
    bookstack_error_code,
    load_bookstack_resources,
)
from mato_loop_backend.integrations.bookstack.client import BookStackError
from mato_loop_backend.integrations.caldav.adapter import (
    CALDAV_CREDENTIAL_KIND,
    caldav_error_code,
    load_caldav_resources,
)
from mato_loop_backend.integrations.caldav.client import CalDavError
from mato_loop_backend.integrations.contracts import IntegrationResource
from mato_loop_backend.integrations.mail import (
    MAIL_CREDENTIAL_KIND,
    MailConfig,
    client_for,
    mail_error_code,
)
from mato_loop_backend.integrations.mail_client import MailError
from mato_loop_backend.integrations.mcp_client import McpClientError
from mato_loop_backend.integrations.mcp_service import SonoBinIntegrationService
from mato_loop_backend.integrations.mealie.adapter import (
    MEALIE_CREDENTIAL_KIND,
    MealieConfig,
    mealie_error_code,
)
from mato_loop_backend.integrations.mealie.client import MealieClient, MealieError
from mato_loop_backend.integrations.opencode.adapter import (
    OpenCodeIntegrationService,
    OpenCodeSettingsError,
)
from mato_loop_backend.integrations.opencode.client import OpenCodeError
from mato_loop_backend.integrations.openscad import (
    OpenScadConfig,
    OpenScadDiscoverResult,
    OpenScadTestResult,
)
from mato_loop_backend.integrations.registry import (
    integration_adapter_type_registered,
    integration_adapters,
)
from mato_loop_backend.integrations.search import (
    SearchIntegrationError,
    effective_search_providers,
    search_provider_options,
    search_with_provider,
)
from mato_loop_backend.integrations.service import (
    BookStackIntegrationService,
    CalDavIntegrationService,
    IntegrationSettingsError,
    MailIntegrationService,
    MealieIntegrationService,
    OpenScadIntegrationService,
    SearchIntegrationService,
)
from mato_loop_backend.models import ExecutionAgent, UserAccount
from mato_loop_backend.security import load_credential, read_credential, require_admin, require_user
from mato_loop_backend.settings import (
    DefaultCalendarSettings,
    DefaultTaskListSettings,
    IntegrationAdapterAvailability,
    IntegrationInstance,
    IntegrationSettings,
    SettingsStore,
    ToolSettings,
    UserIntegrationSettings,
    UserToolSettings,
    WebSearchToolSettings,
    load_effective_integrations,
    load_effective_tools,
)
from mato_loop_backend.settings.effective import personal_integration_tool_settings

router = APIRouter(prefix="/integrations", dependencies=[Depends(require_user)])


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
    return personal_integration_tool_settings(instance)


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


class MailIntegrationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    integration_type: Literal["mail"] = "mail"
    provider_type: Literal["imap_smtp"] = "imap_smtp"
    name: str = Field(min_length=1, max_length=160)
    imap_host: str = Field(min_length=1, max_length=253)
    imap_port: int = Field(ge=1, le=65535)
    imap_security: Literal["tls", "starttls"]
    smtp_host: str = Field(min_length=1, max_length=253)
    smtp_port: int = Field(ge=1, le=65535)
    smtp_security: Literal["tls", "starttls"]
    username: str = Field(min_length=1, max_length=320)
    from_address: str = Field(min_length=3, max_length=320)
    password: str | None = Field(default=None, max_length=10_000)
    enabled: bool = True

    @model_validator(mode="after")
    def validate_mail_config(self) -> MailIntegrationRequest:
        MailConfig.model_validate(self.config())
        return self

    def config(self) -> dict:
        return self.model_dump(
            include={
                "imap_host",
                "imap_port",
                "imap_security",
                "smtp_host",
                "smtp_port",
                "smtp_security",
                "username",
                "from_address",
            }
        )


class MailIntegrationCreateRequest(MailIntegrationRequest):
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


class RecipeIntegrationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    integration_type: Literal["recipes"] = "recipes"
    provider_type: Literal["mealie"] = "mealie"
    name: str = Field(min_length=1, max_length=160)
    base_url: HttpUrl
    token: str | None = Field(default=None, max_length=10_000)
    enabled: bool = True

    @model_validator(mode="after")
    def validate_url(self) -> RecipeIntegrationRequest:
        MealieConfig(base_url=self.base_url)
        return self


class RecipeIntegrationCreateRequest(RecipeIntegrationRequest):
    token: str = Field(min_length=1, max_length=10_000)


class SonoBinIntegrationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    integration_type: Literal["music"] = "music"
    provider_type: Literal["sonobin"] = "sonobin"
    name: str = Field(min_length=1, max_length=160)
    base_url: HttpUrl = Field(validation_alias=AliasChoices("baseUrl", "base_url"))
    bearer_token: str | None = Field(
        default=None,
        min_length=1,
        max_length=10_000,
        validation_alias=AliasChoices("bearerToken", "bearer_token"),
    )
    enabled: bool = True

    @model_validator(mode="after")
    def require_sonobin_endpoint(self) -> SonoBinIntegrationRequest:
        if (
            self.base_url.scheme != "https"
            or self.base_url.username is not None
            or self.base_url.password is not None
            or self.base_url.query is not None
            or self.base_url.fragment is not None
            or not self.base_url.path.endswith("/mcp/")
        ):
            raise ValueError("SonoBin requires an HTTPS /mcp/ endpoint")
        return self


class SonoBinIntegrationCreateRequest(SonoBinIntegrationRequest):
    bearer_token: str = Field(
        min_length=1,
        max_length=10_000,
        validation_alias=AliasChoices("bearerToken", "bearer_token"),
    )


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


class OpenCodeIntegrationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    name: str = Field(default="OpenCode", min_length=1, max_length=160)
    base_url: HttpUrl = Field(validation_alias=AliasChoices("baseUrl", "base_url"))
    username: str = Field(default="opencode", min_length=1, max_length=255)
    password: str | None = Field(default=None, max_length=4096)
    remove_password: bool = Field(
        default=False, validation_alias=AliasChoices("removePassword", "remove_password")
    )
    enabled: bool = True

    @model_validator(mode="after")
    def validate_endpoint(self) -> OpenCodeIntegrationRequest:
        if (
            self.base_url.username is not None
            or self.base_url.password is not None
            or self.base_url.query is not None
            or self.base_url.fragment is not None
            or self.password == ""
            or (self.password is not None and self.remove_password)
        ):
            raise ValueError("Invalid OpenCode connection settings")
        return self


class IntegrationDefaultsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

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


class IntegrationCatalogUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    availability: IntegrationAdapterAvailability


def _catalog_item(adapter, settings: IntegrationSettings) -> dict:
    return {
        "integrationType": adapter.integration_type,
        "adapterType": adapter.adapter_type,
        "executionLocation": adapter.execution_location,
        "availability": settings.availability_for(adapter.adapter_type).value,
        **({"personalOnly": True} if adapter.personal_only else {}),
    }


def _personal_settings_payload(settings: IntegrationSettings) -> dict:
    allowed = sorted(
        adapter.adapter_type
        for adapter in integration_adapters()
        if settings.availability_for(adapter.adapter_type)
        == IntegrationAdapterAvailability.FULL
    )
    return {"enabled": bool(allowed), "allowedAdapterTypes": allowed}


def _provider_payload(provider: IntegrationInstance) -> dict:
    return {
        "id": str(provider.id),
        "name": provider.name,
        "integrationType": provider.integration_type,
        "providerType": provider.adapter_type,
        "enabled": provider.enabled,
        "hasCredential": provider.credential_id is not None,
        "config": (
            {
                "imapHost": provider.config.get("imap_host"),
                "imapPort": provider.config.get("imap_port"),
                "imapSecurity": provider.config.get("imap_security"),
                "smtpHost": provider.config.get("smtp_host"),
                "smtpPort": provider.config.get("smtp_port"),
                "smtpSecurity": provider.config.get("smtp_security"),
                "username": provider.config.get("username"),
                "fromAddress": provider.config.get("from_address"),
            }
            if provider.integration_type == "mail"
            else {
                "baseUrl": provider.config.get("base_url")
                or provider.config.get("endpoint"),
                **(
                    {"username": provider.config.get("username")}
                    if "username" in provider.config
                    else {}
                ),
            }
        ),
    }


def _default_resources_payload(
    instance: IntegrationInstance,
    resources: tuple[IntegrationResource, ...],
    tools: ToolSettingsDocument,
) -> dict:
    default_calendar = tools.default_calendar
    default_task_list = tools.default_task_list
    return {
        "integrationId": str(instance.id),
        "resources": [
            {"key": item.key, "groupKey": item.group_key, "label": item.label}
            for item in resources
        ],
        "defaultResourceKey": (
            default_calendar.calendar_key
            if default_calendar is not None
            and default_calendar.integration_id == instance.id
            and any(item.key == default_calendar.calendar_key for item in resources)
            else None
        ),
        "defaultTaskListKey": (
            default_task_list.task_list_key
            if default_task_list is not None
            and default_task_list.integration_id == instance.id
            and any(item.key == default_task_list.task_list_key for item in resources)
            else None
        ),
    }


def search_settings_payload(
    settings: IntegrationSettingsDocument,
    tools: ToolSettingsDocument | None = None,
    provider_settings: IntegrationSettings | None = None,
) -> dict:
    providers = [
        instance for instance in settings.instances if instance.integration_type == "search"
    ]
    payload = {
        **(
            {
                "personalIntegrationsEnabled": _personal_settings_payload(settings)[
                    "enabled"
                ],
                "allowedPersonalAdapterTypes": _personal_settings_payload(settings)[
                    "allowedAdapterTypes"
                ],
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
    if tools is not None:
        available_settings = provider_settings or settings
        primary, fallback = effective_search_providers(tools, available_settings)
        payload.update(
            {
                "primaryProvider": primary,
                "fallbackProvider": fallback,
                "providerOptions": search_provider_options(available_settings),
            }
        )
    return payload


def calendar_settings_payload(settings: IntegrationSettings) -> dict:
    accounts = [
        instance
        for instance in settings.instances
        if instance.integration_type == "calendar" and instance.adapter_type == "caldav"
    ]
    return {
        **(
            {
                "personalIntegrationsEnabled": _personal_settings_payload(settings)[
                    "enabled"
                ],
                "allowedPersonalAdapterTypes": _personal_settings_payload(settings)[
                    "allowedAdapterTypes"
                ],
            }
            if isinstance(settings, IntegrationSettings)
            else {}
        ),
        "configured": bool(accounts),
        "accounts": [_provider_payload(account) for account in accounts],
    }


def mail_settings_payload(settings: IntegrationSettingsDocument) -> dict:
    accounts = [
        instance
        for instance in settings.instances
        if instance.integration_type == "mail" and instance.adapter_type == "imap_smtp"
    ]
    return {
        **(
            {
                "personalIntegrationsEnabled": _personal_settings_payload(settings)[
                    "enabled"
                ],
                "allowedPersonalAdapterTypes": _personal_settings_payload(settings)[
                    "allowedAdapterTypes"
                ],
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
                "personalIntegrationsEnabled": _personal_settings_payload(settings)[
                    "enabled"
                ],
                "allowedPersonalAdapterTypes": _personal_settings_payload(settings)[
                    "allowedAdapterTypes"
                ],
            }
            if isinstance(settings, IntegrationSettings)
            else {}
        ),
        "configured": bool(bases),
        "bases": [_provider_payload(base) for base in bases],
    }


def recipe_settings_payload(settings: IntegrationSettingsDocument) -> dict:
    accounts = [
        instance
        for instance in settings.instances
        if instance.integration_type == "recipes" and instance.adapter_type == "mealie"
    ]
    return {
        **(
            {
                "personalIntegrationsEnabled": _personal_settings_payload(settings)[
                    "enabled"
                ],
                "allowedPersonalAdapterTypes": _personal_settings_payload(settings)[
                    "allowedAdapterTypes"
                ],
            }
            if isinstance(settings, IntegrationSettings)
            else {}
        ),
        "configured": bool(accounts),
        "accounts": [_provider_payload(account) for account in accounts],
    }


def sonobin_settings_payload(settings: IntegrationSettingsDocument) -> dict:
    instances = [
        instance
        for instance in settings.instances
        if instance.integration_type == "music" and instance.adapter_type == "sonobin"
    ]
    return {
        "configured": bool(instances),
        "instances": [
            {
                **_provider_payload(instance),
                "toolCount": len(instance.config.get("tools", [])),
            }
            for instance in instances
        ],
    }


def coding_settings_payload(settings: UserIntegrationSettings) -> dict:
    instances = [
        instance
        for instance in settings.instances
        if instance.integration_type == "coding" and instance.adapter_type == "opencode"
    ]
    return {
        "configured": bool(instances),
        "instances": [
            {
                **_provider_payload(instance),
                "serverVersion": instance.config.get("server_version"),
            }
            for instance in instances
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
    return ExecutionClient(agent_id, snapshot)


@router.get("/catalog")
async def get_integration_catalog(
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict:
    settings = await SettingsStore.load(db, IntegrationSettings)
    return {
        "adapters": [
            _catalog_item(adapter, settings)
            for adapter in integration_adapters()
        ]
    }


@router.put("/catalog/{adapter_type}")
async def update_integration_catalog(
    adapter_type: str,
    payload: IntegrationCatalogUpdateRequest,
    _admin: Annotated[UserAccount, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict:
    adapter = next(
        (item for item in integration_adapters() if item.adapter_type == adapter_type),
        None,
    )
    if adapter is None or not integration_adapter_type_registered(adapter_type):
        raise ApiError(409, "integration.providerUnsupported")
    if (
        (adapter.execution_location == "agent" or adapter.personal_only)
        and payload.availability == IntegrationAdapterAvailability.INSTANCE_ONLY
    ):
        raise ApiError(422, "validation.invalidChoice", field="availability")
    settings = await SettingsStore.load(db, IntegrationSettings, for_update=True)
    if payload.availability == IntegrationAdapterAvailability.FULL:
        settings.adapter_availability.pop(adapter_type, None)
    else:
        settings.adapter_availability[adapter_type] = payload.availability
    await SettingsStore.save(db, settings)
    await db.commit()
    return _catalog_item(adapter, settings)


@router.get("/visible")
async def visible_instance_integrations(
    db: Annotated[AsyncSession, Depends(get_db_session)],
    user: Annotated[UserAccount, Depends(require_user)],
) -> list[dict]:
    instance = await SettingsStore.load(db, IntegrationSettings)
    personal = await SettingsStore.load_optional(db, UserIntegrationSettings, user_id=user.id)
    return [
        {
            "id": str(item.id),
            "name": item.name,
            "integrationType": item.integration_type,
            "providerType": item.adapter_type,
            "adapterType": item.adapter_type,
            "enabled": item.enabled,
            "scope": scope,
            "config": {},
        }
        for scope, settings in (("instance", instance), ("personal", personal))
        if settings is not None
        for item in settings.instances
        if item.integration_type != "mcp"
    ]


@router.get("/search")
async def get_search_integrations(
    db: Annotated[AsyncSession, Depends(get_db_session)],
    scope: SettingsApiScope = "instance",
    user: Annotated[UserAccount | None, Depends(require_user)] = None,
) -> dict:
    settings, owner_id = await api_integration_access._load_scoped_integrations(db, scope, user)
    tools = await _load_scoped_tools(db, owner_id)
    available_settings = (
        await load_effective_integrations(db, owner_id)
        if owner_id is not None
        else settings
    )
    return search_settings_payload(settings, tools, available_settings)


@router.put("/search")
async def update_search_selection(
    payload: WebSearchToolSettings,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    scope: SettingsApiScope = "instance",
    user: Annotated[UserAccount | None, Depends(require_user)] = None,
) -> dict:
    settings, owner_id = await api_integration_access._load_scoped_integrations(
        db, scope, user, for_update=True, mutation=True
    )
    available_settings = (
        await load_effective_integrations(db, owner_id)
        if owner_id is not None
        else settings
    )
    available = {
        str(option["value"]) for option in search_provider_options(available_settings)
    }
    if payload.primary_provider not in available:
        raise ApiError(409, "integration.providerUnavailable", field="primary_provider")
    if (
        payload.fallback_provider is not None
        and payload.fallback_provider not in available
    ):
        raise ApiError(409, "integration.providerUnavailable", field="fallback_provider")
    tools = await _load_scoped_tools(db, owner_id, for_update=True)
    tools.web_search = payload
    await SettingsStore.save(db, tools, user_id=owner_id)
    await db.commit()
    return search_settings_payload(settings, tools, available_settings)


@router.get("/openscad")
async def get_openscad_integrations(
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
    scope: SettingsApiScope = "instance",
) -> dict:
    settings, _owner_id = await api_integration_access._load_scoped_integrations(db, scope, user)
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
    await api_integration_access._load_scoped_integrations(
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
    _settings, owner_id = await api_integration_access._load_scoped_integrations(
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
    _settings, owner_id = await api_integration_access._load_scoped_integrations(
        db, scope, user, mutation=True
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
    await api_integration_access._load_scoped_integrations(
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
    settings, _owner_id = await api_integration_access._load_scoped_integrations(
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
    settings, _owner_id = await api_integration_access._load_scoped_integrations(db, scope, user)
    return calendar_settings_payload(settings)


@router.post("/calendar/accounts", status_code=status.HTTP_201_CREATED)
async def create_calendar_integration(
    payload: CalendarIntegrationCreateRequest,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    scope: SettingsApiScope = "instance",
    user: Annotated[UserAccount | None, Depends(require_user)] = None,
) -> dict:
    _settings, owner_id = await api_integration_access._load_scoped_integrations(
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
    _settings, owner_id = await api_integration_access._load_scoped_integrations(
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
    _settings, owner_id = await api_integration_access._load_scoped_integrations(
        db, scope, user, mutation=True
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
    settings, _owner_id = await api_integration_access._load_scoped_integrations(
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


@router.get("/mail")
async def get_mail_integrations(
    db: Annotated[AsyncSession, Depends(get_db_session)],
    scope: SettingsApiScope = "instance",
    user: Annotated[UserAccount | None, Depends(require_user)] = None,
) -> dict:
    settings, _owner_id = await api_integration_access._load_scoped_integrations(db, scope, user)
    return mail_settings_payload(settings)


@router.post("/mail/accounts", status_code=status.HTTP_201_CREATED)
async def create_mail_integration(
    payload: MailIntegrationCreateRequest,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    scope: SettingsApiScope = "instance",
    user: Annotated[UserAccount | None, Depends(require_user)] = None,
) -> dict:
    _settings, owner_id = await api_integration_access._load_scoped_integrations(
        db, scope, user, mutation=True, adapter_type="imap_smtp"
    )
    try:
        settings = await MailIntegrationService.create(
            db,
            name=payload.name,
            config=payload.config(),
            password=payload.password,
            enabled=payload.enabled,
            owner_id=owner_id,
        )
    except IntegrationSettingsError as exc:
        raise ApiError(
            409 if exc.code == "mail.alreadyConfigured" else 422,
            exc.code,
            field="username" if exc.code == "mail.alreadyConfigured" else "password",
        ) from exc
    return mail_settings_payload(settings)


@router.put("/mail/accounts/{integration_id}")
async def update_mail_integration(
    integration_id: UUID,
    payload: MailIntegrationRequest,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    scope: SettingsApiScope = "instance",
    user: Annotated[UserAccount | None, Depends(require_user)] = None,
) -> dict:
    _settings, owner_id = await api_integration_access._load_scoped_integrations(
        db, scope, user, mutation=True, adapter_type="imap_smtp"
    )
    try:
        settings = await MailIntegrationService.update(
            db,
            integration_id,
            name=payload.name,
            config=payload.config(),
            password=payload.password,
            enabled=payload.enabled,
            owner_id=owner_id,
        )
    except IntegrationSettingsError as exc:
        raise ApiError(
            404
            if exc.code == "integration.notFound"
            else 409
            if exc.code == "mail.alreadyConfigured"
            else 422,
            exc.code,
        ) from exc
    return mail_settings_payload(settings)


@router.delete("/mail/accounts/{integration_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_mail_integration(
    integration_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    scope: SettingsApiScope = "instance",
    user: Annotated[UserAccount | None, Depends(require_user)] = None,
) -> None:
    _settings, owner_id = await api_integration_access._load_scoped_integrations(
        db, scope, user, mutation=True
    )
    try:
        await MailIntegrationService.delete(db, integration_id, owner_id=owner_id)
    except IntegrationSettingsError as exc:
        raise ApiError(404, exc.code) from exc


@router.post("/mail/accounts/{integration_id}/test")
async def test_mail_integration(
    integration_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    scope: SettingsApiScope = "instance",
    user: Annotated[UserAccount | None, Depends(require_user)] = None,
) -> dict[str, str]:
    settings, _owner_id = await api_integration_access._load_scoped_integrations(
        db, scope, user, adapter_type="imap_smtp"
    )
    account = MailIntegrationService._find(settings, integration_id)
    if account is None:
        raise ApiError(404, "integration.notFound")
    try:
        if account.credential_id is None:
            raise MailError("mail.authenticationFailed")
        record = await load_credential(
            db,
            account.credential_id,
            owner_id=account.owner_id,
            populate_existing=True,
        )
        if record is None or record.kind != MAIL_CREDENTIAL_KIND:
            raise MailError("mail.authenticationFailed")
        credential = read_credential(record)
        if set(credential) != {"password"} or not isinstance(
            credential["password"], str
        ):
            raise MailError("mail.authenticationFailed")
        await db.rollback()
        await client_for(account, credential["password"]).test_connection()
    except MailError as exc:
        await db.rollback()
        code = mail_error_code(exc)
        logger.warning("Mail integration test failed: %s", code)
        raise ApiError(502, code) from exc
    except Exception as exc:
        await db.rollback()
        logger.warning("Mail integration test failed with %s", type(exc).__name__)
        raise ApiError(502, "integration.unavailable") from exc
    return {"status": "ok"}


@router.get("/knowledge")
async def get_knowledge_integrations(
    db: Annotated[AsyncSession, Depends(get_db_session)],
    scope: SettingsApiScope = "instance",
    user: Annotated[UserAccount | None, Depends(require_user)] = None,
) -> dict:
    settings, _owner_id = await api_integration_access._load_scoped_integrations(db, scope, user)
    return knowledge_settings_payload(settings)


@router.post("/knowledge/bases", status_code=status.HTTP_201_CREATED)
async def create_knowledge_integration(
    payload: KnowledgeIntegrationCreateRequest,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    scope: SettingsApiScope = "instance",
    user: Annotated[UserAccount | None, Depends(require_user)] = None,
) -> dict:
    _settings, owner_id = await api_integration_access._load_scoped_integrations(
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
    _settings, owner_id = await api_integration_access._load_scoped_integrations(
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
    _settings, owner_id = await api_integration_access._load_scoped_integrations(
        db, scope, user, mutation=True
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
    settings, _owner_id = await api_integration_access._load_scoped_integrations(
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


@router.get("/recipes")
async def get_recipe_integrations(
    db: Annotated[AsyncSession, Depends(get_db_session)],
    scope: SettingsApiScope = "instance",
    user: Annotated[UserAccount | None, Depends(require_user)] = None,
) -> dict:
    settings, _owner_id = await api_integration_access._load_scoped_integrations(db, scope, user)
    return recipe_settings_payload(settings)


@router.post("/recipes/accounts", status_code=status.HTTP_201_CREATED)
async def create_recipe_integration(
    payload: RecipeIntegrationCreateRequest,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    scope: SettingsApiScope = "instance",
    user: Annotated[UserAccount | None, Depends(require_user)] = None,
) -> dict:
    _settings, owner_id = await api_integration_access._load_scoped_integrations(
        db, scope, user, mutation=True, adapter_type="mealie"
    )
    settings = await MealieIntegrationService.create(
        db,
        name=payload.name,
        base_url=str(payload.base_url),
        token=payload.token,
        enabled=payload.enabled,
        owner_id=owner_id,
    )
    return recipe_settings_payload(settings)


@router.put("/recipes/accounts/{integration_id}")
async def update_recipe_integration(
    integration_id: UUID,
    payload: RecipeIntegrationRequest,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    scope: SettingsApiScope = "instance",
    user: Annotated[UserAccount | None, Depends(require_user)] = None,
) -> dict:
    _settings, owner_id = await api_integration_access._load_scoped_integrations(
        db, scope, user, mutation=True, adapter_type="mealie"
    )
    try:
        settings = await MealieIntegrationService.update(
            db,
            integration_id,
            name=payload.name,
            base_url=str(payload.base_url),
            token=payload.token,
            enabled=payload.enabled,
            owner_id=owner_id,
        )
    except IntegrationSettingsError as exc:
        status_code = 404 if exc.code == "integration.notFound" else 422
        raise ApiError(status_code, exc.code, field="token") from exc
    return recipe_settings_payload(settings)


@router.delete(
    "/recipes/accounts/{integration_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_recipe_integration(
    integration_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    scope: SettingsApiScope = "instance",
    user: Annotated[UserAccount | None, Depends(require_user)] = None,
) -> None:
    _settings, owner_id = await api_integration_access._load_scoped_integrations(
        db, scope, user, mutation=True
    )
    try:
        await MealieIntegrationService.delete(db, integration_id, owner_id=owner_id)
    except IntegrationSettingsError as exc:
        raise ApiError(404, exc.code) from exc


@router.post("/recipes/accounts/{integration_id}/test")
async def test_recipe_integration(
    integration_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    scope: SettingsApiScope = "instance",
    user: Annotated[UserAccount | None, Depends(require_user)] = None,
) -> dict[str, str]:
    settings, _owner_id = await api_integration_access._load_scoped_integrations(
        db, scope, user, adapter_type="mealie"
    )
    account = MealieIntegrationService._find(settings, integration_id)
    if account is None:
        raise ApiError(404, "integration.notFound")
    try:
        if account.credential_id is None:
            raise MealieError("mealie.authenticationFailed")
        record = await load_credential(
            db,
            account.credential_id,
            owner_id=account.owner_id,
            populate_existing=True,
        )
        if record is None or record.kind != MEALIE_CREDENTIAL_KIND:
            raise MealieError("mealie.authenticationFailed")
        try:
            credential = read_credential(record)
        except Exception as exc:
            raise MealieError("mealie.authenticationFailed") from exc
        if (
            not isinstance(credential, dict)
            or set(credential) != {"token"}
            or not isinstance(credential["token"], str)
            or not credential["token"]
        ):
            raise MealieError("mealie.authenticationFailed")
        config = MealieConfig.model_validate(account.config)
        await db.rollback()
        async with MealieClient(str(config.base_url), credential["token"]) as client:
            await client.test_connection()
    except MealieError as exc:
        await db.rollback()
        code = mealie_error_code(exc)
        logger.warning("Mealie integration test failed: %s", code)
        raise ApiError(502, code) from exc
    except Exception as exc:
        await db.rollback()
        logger.warning("Mealie integration test failed with %s", type(exc).__name__)
        raise ApiError(502, "integration.unavailable") from exc
    return {"status": "ok"}


@router.get("/music/sonobin")
async def get_sonobin_integrations(
    db: Annotated[AsyncSession, Depends(get_db_session)],
    scope: SettingsApiScope = "instance",
    user: Annotated[UserAccount | None, Depends(require_user)] = None,
) -> dict:
    settings, _owner_id = await api_integration_access._load_scoped_integrations(db, scope, user)
    return sonobin_settings_payload(settings)


@router.post("/music/sonobin", status_code=status.HTTP_201_CREATED)
async def create_sonobin_integration(
    payload: SonoBinIntegrationCreateRequest,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    scope: SettingsApiScope = "instance",
    user: Annotated[UserAccount | None, Depends(require_user)] = None,
) -> dict:
    _settings, owner_id = await api_integration_access._load_scoped_integrations(
        db, scope, user, mutation=True, adapter_type="sonobin"
    )
    try:
        await SonoBinIntegrationService.create(
            db,
            name=payload.name,
            endpoint=str(payload.base_url),
            token=payload.bearer_token,
            enabled=payload.enabled,
            owner_id=owner_id,
        )
    except (McpClientError, IntegrationSettingsError) as exc:
        await db.rollback()
        logger.warning("SonoBin integration creation failed: %s", exc.code)
        raise ApiError(502, exc.code) from exc
    settings, _owner_id = await api_integration_access._load_scoped_integrations(db, scope, user)
    return sonobin_settings_payload(settings)


@router.put("/music/sonobin/{integration_id}")
async def update_sonobin_integration(
    integration_id: UUID,
    payload: SonoBinIntegrationRequest,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    scope: SettingsApiScope = "instance",
    user: Annotated[UserAccount | None, Depends(require_user)] = None,
) -> dict:
    _settings, owner_id = await api_integration_access._load_scoped_integrations(
        db, scope, user, mutation=True, adapter_type="sonobin"
    )
    try:
        await SonoBinIntegrationService.update(
            db,
            integration_id,
            name=payload.name,
            endpoint=str(payload.base_url),
            token=payload.bearer_token,
            remove_token=False,
            enabled=payload.enabled,
            owner_id=owner_id,
        )
    except (McpClientError, IntegrationSettingsError) as exc:
        await db.rollback()
        status_code = 404 if exc.code == "mcp.notFound" else 409 if exc.code == "integration.changed" else 502
        logger.warning("SonoBin integration update failed: %s", exc.code)
        raise ApiError(status_code, exc.code) from exc
    settings, _owner_id = await api_integration_access._load_scoped_integrations(db, scope, user)
    return sonobin_settings_payload(settings)


@router.delete(
    "/music/sonobin/{integration_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_sonobin_integration(
    integration_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    scope: SettingsApiScope = "instance",
    user: Annotated[UserAccount | None, Depends(require_user)] = None,
) -> None:
    _settings, owner_id = await api_integration_access._load_scoped_integrations(
        db, scope, user, mutation=True
    )
    try:
        await SonoBinIntegrationService.delete(db, integration_id, owner_id=owner_id)
    except IntegrationSettingsError as exc:
        raise ApiError(404, exc.code) from exc


@router.post("/music/sonobin/{integration_id}/test")
async def test_sonobin_integration(
    integration_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    scope: SettingsApiScope = "instance",
    user: Annotated[UserAccount | None, Depends(require_user)] = None,
) -> dict[str, str]:
    _settings, owner_id = await api_integration_access._load_scoped_integrations(
        db, scope, user, adapter_type="sonobin"
    )
    try:
        await SonoBinIntegrationService.test(db, integration_id, owner_id=owner_id)
        await db.rollback()
    except (McpClientError, IntegrationSettingsError) as exc:
        await db.rollback()
        status_code = 404 if exc.code == "mcp.notFound" else 502
        logger.warning("SonoBin integration test failed: %s", exc.code)
        raise ApiError(status_code, exc.code) from exc
    return {"status": "ok"}


@router.get("/{integration_id}/access")
async def get_integration_access(
    integration_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    scope: SettingsApiScope = "instance",
    user: Annotated[UserAccount | None, Depends(require_user)] = None,
) -> dict:
    integrations, owner_id = await api_integration_access._load_scoped_integrations(db, scope, user)
    instance = api_integration_access._integration(integrations, integration_id)
    if instance is None:
        raise ApiError(404, "integration.notFound")
    if instance.integration_type != "calendar" or instance.adapter_type != "caldav":
        raise ApiError(409, "integration.resourcePoliciesUnsupported")
    await api_integration_access._load_scoped_integrations(
        db, scope, user, adapter_type=instance.adapter_type
    )
    resources = await api_integration_resources._integration_resources(db, instance)
    tools = await load_effective_tools(db, owner_id) if owner_id is not None else await _load_scoped_tools(db, owner_id)
    return _default_resources_payload(instance, resources, tools)


@router.put("/{integration_id}/access")
async def update_integration_access(
    integration_id: UUID,
    payload: IntegrationDefaultsRequest,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    scope: SettingsApiScope = "instance",
    user: Annotated[UserAccount | None, Depends(require_user)] = None,
) -> dict:
    integrations, owner_id = await api_integration_access._load_scoped_integrations(
        db, scope, user, mutation=True
    )
    instance = api_integration_access._integration(integrations, integration_id)
    if instance is None:
        raise ApiError(404, "integration.notFound")
    if instance.integration_type != "calendar" or instance.adapter_type != "caldav":
        raise ApiError(409, "integration.resourcePoliciesUnsupported")
    await api_integration_access._load_scoped_integrations(
        db, scope, user, mutation=True, adapter_type=instance.adapter_type
    )
    resources = await api_integration_resources._integration_resources(db, instance)
    revision = instance.model_dump(mode="json")
    resource_keys = {resource.key for resource in resources}
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
    current_integrations, current_owner_id = await api_integration_access._load_scoped_integrations(
        db, scope, user, for_update=True, mutation=True
    )
    if current_owner_id != owner_id:
        raise ApiError(409, "integration.changed")
    current = api_integration_access._integration(current_integrations, integration_id)
    if current is None:
        raise ApiError(404, "integration.notFound")
    if current.model_dump(mode="json") != revision:
        raise ApiError(409, "integration.changed")
    tools = await _load_scoped_tools(db, owner_id, for_update=True)
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
    response_tools = (
        await load_effective_tools(db, owner_id)
        if owner_id is not None
        else tools
    )
    return _default_resources_payload(current, resources, response_tools)


@router.get("/personal-settings")
async def get_personal_integration_settings(
    _user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict:
    settings = await SettingsStore.load(db, IntegrationSettings)
    return _personal_settings_payload(settings)


@router.post("/search/providers", status_code=status.HTTP_201_CREATED)
async def create_search_integration(
    payload: SearchIntegrationRequest,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    scope: SettingsApiScope = "instance",
    user: Annotated[UserAccount | None, Depends(require_user)] = None,
) -> dict:
    _settings, owner_id = await api_integration_access._load_scoped_integrations(
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
    _settings, owner_id = await api_integration_access._load_scoped_integrations(
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
    _settings, owner_id = await api_integration_access._load_scoped_integrations(
        db, scope, user, mutation=True
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
    settings, _owner_id = await api_integration_access._load_scoped_integrations(db, scope, user)
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
    await api_integration_access._load_scoped_integrations(
        db, scope, user, adapter_type=provider.adapter_type
    )
    try:
        await search_with_provider(provider, "MatoLoop Verbindungstest")
    except SearchIntegrationError as exc:
        logger.warning("Search integration test failed: %s", exc.code)
        raise ApiError(502, exc.code) from exc
    return {"status": "ok"}


def _raise_opencode_error(exc: OpenCodeSettingsError | OpenCodeError) -> None:
    code = exc.code
    if code == "integration.notFound":
        raise ApiError(404, code) from exc
    if code in {
        "integration.changed",
        "opencode.alreadyConfigured",
        "opencode.integrationReferenced",
    }:
        raise ApiError(409, code) from exc
    if code in {"opencode.invalidUrl", "opencode.invalidRequest", "validation.invalidChoice"}:
        raise ApiError(422, code) from exc
    raise ApiError(502, code) from exc


@router.get("/coding")
async def get_coding_integrations(
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict:
    settings, _owner_id = await api_integration_access._load_scoped_integrations(
        db, "personal", user, adapter_type="opencode"
    )
    return coding_settings_payload(settings)


@router.post("/coding/opencode", status_code=status.HTTP_201_CREATED)
async def create_opencode_integration(
    payload: OpenCodeIntegrationRequest,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict:
    if payload.remove_password:
        raise ApiError(422, "validation.invalidChoice", field="removePassword")
    await api_integration_access._load_scoped_integrations(db, "personal", user, adapter_type="opencode")
    try:
        settings = await OpenCodeIntegrationService.create(
            db,
            user_id=user.id,
            name=payload.name,
            base_url=str(payload.base_url),
            username=payload.username,
            password=payload.password,
            enabled=payload.enabled,
        )
    except (OpenCodeSettingsError, OpenCodeError) as exc:
        _raise_opencode_error(exc)
    return coding_settings_payload(settings)


@router.put("/coding/opencode/{integration_id}")
async def update_opencode_integration(
    integration_id: UUID,
    payload: OpenCodeIntegrationRequest,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict:
    await api_integration_access._load_scoped_integrations(db, "personal", user, adapter_type="opencode")
    try:
        settings = await OpenCodeIntegrationService.update(
            db,
            integration_id,
            user_id=user.id,
            name=payload.name,
            base_url=str(payload.base_url),
            username=payload.username,
            password=payload.password,
            remove_password=payload.remove_password,
            enabled=payload.enabled,
        )
    except (OpenCodeSettingsError, OpenCodeError) as exc:
        _raise_opencode_error(exc)
    return coding_settings_payload(settings)


@router.delete("/coding/opencode/{integration_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_opencode_integration(
    integration_id: UUID,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> None:
    try:
        await OpenCodeIntegrationService.delete(db, integration_id, user_id=user.id)
    except (OpenCodeSettingsError, OpenCodeError) as exc:
        _raise_opencode_error(exc)


@router.post("/coding/opencode/{integration_id}/test")
async def test_opencode_integration(
    integration_id: UUID,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict[str, str]:
    try:
        return await OpenCodeIntegrationService.test(db, integration_id, user_id=user.id)
    except (OpenCodeSettingsError, OpenCodeError) as exc:
        _raise_opencode_error(exc)
