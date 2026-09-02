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

from mato_loop_backend.auth.sessions import hash_token
from mato_loop_backend.config import get_runtime_config
from mato_loop_backend.db.session import AsyncSessionFactory
from mato_loop_backend.error_contract import describe_error
from mato_loop_backend.execution import (
    ExecutionClient,
    ExecutionClientError,
    TerminalControlResult,
    TerminalOpenResult,
    TerminalReadResult,
    execution_agent_connections,
)
from mato_loop_backend.execution.terminals import (
    BrowserTerminalConnection,
    terminal_coordinator,
)
from mato_loop_backend.models import ChatSession, ExecutionAgent, LoginSession, UserAccount

router = APIRouter(prefix="/terminals")
_TERMINAL_OPERATIONS = frozenset(
    {
        "terminal.open",
        "terminal.read",
        "terminal.write",
        "terminal.resize",
        "terminal.close",
        "terminal.run",
    }
)


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
    mode: Literal["auto", "confirm"] = "auto"


class _DetachMessage(_BrowserMessage):
    type: Literal["detach"]


class _ModeMessage(_BrowserMessage):
    type: Literal["mode"]
    mode: Literal["auto", "confirm"]


class _ExecuteMessage(_BrowserMessage):
    type: Literal["command.execute"]
    approval_id: UUID = Field(alias="approvalId")
    command: str = Field(min_length=1, max_length=256 * 1024)


class _DenyMessage(_BrowserMessage):
    type: Literal["command.deny"]
    approval_id: UUID = Field(alias="approvalId")


_MESSAGES = TypeAdapter(
    Annotated[
        _InputMessage
        | _ResizeMessage
        | _CloseMessage
        | _AttachMessage
        | _DetachMessage
        | _ModeMessage
        | _ExecuteMessage
        | _DenyMessage,
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


async def _watch_login(login: _BrowserLogin) -> None:
    while True:
        remaining = (login.expires_at - datetime.now(UTC)).total_seconds()
        if remaining <= 0:
            raise ExecutionClientError("auth.sessionExpired")
        await asyncio.sleep(min(5, remaining))
        async with AsyncSessionFactory() as db:
            current_login = await db.get(LoginSession, login.login_id)
            user = await db.get(UserAccount, login.user.id)
        if (
            current_login is None
            or current_login.expires_at <= datetime.now(UTC)
            or user is None
            or not user.is_active
        ):
            raise ExecutionClientError("auth.sessionExpired")


async def _reject(websocket: WebSocket, close_code: int, error_code: str) -> None:
    await websocket.close(code=close_code, reason=describe_error(error_code).message)


async def _read_terminal(connection: BrowserTerminalConnection) -> None:
    while True:
        try:
            result = await connection.client.request(
                "terminal.read",
                {"terminalId": connection.terminal_id, "maxBytes": 65_536, "waitSeconds": 5},
                TerminalReadResult,
                timeout=10,
            )
        except ExecutionClientError as exc:
            if exc.code == "executionAgent.timeout":
                continue
            raise
        assert isinstance(result, TerminalReadResult)
        if result.data:
            try:
                data = base64.b64decode(result.data, validate=True)
            except (binascii.Error, ValueError) as exc:
                raise ExecutionClientError("executionAgent.protocolError") from exc
            if data:
                await connection.send_bytes(data)
        if result.eof:
            return


async def _handle_browser(connection: BrowserTerminalConnection) -> None:
    while True:
        raw = await connection.websocket.receive_json()
        try:
            message = _MESSAGES.validate_python(raw)
            if isinstance(message, _InputMessage):
                terminal_id = terminal_coordinator.input_terminal_id(connection)
                if terminal_id is None:
                    raise ValueError("terminal.readOnly")
                try:
                    decoded = base64.b64decode(message.data, validate=True)
                except (binascii.Error, ValueError) as exc:
                    raise ValueError("terminal.inputInvalid") from exc
                if len(decoded) > 65_536:
                    raise ValueError("terminal.inputInvalid")
                await connection.client.request(
                    "terminal.write",
                    {"terminalId": terminal_id, "data": message.data},
                    TerminalControlResult,
                )
            elif isinstance(message, _ResizeMessage):
                terminal_id = terminal_coordinator.resize_terminal_id(
                    connection, rows=message.rows, columns=message.columns
                )
                if terminal_id is not None:
                    await connection.client.request(
                        "terminal.resize",
                        {
                            "terminalId": terminal_id,
                            "rows": message.rows,
                            "columns": message.columns,
                        },
                        TerminalControlResult,
                    )
            elif isinstance(message, _AttachMessage):
                async with AsyncSessionFactory() as db:
                    session = await db.get(ChatSession, message.session_id)
                    if session is None or session.user_id != connection.user_id:
                        raise ValueError("session.notFound")
                    if session.preferred_execution_agent_id != connection.agent_id:
                        raise ValueError("terminal.agentMismatch")
                    await terminal_coordinator.attach(connection, session, message.mode)
            elif isinstance(message, _DetachMessage):
                await terminal_coordinator.detach(connection)
            elif isinstance(message, _ModeMessage):
                await terminal_coordinator.set_mode(connection, message.mode)
            elif isinstance(message, _ExecuteMessage):
                async with AsyncSessionFactory() as db:
                    await terminal_coordinator.resolve(
                        db, connection, message.approval_id, command_line=message.command
                    )
            elif isinstance(message, _DenyMessage):
                async with AsyncSessionFactory() as db:
                    await terminal_coordinator.resolve(
                        db, connection, message.approval_id, command_line=None
                    )
            else:
                return
        except ValidationError:
            await connection.error("terminal.messageInvalid")
        except ValueError as exc:
            code = str(exc)
            await connection.error(code if code.startswith("terminal.") or code == "session.notFound" else "terminal.messageInvalid")
        except ExecutionClientError as exc:
            await connection.error(exc.code)


@router.websocket("/{agent_id}")
async def browser_terminal(websocket: WebSocket, agent_id: UUID) -> None:
    config = get_runtime_config()
    if not _origin_allowed(websocket, config.cors_origin_list):
        await _reject(websocket, 4403, "terminal.originDenied")
        return
    login = await _browser_login(websocket)
    if login is None:
        await _reject(websocket, 4401, "auth.notAuthenticated")
        return
    user = login.user
    operations = execution_agent_connections.snapshot(agent_id)
    async with AsyncSessionFactory() as db:
        agent = await db.get(ExecutionAgent, agent_id)
        valid_agent = (
            agent is not None
            and agent.user_id == user.id
            and agent.revoked_at is None
            and agent.home_directory is not None
            and operations is not None
        )
        home_directory = agent.home_directory if valid_agent else None
    if not valid_agent:
        await _reject(websocket, 4404, "executionAgent.notFound")
        return
    assert operations is not None and home_directory is not None
    if not _TERMINAL_OPERATIONS.issubset(operations):
        await _reject(websocket, 4403, "executionAgent.operationUnavailable")
        return

    await websocket.accept()
    client = ExecutionClient(agent_id, operations, home_directory)
    connection: BrowserTerminalConnection | None = None
    try:
        opened = await client.request(
            "terminal.open", {"rows": 24, "columns": 80}, TerminalOpenResult
        )
        assert isinstance(opened, TerminalOpenResult)
        connection = BrowserTerminalConnection(
            user.id, agent_id, str(opened.terminal_id), client, websocket
        )
        await connection.send_json(
            {"type": "terminal.opened", "terminalId": str(opened.terminal_id)}
        )
        read_task = asyncio.create_task(_read_terminal(connection))
        browser_task = asyncio.create_task(_handle_browser(connection))
        login_task = asyncio.create_task(_watch_login(login))
        done, pending = await asyncio.wait(
            {read_task, browser_task, login_task}, return_when=asyncio.FIRST_COMPLETED
        )
        for task in pending:
            task.cancel()
        for task in pending:
            with suppress(asyncio.CancelledError):
                await task
        for task in done:
            task.result()
    except WebSocketDisconnect:
        pass
    except ExecutionClientError as exc:
        with suppress(Exception):
            if connection is not None:
                await connection.error(exc.code)
            else:
                await websocket.send_json(
                    {"type": "error", "error": describe_error(exc.code).payload()}
                )
    except Exception:
        with suppress(Exception):
            if connection is not None:
                await connection.error("terminal.unavailable")
            else:
                await websocket.send_json(
                    {
                        "type": "error",
                        "error": describe_error("terminal.unavailable").payload(),
                    }
                )
    finally:
        if connection is not None:
            active_terminal_id = connection.active_terminal_id
            await terminal_coordinator.disconnect(connection)
            if active_terminal_id is not None:
                with suppress(Exception):
                    await client.request(
                        "terminal.close",
                        {"terminalId": active_terminal_id},
                        TerminalControlResult,
                        timeout=5,
                    )
            with suppress(Exception):
                await client.request(
                    "terminal.close",
                    {"terminalId": connection.terminal_id},
                    TerminalControlResult,
                    timeout=5,
                )
        with suppress(Exception):
            await websocket.close()
