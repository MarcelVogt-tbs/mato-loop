from __future__ import annotations

import json
import secrets
from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect, status
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
)
from mato_loop_backend.execution.protocol import MAX_MESSAGE_BYTES, SUPPORTED_OPERATIONS
from mato_loop_backend.models import ExecutionAgent, ExecutionAgentRoot, UserAccount
from mato_loop_backend.security import hash_token, require_user

router = APIRouter(prefix="/execution-agents")


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


class ExecutionAgentRequest(ApiModel):
    name: str = Field(min_length=1, max_length=160)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("name cannot be blank")
        return value


def _agent_payload(agent: ExecutionAgent) -> dict:
    return {
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
        "connected": agent.revoked_at is None
        and execution_agent_connections.is_connected(agent.id),
        "lastSeenAt": agent.last_connected_at,
        "revokedAt": agent.revoked_at,
        "createdAt": agent.created_at,
        "updatedAt": agent.updated_at,
    }


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
) -> dict:
    token = secrets.token_urlsafe(48)
    agent = ExecutionAgent(user_id=user.id, name=payload.name, token_hash=hash_token(token))
    db.add(agent)
    await db.flush()
    await db.commit()
    return {
        "agent": _agent_payload(agent),
        "token": token,
        "websocketUrl": "/api/execution-agents/connect",
    }


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
    payload: ExecutionAgentRequest,
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
        await db.commit()
    await execution_agent_connections.close(agent.id)


def _bearer_token(websocket: WebSocket) -> str | None:
    scheme, _, token = websocket.headers.get("authorization", "").partition(" ")
    return token if scheme.lower() == "bearer" and token and " " not in token else None


async def _authenticate(token: str | None) -> UUID | None:
    if token is None:
        return None
    async with AsyncSessionFactory() as db:
        return await db.scalar(
            select(ExecutionAgent.id).where(
                ExecutionAgent.token_hash == hash_token(token),
                ExecutionAgent.revoked_at.is_(None),
            )
        )


async def _register_snapshot(
    agent_id: UUID, hello: HelloMessage, websocket: WebSocket
) -> bool:
    operations = sorted(set(hello.operations) & SUPPORTED_OPERATIONS)
    roots = sorted(hello.roots, key=lambda item: str(item.id))
    async with AsyncSessionFactory() as db:
        agent = await db.scalar(
            select(ExecutionAgent)
            .options(selectinload(ExecutionAgent.roots))
            .where(ExecutionAgent.id == agent_id)
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
    authenticated_id = await _authenticate(_bearer_token(websocket))
    if authenticated_id is None:
        await websocket.close(code=4401, reason="Authentication required")
        return
    await websocket.accept()
    registered = False
    try:
        raw = await websocket.receive_text()
        if len(raw.encode("utf-8")) > MAX_MESSAGE_BYTES:
            raise ExecutionAgentError("executionAgent.protocolError")
        hello = HelloMessage.model_validate_json(raw)
        if hello.agent_id != authenticated_id or not await _register_snapshot(
            authenticated_id, hello, websocket
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
