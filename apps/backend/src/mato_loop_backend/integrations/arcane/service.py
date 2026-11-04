from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.integrations.arcane.adapter import ARCANE_CREDENTIAL_KIND
from mato_loop_backend.integrations.service import (
    CredentialIntegrationService,
    IntegrationSettingsDocument,
    IntegrationSettingsError,
    _load_integrations,
)
from mato_loop_backend.settings import IntegrationInstance


class ArcaneIntegrationService(CredentialIntegrationService):
    integration_type = "infrastructure"
    adapter_type = "arcane"
    credential_kind = ARCANE_CREDENTIAL_KIND

    @classmethod
    async def create(cls, db: AsyncSession, *, name: str, base_url: str, api_key: str,
                     enabled: bool, owner_id: UUID | None = None) -> IntegrationSettingsDocument:
        settings = await _load_integrations(db, owner_id, for_update=True)
        instance = IntegrationInstance(name=name, integration_type="infrastructure", adapter_type="arcane",
                                       enabled=enabled, config={"base_url": base_url.rstrip("/")})
        return await cls._create(db, settings, instance, {"api_key": api_key}, owner_id=owner_id)

    @classmethod
    async def update(cls, db: AsyncSession, integration_id: UUID, *, name: str, base_url: str,
                     api_key: str | None, enabled: bool, owner_id: UUID | None = None,
                     expected_instance: IntegrationInstance | None = None) -> IntegrationSettingsDocument:
        settings = await _load_integrations(db, owner_id, for_update=True, expected_instance=expected_instance)
        instance = cls._find(settings, integration_id)
        if instance is None:
            raise IntegrationSettingsError("integration.notFound")
        config = {"base_url": base_url.rstrip("/")}
        changed = instance.config != config or api_key is not None
        return await cls._update(
            db, settings, instance, name=name, config=config, enabled=enabled,
            payload={"api_key": api_key} if api_key is not None else None,
            owner_id=owner_id, connection_changed=changed,
        )

    @classmethod
    async def delete(cls, db: AsyncSession, integration_id: UUID, *, owner_id: UUID | None = None) -> None:
        await super().delete(db, integration_id, owner_id=owner_id)
