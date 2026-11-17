from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.integrations.service import (
    IntegrationSettingsError,
    _load_integrations,
    save_with_policy_cleanup,
)
from mato_loop_backend.integrations.webiq.adapter import (
    WEBIQ_ADAPTER_TYPES,
    WEBIQ_CREDENTIAL_KIND,
    WebIqConfig,
    WebIqCredentials,
)
from mato_loop_backend.security import (
    delete_credential,
    load_credential,
    read_credential,
    store_credential,
)
from mato_loop_backend.settings import IntegrationInstance, IntegrationSettings


async def credentials(db: AsyncSession, instance: IntegrationInstance) -> WebIqCredentials:
    if instance.owner_id is not None or instance.credential_id is None:
        raise IntegrationSettingsError("integration.invalidConfiguration")
    record = await load_credential(db, instance.credential_id, owner_id=None, populate_existing=True)
    if record is None or record.kind != WEBIQ_CREDENTIAL_KIND:
        raise IntegrationSettingsError("integration.invalidConfiguration")
    try:
        return WebIqCredentials.model_validate(read_credential(record))
    except ValidationError as exc:
        raise IntegrationSettingsError("integration.invalidConfiguration") from exc


def find(settings: IntegrationSettings, integration_id: UUID) -> IntegrationInstance:
    instance = next((
        item for item in settings.instances
        if item.id == integration_id
        and WEBIQ_ADAPTER_TYPES.get(item.integration_type) == item.adapter_type
        and item.owner_id is None
    ), None)
    if instance is None:
        raise IntegrationSettingsError("integration.notFound")
    return instance


class WebIqIntegrationService:
    @staticmethod
    async def save(
        db: AsyncSession, integration_id: UUID | None, *, name: str,
        enabled: bool, api_key: str | None, integration_type: Literal["search", "webfetch"],
        timeout_seconds: int = 30,
    ) -> IntegrationSettings:
        config = WebIqConfig(timeout_seconds=timeout_seconds)
        if api_key is not None:
            try:
                WebIqCredentials(api_key=api_key)
            except ValidationError as exc:
                raise IntegrationSettingsError("validation.invalid", field="api_key") from exc
        settings = await _load_integrations(db, None, for_update=True)
        instance = find(settings, integration_id) if integration_id else IntegrationInstance(
            name=name, integration_type=integration_type,
            adapter_type=WEBIQ_ADAPTER_TYPES[integration_type],
        )
        if instance.integration_type != integration_type:
            raise IntegrationSettingsError("validation.invalidChoice", field="integration_type")
        if api_key is None and instance.credential_id is None:
            raise IntegrationSettingsError("validation.required", field="api_key")
        if api_key is not None:
            previous = instance.credential_id
            credential = await store_credential(
                db, owner_id=None, kind=WEBIQ_CREDENTIAL_KIND, label=name,
                payload={"api_key": api_key},
            )
            instance.credential_id = credential.id
            if previous is not None:
                await delete_credential(db, previous, owner_id=None)
        config_data = config.model_dump(mode="json")
        changed = instance.config != config_data
        instance.name, instance.enabled, instance.config = name, enabled, config_data
        if integration_id is None:
            settings.instances.append(instance)
        return await save_with_policy_cleanup(
            db, settings, owner_id=None,
            clear_policies=instance.id if changed and integration_id else None,
        )

    @staticmethod
    async def delete(db: AsyncSession, integration_id: UUID) -> IntegrationSettings:
        settings = await _load_integrations(db, None, for_update=True)
        instance = find(settings, integration_id)
        if instance.credential_id is not None:
            await delete_credential(db, instance.credential_id, owner_id=None)
        settings.instances.remove(instance)
        return await save_with_policy_cleanup(db, settings, owner_id=None)
