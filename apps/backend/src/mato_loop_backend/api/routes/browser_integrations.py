from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import Field, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import mato_loop_backend.api.browser_errors as api_browser_errors
from mato_loop_backend.db.session import get_db_session
from mato_loop_backend.execution.browser_contracts import (
    BrowserConfig,
    BrowserControlResult,
    BrowserStatusResult,
)
from mato_loop_backend.execution.client import ExecutionClientError
from mato_loop_backend.execution.protocol import WireModel
from mato_loop_backend.integrations.browser import (
    BrowserIntegrationService,
    browser_client,
    browser_settings,
    load_browser_integration,
)
from mato_loop_backend.models import ExecutionAgent, UserAccount
from mato_loop_backend.security import require_user
from mato_loop_backend.settings import UserIntegrationSettings

router = APIRouter(prefix="/integrations/browser", dependencies=[Depends(require_user)])


Db = Annotated[AsyncSession, Depends(get_db_session)]


User = Annotated[UserAccount, Depends(require_user)]


class BrowserIntegrationCreate(WireModel):
    name: str = Field(default="Browser", min_length=1, max_length=160)
    execution_agent_id: UUID = Field(strict=False)
    enabled: bool = True
    config: BrowserConfig = Field(default_factory=BrowserConfig)


class BrowserIntegrationPatch(WireModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    execution_agent_id: UUID | None = Field(default=None, strict=False)
    enabled: bool | None = None
    config: BrowserConfig | None = None

    @model_validator(mode="after")
    def no_explicit_null(self):
        if any(getattr(self, name) is None for name in self.model_fields_set):
            raise ValueError("browser integration fields cannot be null")
        return self


async def browser_settings_payload(db: AsyncSession, owner_id: UUID, settings: UserIntegrationSettings) -> dict:
    owned = set((await db.scalars(select(ExecutionAgent.id).where(
        ExecutionAgent.user_id == owner_id, ExecutionAgent.revoked_at.is_(None),
    ))).all())
    instances = [
        {"id": str(item.id), "name": item.name, "enabled": item.enabled,
          "executionAgentId": str(item.execution_agent_id), "adapterType": "browser", "config": {},
          "supportsIntegrationPolicies": True, "supportsResourcePolicies": False}
        for item in settings.instances
        if item.integration_type == item.adapter_type == "browser" and item.execution_agent_id in owned
    ]
    return {"configured": bool(instances), "instances": instances}


@router.get("")
async def get_browser_integrations(db: Db, user: User, scope: Literal["personal"] = "personal") -> dict:
    try:
        settings = await browser_settings(db, user.id)
    except ExecutionClientError as exc:
        raise api_browser_errors.browser_api_error(exc) from exc
    return await browser_settings_payload(db, user.id, settings)


@router.post("", status_code=201)
async def create_browser_integration(payload: BrowserIntegrationCreate, db: Db, user: User, scope: Literal["personal"] = "personal") -> dict:
    try:
        settings = await BrowserIntegrationService.create(
            db, owner_id=user.id, execution_agent_id=payload.execution_agent_id,
            name=payload.name, enabled=payload.enabled,
        )
    except ExecutionClientError as exc:
        raise api_browser_errors.browser_api_error(exc) from exc
    return await browser_settings_payload(db, user.id, settings)


@router.patch("/{integration_id}")
async def update_browser_integration(integration_id: UUID, payload: BrowserIntegrationPatch, db: Db, user: User, scope: Literal["personal"] = "personal") -> dict:
    try:
        settings = await BrowserIntegrationService.update(
            db, integration_id, owner_id=user.id,
            changes=payload.model_dump(exclude_unset=True, by_alias=False),
        )
    except ExecutionClientError as exc:
        raise api_browser_errors.browser_api_error(exc) from exc
    return await browser_settings_payload(db, user.id, settings)


@router.delete("/{integration_id}")
async def delete_browser_integration(integration_id: UUID, db: Db, user: User, scope: Literal["personal"] = "personal") -> dict:
    try:
        settings = await BrowserIntegrationService.delete(db, integration_id, owner_id=user.id)
    except ExecutionClientError as exc:
        raise api_browser_errors.browser_api_error(exc) from exc
    return await browser_settings_payload(db, user.id, settings)


@router.get("/{integration_id}/status")
@router.post("/{integration_id}/test")
async def browser_integration_status(integration_id: UUID, db: Db, user: User, scope: Literal["personal"] = "personal") -> BrowserStatusResult:
    try:
        instance = await load_browser_integration(db, user.id, integration_id, require_enabled=False)
        return await browser_client(instance.execution_agent_id).request(
            "integration.browser.status", {}, BrowserStatusResult,
        )
    except ExecutionClientError as exc:
        raise api_browser_errors.browser_api_error(exc) from exc


@router.post("/{integration_id}/reset")
async def reset_browser_integration(integration_id: UUID, db: Db, user: User, scope: Literal["personal"] = "personal") -> BrowserControlResult:
    try:
        instance = await load_browser_integration(db, user.id, integration_id, require_enabled=False)
        result = await browser_client(instance.execution_agent_id).request(
            "integration.browser.reset", {"ownerId": str(user.id)}, BrowserControlResult,
        )
        if not result.ok:
            raise ExecutionClientError("browser.actionFailed")
        return result
    except ExecutionClientError as exc:
        raise api_browser_errors.browser_api_error(exc) from exc
