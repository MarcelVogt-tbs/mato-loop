from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.integrations.mail.adapter import MAIL_CREDENTIAL_KIND, MailConfig
from mato_loop_backend.integrations.service import (
    CredentialIntegrationService,
    IntegrationSettingsDocument,
    IntegrationSettingsError,
    _clear_integration_policies,
    _load_integrations,
    _save_settings,
)
from mato_loop_backend.settings import IntegrationInstance, IntegrationSettings


class MailIntegrationService(CredentialIntegrationService):
    integration_type = "mail"
    adapter_type = "imap_smtp"
    credential_kind = MAIL_CREDENTIAL_KIND

    @staticmethod
    def _ensure_unique(
        settings: IntegrationSettingsDocument,
        config: dict,
        *,
        excluding: UUID | None = None,
    ) -> None:
        account_key = (
            config["imap_host"].casefold(),
            config["imap_port"],
            config["username"].casefold(),
        )
        if any(
            item.id != excluding
            and item.integration_type == "mail"
            and item.adapter_type == "imap_smtp"
            and (
                str(item.config.get("imap_host", "")).casefold(),
                item.config.get("imap_port"),
                str(item.config.get("username", "")).casefold(),
            )
            == account_key
            for item in settings.instances
        ):
            raise IntegrationSettingsError("mail.alreadyConfigured")

    @staticmethod
    async def _save(
        db: AsyncSession,
        settings: IntegrationSettingsDocument,
        *,
        owner_id: UUID | None,
        clear_policies: UUID | None = None,
    ) -> IntegrationSettings:
        if clear_policies is not None:
            instance = MailIntegrationService._find(settings, clear_policies)
            if instance is not None:
                _clear_integration_policies(instance)
        await _save_settings(db, settings, owner_id)
        await db.commit()
        return settings

    @classmethod
    async def create(
        cls,
        db: AsyncSession,
        *,
        name: str,
        config: dict,
        password: str,
        enabled: bool,
        owner_id: UUID | None = None,
    ) -> IntegrationSettings:
        if not password:
            raise IntegrationSettingsError("validation.required")
        settings = await _load_integrations(db, owner_id, for_update=True)
        cls._ensure_unique(settings, config)
        instance = IntegrationInstance(
            name=name,
            integration_type="mail",
            adapter_type="imap_smtp",
            enabled=enabled,
            config=config,
        )
        return await cls._create(
            db, settings, instance, {"password": password}, owner_id=owner_id,
        )

    @classmethod
    async def update(
        cls,
        db: AsyncSession,
        integration_id: UUID,
        *,
        name: str,
        config: dict,
        password: str | None,
        enabled: bool,
        owner_id: UUID | None = None,
        expected_instance: IntegrationInstance | None = None,
    ) -> IntegrationSettings:
        if password == "":
            raise IntegrationSettingsError("validation.required")
        settings = await _load_integrations(db, owner_id, for_update=True, expected_instance=expected_instance)
        instance = cls._find(settings, integration_id)
        if instance is None:
            raise IntegrationSettingsError("integration.notFound")
        cls._ensure_unique(settings, config, excluding=integration_id)
        connection_changed = (
            MailConfig.model_validate(instance.config).model_dump(exclude={"signature"})
            != MailConfig.model_validate(config).model_dump(exclude={"signature"})
        )
        return await cls._update(
            db, settings, instance, name=name, config=config, enabled=enabled,
            payload={"password": password} if password is not None else None,
            owner_id=owner_id, connection_changed=connection_changed,
        )
