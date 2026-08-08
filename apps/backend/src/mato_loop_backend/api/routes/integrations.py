from __future__ import annotations

import logging
import re
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.agent.tools import (
    resolve_tool_execution_policy,
    tool_registry,
)
from mato_loop_backend.api.errors import ApiError
from mato_loop_backend.db.session import get_db_session
from mato_loop_backend.integrations import (
    BOOKSTACK_CREDENTIAL_KIND,
    CALDAV_CREDENTIAL_KIND,
    BookStackIntegrationService,
    CalDavIntegrationService,
    IntegrationSettingsError,
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
from mato_loop_backend.integrations.registry import integration_adapter
from mato_loop_backend.models import Credential
from mato_loop_backend.security import read_credential, require_user
from mato_loop_backend.settings import (
    IntegrationInstance,
    IntegrationSettings,
    SettingsStore,
    ToolExecutionPolicy,
    ToolSettings,
)

router = APIRouter(prefix="/integrations", dependencies=[Depends(require_user)])
logger = logging.getLogger(__name__)
_RESOURCE_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,499}$")


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


class IntegrationAccessRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    policies: dict[str, ToolExecutionPolicy] | None = None
    resources: dict[str, dict[str, ToolExecutionPolicy]] = Field(default_factory=dict)


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
            record = await db.get(
                Credential, instance.credential_id, populate_existing=True
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
    return {
        "integrationId": str(instance.id),
        "actions": actions,
        "resources": [
            {
                "key": resource.key,
                "label": resource.label.strip(),
                "hardUnavailableCapabilities": sorted(
                    resource.hard_unavailable_capabilities
                ),
                "policies": {
                    capability: policy.value
                    for capability, policy in resource_policies.get(
                        resource.key, {}
                    ).items()
                    if capability in capabilities
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
                },
            }
            for resource in resources
        ],
    }


def search_settings_payload(settings: IntegrationSettings) -> dict:
    providers = [
        instance for instance in settings.instances if instance.integration_type == "search"
    ]
    return {
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
        "configured": bool(bases),
        "bases": [_provider_payload(base) for base in bases],
    }


@router.get("/search")
async def get_search_integrations(
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict:
    return search_settings_payload(await SettingsStore.load(db, IntegrationSettings))


@router.get("/calendar")
async def get_calendar_integrations(
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict:
    return calendar_settings_payload(await SettingsStore.load(db, IntegrationSettings))


@router.post("/calendar/accounts", status_code=status.HTTP_201_CREATED)
async def create_calendar_integration(
    payload: CalendarIntegrationCreateRequest,
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict:
    settings = await CalDavIntegrationService.create(
        db,
        name=payload.name,
        base_url=str(payload.base_url),
        username=payload.username,
        password=payload.password,
        enabled=payload.enabled,
    )
    return calendar_settings_payload(settings)


@router.put("/calendar/accounts/{integration_id}")
async def update_calendar_integration(
    integration_id: UUID,
    payload: CalendarIntegrationRequest,
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict:
    try:
        settings = await CalDavIntegrationService.update(
            db,
            integration_id,
            name=payload.name,
            base_url=str(payload.base_url),
            username=payload.username,
            password=payload.password,
            enabled=payload.enabled,
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
) -> None:
    try:
        await CalDavIntegrationService.delete(db, integration_id)
    except IntegrationSettingsError as exc:
        raise ApiError(404, exc.code) from exc


@router.post("/calendar/accounts/{integration_id}/test")
async def test_calendar_integration(
    integration_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict[str, str]:
    settings = await SettingsStore.load(db, IntegrationSettings)
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
        record = await db.get(Credential, account.credential_id, populate_existing=True)
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
) -> dict:
    return knowledge_settings_payload(await SettingsStore.load(db, IntegrationSettings))


@router.post("/knowledge/bases", status_code=status.HTTP_201_CREATED)
async def create_knowledge_integration(
    payload: KnowledgeIntegrationCreateRequest,
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict:
    settings = await BookStackIntegrationService.create(
        db,
        name=payload.name,
        base_url=str(payload.base_url),
        token_id=payload.token_id,
        token_secret=payload.token_secret,
        enabled=payload.enabled,
    )
    return knowledge_settings_payload(settings)


@router.put("/knowledge/bases/{integration_id}")
async def update_knowledge_integration(
    integration_id: UUID,
    payload: KnowledgeIntegrationRequest,
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict:
    try:
        settings = await BookStackIntegrationService.update(
            db,
            integration_id,
            name=payload.name,
            base_url=str(payload.base_url),
            token_id=payload.token_id,
            token_secret=payload.token_secret,
            enabled=payload.enabled,
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
) -> None:
    try:
        await BookStackIntegrationService.delete(db, integration_id)
    except IntegrationSettingsError as exc:
        raise ApiError(404, exc.code) from exc


@router.post("/knowledge/bases/{integration_id}/test")
async def test_knowledge_integration(
    integration_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict[str, str]:
    settings = await SettingsStore.load(db, IntegrationSettings)
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
        record = await db.get(Credential, base.credential_id, populate_existing=True)
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
) -> dict:
    integrations = await SettingsStore.load(db, IntegrationSettings)
    instance = _integration(integrations, integration_id)
    if instance is None:
        raise ApiError(404, "integration.notFound")
    resources = await _integration_resources(db, instance)
    tools = await SettingsStore.load(db, ToolSettings)
    return _access_payload(instance, resources, tools)


@router.put("/{integration_id}/access")
async def update_integration_access(
    integration_id: UUID,
    payload: IntegrationAccessRequest,
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict:
    integrations = await SettingsStore.load(db, IntegrationSettings)
    instance = _integration(integrations, integration_id)
    if instance is None:
        raise ApiError(404, "integration.notFound")
    resources = await _integration_resources(db, instance)
    revision = instance.model_dump(mode="json")
    adapter = integration_adapter(instance.integration_type, instance.adapter_type)
    if adapter is None:
        raise ApiError(409, "integration.resourcePoliciesUnsupported")
    resource_actions = tool_registry.integration_resource_actions(instance)
    if not resource_actions:
        raise ApiError(409, "integration.resourcePoliciesUnsupported")
    resource_keys = {resource.key for resource in resources}
    capabilities = {action.capability for action in resource_actions}
    actions = {action.capability: action for action in resource_actions}
    if set(payload.resources) - resource_keys:
        raise ApiError(422, "validation.invalidChoice", field="resources")
    if any(set(policies) - capabilities for policies in payload.resources.values()):
        raise ApiError(422, "validation.invalidChoice", field="resources")
    if payload.policies is not None and set(payload.policies) - capabilities:
        raise ApiError(422, "validation.invalidChoice", field="policies")
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

    current_integrations = await SettingsStore.load(
        db, IntegrationSettings, for_update=True
    )
    current = _integration(current_integrations, integration_id)
    if current is None:
        raise ApiError(404, "integration.notFound")
    if current.model_dump(mode="json") != revision:
        raise ApiError(409, "integration.changed")
    tools = await SettingsStore.load(db, ToolSettings, for_update=True)
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
    await SettingsStore.save(db, tools)
    await db.commit()
    return _access_payload(current, resources, tools)


@router.post("/search/providers", status_code=status.HTTP_201_CREATED)
async def create_search_integration(
    payload: SearchIntegrationRequest,
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict:
    settings = await SearchIntegrationService.create(
        db,
        name=payload.name,
        provider_type=payload.provider_type,
        base_url=str(payload.base_url),
        enabled=payload.enabled,
    )
    return search_settings_payload(settings)


@router.put("/search/providers/{integration_id}")
async def update_search_integration(
    integration_id: UUID,
    payload: SearchIntegrationRequest,
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict:
    try:
        settings = await SearchIntegrationService.update(
            db,
            integration_id,
            name=payload.name,
            provider_type=payload.provider_type,
            base_url=str(payload.base_url),
            enabled=payload.enabled,
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
) -> None:
    try:
        await SearchIntegrationService.delete(db, integration_id)
    except IntegrationSettingsError as exc:
        raise ApiError(404, exc.code) from exc


@router.post("/search/providers/{integration_id}/test")
async def test_search_integration(
    integration_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict[str, str]:
    settings = await SettingsStore.load(db, IntegrationSettings)
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
    try:
        await search_with_provider(provider, "MatoLoop Verbindungstest")
    except SearchIntegrationError as exc:
        logger.warning("Search integration test failed: %s", exc.code)
        raise ApiError(502, exc.code) from exc
    return {"status": "ok"}
