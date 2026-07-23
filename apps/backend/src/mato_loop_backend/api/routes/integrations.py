from __future__ import annotations

import logging
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, Field, HttpUrl
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.api.errors import ApiError
from mato_loop_backend.db.session import get_db_session
from mato_loop_backend.integrations import (
    IntegrationSettingsError,
    SearchIntegrationError,
    SearchIntegrationService,
    search_available,
    search_unavailable_reason,
    search_with_provider,
)
from mato_loop_backend.security import require_user
from mato_loop_backend.settings import SearchSettings, SettingsStore
from mato_loop_backend.settings.models import SearchProviderConfig

router = APIRouter(prefix="/integrations", dependencies=[Depends(require_user)])
logger = logging.getLogger(__name__)


class SearchIntegrationRequest(BaseModel):
    integration_type: Literal["search"] = "search"
    provider_type: Literal["searxng"] = "searxng"
    name: str = Field(min_length=1, max_length=160)
    base_url: HttpUrl
    enabled: bool = True


class DefaultSearchIntegrationRequest(BaseModel):
    integration_id: UUID


def _provider_payload(provider: SearchProviderConfig, *, legacy_disabled: bool = False) -> dict:
    return {
        "id": str(provider.id),
        "name": provider.name,
        "integrationType": "search",
        "providerType": provider.adapter_type,
        "enabled": provider.enabled and not legacy_disabled,
        "hasCredential": provider.credential_id is not None,
        "config": {"baseUrl": provider.config.get("base_url")},
    }


def search_settings_payload(settings: SearchSettings) -> dict:
    legacy_disabled = bool(settings.providers) and not settings.enabled
    return {
        "configured": bool(settings.providers),
        "enabled": settings.enabled,
        "effectiveEnabled": search_available(settings),
        "defaultIntegrationId": (
            str(settings.default_provider_id) if settings.default_provider_id else None
        ),
        "unavailableReason": search_unavailable_reason(settings),
        "providers": [
            _provider_payload(provider, legacy_disabled=legacy_disabled)
            for provider in settings.providers
        ],
    }


@router.get("/search")
async def get_search_integrations(
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict:
    return search_settings_payload(await SettingsStore.load(db, SearchSettings))


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


@router.put("/search/default")
async def set_default_search_integration(
    payload: DefaultSearchIntegrationRequest,
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict:
    try:
        settings = await SearchIntegrationService.set_default(db, payload.integration_id)
    except IntegrationSettingsError as exc:
        raise ApiError(409, exc.code) from exc
    return search_settings_payload(settings)


@router.post("/search/providers/{integration_id}/test")
async def test_search_integration(
    integration_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict[str, str]:
    settings = await SettingsStore.load(db, SearchSettings)
    provider = next((item for item in settings.providers if item.id == integration_id), None)
    if provider is None:
        raise ApiError(404, "integration.notFound")
    try:
        await search_with_provider(provider, "MatoLoop Verbindungstest")
    except SearchIntegrationError as exc:
        logger.warning("Search integration test failed: %s", exc.code)
        raise ApiError(502, exc.code) from exc
    return {"status": "ok"}
