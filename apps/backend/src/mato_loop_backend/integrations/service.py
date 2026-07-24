from __future__ import annotations

from uuid import UUID

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.integrations.registry import validate_integration
from mato_loop_backend.integrations.search import normalize_search_tool_settings
from mato_loop_backend.models import Credential
from mato_loop_backend.settings import (
    IntegrationInstance,
    IntegrationSettings,
    SettingsStore,
    ToolSettings,
)


class IntegrationSettingsError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class SearchIntegrationService:
    @staticmethod
    async def _save(
        db: AsyncSession,
        settings: IntegrationSettings,
        *,
        normalize_tools: bool = False,
    ) -> IntegrationSettings:
        if normalize_tools:
            tool_settings = await SettingsStore.load(db, ToolSettings, for_update=True)
            if normalize_search_tool_settings(tool_settings, settings):
                await SettingsStore.save(db, tool_settings)
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
    ) -> IntegrationSettings:
        settings = await SettingsStore.load(db, IntegrationSettings, for_update=True)
        instance = IntegrationInstance(
            name=name,
            integration_type="search",
            adapter_type=provider_type,
            enabled=enabled,
            config={"base_url": base_url.rstrip("/")},
        )
        validate_integration(instance)
        settings.instances.append(instance)
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
    ) -> IntegrationSettings:
        settings = await SettingsStore.load(db, IntegrationSettings, for_update=True)
        instance = next(
            (
                item
                for item in settings.instances
                if item.id == integration_id
                and item.integration_type == "search"
                and item.adapter_type == provider_type
            ),
            None,
        )
        if instance is None:
            raise IntegrationSettingsError("integration.notFound")
        instance.name = name
        instance.enabled = enabled
        instance.config = {"base_url": base_url.rstrip("/")}
        validate_integration(instance)
        return await cls._save(db, settings, normalize_tools=True)

    @classmethod
    async def delete(
        cls, db: AsyncSession, integration_id: UUID
    ) -> IntegrationSettings:
        settings = await SettingsStore.load(db, IntegrationSettings, for_update=True)
        instance = next(
            (
                item
                for item in settings.instances
                if item.id == integration_id and item.integration_type == "search"
            ),
            None,
        )
        if instance is None:
            raise IntegrationSettingsError("integration.notFound")
        settings.instances = [item for item in settings.instances if item.id != integration_id]
        if instance.credential_id:
            await db.execute(delete(Credential).where(Credential.id == instance.credential_id))
        return await cls._save(db, settings, normalize_tools=True)
