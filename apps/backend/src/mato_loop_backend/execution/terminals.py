from __future__ import annotations

import asyncio
import base64
import json
import logging
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from fastapi import WebSocket
from sqlalchemy.exc import SQLAlchemyError

from mato_loop_backend.db.session import AsyncSessionFactory
from mato_loop_backend.error_contract import describe_error
from mato_loop_backend.execution.client import (
    ExecutionClient,
    ExecutionClientError,
    SharedTerminalStatus,
    TerminalControlResult,
    TerminalOpenResult,
    TerminalReadResult,
)
from mato_loop_backend.execution.manager import execution_agent_connections
from mato_loop_backend.models import ChatSession, ExecutionAgent, LoginSession, UserAccount

MAX_RUNTIMES = 64
MAX_OWNER_RUNTIMES = 16
MAX_AGENT_RUNTIMES = 6
REPLAY_BYTES = 256 * 1024
VIEWER_BYTES = 2 * 1024 * 1024
VALIDITY_INTERVAL = 2
VALIDITY_GRACE = 30
COMMAND_RESULT_GRACE = 2
TERMINAL_OPERATIONS = frozenset({
    "terminal.read", "terminal.write", "terminal.resize", "terminal.close",
    "terminal.shared.open", "terminal.shared.status", "terminal.shared.acquire",
    "terminal.shared.release", "terminal.shared.prepare", "terminal.shared.execute",
    "terminal.shared.cancel",
})
logger = logging.getLogger(__name__)


def agent_generation(agent_id: UUID) -> int | None:
    return execution_agent_connections.generation(agent_id)


async def validate_terminal_access(
    user_id: UUID, session_id: UUID, agent_id: UUID, login_id: UUID,
) -> ExecutionAgent:
    async with asyncio.timeout(3), AsyncSessionFactory() as db:
        login = await db.get(LoginSession, login_id)
        user = await db.get(UserAccount, user_id)
        if (
            login is None or login.user_id != user_id
            or login.expires_at <= datetime.now(UTC) or user is None or not user.is_active
        ):
            raise ExecutionClientError("auth.sessionExpired")
        session = await db.get(ChatSession, session_id)
        if (
            session is None or session.user_id != user_id
            or session.session_type != "chat" or session.parent_session_id is not None
        ):
            raise ExecutionClientError("session.notFound")
        if session.preferred_execution_agent_id != agent_id:
            raise ExecutionClientError("terminal.agentMismatch")
        if not any(
            tab.get("app_id") == "terminal" and tab.get("kind", "app") == "app"
            for tab in (session.control_center or {}).get("tabs", [])
        ):
            raise ExecutionClientError("terminal.disconnected")
        agent = await db.get(ExecutionAgent, agent_id)
        if (
            agent is None or agent.user_id != user_id or agent.revoked_at is not None
            or agent.home_directory is None
        ):
            raise ExecutionClientError("executionAgent.notFound")
        operations = execution_agent_connections.snapshot(agent_id)
        if operations is None:
            raise ExecutionClientError("executionAgent.unavailable")
        if not TERMINAL_OPERATIONS.issubset(operations):
            raise ExecutionClientError("executionAgent.operationUnavailable")
        return agent


@dataclass(slots=True)
class BrowserTerminalConnection:
    runtime: TerminalRuntime
    websocket: WebSocket
    queue: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(maxsize=128))
    queued_bytes: int = 0
    ended: asyncio.Event = field(default_factory=asyncio.Event)
    reason: str = "released"

    def stop(self, reason: str) -> None:
        self.reason = reason
        self.ended.set()
        while not self.queue.empty():
            self.queue.get_nowait()
        self.queued_bytes = 0

    def enqueue(self, payload: dict[str, Any] | bytes) -> None:
        if self.ended.is_set():
            return
        data = payload if isinstance(payload, bytes) else json.dumps(payload)
        size = len(data) if isinstance(data, bytes) else len(data.encode())
        if self.queue.full() or self.queued_bytes + size > VIEWER_BYTES:
            self.stop("resyncRequired")
            return
        self.queue.put_nowait((data, size))
        self.queued_bytes += size

    async def send(self) -> None:
        while not self.ended.is_set():
            data, size = await self.queue.get()
            self.queued_bytes -= size
            try:
                async with asyncio.timeout(5):
                    if isinstance(data, bytes):
                        await self.websocket.send_bytes(data)
                    else:
                        await self.websocket.send_text(data)
            except TimeoutError:
                self.stop("resyncRequired")


@dataclass(slots=True)
class TerminalRuntime:
    user_id: UUID
    owner_session_id: UUID
    agent_id: UUID
    login_id: UUID
    terminal_id: str
    client: ExecutionClient
    generation: int
    viewer: BrowserTerminalConnection | None = None
    attached: bool = False
    active_tool_call_id: str | None = None
    command_id: UUID | None = None
    command_task: asyncio.Task | None = None
    task: asyncio.Task | None = None
    opening_task: asyncio.Task | None = None
    cleanup_task: asyncio.Task | None = None
    closed: bool = False
    shell_exited: bool = False
    suspended_since: float | None = None
    personal_buffer: bytes = b""
    personal_truncated: bool = False
    native_status: SharedTerminalStatus | None = None
    rows: int = 24
    columns: int = 80
    send_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    control_lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def status(self) -> dict[str, Any]:
        native = self.native_status
        return {
            "terminalId": self.terminal_id,
            "attached": self.attached,
            "busy": self.command_id is not None or (native is not None and native.state == "running"),
            "state": "closed" if self.closed or self.shell_exited else
            "running" if self.command_task is not None and not self.command_task.done() else
            native.state if native else "busy",
            "activeToolCallId": self.active_tool_call_id,
            "currentDirectory": native.current_directory if native else self.client.current_directory,
            "suspended": self.suspended_since is not None,
        }

    async def send_json(self, payload: dict[str, Any]) -> None:
        async with self.send_lock:
            if self.viewer is not None:
                self.viewer.enqueue(payload)

    async def send_bytes(self, payload: bytes) -> None:
        async with self.send_lock:
            self.personal_truncated |= len(self.personal_buffer) + len(payload) > REPLAY_BYTES
            self.personal_buffer = (self.personal_buffer + payload)[-REPLAY_BYTES:]
            if self.viewer is not None:
                self.viewer.enqueue(payload)

    async def send_status(self) -> None:
        await self.send_json({"type": "terminal.status", **self.status()})

    async def refresh_status(self) -> SharedTerminalStatus:
        result = await self.client.request(
            "terminal.shared.status", {"terminalId": self.terminal_id},
            SharedTerminalStatus, timeout=2,
        )
        if str(result.terminal_id) != self.terminal_id:
            raise ExecutionClientError("executionAgent.protocolError")
        if self.native_status != result:
            self.native_status = result
            await self.send_status()
        if result.state == "closed":
            raise ExecutionClientError("terminal.disconnected")
        return result

    async def check_access(self) -> None:
        while True:
            if self.closed or self.shell_exited or self.generation != agent_generation(self.agent_id):
                raise ExecutionClientError("terminal.disconnected")
            login_id = self.login_id
            try:
                await validate_terminal_access(self.user_id, self.owner_session_id, self.agent_id, login_id)
            except (SQLAlchemyError, TimeoutError) as exc:
                if login_id != self.login_id:
                    continue
                if self.suspended_since is None:
                    self.suspended_since = asyncio.get_running_loop().time()
                raise ExecutionClientError("terminal.unavailable") from exc
            except ExecutionClientError:
                if login_id != self.login_id:
                    continue
                raise
            if login_id == self.login_id:
                break
        if self.closed or self.shell_exited:
            raise ExecutionClientError("terminal.disconnected")
        self.suspended_since = None

    async def error(self, code: str) -> None:
        await self.send_json({"type": "error", "error": describe_error(code).payload()})


async def read_terminal(runtime: TerminalRuntime) -> None:
    while not runtime.closed:
        try:
            result = await runtime.client.request(
                "terminal.read",
                {"terminalId": runtime.terminal_id, "maxBytes": 65_536, "waitSeconds": 5},
                TerminalReadResult, timeout=10,
            )
        except ExecutionClientError as exc:
            if exc.code == "executionAgent.timeout":
                continue
            raise
        if result.data:
            await runtime.send_bytes(base64.b64decode(result.data, validate=True))
        if result.eof:
            runtime.shell_exited = True
            # PTY EOF can arrive before the execute response and its final output.
            # Keep access supervision active while that response finishes.
            if runtime.command_task is not None:
                await asyncio.wait({runtime.command_task}, timeout=COMMAND_RESULT_GRACE)
            return


class TerminalCoordinator:
    def __init__(self) -> None:
        self._runtimes: dict[tuple[UUID, UUID, UUID], TerminalRuntime] = {}
        self._opening: dict[tuple[UUID, UUID, UUID], asyncio.Task] = {}
        self._stopping = False

    async def open(
        self, user_id: UUID, session_id: UUID, agent_id: UUID, login_id: UUID,
        websocket: WebSocket,
    ) -> BrowserTerminalConnection:
        agent = await validate_terminal_access(user_id, session_id, agent_id, login_id)
        if self._stopping:
            raise ExecutionClientError("terminal.unavailable")
        key = (user_id, session_id, agent_id)
        runtime = self._runtimes.get(key)
        if runtime is not None and runtime.generation != agent_generation(agent_id):
            await self.terminate(runtime)
            runtime = None
        restored = runtime is not None or key in self._opening
        opening = self._opening.get(key)
        if runtime is None and opening is None:
            keys = self._runtimes.keys() | self._opening.keys()
            if (
                len(keys) >= MAX_RUNTIMES
                or sum(item[0] == user_id for item in keys) >= MAX_OWNER_RUNTIMES
                or sum(item[2] == agent_id for item in keys) >= MAX_AGENT_RUNTIMES
            ):
                raise ExecutionClientError("terminal.unavailable")
            # Reservations and publication are synchronous in the single worker.
            opening = asyncio.create_task(self._create(user_id, session_id, agent_id, login_id, agent.home_directory))
            self._opening[key] = opening

            def finished(task: asyncio.Task) -> None:
                if self._opening.get(key) is task:
                    self._opening.pop(key)
                if not task.cancelled():
                    task.exception()

            opening.add_done_callback(finished)
        if opening is not None:
            runtime = await asyncio.shield(opening)
        assert runtime is not None
        async with runtime.control_lock:
            await validate_terminal_access(user_id, session_id, agent_id, login_id)
            if runtime.closed or runtime.generation != agent_generation(agent_id):
                raise ExecutionClientError("terminal.disconnected")
            await runtime.refresh_status()
            if runtime.closed or runtime.native_status.state == "closed":
                raise ExecutionClientError("terminal.disconnected")
            async with runtime.send_lock:
                if runtime.viewer is not None:
                    runtime.viewer.stop("replaced")
                viewer = BrowserTerminalConnection(runtime, websocket)
                runtime.viewer = viewer
                runtime.login_id = login_id
                runtime.suspended_since = None
                viewer.enqueue({"type": "terminal.opened", "restored": restored, **runtime.status()})
                viewer.enqueue({
                    "type": "terminal.snapshot", **runtime.status(),
                    "personalData": base64.b64encode(runtime.personal_buffer).decode("ascii"),
                    "personalTruncated": runtime.personal_truncated,
                    "rows": runtime.rows, "columns": runtime.columns,
                })
                return viewer

    async def _create(self, user_id, session_id, agent_id, login_id, home) -> TerminalRuntime:
        generation = agent_generation(agent_id)
        if generation is None:
            raise ExecutionClientError("executionAgent.unavailable")
        runtime = TerminalRuntime(
            user_id, session_id, agent_id, login_id, str(uuid4()),
            ExecutionClient(agent_id, TERMINAL_OPERATIONS, home, expected_generation=generation), generation,
        )
        key = (user_id, session_id, agent_id)
        runtime.opening_task = asyncio.current_task()
        self._runtimes[key] = runtime
        try:
            opened = await runtime.client.request(
                "terminal.shared.open",
                {"terminalId": runtime.terminal_id, "rows": 24, "columns": 80},
                TerminalOpenResult, timeout=5,
            )
            if str(opened.terminal_id) != runtime.terminal_id:
                raise ExecutionClientError("executionAgent.protocolError")
            if self._stopping or runtime.closed:
                raise ExecutionClientError("terminal.disconnected")
            async with asyncio.timeout(3), AsyncSessionFactory() as db:
                session = await db.get(ChatSession, session_id)
                restore_attachment = (
                    session is not None and session.user_id == user_id
                    and session.preferred_execution_agent_id == agent_id
                    and (session.control_center or {}).get("terminal_auto_attach") is True
                )
            if restore_attachment:
                await runtime.check_access()
                await self.attach(runtime, remember=False)
            runtime.task = asyncio.create_task(self._supervise(runtime))
            return runtime
        except BaseException:
            # The caller-specified ID also permits cleanup after an uncertain open response.
            with suppress(Exception):
                async with asyncio.timeout(4):
                    await runtime.client.request(
                        "terminal.close", {"terminalId": runtime.terminal_id}, TerminalControlResult, timeout=3,
                    )
            if self._runtimes.get(key) is runtime:
                self._runtimes.pop(key)
            raise

    async def release(self, viewer: BrowserTerminalConnection) -> None:
        async with viewer.runtime.control_lock:
            if viewer.runtime.viewer is viewer:
                viewer.runtime.viewer = None
            viewer.stop(viewer.reason)

    async def _watch(self, runtime: TerminalRuntime) -> None:
        while True:
            await asyncio.sleep(VALIDITY_INTERVAL)
            login_id = runtime.login_id
            try:
                await validate_terminal_access(runtime.user_id, runtime.owner_session_id, runtime.agent_id, login_id)
            except (SQLAlchemyError, TimeoutError):
                if login_id != runtime.login_id:
                    continue
                now = asyncio.get_running_loop().time()
                if runtime.suspended_since is None:
                    runtime.suspended_since = now
                    await runtime.send_status()
                if now - runtime.suspended_since < VALIDITY_GRACE:
                    continue
                raise ExecutionClientError("terminal.unavailable") from None
            except ExecutionClientError:
                if login_id != runtime.login_id:
                    continue
                raise
            if runtime.generation != agent_generation(runtime.agent_id):
                raise ExecutionClientError("executionAgent.replaced")
            if runtime.suspended_since is not None:
                runtime.suspended_since = None
                await runtime.send_status()

    async def _watch_status(self, runtime: TerminalRuntime) -> None:
        while True:
            await asyncio.sleep(VALIDITY_INTERVAL)
            # Native foreground status may take a second; never delay login reconciliation.
            if runtime.viewer is not None and runtime.command_id is None:
                try:
                    await runtime.refresh_status()
                except ExecutionClientError as exc:
                    if exc.code != "executionAgent.timeout":
                        raise

    async def _supervise(self, runtime: TerminalRuntime) -> None:
        tasks = {
            asyncio.create_task(read_terminal(runtime)), asyncio.create_task(self._watch(runtime)),
            asyncio.create_task(self._watch_status(runtime)),
        }
        try:
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
        except ExecutionClientError as exc:
            await runtime.error(exc.code)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Shared terminal runtime failed")
        finally:
            runtime.closed = True
            if runtime.viewer is not None:
                runtime.viewer.stop("terminated")
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            if runtime.cleanup_task is None:
                await self.terminate(runtime)

    async def terminate(self, runtime: TerminalRuntime, viewer: BrowserTerminalConnection | None = None) -> None:
        # Do not wait for browser controls before stopping the real process.
        if viewer is not None and runtime.viewer is not viewer:
            return
        if runtime.cleanup_task is None:
            runtime.closed = True
            if runtime.viewer is not None:
                runtime.viewer.stop("terminated")
            runtime.cleanup_task = asyncio.create_task(self._terminate(runtime, asyncio.current_task()))
        await asyncio.shield(runtime.cleanup_task)

    async def _terminate(self, runtime: TerminalRuntime, initiator: asyncio.Task | None) -> None:
        try:
            if runtime.opening_task is not None and not runtime.opening_task.done():
                with suppress(Exception, asyncio.CancelledError):
                    async with asyncio.timeout(6):
                        await asyncio.shield(runtime.opening_task)
            with suppress(Exception):
                async with asyncio.timeout(4):
                    await runtime.client.request(
                        "terminal.close", {"terminalId": runtime.terminal_id}, TerminalControlResult, timeout=3,
                    )
            tasks = [task for task in (runtime.task, runtime.command_task)
                     if task is not None and task is not initiator]
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.wait(tasks, timeout=2)
        finally:
            runtime.viewer = None
            runtime.attached = False
            runtime.personal_buffer = b""
            key = (runtime.user_id, runtime.owner_session_id, runtime.agent_id)
            if self._runtimes.get(key) is runtime:
                self._runtimes.pop(key)

    async def shutdown(self) -> None:
        self._stopping = True
        if self._opening:
            _, pending = await asyncio.wait(list(self._opening.values()), timeout=10)
            for task in pending:
                task.cancel()
        tasks = [asyncio.create_task(self.terminate(runtime)) for runtime in list(self._runtimes.values())]
        if tasks:
            await asyncio.wait(tasks, timeout=16)

    def attached_for(self, *, user_id: UUID, session_id: UUID, agent_id: UUID) -> TerminalRuntime | None:
        runtime = self._runtimes.get((user_id, session_id, agent_id))
        # Keep failed/suspended bindings visible: a tool must not fall back to an isolated shell.
        return runtime if runtime is not None and runtime.attached else None

    async def _remember_attachment(self, runtime: TerminalRuntime) -> None:
        attached = runtime.attached
        try:
            async with asyncio.timeout(3), AsyncSessionFactory() as db:
                session = await db.get(ChatSession, runtime.owner_session_id, with_for_update=True)
                if (
                    session is None or session.user_id != runtime.user_id
                    or session.preferred_execution_agent_id != runtime.agent_id
                    or not any(
                        tab.get("app_id") == "terminal" and tab.get("kind", "app") == "app"
                        for tab in (session.control_center or {}).get("tabs", [])
                    )
                ):
                    raise ExecutionClientError("terminal.disconnected")
                session.control_center = {
                    **(session.control_center or {}), "terminal_auto_attach": attached,
                }
                await db.commit()
        except (SQLAlchemyError, TimeoutError) as exc:
            logger.warning("Could not persist terminal attachment", exc_info=True)
            raise ExecutionClientError("terminal.unavailable") from exc

    async def attach(self, runtime: TerminalRuntime, *, remember: bool = True) -> None:
        if runtime.closed:
            raise ValueError("terminal.disconnected")
        await runtime.client.request(
            "terminal.shared.acquire", {"terminalId": runtime.terminal_id},
            TerminalControlResult, timeout=3,
        )
        runtime.attached = True
        try:
            if remember:
                await self._remember_attachment(runtime)
        finally:
            await runtime.send_status()
        await runtime.refresh_status()

    async def detach(self, runtime: TerminalRuntime) -> None:
        if runtime.command_id is not None:
            raise ValueError("terminal.commandActive")
        await runtime.client.request(
            "terminal.shared.release", {"terminalId": runtime.terminal_id},
            TerminalControlResult, timeout=3,
        )
        runtime.attached = False
        try:
            await self._remember_attachment(runtime)
        finally:
            await runtime.send_status()
        await runtime.refresh_status()


terminal_coordinator = TerminalCoordinator()
