from __future__ import annotations

import logging
from typing import Annotated
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, Form, Request, Response
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.api.errors import ApiError
from mato_loop_backend.api.integration_access import SettingsApiScope, _load_scoped_integrations
from mato_loop_backend.config import get_runtime_config
from mato_loop_backend.db.session import get_db_session
from mato_loop_backend.integrations.microsoft365.client import (
    GraphClient,
    Microsoft365Config,
    Microsoft365Error,
)
from mato_loop_backend.integrations.microsoft365.oauth import (
    CALLBACK_PATH,
    LoginAttempt,
    access_token,
    credential_payload,
    oauth_logins,
)
from mato_loop_backend.integrations.microsoft365.service import Microsoft365Service
from mato_loop_backend.integrations.service import IntegrationSettingsError
from mato_loop_backend.models import UserAccount
from mato_loop_backend.public_url import resolve_public_base_url
from mato_loop_backend.security import hash_token, load_credential, require_user
from mato_loop_backend.settings import GeneralSettings, IntegrationInstance, SettingsStore

router = APIRouter(prefix="/integrations/microsoft365", dependencies=[Depends(require_user)])
callback_router = APIRouter(prefix="/integrations/microsoft365")
logger = logging.getLogger(__name__)
Db = Annotated[AsyncSession, Depends(get_db_session)]
User = Annotated[UserAccount, Depends(require_user)]


class ConnectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=160)
    config: Microsoft365Config
    enabled: bool = True
    client_secret: str | None = Field(default=None, min_length=1, max_length=10_000)


def _api_error(exc: Microsoft365Error | IntegrationSettingsError) -> ApiError:
    logger.warning("Microsoft 365 operation failed: %s", exc.code)
    code = exc.code
    status = (
        404
        if code in {"integration.notFound", "microsoft365.loginExpired"}
        else 409
        if code
        in {"integration.changed", "microsoft365.loginBusy", "microsoft365.authorizationRequired"}
        else 422
        if code == "microsoft365.configurationInvalid"
        else 429
        if code == "microsoft365.throttled"
        else 502
    )
    return ApiError(status, code)


async def _settings(
    db: AsyncSession, scope: SettingsApiScope, user: UserAccount, *, mutation=False
):
    return await _load_scoped_integrations(
        db,
        scope,
        user,
        mutation=mutation,
        adapter_type="microsoft_graph",
    )


def _instance(settings, integration_id: UUID) -> IntegrationInstance:
    instance = Microsoft365Service._find(settings, integration_id)
    if instance is None:
        raise ApiError(404, "integration.notFound")
    return instance


def _session_hash(request: Request) -> str:
    return hash_token(request.cookies.get(get_runtime_config().session_cookie_name, ""))


async def _callback_uri(db: AsyncSession, request: Request) -> str:
    settings = await SettingsStore.load(db, GeneralSettings)
    try:
        base, _source = resolve_public_base_url(settings.public_base_url, request)
        url = httpx.URL(base)
        if url.scheme != "https" and not (url.scheme == "http" and url.host == "localhost"):
            raise ValueError("A browser callback requires HTTPS or localhost")
        return base + CALLBACK_PATH
    except ValueError as exc:
        raise Microsoft365Error("microsoft365.configurationInvalid") from exc


async def _payload(db, instance: IntegrationInstance) -> dict:
    config = Microsoft365Config.model_validate(instance.config)
    record = await load_credential(db, instance.credential_id, owner_id=instance.owner_id)
    credential = credential_payload(record, config)
    return {
        "id": str(instance.id),
        "name": instance.name,
        "integrationType": "microsoft365",
        "providerType": "microsoft_graph",
        "enabled": instance.enabled,
        "config": config.model_dump(mode="json"),
        "hasClientSecret": bool(credential.client_secret),
        "hasCredential": credential.tokens is not None,
        "authStatus": "authorized"
        if credential.tokens and not credential.authorization_required
        else "authorization_required",
    }


@router.get("")
async def connections(db: Db, user: User, scope: SettingsApiScope = "personal"):
    # The common settings loader also requests empty collections in the global view.
    if scope == "instance":
        await _load_scoped_integrations(db, scope, user)
        return {"configured": False, "accounts": []}
    settings, _owner = await _load_scoped_integrations(db, scope, user)
    try:
        accounts = [
            await _payload(db, item)
            for item in settings.instances
            if item.integration_type == "microsoft365" and item.adapter_type == "microsoft_graph"
        ]
    except Microsoft365Error as exc:
        raise _api_error(exc) from exc
    return {"configured": bool(accounts), "accounts": accounts}


@router.post("", status_code=201)
async def create_connection(
    payload: ConnectionRequest, db: Db, user: User, scope: SettingsApiScope = "personal"
):
    _current, owner_id = await _settings(db, scope, user, mutation=True)
    try:
        await Microsoft365Service.save(
            db,
            owner_id=owner_id,
            integration_id=None,
            **payload.model_dump(exclude={"config"}),
            config=payload.config,
        )
    except (Microsoft365Error, IntegrationSettingsError) as exc:
        await db.rollback()
        raise _api_error(exc) from exc
    return {"status": "ok"}


@router.put("/{integration_id}")
async def update_connection(
    integration_id: UUID,
    payload: ConnectionRequest,
    db: Db,
    user: User,
    scope: SettingsApiScope = "personal",
):
    _current, owner_id = await _settings(db, scope, user, mutation=True)
    try:
        await Microsoft365Service.save(
            db,
            owner_id=owner_id,
            integration_id=integration_id,
            **payload.model_dump(exclude={"config"}),
            config=payload.config,
        )
    except (Microsoft365Error, IntegrationSettingsError) as exc:
        await db.rollback()
        raise _api_error(exc) from exc
    return {"status": "ok"}


@router.delete("/{integration_id}", status_code=204)
async def delete_connection(
    integration_id: UUID, db: Db, user: User, scope: SettingsApiScope = "personal"
):
    _current, owner_id = await _settings(db, scope, user, mutation=True)
    try:
        await Microsoft365Service.delete(db, integration_id, owner_id=owner_id)
    except IntegrationSettingsError as exc:
        await db.rollback()
        raise _api_error(exc) from exc


@router.post("/{integration_id}/disconnect")
async def disconnect(
    integration_id: UUID, db: Db, user: User, scope: SettingsApiScope = "personal"
):
    _current, owner_id = await _settings(db, scope, user, mutation=True)
    try:
        await Microsoft365Service.disconnect(db, integration_id, owner_id=owner_id)
    except (Microsoft365Error, IntegrationSettingsError) as exc:
        await db.rollback()
        raise _api_error(exc) from exc
    return {"status": "ok"}


@router.post("/{integration_id}/test")
async def test_connection(
    integration_id: UUID, db: Db, user: User, scope: SettingsApiScope = "personal"
):
    settings, _owner = await _settings(db, scope, user)
    instance = _instance(settings, integration_id)
    await db.rollback()
    try:
        await GraphClient(await access_token(instance)).get(
            "/me/mailFolders/inbox",
            params={"$select": "id"},
        )
    except Microsoft365Error as exc:
        raise _api_error(exc) from exc
    return {"status": "ok"}


@router.post("/{integration_id}/login", status_code=201)
async def start_login(
    integration_id: UUID,
    request: Request,
    response: Response,
    db: Db,
    user: User,
    scope: SettingsApiScope = "personal",
):
    response.headers["Cache-Control"] = "no-store"
    settings, owner_id = await _settings(db, scope, user, mutation=True)
    instance = _instance(settings, integration_id)
    try:
        config = Microsoft365Config.model_validate(instance.config)
        record = await load_credential(db, instance.credential_id, owner_id=owner_id)
        payload = credential_payload(record, config)
        if config.auth_flow == "browser" and not payload.client_secret:
            raise Microsoft365Error("microsoft365.configurationInvalid")
        callback_uri = await _callback_uri(db, request) if config.auth_flow == "browser" else None
        attempt = LoginAttempt(
            user.id,
            _session_hash(request),
            instance.model_copy(deep=True),
            payload.model_copy(update={"tokens": None, "expires_at": 0}),
            redirect_uri=callback_uri,
        )
        await db.rollback()
        return await oauth_logins.start(attempt)
    except Microsoft365Error as exc:
        raise _api_error(exc) from exc


@callback_router.post("/oauth/callback")
async def login_callback(
    state: Annotated[str, Form(max_length=256)] = "",
    code: Annotated[str | None, Form(max_length=10_000)] = None,
    error: Annotated[str | None, Form(max_length=256)] = None,
):
    try:
        oauth_logins.callback(state, code, error)
        message = "Kehre zu MatoLoop zurück, um die Anmeldung abzuschließen. Du kannst diesen Tab schließen."
        status = 200
    except Microsoft365Error:
        message = "Diese Anmeldung ist abgelaufen. Starte die Anmeldung in MatoLoop erneut."
        status = 400
    return HTMLResponse(
        '<!doctype html><html lang="de"><meta charset="utf-8"><meta name="viewport" content="width=device-width">'
        "<title>Microsoft-365-Anmeldung</title><p>" + message + "</p></html>",
        status_code=status,
        headers={
            "Cache-Control": "no-store",
            "Referrer-Policy": "no-referrer",
            "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
        },
    )


@router.post("/oauth/attempts/{attempt_id}/finish")
async def finish_login(attempt_id: str, request: Request, response: Response, db: Db, user: User):
    response.headers["Cache-Control"] = "no-store"
    try:
        attempt = oauth_logins.get(attempt_id, user.id, _session_hash(request))
        if attempt.lock.locked():
            return {"status": "pending", "interval": attempt.interval}
        async with attempt.lock:
            await _settings(db, "personal", user, mutation=True)
            await db.rollback()
            if not await oauth_logins.poll(attempt):
                return {"status": "pending", "interval": attempt.interval}
            # Recheck live availability and the full original binding before saving tokens.
            await db.refresh(user)
            await _settings(db, "personal", user, mutation=True)
            await Microsoft365Service.authorize(db, attempt)
            oauth_logins.attempts.pop(attempt.id, None)
            return {"status": "authorized", "integrationId": str(attempt.instance.id)}
    except (Microsoft365Error, IntegrationSettingsError) as exc:
        await db.rollback()
        raise _api_error(exc) from exc


@router.delete("/oauth/attempts/{attempt_id}", status_code=204)
async def cancel_login(attempt_id: str, request: Request, user: User):
    try:
        attempt = oauth_logins.get(attempt_id, user.id, _session_hash(request))
        if attempt.lock.locked():
            raise Microsoft365Error("microsoft365.loginBusy")
        oauth_logins.attempts.pop(attempt.id, None)
    except Microsoft365Error as exc:
        raise _api_error(exc) from exc
