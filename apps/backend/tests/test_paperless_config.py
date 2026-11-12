from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

import mato_loop_backend.api.routes.integrations as routes
import mato_loop_backend.integrations.paperless.service as paperless_service
import mato_loop_backend.integrations.service as service
from mato_loop_backend.api.errors import ApiError
from mato_loop_backend.integrations.paperless.adapter import PAPERLESS_CREDENTIAL_KIND
from mato_loop_backend.integrations.paperless.client import PaperlessError
from mato_loop_backend.integrations.paperless.service import PaperlessIntegrationService
from mato_loop_backend.settings import IntegrationInstance, UserIntegrationSettings


async def test_create_stores_owned_token_outside_settings(monkeypatch) -> None:
    owner_id, credential_id = uuid4(), uuid4()
    settings = UserIntegrationSettings()
    monkeypatch.setattr(paperless_service, "_load_integrations", AsyncMock(return_value=settings))
    monkeypatch.setattr(service, "_save_settings", AsyncMock())
    store = AsyncMock(return_value=SimpleNamespace(id=credential_id))
    monkeypatch.setattr(service, "store_credential", store)
    db = AsyncMock()

    result = await PaperlessIntegrationService.create(
        db, name="Archive", base_url="https://paperless.example.test/",
        token="private-token", enabled=True, owner_id=owner_id,
    )

    store.assert_awaited_once_with(
        db, owner_id=owner_id, kind=PAPERLESS_CREDENTIAL_KIND,
        label="Archive", payload={"token": "private-token"},
    )
    assert "private-token" not in result.model_dump_json()
    assert (result.instances[0].owner_id, result.instances[0].credential_id) == (owner_id, credential_id)


async def test_update_without_token_preserves_owned_credential(monkeypatch) -> None:
    owner_id, credential_id = uuid4(), uuid4()
    account = IntegrationInstance(
        name="Archive", integration_type="documents", adapter_type="paperless",
        config={"base_url": "https://paperless.example.test"},
        credential_id=credential_id, owner_id=owner_id,
    )
    settings = UserIntegrationSettings(instances=[account])
    monkeypatch.setattr(paperless_service, "_load_integrations", AsyncMock(return_value=settings))
    monkeypatch.setattr(service, "_save_settings", AsyncMock())
    store, delete = AsyncMock(), AsyncMock()
    monkeypatch.setattr(service, "store_credential", store)
    monkeypatch.setattr(service, "delete_credential", delete)

    result = await PaperlessIntegrationService.update(
        AsyncMock(), account.id, name="Renamed archive", base_url=account.config["base_url"],
        token=None, enabled=True, owner_id=owner_id,
    )

    assert (result.instances[0].name, result.instances[0].credential_id) == ("Renamed archive", credential_id)
    store.assert_not_awaited()
    delete.assert_not_awaited()


@pytest.mark.parametrize("failure,expected", [
    (PaperlessError("paperless.authenticationFailed"), "integration.authenticationFailed"),
    (PaperlessError("paperless.permissionDenied"), "integration.permissionDenied"),
    (RuntimeError("private-server-detail"), "integration.unavailable"),
])
async def test_connection_test_maps_remote_errors(monkeypatch, failure, expected) -> None:
    account = IntegrationInstance(
        name="Archive", integration_type="documents", adapter_type="paperless",
        config={"base_url": "https://paperless.example.test"},
        credential_id=uuid4(), owner_id=uuid4(),
    )
    monkeypatch.setattr(routes.api_integration_access, "_load_scoped_integrations", AsyncMock(
        return_value=(UserIntegrationSettings(instances=[account]), account.owner_id),
    ))
    monkeypatch.setattr(routes, "load_credential", AsyncMock(return_value=SimpleNamespace(kind=PAPERLESS_CREDENTIAL_KIND)))
    monkeypatch.setattr(routes, "read_credential", lambda _record: {"token": "private-token"})
    client = AsyncMock()
    client.__aenter__.return_value.test_connection.side_effect = failure
    monkeypatch.setattr(routes, "PaperlessClient", Mock(return_value=client))

    with pytest.raises(ApiError) as error:
        await routes.test_document_integration(account.id, AsyncMock(), scope="personal", user=SimpleNamespace(id=account.owner_id))

    assert (error.value.status_code, error.value.code) == (502, expected)
