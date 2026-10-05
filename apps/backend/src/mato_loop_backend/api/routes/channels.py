from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.api.errors import ApiError
from mato_loop_backend.db.session import get_db_session
from mato_loop_backend.messaging.policy import (
    CHANNEL_PROVIDERS,
    channel_provider_allowed,
    lock_channel_provider,
)
from mato_loop_backend.messaging.telegram import (
    TELEGRAM_CREDENTIAL_KIND,
    TelegramServiceError,
    endpoint_payload,
    new_pairing,
    probe_bot,
    stop_endpoint_work,
)
from mato_loop_backend.models import (
    AutomationRun,
    MessagingEndpoint,
    MessagingOutbox,
    UserAccount,
)
from mato_loop_backend.security import (
    delete_credential,
    load_credential,
    read_credential,
    require_admin,
    require_user,
    store_credential,
)
from mato_loop_backend.settings import (
    ChannelSettings,
    SettingsStore,
)

router = APIRouter(prefix="/channels", dependencies=[Depends(require_user)])


class TelegramEndpointRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(default="Telegram", min_length=1, max_length=160)
    token: str | None = Field(default=None, min_length=1, max_length=512)
    enabled: bool = True


class TelegramEndpointCreateRequest(TelegramEndpointRequest):
    token: str = Field(min_length=1, max_length=512)


class ChannelProviderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    allowed: bool


class ChannelSettingsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool


async def _ensure_available(db: AsyncSession) -> None:
    if not await channel_provider_allowed(db, "telegram", lock=True):
        raise ApiError(409, "channel.providerDisabled")


async def _owned(
    db: AsyncSession, endpoint_id: UUID, user_id: UUID, *, for_update: bool = False
) -> MessagingEndpoint:
    statement = select(MessagingEndpoint).where(
        MessagingEndpoint.id == endpoint_id,
        MessagingEndpoint.user_id == user_id,
        MessagingEndpoint.provider == "telegram",
        MessagingEndpoint.deleted_at.is_(None),
    )
    if for_update:
        statement = statement.with_for_update()
    endpoint = await db.scalar(statement)
    if endpoint is None:
        raise ApiError(404, "channel.endpointNotFound")
    return endpoint


def _raise_telegram(exc: TelegramServiceError) -> None:
    status_code = 422 if exc.code == "channel.authenticationFailed" else 502
    raise ApiError(status_code, exc.code, field="token") from exc


@router.get("/providers")
async def list_channel_providers(
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict:
    settings = await SettingsStore.load(db, ChannelSettings)
    return {
        "enabled": settings.enabled,
        "providers": [
            {
                "provider": provider,
                "label": label,
                "allowed": provider in settings.allowed_providers,
            }
            for provider, label in CHANNEL_PROVIDERS.items()
        ]
    }


async def _stop_channel_deliveries(db: AsyncSession, provider: str | None = None) -> None:
    endpoint_ids = select(MessagingEndpoint.id)
    if provider is not None:
        endpoint_ids = endpoint_ids.where(MessagingEndpoint.provider == provider)
    await db.execute(
        update(AutomationRun).where(
            AutomationRun.delivery_endpoint_id.in_(endpoint_ids),
            AutomationRun.delivery_status == "pending",
        ).values(delivery_channel_type="app", delivery_endpoint_id=None, delivery_destination=None)
    )
    await db.execute(
        update(MessagingOutbox).where(
            MessagingOutbox.endpoint_id.in_(endpoint_ids),
            MessagingOutbox.status.in_(("pending", "processing")),
        ).values(status="failed", processing_at=None, next_attempt_at=None, error_code="channel.providerDisabled")
    )


@router.put("/settings")
async def update_channel_settings(
    payload: ChannelSettingsRequest,
    _admin: Annotated[UserAccount, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict:
    for provider in sorted(CHANNEL_PROVIDERS):
        await lock_channel_provider(db, provider)
    settings = await SettingsStore.load(db, ChannelSettings, for_update=True)
    settings.enabled = payload.enabled
    if not settings.enabled:
        await _stop_channel_deliveries(db)
    await SettingsStore.save(db, settings)
    await db.commit()
    return {"enabled": settings.enabled}


@router.put("/providers/{provider}")
async def update_channel_provider(
    provider: str,
    payload: ChannelProviderRequest,
    _admin: Annotated[UserAccount, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict:
    if provider not in CHANNEL_PROVIDERS:
        raise ApiError(404, "channel.providerUnsupported")
    await lock_channel_provider(db, provider)
    settings = await SettingsStore.load(db, ChannelSettings, for_update=True)
    if payload.allowed:
        settings.allowed_providers.add(provider)
    else:
        settings.allowed_providers.discard(provider)
        await _stop_channel_deliveries(db, provider)
    await SettingsStore.save(db, settings)
    await db.commit()
    return {
        "provider": provider,
        "label": CHANNEL_PROVIDERS[provider],
        "allowed": payload.allowed,
    }


@router.get("/endpoints")
async def list_channel_endpoints(
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict:
    endpoints = list(
        (
            await db.scalars(
                select(MessagingEndpoint)
                .where(
                    MessagingEndpoint.user_id == user.id,
                    MessagingEndpoint.deleted_at.is_(None),
                )
                .order_by(MessagingEndpoint.created_at, MessagingEndpoint.id)
            )
        ).all()
    )
    return {"endpoints": [endpoint_payload(endpoint) for endpoint in endpoints]}


@router.post("/telegram/endpoints", status_code=status.HTTP_201_CREATED)
async def create_telegram_endpoint(
    payload: TelegramEndpointCreateRequest,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict:
    await _ensure_available(db)
    try:
        external_identity, external_handle = await probe_bot(payload.token)
    except TelegramServiceError as exc:
        _raise_telegram(exc)
    credential = await store_credential(
        db,
        owner_id=user.id,
        kind=TELEGRAM_CREDENTIAL_KIND,
        label=payload.name,
        payload={"token": payload.token},
    )
    endpoint = MessagingEndpoint(
        user_id=user.id,
        credential_id=credential.id,
        provider="telegram",
        name=payload.name.strip(),
        enabled=payload.enabled,
        external_identity=external_identity,
        external_handle=external_handle,
    )
    code = new_pairing(endpoint)
    db.add(endpoint)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise ApiError(409, "http.conflict") from exc
    return endpoint_payload(endpoint, code=code)


@router.put("/telegram/endpoints/{endpoint_id}")
async def update_telegram_endpoint(
    endpoint_id: UUID,
    payload: TelegramEndpointRequest,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict:
    await _ensure_available(db)
    endpoint = await _owned(db, endpoint_id, user.id, for_update=True)
    if payload.token is not None:
        try:
            external_identity, external_handle = await probe_bot(payload.token)
        except TelegramServiceError as exc:
            await db.rollback()
            _raise_telegram(exc)
        duplicate = await db.scalar(
            select(MessagingEndpoint.id).where(
                MessagingEndpoint.provider == "telegram",
                MessagingEndpoint.external_identity == external_identity,
                MessagingEndpoint.id != endpoint.id,
                MessagingEndpoint.deleted_at.is_(None),
            )
        )
        if duplicate is not None:
            raise ApiError(409, "http.conflict", field="token")
        if external_identity != endpoint.external_identity:
            await stop_endpoint_work(db, endpoint.id)
            endpoint.paired_user_id = None
            endpoint.paired_chat_id = None
            endpoint.paired_display_name = None
            endpoint.pairing_code_hash = None
            endpoint.pairing_expires_at = None
            endpoint.current_session_id = None
        credential = (
            await load_credential(db, endpoint.credential_id, owner_id=user.id)
            if endpoint.credential_id
            else None
        )
        credential = await store_credential(
            db,
            owner_id=user.id,
            kind=TELEGRAM_CREDENTIAL_KIND,
            label=payload.name,
            payload={"token": payload.token},
            credential=credential,
        )
        endpoint.credential_id = credential.id
        endpoint.external_identity = external_identity
        endpoint.external_handle = external_handle
        endpoint.last_error_code = None
    endpoint.name = payload.name.strip()
    if endpoint.enabled and not payload.enabled:
        await stop_endpoint_work(db, endpoint.id)
    endpoint.enabled = payload.enabled
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise ApiError(409, "http.conflict", field="token") from exc
    return endpoint_payload(endpoint)


@router.post("/telegram/endpoints/{endpoint_id}/test")
async def test_telegram_endpoint(
    endpoint_id: UUID,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict:
    owner_id = user.id
    await _ensure_available(db)
    endpoint = await _owned(db, endpoint_id, owner_id)
    credential = (
        await load_credential(db, endpoint.credential_id, owner_id=owner_id)
        if endpoint.credential_id
        else None
    )
    if credential is None:
        raise ApiError(409, "channel.authenticationFailed")
    token = read_credential(credential).get("token")
    expected_identity = endpoint.external_identity
    await db.rollback()
    try:
        identity, handle = await probe_bot(token)
    except TelegramServiceError as exc:
        _raise_telegram(exc)
    await _ensure_available(db)
    endpoint = await _owned(db, endpoint_id, owner_id, for_update=True)
    if identity != endpoint.external_identity or endpoint.external_identity != expected_identity:
        raise ApiError(409, "channel.authenticationFailed")
    endpoint.external_handle = handle
    await db.commit()
    return {"ok": True}


@router.post("/telegram/endpoints/{endpoint_id}/pairing")
async def rotate_telegram_pairing(
    endpoint_id: UUID,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict:
    await _ensure_available(db)
    endpoint = await _owned(db, endpoint_id, user.id, for_update=True)
    endpoint.paired_user_id = None
    endpoint.paired_chat_id = None
    endpoint.paired_display_name = None
    endpoint.current_session_id = None
    await stop_endpoint_work(db, endpoint.id)
    code = new_pairing(endpoint)
    await db.commit()
    return endpoint_payload(endpoint, code=code)


@router.delete(
    "/telegram/endpoints/{endpoint_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_telegram_endpoint(
    endpoint_id: UUID,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> None:
    endpoint = await _owned(db, endpoint_id, user.id, for_update=True)
    credential_id = endpoint.credential_id
    endpoint.enabled = False
    endpoint.deleted_at = datetime.now(UTC)
    endpoint.credential_id = None
    endpoint.pairing_code_hash = None
    endpoint.pairing_expires_at = None
    endpoint.paired_user_id = None
    endpoint.paired_chat_id = None
    endpoint.paired_display_name = None
    endpoint.current_session_id = None
    await stop_endpoint_work(db, endpoint.id)
    if credential_id is not None:
        await delete_credential(db, credential_id, owner_id=user.id)
    await db.commit()
