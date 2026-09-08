from __future__ import annotations

import asyncio
import base64
import binascii
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Literal
from urllib.parse import urlsplit
from uuid import UUID

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from mato_loop_backend.auth.sessions import hash_token
from mato_loop_backend.config import get_runtime_config
from mato_loop_backend.db.session import AsyncSessionFactory
from mato_loop_backend.error_contract import describe_error
from mato_loop_backend.execution import (
    ExecutionClientError,
    TerminalControlResult,
)
from mato_loop_backend.execution.terminals import (
    BrowserTerminalConnection,
    agent_generation,
    terminal_coordinator,
    validate_terminal_access,
)
from mato_loop_backend.models import LoginSession, UserAccount

router = APIRouter(prefix="/terminals")


@dataclass(frozen=True, slots=True)
class _BrowserLogin:
    user: UserAccount
    login_id: UUID
    expires_at: datetime


class _BrowserMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: str


class _InputMessage(_BrowserMessage):
    type: Literal["input"]
    data: str = Field(max_length=87_384)


class _ResizeMessage(_BrowserMessage):
    type: Literal["resize"]
    rows: int = Field(ge=1, le=1_000)
    columns: int = Field(ge=1, le=1_000)


class _CloseMessage(_BrowserMessage):
    type: Literal["close"]


class _AttachMessage(_BrowserMessage):
    type: Literal["attach"]
    session_id: UUID = Field(alias="sessionId")


class _DetachMessage(_BrowserMessage):
    type: Literal["detach"]


_MESSAGES = TypeAdapter(
    Annotated[
        _InputMessage
        | _ResizeMessage
        | _CloseMessage
        | _AttachMessage
        | _DetachMessage,
        Field(discriminator="type"),
    ]
)


async def _browser_login(websocket: WebSocket) -> _BrowserLogin | None:
    config = get_runtime_config()
    token = websocket.cookies.get(config.session_cookie_name)
    if not token:
        return None
    async with AsyncSessionFactory() as db:
        login = await db.scalar(
            select(LoginSession).where(
                LoginSession.token_hash == hash_token(token),
                LoginSession.expires_at > datetime.now(UTC),
            )
        )
        user = await db.get(UserAccount, login.user_id) if login is not None else None
        if login is None or user is None or not user.is_active:
            return None
        return _BrowserLogin(user, login.id, login.expires_at)


def _origin_allowed(websocket: WebSocket, configured_origins: list[str]) -> bool:
    origin = websocket.headers.get("origin")
    if origin is None:
        return False
    if origin in configured_origins:
        return True
    parsed = urlsplit(origin)
    host = websocket.headers.get("host")
    return (
        parsed.scheme in {"http", "https"}
        and parsed.path in {"", "/"}
        and not parsed.query
        and not parsed.fragment
        and host is not None
        and parsed.netloc.casefold() == host.casefold()
    )


async def _reject(websocket: WebSocket, close_code: int, error_code: str) -> None:
    await websocket.close(code=close_code, reason=describe_error(error_code).message)


async def _handle_browser(viewer: BrowserTerminalConnection) -> None:
    connection = viewer.runtime
    while True:
        raw = await viewer.websocket.receive_json()
        async with connection.control_lock:
            if connection.viewer is not viewer or viewer.ended.is_set() or connection.closed:
                return
            if connection.generation != agent_generation(connection.agent_id):
                return
            if await _handle_control(viewer, raw):
                break
    await terminal_coordinator.terminate(connection, viewer)


async def _handle_control(viewer: BrowserTerminalConnection, raw: object) -> bool:
    connection = viewer.runtime
    try:
        message = _MESSAGES.validate_python(raw)
        if isinstance(message, _CloseMessage):
            return True
        await connection.check_access()
        if isinstance(message, _InputMessage):
            try:
                decoded = base64.b64decode(message.data, validate=True)
            except (binascii.Error, ValueError) as exc:
                raise ValueError("terminal.inputInvalid") from exc
            if len(decoded) > 65_536:
                raise ValueError("terminal.inputInvalid")
            await connection.client.request(
                "terminal.write",
                {"terminalId": connection.terminal_id, "data": message.data},
                TerminalControlResult,
                timeout=3,
            )
        elif isinstance(message, _ResizeMessage):
            await connection.client.request(
                "terminal.resize",
                {"terminalId": connection.terminal_id, "rows": message.rows, "columns": message.columns},
                TerminalControlResult, timeout=3,
            )
            connection.rows, connection.columns = message.rows, message.columns
        elif isinstance(message, _AttachMessage):
            if message.session_id != connection.owner_session_id:
                raise ValueError("terminal.alreadyAttached")
            await terminal_coordinator.attach(connection)
        elif isinstance(message, _DetachMessage):
            await terminal_coordinator.detach(connection)
    except ValidationError:
        await connection.error("terminal.messageInvalid")
    except ValueError as exc:
        code = str(exc)
        await connection.error(code if code.startswith("terminal.") or code == "session.notFound" else "terminal.messageInvalid")
    except ExecutionClientError as exc:
        await connection.error(exc.code)
    return False


@router.websocket("/{agent_id}")
async def browser_terminal(websocket: WebSocket, agent_id: UUID) -> None:
    config = get_runtime_config()
    if not _origin_allowed(websocket, config.cors_origin_list):
        await _reject(websocket, 4403, "terminal.originDenied")
        return
    try:
        login = await _browser_login(websocket)
    except (SQLAlchemyError, TimeoutError):
        await _reject(websocket, 1013, "terminal.unavailable")
        return
    if login is None:
        await _reject(websocket, 4401, "auth.notAuthenticated")
        return
    try:
        session_id = UUID(websocket.query_params.get("sessionId", ""))
    except ValueError:
        await _reject(websocket, 4404, "session.notFound")
        return
    try:
        await validate_terminal_access(login.user.id, session_id, agent_id, login.login_id)
    except ExecutionClientError as exc:
        await _reject(websocket, 4403 if exc.code == "executionAgent.operationUnavailable" else 4404, exc.code)
        return
    except (SQLAlchemyError, TimeoutError):
        await _reject(websocket, 1013, "terminal.unavailable")
        return
    await websocket.accept()
    viewer: BrowserTerminalConnection | None = None
    tasks: set[asyncio.Task] = set()
    try:
        viewer = await terminal_coordinator.open(
            login.user.id, session_id, agent_id, login.login_id, websocket
        )
        tasks = {
            asyncio.create_task(_handle_browser(viewer)), asyncio.create_task(viewer.send()),
            asyncio.create_task(viewer.ended.wait()),
        }
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            task.result()
    except WebSocketDisconnect:
        pass
    except ExecutionClientError as exc:
        with suppress(Exception):
            async with asyncio.timeout(1):
                await websocket.send_json(
                    {"type": "error", "error": describe_error(exc.code).payload()}
                )
    except Exception:
        with suppress(Exception):
            async with asyncio.timeout(1):
                await websocket.send_json(
                    {
                        "type": "error",
                        "error": describe_error("terminal.unavailable").payload(),
                    }
                )
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if viewer is not None:
            if viewer.reason == "terminated" and viewer.runtime.cleanup_task is not None:
                with suppress(Exception):
                    async with asyncio.timeout(16):
                        await asyncio.shield(viewer.runtime.cleanup_task)
            await terminal_coordinator.release(viewer)
            if viewer.reason != "released":
                with suppress(Exception):
                    async with asyncio.timeout(1):
                        await websocket.send_json({
                            "type": "terminal.replaced" if viewer.reason == "replaced" else "terminal.closed",
                            "reason": viewer.reason,
                        })
        with suppress(Exception):
            async with asyncio.timeout(1):
                await websocket.close(
                    code=4001 if viewer is not None and viewer.reason == "replaced" else
                    4002 if viewer is not None and viewer.reason == "resyncRequired" else 1000,
                    reason=viewer.reason if viewer is not None else "unavailable",
                )
