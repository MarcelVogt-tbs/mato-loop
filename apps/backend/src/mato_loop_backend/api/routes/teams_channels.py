from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Response, status
from matoloop_teams_protocol import Identity
from matoloop_teams_protocol.app_package import PACKAGE_VERSION_PATTERN, build_app_package
from pydantic import BaseModel, ConfigDict, Field, HttpUrl
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.api.errors import ApiError
from mato_loop_backend.appearance import AppearanceSettings
from mato_loop_backend.db.session import get_db_session
from mato_loop_backend.messaging.endpoints import endpoint_payload, new_pairing, stop_endpoint_work
from mato_loop_backend.messaging.policy import channel_provider_allowed, lock_channel_provider
from mato_loop_backend.messaging.teams_client import (
    TEAMS_CREDENTIAL_KIND,
    TeamsServiceError,
    relay_client,
    relay_url,
)
from mato_loop_backend.messaging.teams_package import teams_brand_icons
from mato_loop_backend.models import MessagingEndpoint, UserAccount
from mato_loop_backend.security import (
    load_credential,
    require_admin,
    require_user,
    store_credential,
)
from mato_loop_backend.settings import AgentIdentitySettings, SettingsStore
from mato_loop_backend.settings.models import TeamsChannelSettings

router = APIRouter(prefix="/channels/teams", dependencies=[Depends(require_user)])


class TeamsConfigurationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    relayUrl: str = Field(min_length=1, max_length=2083)
    tenantId: UUID
    appId: UUID
    token: str | None = Field(default=None, min_length=43, max_length=1024)
    streamingEnabled: bool | None = None


class TeamsEndpointRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(default="Microsoft Teams", min_length=1, max_length=160, pattern=r"\S")
    enabled: bool = True


class TeamsPackageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    version: str = Field(pattern=PACKAGE_VERSION_PATTERN, max_length=32)
    publisher: str = Field(min_length=1, max_length=32)
    websiteUrl: HttpUrl
    privacyUrl: HttpUrl
    termsUrl: HttpUrl


def configuration_payload(settings: TeamsChannelSettings) -> dict:
    return {
        "relayUrl": settings.relay_url,
        "appId": settings.app_id,
        "tenantId": settings.tenant_id,
        "hasCredential": settings.credential_id is not None,
        "streamingEnabled": settings.streaming_enabled,
    }


async def available(db: AsyncSession) -> TeamsChannelSettings:
    if not await channel_provider_allowed(db, "teams", lock=True):
        raise ApiError(409, "channel.providerDisabled")
    settings = await SettingsStore.load(db, TeamsChannelSettings)
    if not settings.credential_id:
        raise ApiError(409, "channel.relayUnconfigured")
    return settings


async def owned(db: AsyncSession, endpoint_id: UUID, owner: UUID) -> MessagingEndpoint:
    item = await db.scalar(
        select(MessagingEndpoint)
        .where(
            MessagingEndpoint.id == endpoint_id,
            MessagingEndpoint.user_id == owner,
            MessagingEndpoint.provider == "teams",
            MessagingEndpoint.deleted_at.is_(None),
        )
        .with_for_update()
    )
    if item is None:
        raise ApiError(404, "channel.endpointNotFound")
    return item


@router.get("/configuration")
async def configuration(
    _admin: Annotated[UserAccount, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict:
    return configuration_payload(await SettingsStore.load(db, TeamsChannelSettings))


@router.put("/configuration")
async def update_configuration(
    payload: TeamsConfigurationRequest,
    _admin: Annotated[UserAccount, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict:
    try:
        url = relay_url(payload.relayUrl)
    except TeamsServiceError as exc:
        raise ApiError(422, exc.code, field="relayUrl") from None
    await lock_channel_provider(db, "teams")
    settings = await SettingsStore.load(db, TeamsChannelSettings, for_update=True)
    if (settings.credential_id is None or settings.relay_url != url) and payload.token is None:
        raise ApiError(422, "validation.required", field="token")
    if payload.streamingEnabled is not None:
        settings.streaming_enabled = payload.streamingEnabled
    if payload.token is None and (settings.relay_url, settings.app_id, settings.tenant_id) == (
        url, payload.appId, payload.tenantId,
    ):
        await SettingsStore.save(db, settings)
        await db.commit()
        return configuration_payload(settings)
    if payload.token:
        credential = (
            await load_credential(db, settings.credential_id, owner_id=None)
            if settings.credential_id
            else None
        )
        credential = await store_credential(
            db,
            owner_id=None,
            kind=TEAMS_CREDENTIAL_KIND,
            label="Microsoft Teams relay",
            payload={"token": payload.token},
            credential=credential,
        )
        settings.credential_id = credential.id
    settings.relay_url, settings.app_id, settings.tenant_id = url, payload.appId, payload.tenantId
    settings.revision = str(uuid4())
    for endpoint in (
        await db.scalars(
            select(MessagingEndpoint)
            .where(
                MessagingEndpoint.provider == "teams",
                MessagingEndpoint.deleted_at.is_(None),
            )
            .with_for_update()
        )
    ).all():
        await stop_endpoint_work(db, endpoint.id)
        endpoint.paired_chat_id = endpoint.paired_user_id = endpoint.paired_display_name = None
        endpoint.current_session_id = None
        endpoint.pairing_code_hash = endpoint.pairing_expires_at = None
        endpoint.external_identity = f"pending:{endpoint.id}"
        endpoint.channel_state = {}
        endpoint.last_error_code = "channel.pairingChanged"
    await SettingsStore.save(db, settings)
    await db.commit()
    return configuration_payload(settings)


@router.post("/configuration/test")
async def test_configuration(
    _admin: Annotated[UserAccount, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict:
    settings = await SettingsStore.load(db, TeamsChannelSettings)
    try:
        async with relay_client(db, settings) as client:
            result = await client.status(settings)
    except TeamsServiceError as exc:
        raise ApiError(502, exc.code) from None
    return {"ok": True, "receiverOnline": result.online, "pairingLinkConfigured": bool(result.pairing_origin)}


@router.post("/configuration/app-package", response_class=Response)
async def export_app_package(
    payload: TeamsPackageRequest,
    _admin: Annotated[UserAccount, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> Response:
    for field in ("websiteUrl", "privacyUrl", "termsUrl"):
        url: HttpUrl = getattr(payload, field)
        if url.scheme != "https" or url.username is not None or url.password is not None:
            raise ApiError(422, "validation.invalidUrl", field=field)
    settings = await SettingsStore.load(db, TeamsChannelSettings)
    if not settings.app_id:
        raise ApiError(409, "channel.relayUnconfigured")
    identity = await SettingsStore.load(db, AgentIdentitySettings)
    appearance = await SettingsStore.load(db, AppearanceSettings)
    content = build_app_package(
        settings.app_id,
        website=str(payload.websiteUrl),
        privacy_url=str(payload.privacyUrl),
        terms_url=str(payload.termsUrl),
        publisher=payload.publisher,
        version=payload.version,
        name=identity.display_name,
        icons=teams_brand_icons(appearance.logo_png) if appearance.logo_png else None,
    )
    return Response(
        content,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="matoloop-teams-{payload.version}.zip"',
            "Cache-Control": "no-store",
        },
    )


@router.post("/endpoints", status_code=status.HTTP_201_CREATED)
async def create_endpoint(
    payload: TeamsEndpointRequest,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict:
    await available(db)
    await db.execute(select(UserAccount.id).where(UserAccount.id == user.id).with_for_update())
    existing = await db.scalar(
        select(MessagingEndpoint.id).where(
            MessagingEndpoint.provider == "teams",
            MessagingEndpoint.user_id == user.id,
            MessagingEndpoint.deleted_at.is_(None),
        )
    )
    if existing is not None:
        raise ApiError(409, "http.conflict")
    identifier = uuid4()
    item = MessagingEndpoint(
        id=identifier,
        user_id=user.id,
        provider="teams",
        external_identity=f"pending:{identifier}",
        name=payload.name.strip(),
        enabled=payload.enabled,
        channel_state={},
    )
    code = new_pairing(item)
    db.add(item)
    await db.commit()
    return endpoint_payload(item, code=code)


@router.put("/endpoints/{endpoint_id}")
async def update_endpoint(
    endpoint_id: UUID,
    payload: TeamsEndpointRequest,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict:
    await available(db)
    item = await owned(db, endpoint_id, user.id)
    if item.enabled and not payload.enabled:
        await stop_endpoint_work(db, item.id)
    item.name, item.enabled = payload.name.strip(), payload.enabled
    await db.commit()
    return endpoint_payload(item)


@router.post("/endpoints/{endpoint_id}/pairing")
async def pairing(
    endpoint_id: UUID,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict:
    await available(db)
    item = await owned(db, endpoint_id, user.id)
    await stop_endpoint_work(db, item.id)
    item.paired_chat_id = item.paired_user_id = item.paired_display_name = None
    item.current_session_id = None
    item.channel_state = {}
    item.external_identity = f"pending:{item.id}"
    code = new_pairing(item)
    await db.commit()
    return endpoint_payload(item, code=code)


@router.post("/endpoints/{endpoint_id}/confirm")
async def confirm_pairing(
    endpoint_id: UUID,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict:
    settings = await available(db)
    item = await owned(db, endpoint_id, user.id)
    state = item.channel_state or {}
    if (
        state.get("revision") != settings.revision
        or not state.get("candidate")
        or not item.pairing_expires_at
        or item.pairing_expires_at <= datetime.now(UTC)
        or not item.enabled
    ):
        raise ApiError(409, "channel.pairingChanged")
    identity = Identity.model_validate(state["candidate"])
    if identity.app_id != settings.app_id or identity.tenant_id != settings.tenant_id:
        raise ApiError(409, "channel.pairingChanged")
    item.external_identity = f"{identity.tenant_id}:{identity.user_id}"
    item.paired_chat_id, item.paired_user_id = identity.conversation_id, str(identity.user_id)
    item.paired_display_name = identity.display_name
    item.pairing_code_hash = item.pairing_expires_at = None
    item.channel_state = {
        "identity": identity.model_dump(mode="json"),
        "revision": settings.revision,
        "bindingId": str(uuid4()),
        "boundAt": datetime.now(UTC).isoformat(),
    }
    item.last_error_code = None
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise ApiError(409, "http.conflict") from None
    return endpoint_payload(item)


@router.delete("/endpoints/{endpoint_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_endpoint(
    endpoint_id: UUID,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> None:
    item = await owned(db, endpoint_id, user.id)
    await stop_endpoint_work(db, item.id)
    item.enabled, item.deleted_at = False, datetime.now(UTC)
    item.paired_chat_id = item.paired_user_id = item.paired_display_name = None
    item.pairing_code_hash = item.pairing_expires_at = None
    item.current_session_id, item.channel_state = None, {}
    item.external_identity = f"deleted:{item.id}"
    await db.commit()
