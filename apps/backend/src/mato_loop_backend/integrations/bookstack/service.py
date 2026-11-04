from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.integrations.bookstack.adapter import BOOKSTACK_CREDENTIAL_KIND
from mato_loop_backend.integrations.service import (
    CredentialIntegrationService,
    IntegrationSettingsError,
    _load_integrations,
)
from mato_loop_backend.settings import IntegrationInstance, IntegrationSettings


class BookStackIntegrationService(CredentialIntegrationService):
    integration_type = "knowledge"
    adapter_type = "bookstack"
    credential_kind = BOOKSTACK_CREDENTIAL_KIND

    @classmethod
    async def create(
        cls,
        db: AsyncSession,
        *,
        name: str,
        base_url: str,
        token_id: str,
        token_secret: str,
        enabled: bool,
        owner_id: UUID | None = None,
    ) -> IntegrationSettings:
        if not token_id or not token_secret:
            raise IntegrationSettingsError("validation.required")
        settings = await _load_integrations(db, owner_id, for_update=True)
        instance = IntegrationInstance(
            name=name, integration_type=cls.integration_type, adapter_type=cls.adapter_type,
            enabled=enabled, config={"base_url": base_url},
        )
        return await cls._create(
            db, settings, instance, {"token_id": token_id, "token_secret": token_secret},
            owner_id=owner_id,
        )

    @classmethod
    async def update(
        cls,
        db: AsyncSession,
        integration_id: UUID,
        *,
        name: str,
        base_url: str,
        token_id: str | None,
        token_secret: str | None,
        enabled: bool,
        owner_id: UUID | None = None,
        expected_instance: IntegrationInstance | None = None,
    ) -> IntegrationSettings:
        settings = await _load_integrations(db, owner_id, for_update=True, expected_instance=expected_instance)
        instance = cls._find(settings, integration_id)
        if instance is None:
            raise IntegrationSettingsError("integration.notFound")
        if token_id == "" or token_secret == "":
            raise IntegrationSettingsError("validation.required")
        if (token_id is None) != (token_secret is None):
            raise IntegrationSettingsError("validation.required")
        config = {"base_url": base_url}
        return await cls._update(
            db, settings, instance, name=name, config=config, enabled=enabled,
            payload={"token_id": token_id, "token_secret": token_secret} if token_id is not None else None,
            owner_id=owner_id, connection_changed=instance.config != config,
        )
