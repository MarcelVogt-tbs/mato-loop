from __future__ import annotations

import asyncio
import json
import secrets
from datetime import UTC, datetime, timedelta
from typing import Annotated
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
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

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
from mato_loop_backend.models import ExecutionAgent, ExecutionAgentRoot, UserAccount
from mato_loop_backend.security import hash_token, require_user

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


class ExecutionAgentNameRequest(ApiModel):
    name: str = Field(min_length=1, max_length=160)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("name cannot be blank")
        return value


class ExecutionAgentRequest(ExecutionAgentNameRequest):
    pairing_token_duration_seconds: int = Field(
        default=PAIRING_DURATION_DEFAULT_SECONDS,
        ge=PAIRING_DURATION_DEFAULT_SECONDS,
        le=PAIRING_DURATION_MAXIMUM_SECONDS,
    )


class PairingTokenRequest(ApiModel):
    pairing_token_duration_seconds: int = Field(
        default=PAIRING_DURATION_DEFAULT_SECONDS,
        ge=PAIRING_DURATION_DEFAULT_SECONDS,
        le=PAIRING_DURATION_MAXIMUM_SECONDS,
    )


class PairExecutionAgentRequest(ApiModel):
    agent_id: UUID
    access_key_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


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
        "name": agent.name,
        "platform": agent.platform,
        "revision": agent.revision,
        "protocolVersion": agent.protocol_version,
        "operations": agent.operations,
        "roots": [
            {"id": str(root.id), "label": root.label, "revision": root.revision}
            for root in agent.roots
        ],
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
    statement = (
        select(ExecutionAgent)
        .options(selectinload(ExecutionAgent.roots))
        .where(ExecutionAgent.id == agent_id, ExecutionAgent.user_id == user_id)
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
        user_id=user.id, name=payload.name, token_hash=None, token_is_legacy=None
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
) -> dict[str, list[dict]]:
    agents = list(
        (
            await db.scalars(
                select(ExecutionAgent)
                .options(selectinload(ExecutionAgent.roots))
                .where(
                    ExecutionAgent.user_id == user.id,
                    ExecutionAgent.revoked_at.is_(None),
                )
                .order_by(ExecutionAgent.created_at, ExecutionAgent.id)
            )
        ).all()
    )
    return {"items": [_agent_payload(agent) for agent in agents]}


@router.get("/{agent_id}")
async def get_execution_agent(
    agent_id: UUID,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict:
    return _agent_payload(await _owned_agent(db, user.id, agent_id))


@router.patch("/{agent_id}")
async def rename_execution_agent(
    agent_id: UUID,
    payload: ExecutionAgentNameRequest,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict:
    agent = await _owned_agent(db, user.id, agent_id, for_update=True)
    if agent.revoked_at is not None:
        raise ApiError(status.HTTP_409_CONFLICT, "executionAgent.revoked")
    if agent.name != payload.name:
        agent.name = payload.name
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
    await _commit_and_revoke_live_capabilities(db, agent.id)
    return _agent_payload(agent)


@router.delete("/{agent_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_execution_agent(
    agent_id: UUID,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> None:
    agent = await _owned_agent(db, user.id, agent_id, for_update=True)
    if agent.revoked_at is None:
        agent.revoked_at = datetime.now(UTC)
        agent.revision += 1
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
    agent_id: UUID, authenticated_token_hash: str, hello: HelloMessage, websocket: WebSocket
) -> bool:
    operations = sorted(set(hello.operations) & SUPPORTED_OPERATIONS)
    roots = sorted(hello.roots, key=lambda item: str(item.id))
    async with AsyncSessionFactory() as db:
        agent = await db.scalar(
            select(ExecutionAgent)
            .options(selectinload(ExecutionAgent.roots))
            .where(
                ExecutionAgent.id == agent_id,
                ExecutionAgent.token_hash == authenticated_token_hash,
            )
            .with_for_update()
        )
        if agent is None or agent.revoked_at is not None:
            return False
        old_roots = sorted(
            ((root.id, root.label, root.revision) for root in agent.roots),
            key=lambda item: str(item[0]),
        )
        new_roots = [(root.id, root.label, root.revision) for root in roots]
        changed = (
            agent.protocol_version != hello.protocol_version
            or agent.platform != hello.platform
            or agent.operations != operations
            or old_roots != new_roots
        )
        agent.protocol_version = hello.protocol_version
        agent.platform = hello.platform
        agent.operations = operations
        agent.last_connected_at = datetime.now(UTC)
        if changed:
            agent.revision += 1
            roots_by_id = {root.id: root for root in agent.roots}
            agent.roots[:] = [root for root in agent.roots if root.id in {item.id for item in roots}]
            for root in roots:
                stored = roots_by_id.get(root.id)
                if stored is None:
                    agent.roots.append(
                        ExecutionAgentRoot(
                            id=root.id, label=root.label, revision=root.revision
                        )
                    )
                else:
                    stored.label = root.label
                    stored.revision = root.revision
        await execution_agent_connections.register(hello, websocket)
        try:
            await db.commit()
        except BaseException:
            await execution_agent_connections.unregister(
                agent_id, websocket, error_code="executionAgent.unavailable"
            )
            raise
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
            authenticated_id, authenticated_token_hash, hello, websocket
        ):
            await websocket.close(code=4403, reason="Agent access rejected")
            return
        registered = True
        await websocket.send_text(
            WelcomeMessage(agent_id=authenticated_id).model_dump_json(by_alias=True)
        )
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
            await websocket.close(code=4400, reason="Invalid execution agent protocol")
        except RuntimeError:
            pass
    finally:
        if registered:
            await execution_agent_connections.unregister(authenticated_id, websocket)
