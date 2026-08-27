from __future__ import annotations

import asyncio
import json
import secrets
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal
from uuid import UUID

from fastapi import (
    APIRouter,
    Body,
    Depends,
    Header,
    Response,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.api.errors import ApiError
from mato_loop_backend.db.session import AsyncSessionFactory, get_db_session
from mato_loop_backend.execution import (
    ExecutionAgentError,
    HelloMessage,
    WelcomeMessage,
    execution_agent_connections,
    transfer_tickets,
)
from mato_loop_backend.execution.protocol import MAX_MESSAGE_BYTES, SUPPORTED_OPERATIONS
from mato_loop_backend.models import ExecutionAgent, UserAccount
from mato_loop_backend.security import hash_token, require_user
from mato_loop_backend.settings import SettingsStore, UserPreferences

router = APIRouter(prefix="/execution-agents")
PAIRING_DURATION_DEFAULT_SECONDS = 1800
PAIRING_DURATION_MAXIMUM_SECONDS = 86400


def _camel(value: str) -> str:
    first, *rest = value.split("_")
    return first + "".join(part.capitalize() for part in rest)


class ApiModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=_camel,
        populate_by_name=True,
        serialize_by_alias=True,
        extra="forbid",
    )


class ExecutionAgentAliasRequest(ApiModel):
    alias: str = Field(min_length=1, max_length=160)

    @field_validator("alias")
    @classmethod
    def normalize_alias(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("alias cannot be blank")
        return value


ExecutionPolicy = Literal["auto", "ask", "deny"]


class ExecutionAgentRequest(ExecutionAgentAliasRequest):
    pairing_token_duration_seconds: int = Field(
        default=PAIRING_DURATION_DEFAULT_SECONDS,
        ge=PAIRING_DURATION_DEFAULT_SECONDS,
        le=PAIRING_DURATION_MAXIMUM_SECONDS,
    )
    home_filesystem_policy: ExecutionPolicy = "auto"
    external_filesystem_policy: ExecutionPolicy = "ask"
    home_shell_enabled: bool = True
    external_shell_enabled: bool = True


class PairingTokenRequest(ApiModel):
    pairing_token_duration_seconds: int = Field(
        default=PAIRING_DURATION_DEFAULT_SECONDS,
        ge=PAIRING_DURATION_DEFAULT_SECONDS,
        le=PAIRING_DURATION_MAXIMUM_SECONDS,
    )


class PairExecutionAgentRequest(ApiModel):
    agent_id: UUID
    access_key_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class DefaultExecutionAgentRequest(ApiModel):
    default_execution_agent_id: UUID | None = None


class ExecutionAgentUpdateRequest(ApiModel):
    alias: str | None = Field(default=None, min_length=1, max_length=160)
    home_filesystem_policy: ExecutionPolicy | None = None
    external_filesystem_policy: ExecutionPolicy | None = None
    home_shell_enabled: bool | None = None
    external_shell_enabled: bool | None = None

    _normalize_alias = field_validator("alias")(ExecutionAgentAliasRequest.normalize_alias.__func__)

    @model_validator(mode="after")
    def require_change(self) -> ExecutionAgentUpdateRequest:
        if not self.model_fields_set:
            raise ValueError("at least one field is required")
        return self


def _valid_registration(agent: ExecutionAgent, now: datetime) -> bool:
    return (
        agent.revoked_at is None
        and agent.token_hash is None
        and agent.registration_token_hash is not None
        and agent.registration_expires_at is not None
        and agent.registration_expires_at > now
    )


def _agent_payload(agent: ExecutionAgent) -> dict:
    now = datetime.now(UTC)
    pending = _valid_registration(agent, now)
    pairing_state = "pending" if pending else "paired" if agent.token_hash is not None else "unpaired"
    payload = {
        "id": str(agent.id),
        "alias": agent.alias,
        "computerName": agent.computer_name,
        "agentVersion": agent.agent_version,
        "platform": agent.platform,
        "revision": agent.revision,
        "operations": agent.operations,
        "homeDirectory": agent.home_directory,
        "homeFilesystemPolicy": agent.home_filesystem_policy,
        "externalFilesystemPolicy": agent.external_filesystem_policy,
        "homeShellEnabled": agent.home_shell_enabled,
        "externalShellEnabled": agent.external_shell_enabled,
        "pairingState": pairing_state,
        "connected": pairing_state == "paired"
        and agent.revoked_at is None
        and execution_agent_connections.is_connected(agent.id),
        "lastSeenAt": agent.last_connected_at,
        "revokedAt": agent.revoked_at,
        "createdAt": agent.created_at,
        "updatedAt": agent.updated_at,
    }
    if pending:
        payload["pairingTokenExpiresAt"] = agent.registration_expires_at
    return payload


def _issue_pairing_token(agent: ExecutionAgent, duration_seconds: int) -> str:
    token = secrets.token_urlsafe(48)
    agent.registration_token_hash = hash_token(token)
    agent.registration_expires_at = datetime.now(UTC) + timedelta(seconds=duration_seconds)
    return token


def _pairing_envelope(agent: ExecutionAgent, token: str) -> dict:
    return {
        "agent": _agent_payload(agent),
        "pairingToken": token,
        "pairingTokenExpiresAt": agent.registration_expires_at,
        "websocketUrl": "/api/execution-agents/connect",
    }


def _http_bearer_token(authorization: str | None) -> str:
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not token or " " in token:
        raise ApiError(status.HTTP_401_UNAUTHORIZED, "executionAgent.pairingTokenInvalid")
    return token


async def _commit_and_revoke_live_capabilities(db: AsyncSession, agent_id: UUID) -> None:
    async def cleanup() -> None:
        await db.commit()
        try:
            await execution_agent_connections.close(agent_id)
        finally:
            transfer_tickets.revoke_owner(agent_id)

    task = asyncio.create_task(cleanup())
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
    task.result()
    if cancelled:
        raise asyncio.CancelledError


async def _owned_agent(
    db: AsyncSession, user_id: UUID, agent_id: UUID, *, for_update: bool = False
) -> ExecutionAgent:
    statement = select(ExecutionAgent).where(
        ExecutionAgent.id == agent_id, ExecutionAgent.user_id == user_id
    )
    if for_update:
        statement = statement.with_for_update()
    agent = await db.scalar(statement)
    if agent is None:
        raise ApiError(status.HTTP_404_NOT_FOUND, "executionAgent.notFound")
    return agent


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_execution_agent(
    payload: ExecutionAgentRequest,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
    response: Response,
) -> dict:
    agent = ExecutionAgent(
        user_id=user.id,
        alias=payload.alias,
        token_hash=None,
        token_is_legacy=None,
        home_filesystem_policy=payload.home_filesystem_policy,
        external_filesystem_policy=payload.external_filesystem_policy,
        home_shell_enabled=payload.home_shell_enabled,
        external_shell_enabled=payload.external_shell_enabled,
    )
    token = _issue_pairing_token(agent, payload.pairing_token_duration_seconds)
    db.add(agent)
    await db.flush()
    await db.commit()
    response.headers["Cache-Control"] = "no-store"
    return _pairing_envelope(agent, token)


@router.get("/pairing-policy")
async def execution_agent_pairing_policy(
    _user: Annotated[UserAccount, Depends(require_user)],
) -> dict[str, int]:
    return {
        "defaultDurationSeconds": PAIRING_DURATION_DEFAULT_SECONDS,
        "maximumDurationSeconds": PAIRING_DURATION_MAXIMUM_SECONDS,
    }


@router.post("/pair", status_code=status.HTTP_204_NO_CONTENT)
async def pair_execution_agent(
    payload: PairExecutionAgentRequest,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    authorization: Annotated[str | None, Header()] = None,
) -> Response:
    presented_hash = hash_token(_http_bearer_token(authorization))
    agent = await db.scalar(
        select(ExecutionAgent)
        .where(ExecutionAgent.id == payload.agent_id)
        .with_for_update()
    )
    if agent is None or agent.revoked_at is not None:
        raise ApiError(status.HTTP_401_UNAUTHORIZED, "executionAgent.pairingTokenInvalid")
    if agent.token_hash == payload.access_key_hash:
        await db.commit()
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    migrated_legacy_token = False
    if agent.token_hash is not None:
        if agent.token_is_legacy is not True or not secrets.compare_digest(
            agent.token_hash, presented_hash
        ):
            raise ApiError(status.HTTP_409_CONFLICT, "executionAgent.alreadyPaired")
        migrated_legacy_token = True
    else:
        if agent.registration_token_hash is None or not secrets.compare_digest(
            agent.registration_token_hash, presented_hash
        ):
            raise ApiError(status.HTTP_401_UNAUTHORIZED, "executionAgent.pairingTokenInvalid")
        if (
            agent.registration_expires_at is None
            or agent.registration_expires_at <= datetime.now(UTC)
        ):
            raise ApiError(status.HTTP_410_GONE, "executionAgent.pairingTokenExpired")
    agent.token_hash = payload.access_key_hash
    agent.token_is_legacy = False
    agent.registration_token_hash = None
    agent.registration_expires_at = None
    agent.revoked_at = None
    agent.revision += 1
    agent.updated_at = datetime.now(UTC)
    if migrated_legacy_token:
        await _commit_and_revoke_live_capabilities(db, agent.id)
    else:
        await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("")
async def list_execution_agents(
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict:
    agents = list(
        (
            await db.scalars(
                select(ExecutionAgent).where(
                    ExecutionAgent.user_id == user.id,
                    ExecutionAgent.revoked_at.is_(None),
                )
                .order_by(ExecutionAgent.created_at, ExecutionAgent.id)
            )
        ).all()
    )
    preferences = await SettingsStore.load(db, UserPreferences, user_id=user.id)
    return {
        "items": [_agent_payload(agent) for agent in agents],
        "defaultExecutionAgentId": (
            str(preferences.default_execution_agent_id)
            if preferences.default_execution_agent_id
            else None
        ),
    }


@router.put("/default")
async def set_default_execution_agent(
    payload: DefaultExecutionAgentRequest,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict[str, str | None]:
    if payload.default_execution_agent_id is not None:
        agent = await _owned_agent(db, user.id, payload.default_execution_agent_id)
        if agent.revoked_at is not None:
            raise ApiError(status.HTTP_409_CONFLICT, "executionAgent.revoked")
    preferences = await SettingsStore.load(
        db, UserPreferences, user_id=user.id, for_update=True
    )
    preferences.default_execution_agent_id = payload.default_execution_agent_id
    await SettingsStore.save(db, preferences, user_id=user.id)
    await db.commit()
    return {
        "defaultExecutionAgentId": (
            str(preferences.default_execution_agent_id)
            if preferences.default_execution_agent_id
            else None
        )
    }


@router.get("/{agent_id}")
async def get_execution_agent(
    agent_id: UUID,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict:
    return _agent_payload(await _owned_agent(db, user.id, agent_id))


@router.patch("/{agent_id}")
async def update_execution_agent(
    agent_id: UUID,
    payload: ExecutionAgentUpdateRequest,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict:
    agent = await _owned_agent(db, user.id, agent_id, for_update=True)
    if agent.revoked_at is not None:
        raise ApiError(status.HTTP_409_CONFLICT, "executionAgent.revoked")
    changed = False
    for field in payload.model_fields_set:
        value = getattr(payload, field)
        if value is not None and getattr(agent, field) != value:
            setattr(agent, field, value)
            changed = True
    if changed:
        agent.revision += 1
        agent.updated_at = datetime.now(UTC)
    await db.commit()
    return _agent_payload(agent)


@router.post("/{agent_id}/pairing-token")
async def create_execution_agent_pairing_token(
    agent_id: UUID,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
    response: Response,
    payload: Annotated[PairingTokenRequest | None, Body()] = None,
) -> dict:
    agent = await _owned_agent(db, user.id, agent_id, for_update=True)
    if agent.revoked_at is not None:
        raise ApiError(status.HTTP_409_CONFLICT, "executionAgent.revoked")
    if agent.token_hash is not None:
        raise ApiError(status.HTTP_409_CONFLICT, "executionAgent.alreadyPaired")
    duration_seconds = (
        payload.pairing_token_duration_seconds
        if payload is not None
        else PAIRING_DURATION_DEFAULT_SECONDS
    )
    token = _issue_pairing_token(agent, duration_seconds)
    agent.revision += 1
    agent.updated_at = datetime.now(UTC)
    await db.commit()
    response.headers["Cache-Control"] = "no-store"
    return _pairing_envelope(agent, token)


@router.delete("/{agent_id}/pairing")
async def unpair_execution_agent(
    agent_id: UUID,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict:
    agent = await _owned_agent(db, user.id, agent_id, for_update=True)
    preferences = await SettingsStore.load(
        db, UserPreferences, user_id=user.id, for_update=True
    )
    changed = any(
        value is not None
        for value in (
            agent.token_hash,
            agent.token_is_legacy,
            agent.registration_token_hash,
            agent.registration_expires_at,
        )
    )
    agent.token_hash = None
    agent.token_is_legacy = None
    agent.registration_token_hash = None
    agent.registration_expires_at = None
    if changed:
        agent.revision += 1
        agent.updated_at = datetime.now(UTC)
    if preferences.default_execution_agent_id == agent.id:
        preferences.default_execution_agent_id = None
        await SettingsStore.save(db, preferences, user_id=user.id)
    await _commit_and_revoke_live_capabilities(db, agent.id)
    return _agent_payload(agent)


@router.delete("/{agent_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_execution_agent(
    agent_id: UUID,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> None:
    agent = await _owned_agent(db, user.id, agent_id, for_update=True)
    preferences = await SettingsStore.load(
        db, UserPreferences, user_id=user.id, for_update=True
    )
    if agent.revoked_at is None:
        agent.revoked_at = datetime.now(UTC)
        agent.revision += 1
    if preferences.default_execution_agent_id == agent.id:
        preferences.default_execution_agent_id = None
        await SettingsStore.save(db, preferences, user_id=user.id)
    await _commit_and_revoke_live_capabilities(db, agent.id)


def _bearer_token(websocket: WebSocket) -> str | None:
    scheme, _, token = websocket.headers.get("authorization", "").partition(" ")
    return token if scheme.lower() == "bearer" and token and " " not in token else None


async def _authenticate(token: str | None) -> tuple[UUID, str] | None:
    if token is None:
        return None
    token_hash = hash_token(token)
    async with AsyncSessionFactory() as db:
        agent_id = await db.scalar(
            select(ExecutionAgent.id).where(
                ExecutionAgent.token_hash == token_hash,
                ExecutionAgent.revoked_at.is_(None),
            )
        )
    return (agent_id, token_hash) if agent_id is not None else None


async def _register_snapshot(
    agent_id: UUID, authenticated_token_hash: str, hello: HelloMessage
) -> bool:
    operations = sorted(set(hello.operations) & SUPPORTED_OPERATIONS)
    async with AsyncSessionFactory() as db:
        agent = await db.scalar(
            select(ExecutionAgent)
            .where(
                ExecutionAgent.id == agent_id,
                ExecutionAgent.token_hash == authenticated_token_hash,
            )
            .with_for_update()
        )
        if agent is None or agent.revoked_at is not None:
            return False
        changed = (
            agent.agent_version != hello.agent_version
            or agent.computer_name != hello.computer_name
            or agent.platform != hello.platform
            or agent.operations != operations
            or agent.home_directory != hello.home_directory
        )
        agent.agent_version = hello.agent_version
        agent.computer_name = hello.computer_name
        agent.platform = hello.platform
        agent.operations = operations
        agent.home_directory = hello.home_directory
        agent.last_connected_at = datetime.now(UTC)
        if changed:
            agent.revision += 1
        await db.commit()
        return True


@router.websocket("/connect")
async def connect_execution_agent(websocket: WebSocket) -> None:
    authentication = await _authenticate(_bearer_token(websocket))
    if authentication is None:
        await websocket.close(code=4401, reason="Authentication required")
        return
    authenticated_id, authenticated_token_hash = authentication
    await websocket.accept()
    registered = False
    try:
        raw = await websocket.receive_text()
        if len(raw.encode("utf-8")) > MAX_MESSAGE_BYTES:
            raise ExecutionAgentError("executionAgent.protocolError")
        hello = HelloMessage.model_validate_json(raw)
        if hello.agent_id != authenticated_id or not await _register_snapshot(
            authenticated_id, authenticated_token_hash, hello
        ):
            await websocket.close(code=4403, reason="Agent access rejected")
            return
        registered = True
        await websocket.send_text(
            WelcomeMessage(agent_id=authenticated_id).model_dump_json(by_alias=True)
        )
        await execution_agent_connections.register(hello, websocket)
        registered = True
        while True:
            await execution_agent_connections.receive(
                authenticated_id, websocket, await websocket.receive_text()
            )
    except WebSocketDisconnect:
        pass
    except (ExecutionAgentError, ValidationError, ValueError, json.JSONDecodeError) as exc:
        code = exc.code if isinstance(exc, ExecutionAgentError) else "executionAgent.protocolError"
        if registered:
            await execution_agent_connections.unregister(
                authenticated_id, websocket, error_code=code
            )
            registered = False
        try:
            await websocket.close(code=4400, reason="Invalid MatoLoop Agent protocol")
        except RuntimeError:
            pass
    finally:
        if registered:
            await execution_agent_connections.unregister(authenticated_id, websocket)
