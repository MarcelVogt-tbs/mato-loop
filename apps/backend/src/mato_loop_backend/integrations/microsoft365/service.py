from __future__ import annotations

from uuid import UUID

from mato_loop_backend.integrations.microsoft365.client import Microsoft365Config, Microsoft365Error
from mato_loop_backend.integrations.microsoft365.oauth import (
    CREDENTIAL_KIND,
    OAuthCredential,
    credential_payload,
)
from mato_loop_backend.integrations.service import (
    CredentialIntegrationService,
    IntegrationSettingsError,
    _load_integrations,
)
from mato_loop_backend.security import load_credential
from mato_loop_backend.settings import IntegrationInstance


class Microsoft365Service(CredentialIntegrationService):
    integration_type = "microsoft365"
    adapter_type = "microsoft_graph"
    credential_kind = CREDENTIAL_KIND

    @classmethod
    async def save(
        cls,
        db,
        *,
        owner_id: UUID,
        integration_id: UUID | None,
        name: str,
        config: Microsoft365Config,
        enabled: bool,
        client_secret: str | None,
    ):
        settings = await _load_integrations(db, owner_id, for_update=True)
        if integration_id is None:
            instance = IntegrationInstance(
                name=name,
                integration_type=cls.integration_type,
                adapter_type=cls.adapter_type,
                enabled=enabled,
                config=config.model_dump(mode="json"),
            )
            credential = OAuthCredential(config=config, client_secret=client_secret)
            if config.auth_flow == "browser" and not credential.client_secret:
                raise Microsoft365Error("microsoft365.configurationInvalid")
            return await cls._create(
                db,
                settings,
                instance,
                credential.model_dump(mode="json"),
                owner_id=owner_id,
            )
        instance = cls._find(settings, integration_id)
        if instance is None:
            raise IntegrationSettingsError("integration.notFound")
        previous_config = Microsoft365Config.model_validate(instance.config)
        changed = previous_config.authorization_binding() != config.authorization_binding() or client_secret is not None
        credential = None
        if changed:
            secret = client_secret
            if secret is None and (
                config.tenant_id, config.client_id, config.auth_flow
            ) == (
                previous_config.tenant_id, previous_config.client_id, previous_config.auth_flow
            ):
                record = await load_credential(db, instance.credential_id, owner_id=owner_id)
                secret = credential_payload(record, previous_config).client_secret
            if config.auth_flow == "browser" and not secret:
                raise Microsoft365Error("microsoft365.configurationInvalid")
            credential = OAuthCredential(
                config=config,
                client_secret=secret if config.auth_flow == "browser" else None,
            ).model_dump(mode="json")
        elif previous_config != config:
            record = await load_credential(db, instance.credential_id, owner_id=owner_id)
            payload = credential_payload(record, previous_config)
            payload.config = config
            credential = payload.model_dump(mode="json")
        return await cls._update(
            db,
            settings,
            instance,
            name=name,
            config=config.model_dump(mode="json"),
            enabled=enabled,
            payload=credential,
            owner_id=owner_id,
            connection_changed=changed,
        )

    @classmethod
    async def authorize(cls, db, attempt):
        instance = attempt.instance
        settings = await _load_integrations(
            db,
            instance.owner_id,
            for_update=True,
            expected_instance=instance,
        )
        current = cls._find(settings, instance.id)
        if current is None:
            raise IntegrationSettingsError("integration.notFound")
        await cls._replace_credential(
            db,
            current,
            attempt.credential.model_dump(mode="json"),
            owner_id=instance.owner_id,
        )
        return await cls._save(
            db,
            settings,
            owner_id=instance.owner_id,
            clear_policies=current.id,
        )

    @classmethod
    async def disconnect(cls, db, integration_id: UUID, *, owner_id: UUID):
        settings = await _load_integrations(db, owner_id, for_update=True)
        instance = cls._find(settings, integration_id)
        if instance is None:
            raise IntegrationSettingsError("integration.notFound")
        config = Microsoft365Config.model_validate(instance.config)
        record = await load_credential(db, instance.credential_id, owner_id=owner_id)
        payload = credential_payload(record, config)
        payload.tokens, payload.expires_at, payload.authorization_required = None, 0, False
        await cls._replace_credential(
            db, instance, payload.model_dump(mode="json"), owner_id=owner_id
        )
        return await cls._save(db, settings, owner_id=owner_id, clear_policies=instance.id)
