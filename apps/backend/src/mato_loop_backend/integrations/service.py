from __future__ import annotations

from uuid import UUID

from sqlalchemy import delete, update
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.integrations.search import search_available
from mato_loop_backend.models import ChatSession, Credential, WebSearchPolicy
from mato_loop_backend.settings import SearchSettings, SettingsStore
from mato_loop_backend.settings.models import SearchProviderConfig


class IntegrationSettingsError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class SearchIntegrationService:
    @staticmethod
    def _normalize_legacy_state(settings: SearchSettings) -> None:
        if settings.providers and not settings.enabled:
            for provider in settings.providers:
                provider.enabled = False

    @staticmethod
    def _sync_default(settings: SearchSettings) -> None:
        enabled = [
            provider
            for provider in settings.providers
            if provider.enabled and provider.config.get("base_url")
        ]
        current = next(
            (provider for provider in enabled if provider.id == settings.default_provider_id), None
        )
        settings.default_provider_id = current.id if current else (enabled[0].id if enabled else None)
        settings.enabled = settings.default_provider_id is not None

    @classmethod
    async def _save(cls, db: AsyncSession, settings: SearchSettings) -> SearchSettings:
        cls._sync_default(settings)
        if not search_available(settings):
            await db.execute(
                update(ChatSession)
                .where(ChatSession.web_search_policy == WebSearchPolicy.REQUIRED.value)
                .values(web_search_policy=WebSearchPolicy.AVAILABLE.value)
            )
        await SettingsStore.save(db, settings)
        await db.commit()
        return settings

    @classmethod
    async def create(
        cls,
        db: AsyncSession,
        *,
        name: str,
        provider_type: str,
        base_url: str,
        enabled: bool,
    ) -> SearchSettings:
        settings = await SettingsStore.load(db, SearchSettings, for_update=True)
        cls._normalize_legacy_state(settings)
        settings.providers.append(
            SearchProviderConfig(
                name=name,
                adapter_type=provider_type,
                enabled=enabled,
                config={"base_url": base_url.rstrip("/")},
            )
        )
        return await cls._save(db, settings)

    @classmethod
    async def update(
        cls,
        db: AsyncSession,
        integration_id: UUID,
        *,
        name: str,
        provider_type: str,
        base_url: str,
        enabled: bool,
    ) -> SearchSettings:
        settings = await SettingsStore.load(db, SearchSettings, for_update=True)
        cls._normalize_legacy_state(settings)
        provider = next(
            (
                item
                for item in settings.providers
                if item.id == integration_id and item.adapter_type == provider_type
            ),
            None,
        )
        if provider is None:
            raise IntegrationSettingsError("integration.notFound")
        provider.name = name
        provider.enabled = enabled
        provider.config = {"base_url": base_url.rstrip("/")}
        return await cls._save(db, settings)

    @classmethod
    async def delete(
        cls, db: AsyncSession, integration_id: UUID
    ) -> SearchSettings:
        settings = await SettingsStore.load(db, SearchSettings, for_update=True)
        cls._normalize_legacy_state(settings)
        provider = next((item for item in settings.providers if item.id == integration_id), None)
        if provider is None:
            raise IntegrationSettingsError("integration.notFound")
        settings.providers = [item for item in settings.providers if item.id != integration_id]
        if provider.credential_id:
            await db.execute(delete(Credential).where(Credential.id == provider.credential_id))
        return await cls._save(db, settings)

    @classmethod
    async def set_default(
        cls, db: AsyncSession, integration_id: UUID
    ) -> SearchSettings:
        settings = await SettingsStore.load(db, SearchSettings, for_update=True)
        cls._normalize_legacy_state(settings)
        provider = next(
            (
                item
                for item in settings.providers
                if item.id == integration_id
                and item.enabled
                and item.config.get("base_url")
            ),
            None,
        )
        if provider is None:
            raise IntegrationSettingsError("integration.providerUnavailable")
        settings.default_provider_id = provider.id
        return await cls._save(db, settings)
