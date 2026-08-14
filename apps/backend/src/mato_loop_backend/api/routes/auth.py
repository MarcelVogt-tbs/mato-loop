from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.api.errors import ApiError
from mato_loop_backend.config import get_runtime_config
from mato_loop_backend.db.session import get_db_session
from mato_loop_backend.models import UserAccount
from mato_loop_backend.security import (
    create_login_session,
    hash_password,
    remove_login_session,
    require_user,
    verify_password,
)
from mato_loop_backend.settings import SettingsStore, UserPreferences

router = APIRouter(prefix="/auth")
config = get_runtime_config()


class CredentialsRequest(BaseModel):
    username: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=10, max_length=1024)


class AuthStatusResponse(BaseModel):
    setup_required: bool
    authenticated: bool
    username: str | None = None


class UserPreferencesRequest(BaseModel):
    thinking_expanded: bool


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
    return AuthStatusResponse(setup_required=False, authenticated=True, username=user.username)


@router.post("/setup", status_code=status.HTTP_201_CREATED)
async def setup(
    payload: CredentialsRequest,
    response: Response,
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict[str, str]:
    if (await db.scalar(select(func.count(UserAccount.id)))) != 0:
        raise ApiError(status.HTTP_409_CONFLICT, "auth.setupCompleted")
    user = UserAccount(
        username=payload.username.strip(), password_hash=hash_password(payload.password)
    )
    db.add(user)
    await db.flush()
    token, expires_at = await create_login_session(db, user)
    await db.commit()
    _set_cookie(response, token, expires_at)
    return {"username": user.username}


@router.post("/login")
async def login(
    payload: CredentialsRequest,
    response: Response,
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict[str, str]:
    user = await db.scalar(
        select(UserAccount).where(UserAccount.username == payload.username.strip())
    )
    if user is None or not verify_password(payload.password, user.password_hash):
        raise ApiError(status.HTTP_401_UNAUTHORIZED, "auth.invalidCredentials")
    token, expires_at = await create_login_session(db, user)
    await db.commit()
    _set_cookie(response, token, expires_at)
    return {"username": user.username}


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
async def me(user: Annotated[UserAccount, Depends(require_user)]) -> dict[str, str]:
    return {"username": user.username}


@router.get("/preferences")
async def user_preferences(
    _user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict:
    preferences = await SettingsStore.load(db, UserPreferences, user_id=_user.id)
    return {
        "thinkingExpanded": preferences.thinking_expanded,
        "defaultExecutionAgentId": (
            str(preferences.default_execution_agent_id)
            if preferences.default_execution_agent_id
            else None
        ),
    }


@router.patch("/preferences")
async def update_user_preferences(
    payload: UserPreferencesRequest,
    _user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict:
    preferences = await SettingsStore.load(
        db, UserPreferences, user_id=_user.id, for_update=True
    )
    preferences.thinking_expanded = payload.thinking_expanded
    await SettingsStore.save(db, preferences, user_id=_user.id)
    await db.commit()
    return {
        "thinkingExpanded": preferences.thinking_expanded,
        "defaultExecutionAgentId": (
            str(preferences.default_execution_agent_id)
            if preferences.default_execution_agent_id
            else None
        ),
    }
