from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.integrations.paperless.adapter import PAPERLESS_CREDENTIAL_KIND
from mato_loop_backend.integrations.service import (
    CredentialIntegrationService,
    IntegrationSettingsError,
    _load_integrations,
)
from mato_loop_backend.settings import IntegrationInstance, IntegrationSettings


class PaperlessIntegrationService(CredentialIntegrationService):
    integration_type = "documents"
    adapter_type = "paperless"
    credential_kind = PAPERLESS_CREDENTIAL_KIND

    @classmethod
    async def create(
        cls,
        db: AsyncSession,
        *,
        name: str,
        base_url: str,
        token: str,
        enabled: bool,
        owner_id: UUID | None = None,
    ) -> IntegrationSettings:
        if not token or not token.strip():
            raise IntegrationSettingsError("validation.required")
        settings = await _load_integrations(db, owner_id, for_update=True)
        instance = IntegrationInstance(
            name=name, integration_type=cls.integration_type, adapter_type=cls.adapter_type,
            enabled=enabled, config={"base_url": base_url.rstrip("/")},
        )
        return await cls._create(db, settings, instance, {"token": token}, owner_id=owner_id)

    @classmethod
    async def update(
        cls,
        db: AsyncSession,
        integration_id: UUID,
        *,
        name: str,
        base_url: str,
        token: str | None,
        enabled: bool,
        owner_id: UUID | None = None,
        expected_instance: IntegrationInstance | None = None,
    ) -> IntegrationSettings:
        settings = await _load_integrations(db, owner_id, for_update=True, expected_instance=expected_instance)
        instance = cls._find(settings, integration_id)
        if instance is None:
            raise IntegrationSettingsError("integration.notFound")
        if token is not None and not token.strip():
            raise IntegrationSettingsError("validation.required")
        config = {"base_url": base_url.rstrip("/")}
        return await cls._update(
            db, settings, instance, name=name, config=config, enabled=enabled,
            payload={"token": token} if token is not None else None,
            owner_id=owner_id, connection_changed=instance.config != config,
        )
