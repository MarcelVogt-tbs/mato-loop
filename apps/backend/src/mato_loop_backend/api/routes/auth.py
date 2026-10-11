from __future__ import annotations

import secrets
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.api.errors import ApiError
from mato_loop_backend.auth import LocalAuthProvider
from mato_loop_backend.config import get_runtime_config
from mato_loop_backend.db.session import get_db_session
from mato_loop_backend.models import UserAccount
from mato_loop_backend.security import (
    create_login_session,
    hash_password,
    hash_token,
    remove_login_session,
    require_admin,
    require_user,
)
from mato_loop_backend.settings import AgentProfilesSettings, GeneralSettings, SettingsStore

router = APIRouter(prefix="/auth")
config = get_runtime_config()


class CredentialsRequest(BaseModel):
    username: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=10, max_length=1024)


class LoginRequest(BaseModel):
    username: str = Field(max_length=120)
    password: str = Field(max_length=1024)


class AuthStatusResponse(BaseModel):
    setup_required: bool
    authenticated: bool
    user_id: str | None = None
    username: str | None = None
    is_admin: bool = False
    model_trust_enabled: bool = True


def _identity_payload(user: UserAccount, model_trust_enabled: bool = True) -> dict[str, str | bool]:
    return {
        "userId": str(user.id),
        "username": user.username,
        "isAdmin": user.is_admin,
        "modelTrustEnabled": model_trust_enabled,
    }


def _set_cookie(response: Response, token: str, expires_at: datetime) -> None:
    response.set_cookie(
        config.session_cookie_name,
        token,
        expires=expires_at,
        httponly=True,
        secure=config.secure_cookies,
        samesite="strict",
        path="/",
    )


@router.get("/status", response_model=AuthStatusResponse)
async def auth_status(
    db: Annotated[AsyncSession, Depends(get_db_session)],
    session_token: Annotated[str | None, Cookie(alias=config.session_cookie_name)] = None,
) -> AuthStatusResponse:
    setup_required = (await db.scalar(select(func.count(UserAccount.id)))) == 0
    if setup_required or not session_token:
        return AuthStatusResponse(setup_required=setup_required, authenticated=False)
    try:
        user = await require_user(db=db, session_token=session_token)
    except ApiError:
        return AuthStatusResponse(setup_required=False, authenticated=False)
    return AuthStatusResponse(
        setup_required=False,
        authenticated=True,
        user_id=str(user.id),
        username=user.username,
        is_admin=user.is_admin,
        model_trust_enabled=(await SettingsStore.load(db, GeneralSettings)).model_trust_enabled,
    )


@router.get("/options")
async def auth_options() -> dict[str, list[dict[str, str]]]:
    return {
        "providers": [
            {"id": LocalAuthProvider.provider_id, "type": "local", "name": "Lokales Konto"}
        ]
    }


@router.post("/setup", status_code=status.HTTP_201_CREATED)
async def setup(
    payload: CredentialsRequest,
    response: Response,
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict[str, str | bool]:
    username = payload.username.strip()
    if not username:
        raise ApiError(422, "validation.required", field="username")
    await db.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended('mato-loop-setup', 0))")
    )
    if (await db.scalar(select(func.count(UserAccount.id)))) != 0:
        raise ApiError(status.HTTP_409_CONFLICT, "auth.setupCompleted")
    # Create the instance document; shipped profiles remain code-owned.
    await SettingsStore.save(db, AgentProfilesSettings())
    user = UserAccount(
        username=username,
        password_hash=hash_password(payload.password),
        is_admin=True,
        is_active=True,
    )
    db.add(user)
    await db.flush()
    token, expires_at = await create_login_session(db, user)
    await db.commit()
    _set_cookie(response, token, expires_at)
    return _identity_payload(user, (await SettingsStore.load(db, GeneralSettings)).model_trust_enabled)


@router.post("/login")
async def login(
    payload: LoginRequest,
    response: Response,
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict[str, str | bool]:
    user = await LocalAuthProvider.authenticate(db, payload.username, payload.password)
    if user is None:
        raise ApiError(status.HTTP_401_UNAUTHORIZED, "auth.invalidCredentials")
    token, expires_at = await create_login_session(db, user)
    await db.commit()
    _set_cookie(response, token, expires_at)
    return _identity_payload(user, (await SettingsStore.load(db, GeneralSettings)).model_trust_enabled)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    response: Response,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    session_token: Annotated[str | None, Cookie(alias=config.session_cookie_name)] = None,
) -> Response:
    await remove_login_session(db, session_token)
    await db.commit()
    response.delete_cookie(config.session_cookie_name, path="/")
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.get("/me")
async def me(
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict[str, str | bool]:
    return _identity_payload(user, (await SettingsStore.load(db, GeneralSettings)).model_trust_enabled)


@router.get("/debug-token")
async def debug_token_status(
    user: Annotated[UserAccount, Depends(require_admin)],
) -> dict[str, str | bool | None]:
    return {
        "configured": user.debug_token_hash is not None,
        "createdAt": user.debug_token_created_at.isoformat()
        if user.debug_token_created_at
        else None,
    }


@router.post("/debug-token")
async def create_debug_token(
    response: Response,
    user: Annotated[UserAccount, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict[str, str]:
    token = secrets.token_urlsafe(48)
    user.debug_token_hash = hash_token(token)
    user.debug_token_created_at = datetime.now(UTC)
    await db.commit()
    response.headers["Cache-Control"] = "no-store"
    return {"token": token, "createdAt": user.debug_token_created_at.isoformat()}


@router.delete("/debug-token", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_debug_token(
    user: Annotated[UserAccount, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> Response:
    user.debug_token_hash = None
    user.debug_token_created_at = None
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
