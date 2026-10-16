from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import json
import logging
import secrets
from contextlib import ExitStack, contextmanager, suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import PurePosixPath
from tempfile import SpooledTemporaryFile
from time import monotonic
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from anyio import CancelScope
from fastapi import WebSocket
from pydantic import ValidationError

from mato_loop_backend.db.session import AsyncSessionFactory
from mato_loop_backend.diagnostics.service import schedule_diagnostic_event
from mato_loop_backend.error_contract import describe_error
from mato_loop_backend.execution.browser_contracts import (
    BrowserActionArguments,
    BrowserActionResult,
    BrowserControlResult,
    BrowserExecuteArguments,
    BrowserFileArguments,
    BrowserFileListResult,
    BrowserFileResult,
    BrowserInspectionData,
    BrowserNavigationArguments,
    BrowserNavigationResult,
    BrowserOpenResult,
    BrowserOrigin,
    BrowserPublishedDownloadResult,
    BrowserResizeResult,
)
from mato_loop_backend.execution.client import (
    ExecutionClient,
    ExecutionClientError,
    WorkspaceStatResult,
)
from mato_loop_backend.execution.manager import execution_agent_connections
from mato_loop_backend.files.service import RootFileService
from mato_loop_backend.integrations.browser.adapter import browser_client, load_browser_integration
from mato_loop_backend.models import (
    AgentRun,
    ChatSession,
    ExecutionAgent,
    LoginSession,
    UserAccount,
)
from mato_loop_backend.permissions.policy import (
    PermissionDecision,
    execution_directory_permission,
    integration_operation_permission,
)
from mato_loop_backend.providers.base import ToolCall
from mato_loop_backend.settings.effective import (
    AgentProfileUnavailable,
    integration_profile_available,
    load_agent_profile,
)

if TYPE_CHECKING:
    from mato_loop_backend.execution.browser_control import ControlRequest

ACCESS_INTERVAL = 2
IDLE_SECONDS = 30
MAX_BROWSERS = 32
DOWNLOAD_COLLISION_LIMIT = 100
logger = logging.getLogger(__name__)


def browser_download_path(home: PurePosixPath, filename: str, index: int) -> str:
    if index == 0:
        name = filename
    else:
        suffix = PurePosixPath(filename).suffix
        marker = f" ({index})"
        if len(suffix) + len(marker) >= 255:
            suffix = ""
        stem = filename[:-len(suffix)] if suffix else filename
        name = f"{stem[:255 - len(marker) - len(suffix)]}{marker}{suffix}"
    return str(home / "browser" / "downloads" / name)


async def available_browser_download_path(connection: Any, home: PurePosixPath, filename: str) -> str:
    for index in range(DOWNLOAD_COLLISION_LIMIT):
        candidate = browser_download_path(home, filename, index)
        try:
            result = await connection.request(
                "filesystem.stat", {"path": candidate}, WorkspaceStatResult,
            )
        except ExecutionClientError as exc:
            if exc.code == "file.pathNotFound":
                return candidate
            raise
        if result.path != candidate:
            raise ExecutionClientError("executionAgent.protocolError")
    raise ExecutionClientError("file.pathAlreadyExists")


@contextmanager
def _browser_timing(timings: dict, phase: str):
    started = monotonic()
    try:
        yield
    finally:
        timings[phase] = (monotonic() - started) * 1000


@contextmanager
def _browser_latency(operation: str, session_id: UUID):
    timings = {}
    outcome = "error"
    try:
        with _browser_timing(timings, "total_ms"):
            yield timings
        outcome = "ok"
    except asyncio.CancelledError:
        outcome = "cancelled"
        raise
    finally:
        logger.info(
            "Browser coordinator latency operation=%s session_id=%s outcome=%s timings=%s",
            operation, session_id, outcome, timings,
        )


async def validate_browser_access(
    owner_id: UUID, integration_id: UUID, *, login_id: UUID | None = None,
    session_id: UUID | None = None, require_enabled: bool = True, run_id: UUID | None = None,
):
    """Resolve live authorization, never trust a client-supplied agent/profile handle."""
    async with asyncio.timeout(5), AsyncSessionFactory() as db:
        user = await db.get(UserAccount, owner_id)
        if user is None or not user.is_active:
            raise ExecutionClientError("auth.sessionExpired")
        if login_id is not None:
            login = await db.get(LoginSession, login_id)
            if login is None or login.user_id != owner_id or login.expires_at <= datetime.now(UTC):
                raise ExecutionClientError("auth.sessionExpired")
        instance = await load_browser_integration(
            db, owner_id, integration_id, require_enabled=require_enabled,
        )
        can_attach = False
        if session_id is not None:
            session = await db.get(ChatSession, session_id)
            if (session is None or session.user_id != owner_id or session.session_type != "chat"
                    or session.parent_session_id is not None):
                raise ExecutionClientError("session.notFound")
            if run_id is not None:
                from mato_loop_backend.agent.session_settings import apply_run_session_settings

                run = await db.get(AgentRun, run_id)
                if run is None or run.session_id != session.id or run.parent_run_id is not None:
                    raise ExecutionClientError("browser.bindingChanged")
                if run.status in {"queued", "running", "waiting_approval", "waiting_input"}:
                    apply_run_session_settings(session, run)
            if session.preferred_execution_agent_id != instance.execution_agent_id:
                raise ExecutionClientError("browser.bindingChanged")
            if not any(tab.get("app_id") == "browser" and tab.get("kind", "app") == "app"
                       for tab in (session.control_center or {}).get("tabs", [])):
                raise ExecutionClientError("browser.notConnected")
            try:
                profile = await load_agent_profile(db, owner_id, session.agent_profile_id)
                can_attach = integration_profile_available(instance, profile)
            except AgentProfileUnavailable:
                pass
        return instance, can_attach


@dataclass(slots=True)
class BrowserConnection:
    owner_id: UUID
    integration_id: UUID
    agent_id: UUID
    session_id: UUID
    login_id: UUID | None
    client: ExecutionClient
    generation: int
    run_id: UUID | None = None
    browser_id: UUID = field(default_factory=uuid4)
    view_id: UUID = field(default_factory=uuid4)
    websocket: WebSocket | None = None
    view_socket: WebSocket | None = None
    attached: bool = False
    attachment_revision: int = 0
    busy: bool = False
    can_attach: bool = False
    closed: bool = False
    ready: bool = False
    resetting_view: bool = False
    manual_enabled: bool = True
    navigation: BrowserNavigationResult | None = None
    downloads: list[dict[str, Any]] = field(default_factory=list)
    origins: dict[str, tuple[BrowserOrigin, int]] = field(default_factory=dict)
    control_epoch: int = 0
    task: asyncio.Task | None = None
    idle_task: asyncio.Task | None = None
    action_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    send_lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def diagnostic(self, event: str, reason: str, *, error_code: str | None = None) -> None:
        schedule_diagnostic_event(
            self.owner_id, f"browser.{event}", component="browser",
            session_id=self.session_id, correlation_id=self.browser_id,
            payload={
                "browserId": str(self.browser_id), "viewId": str(self.view_id),
                "agentId": str(self.agent_id), "integrationId": str(self.integration_id),
                "attachmentRevision": self.attachment_revision,
                "reason": reason,
                "errorCode": describe_error(error_code).code if error_code is not None else None,
            },
        )

    def detach(self, reason: str = "manualTakeover", *, error_code: str | None = None) -> None:
        was_attached = self.attached
        self.attached = False
        self.run_id = None
        self.attachment_revision += 1
        if was_attached:
            self.diagnostic("detached", reason, error_code=error_code)

    async def send(self, payload: dict) -> None:
        async with self.send_lock:
            if self.websocket is not None and not self.closed:
                async with asyncio.timeout(3):
                    await self.websocket.send_json(payload)

    async def state(self) -> None:
        await self.send({
            "type": "state", "browserId": str(self.browser_id), "viewId": str(self.view_id),
            "attached": self.attached,
            "busy": self.busy or self.resetting_view or (not self.attached and not self.manual_enabled),
            "canAttach": self.can_attach,
            "attachReason": None if self.can_attach else "tool.unavailable",
            "downloads": self.downloads,
        })

    def origin(self, kind: str, *, run_id: UUID | None = None) -> dict[str, Any]:
        origin = BrowserOrigin(
            id=uuid4(), kind=kind, owner_id=self.owner_id, session_id=self.session_id,
            integration_id=self.integration_id, run_id=run_id,
            view_id=self.view_id if kind == "human" else None,
        )
        self.control_epoch += 1
        token = str(origin.id)
        self.origins[token] = (origin, self.control_epoch)
        return {"origin": token, "controlEpoch": self.control_epoch}

    async def check_access(self) -> None:
        if self.closed or execution_agent_connections.generation(self.agent_id) != self.generation:
            raise ExecutionClientError("browser.notConnected")
        instance, self.can_attach = await validate_browser_access(
            self.owner_id, self.integration_id, login_id=self.login_id, session_id=self.session_id,
            run_id=self.run_id,
        )
        if self.closed or instance.execution_agent_id != self.agent_id:
            raise ExecutionClientError("browser.bindingChanged")
        if not self.can_attach and self.attached:
            self.detach("accessRevoked", error_code="tool.unavailable")

    async def check_manual(self) -> None:
        await self.check_access()
        if not self.ready or self.websocket is None:
            raise ExecutionClientError("browser.notConnected")
        if self.attached or self.run_id is not None or self.busy or not self.manual_enabled:
            raise ExecutionClientError("browser.busy")

    async def request(self, *args, **kwargs):
        """Keep dispatched human operations alive until the shared action lock can pass."""
        pending = asyncio.create_task(self.client.request(*args, **kwargs))
        try:
            return await asyncio.shield(pending)
        except asyncio.CancelledError:
            with CancelScope(shield=True):
                await asyncio.gather(pending, return_exceptions=True)
            raise


@dataclass(slots=True)
class BrowserRelay:
    connection: BrowserConnection
    token: str = field(default_factory=lambda: secrets.token_urlsafe(32), repr=False)
    relay_id: UUID = field(default_factory=uuid4)
    incoming: asyncio.Queue[bytes] = field(default_factory=lambda: asyncio.Queue(maxsize=4))
    outgoing: asyncio.Queue[bytes] = field(default_factory=lambda: asyncio.Queue(maxsize=4))
    ended: asyncio.Event = field(default_factory=asyncio.Event)
    accepted: bool = False
    ready: bool = False
    connected: bool = False


class BrowserCoordinator:
    def __init__(self) -> None:
        self.connections: dict[UUID, BrowserConnection] = {}
        self.relays: dict[UUID, BrowserRelay] = {}
        self.lock = asyncio.Lock()

    def attached_for(self, session_id: UUID, agent_id: UUID) -> BrowserConnection | None:
        connection = self.connections.get(agent_id)
        return connection if (
            connection is not None and connection.session_id == session_id
            and connection.attached and not connection.closed and connection.ready
            and connection.generation == execution_agent_connections.generation(agent_id)
        ) else None

    async def open(self, owner_id, integration_id, session_id, login_id, websocket,
                   *, expected_browser_id: UUID | None = None, expected_view_id: UUID | None = None,
                   control_request: ControlRequest | None = None,
                   color_scheme: str = "light") -> BrowserConnection:
        instance, can_attach = await validate_browser_access(
            owner_id, integration_id, login_id=login_id, session_id=session_id,
            run_id=control_request.info.run_id if control_request is not None else None,
        )
        agent_id = instance.execution_agent_id
        async with self.lock:
            if control_request is not None:
                await control_request.validate()
            previous = self.connections.get(agent_id)
            if previous is not None:
                if previous.integration_id != integration_id or previous.owner_id != owner_id:
                    raise ExecutionClientError("browser.busy")
                requested = expected_view_id is not None
                if (not requested and previous.websocket is None and previous.session_id == session_id
                        and previous.ready and not previous.closed):
                    previous.login_id = login_id
                    previous.websocket = websocket
                    previous.can_attach = can_attach
                    if control_request is not None:
                        previous.run_id = control_request.info.run_id
                    if previous.idle_task is not None:
                        previous.idle_task.cancel()
                        previous.idle_task = None
                    return previous
                resume = (not requested and previous.websocket is None
                          and previous.session_id == session_id and previous.login_id == login_id)
                if (previous.closed or not previous.ready or (not requested and not resume)
                        or (requested and (previous.view_id != expected_view_id or previous.browser_id != expected_browser_id))):
                    return previous
            elif expected_view_id is not None:
                raise ExecutionClientError("browser.notConnected")
            if previous is None and len(self.connections) >= MAX_BROWSERS:
                raise ExecutionClientError("browser.busy")
            connection = BrowserConnection(
                owner_id, integration_id, agent_id, session_id, login_id, browser_client(agent_id),
                execution_agent_connections.generation(agent_id), websocket=websocket,
                run_id=control_request.info.run_id if control_request is not None else None,
                can_attach=can_attach,
            )
            if previous is not None:
                # Retire the immutable view binding, not the Chromium runtime it controlled.
                previous.closed = True
                previous.detach("takeover")
                for task in (previous.task, previous.idle_task):
                    if task is not None:
                        task.cancel()
                connection.browser_id = previous.browser_id
                connection.client = previous.client
                connection.generation = previous.generation
                connection.action_lock = previous.action_lock
                connection.attachment_revision = previous.attachment_revision
                connection.manual_enabled = False
                connection.origins = previous.origins
                connection.control_epoch = previous.control_epoch
            from mato_loop_backend.execution.browser_control import browser_control_requests

            browser_control_requests.invalidate_agent(agent_id, control_request)
            self.connections[agent_id] = connection
            if control_request is not None:
                control_request.bind(connection)
        opening = asyncio.create_task(self._start(connection, previous, control_request, color_scheme))
        connection.task = opening
        try:
            return await asyncio.shield(opening)
        except asyncio.CancelledError:
            with CancelScope(shield=True):
                await asyncio.gather(opening, return_exceptions=True)
                await self.release(connection, websocket)
            raise

    async def _start(self, connection: BrowserConnection, previous: BrowserConnection | None,
                     control_request: ControlRequest | None = None,
                     color_scheme: str = "light") -> BrowserConnection:
        try:
            if control_request is not None:
                await control_request.validate()
            if previous is None:
                result = await connection.request(
                    "integration.browser.open",
                    {
                        "ownerId": str(connection.owner_id),
                        "browserId": str(connection.browser_id),
                        "colorScheme": color_scheme,
                    },
                    BrowserOpenResult, timeout=60,
                )
                if result.browser_id != connection.browser_id:
                    raise ExecutionClientError("executionAgent.protocolError")
                if control_request is not None:
                    await control_request.validate()
                result = await connection.request(
                    "integration.browser.control",
                    {"browserId": str(connection.browser_id), "manual": True,
                     **connection.origin("human")},
                    BrowserControlResult,
                )
                if not result.ok:
                    raise ExecutionClientError("browser.actionFailed")
            else:
                await connection.send({"type": "availability", "status": "takingOver",
                                       "browserId": str(connection.browser_id)})
                async with connection.action_lock:
                    await connection.check_access()
                    # Always fence manual-to-manual handoffs too, releasing held keys/buttons.
                    for manual in (False, True):
                        if control_request is not None:
                            await control_request.validate()
                        if manual and previous.websocket is not None:
                            with suppress(Exception):
                                async with previous.send_lock, asyncio.timeout(2):
                                    await previous.websocket.send_json({"type": "replaced"})
                            if control_request is not None:
                                await control_request.validate()
                        result = await connection.request(
                            "integration.browser.control",
                            {"browserId": str(connection.browser_id), "manual": manual,
                             "rotateView": not manual, **connection.origin(
                                 "model" if not manual and control_request is not None else "human",
                                 run_id=control_request.info.run_id if not manual and control_request is not None else None,
                             )},
                            BrowserControlResult,
                        )
                        if not result.ok:
                            raise ExecutionClientError("browser.actionFailed")
                    await connection.check_access()
                    connection.manual_enabled = True
            await connection.check_access()
            if control_request is not None:
                await control_request.validate()
            connection.ready = True
            connection.diagnostic("opened", "takeover" if previous is not None else "start")
            connection.task = asyncio.create_task(self.watch(connection))
            return connection
        except BaseException as exc:
            code = (control_request.error if control_request is not None else None) or (
                exc.code if isinstance(exc, ExecutionClientError) else "browser.unavailable"
            )
            if isinstance(exc, Exception):
                with suppress(Exception):
                    await connection.send({"type": "error", "error": describe_error(code).payload()})
            await self.close(
                connection, reason="controlTimeout" if code == "browser.controlTimeout" else "startupFailed",
                error_code=code,
            )
            raise
        finally:
            if previous is not None:
                for relay_id, relay in list(self.relays.items()):
                    if relay.connection is previous:
                        relay.ended.set()
                        self.relays.pop(relay_id, None)
                for socket in (previous.view_socket, previous.websocket):
                    if socket is not None:
                        with suppress(Exception):
                            async with asyncio.timeout(1):
                                await socket.close(code=4001)

    async def availability(self, connection: BrowserConnection) -> dict:
        async with AsyncSessionFactory() as db:
            session = await db.get(ChatSession, connection.session_id)
            title = session.title if session is not None and session.user_id == connection.owner_id else None
        available = connection.ready and not connection.closed
        return {"type": "availability", "status": "occupied" if available else "waiting",
                "browserId": str(connection.browser_id), "viewId": str(connection.view_id),
                "ownerSession": {"id": str(connection.session_id), "title": title or "Session"},
                "busy": connection.busy or connection.action_lock.locked(), "canTakeover": available}

    async def watch(self, connection: BrowserConnection) -> None:
        try:
            while not connection.closed:
                await asyncio.sleep(ACCESS_INTERVAL)
                previous = connection.can_attach, connection.attached
                await connection.check_access()
                if (not connection.attached and not connection.manual_enabled
                        and not connection.action_lock.locked()):
                    await self.attachment(connection, False)
                if previous != (connection.can_attach, connection.attached):
                    await connection.state()
                if connection.websocket is not None and connection.navigation is not None and not connection.action_lock.locked():
                    try:
                        await self.navigate(connection, BrowserNavigationArguments(action="state"))
                    except ExecutionClientError:
                        # Command failures have their own reply; transient observations retain state.
                        pass
                if not connection.action_lock.locked():
                    async with connection.action_lock:
                        await connection.check_access()
                        changed = await self.poll_downloads(connection)
                    if changed:
                        await connection.state()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            code = exc.code if isinstance(exc, ExecutionClientError) else "browser.unavailable"
            with suppress(Exception):
                await connection.send({"type": "error", "error": describe_error(code).payload()})
            await self.close(
                connection,
                reason=("agentDisconnected" if execution_agent_connections.generation(connection.agent_id)
                        != connection.generation else "watchFailed"),
                error_code=code,
            )

    async def release(self, connection: BrowserConnection, websocket: WebSocket) -> None:
        if connection.closed or connection.websocket is not websocket:
            return
        connection.websocket = None
        for relay in list(self.relays.values()):
            if relay.connection is connection:
                relay.ended.set()
        if connection.view_socket is not None:
            with suppress(Exception):
                await connection.view_socket.close()
            connection.view_socket = None
        if not connection.closed and not connection.attached:
            connection.detach("controlDisconnected")
            if connection.idle_task is not None:
                connection.idle_task.cancel()
            async def expire():
                await asyncio.sleep(IDLE_SECONDS)
                await self.close(connection, reason="idleTimeout")
            connection.idle_task = asyncio.create_task(expire())

    async def close(self, connection: BrowserConnection, *, reason: str = "manualClose",
                    error_code: str | None = None) -> None:
        if connection.closed:
            return
        connection.closed = True
        connection.detach(reason, error_code=error_code)
        connection.diagnostic("closed", reason, error_code=error_code)
        for task in (connection.task, connection.idle_task):
            if task is not None and task is not asyncio.current_task():
                task.cancel()
        for relay_id, relay in list(self.relays.items()):
            if relay.connection is connection:
                relay.ended.set()
                self.relays.pop(relay_id, None)
        try:
            async with connection.action_lock:
                with suppress(Exception):
                    await connection.request(
                        "integration.browser.close", {"browserId": str(connection.browser_id)},
                        BrowserControlResult, timeout=15,
                    )
        finally:
            for socket in (connection.view_socket, connection.websocket):
                if socket is not None:
                    with suppress(Exception):
                        async with asyncio.timeout(1):
                            await socket.close()
            if self.connections.get(connection.agent_id) is connection:
                self.connections.pop(connection.agent_id)

    async def attachment(self, connection: BrowserConnection, attached: bool,
                         *, control_request: ControlRequest | None = None,
                         reason: str | None = None) -> None:
        if control_request is not None:
            await control_request.validate()
            connection.run_id = control_request.info.run_id
        await connection.check_access()
        if attached:
            if not connection.can_attach:
                raise ExecutionClientError("tool.unavailable")
            if connection.busy or (control_request is None and (
                connection.action_lock.locked() or connection.view_socket is None
            )):
                raise ExecutionClientError("browser.busy")
            async with connection.action_lock:
                await connection.check_access()
                if control_request is not None:
                    await control_request.validate()
                    control_request.binding_ready()
                result = await connection.request(
                    "integration.browser.control", {"browserId": str(connection.browser_id), "manual": False,
                    **connection.origin(
                        "model" if control_request is not None else "human",
                        run_id=control_request.info.run_id if control_request is not None else None,
                    )},
                    BrowserControlResult,
                )
                if not result.ok:
                    raise ExecutionClientError("browser.actionFailed")
                connection.manual_enabled = False
                await connection.check_access()
                if not connection.can_attach or (
                    control_request is None and connection.view_socket is None
                ):
                    raise ExecutionClientError("browser.bindingChanged")
                if control_request is not None:
                    await control_request.validate()
                    control_request.binding_ready()
                connection.detach("attachmentReplaced")
                connection.attached = True
                connection.run_id = control_request.info.run_id if control_request is not None else None
                connection.diagnostic("attached", "modelRequest" if control_request is not None else "manualAttach")
                if control_request is not None:
                    from mato_loop_backend.execution.browser_control import browser_control_requests

                    browser_control_requests.finish(control_request)
                await connection.state()
        else:
            connection.detach(reason or "manualTakeover")
            await connection.state()
            # Revoke queued actions immediately, but let a dispatched action settle.
            async with connection.action_lock:
                if connection.closed:
                    return
                if connection.manual_enabled:
                    await connection.state()
                    return
                connection.resetting_view = True
                try:
                    await connection.send({"type": "view.reset"})
                    result = await connection.request(
                        "integration.browser.control", {"browserId": str(connection.browser_id), "manual": True,
                         **connection.origin("human")},
                        BrowserControlResult,
                    )
                    if not result.ok:
                        raise ExecutionClientError("browser.actionFailed")
                    connection.manual_enabled = True
                    old_view, connection.view_socket = connection.view_socket, None
                    for relay in list(self.relays.values()):
                        if relay.connection is connection:
                            relay.ended.set()
                    if old_view is not None:
                        with suppress(Exception):
                            await old_view.close()
                finally:
                    connection.resetting_view = False
                await connection.state()
                if connection.websocket is None and not connection.closed:
                    if connection.idle_task is not None:
                        connection.idle_task.cancel()
                    async def expire():
                        await asyncio.sleep(IDLE_SECONDS)
                        await self.close(connection, reason="idleTimeout")
                    connection.idle_task = asyncio.create_task(expire())

    async def _snapshot(self, connection: BrowserConnection, tab_id: str, *, settle: bool = False) -> tuple[str, BrowserActionResult]:
        result = await connection.client.request(
            "integration.browser.execute",
            {"browserId": str(connection.browser_id), "action": "inspect", "tabId": tab_id, "settle": settle, "guard": True},
            BrowserActionResult, timeout=30,
        )
        if result.tab_id != tab_id or result.data is None or "snapshotSha256" not in result.data:
            raise ExecutionClientError("browser.bindingChanged")
        if result.batch is not None or result.script is not None:
            raise ExecutionClientError("executionAgent.protocolError")
        try:
            inspection = BrowserInspectionData.model_validate(result.data)
        except ValidationError as exc:
            raise ExecutionClientError("executionAgent.protocolError") from exc
        digest = hashlib.sha256(f"{result.url}\n{inspection.snapshot_sha256}".encode()).hexdigest()
        return digest, result.model_copy(update={
            "data": {**result.data, "snapshotSha256": digest},
        })

    async def resize(self, connection: BrowserConnection, width: int, height: int) -> BrowserResizeResult:
        dimensions = BrowserResizeResult(width=width, height=height)
        async with connection.action_lock:
            await connection.check_access()
            if self.connections.get(connection.agent_id) is not connection or connection.websocket is None:
                raise ExecutionClientError("browser.notConnected")
            result = await connection.request(
                "integration.browser.resize", {"browserId": str(connection.browser_id), **dimensions.model_dump()},
                BrowserResizeResult, timeout=30,
            )
            if result != dimensions:
                raise ExecutionClientError("executionAgent.protocolError")
            return result

    async def navigate(self, connection: BrowserConnection, arguments: BrowserNavigationArguments,
                       *, completed: bool = False) -> BrowserNavigationResult:
        async with connection.action_lock:
            await connection.check_access()
            if self.connections.get(connection.agent_id) is not connection or connection.websocket is None:
                raise ExecutionClientError("browser.notConnected")
            if arguments.action != "state" and (
                connection.attached or connection.busy or not connection.manual_enabled
                or connection.view_socket is None
            ):
                raise ExecutionClientError("browser.busy")
            result = await connection.request(
                "integration.browser.navigation",
                {"browserId": str(connection.browser_id),
                 **arguments.model_dump(mode="json", exclude_unset=True)},
                BrowserNavigationResult, timeout=30,
            )
            if result != connection.navigation or completed:
                connection.navigation = result
                await connection.send({"type": "navigation", "browserId": str(connection.browser_id),
                                       "completed": completed, **result.model_dump(mode="json")})
            return result

    async def snapshot(self, session_id, agent_id, integration_id, tab_id) -> str:
        with _browser_latency("snapshot", session_id) as timings:
            connection = self.attached_for(session_id, agent_id)
            if connection is None or connection.integration_id != integration_id:
                raise ExecutionClientError("browser.notConnected")
            timings["browser_id"] = str(connection.browser_id)
            with ExitStack() as waiting:
                waiting.enter_context(_browser_timing(timings, "lock_wait_ms"))
                async with connection.action_lock:
                    waiting.close()
                    with _browser_timing(timings, "access_ms"):
                        await connection.check_access()
                    if self.attached_for(session_id, agent_id) is not connection:
                        raise ExecutionClientError("browser.bindingChanged")
                    with _browser_timing(timings, "reinspection_rpc_ms"):
                        digest, _ = await self._snapshot(connection, tab_id, settle=True)
                    return digest

    async def execute(self, session_id, agent_id, integration_id, arguments: dict[str, Any]) -> BrowserActionResult:
        with _browser_latency("execute", session_id) as timings:
            connection = self.attached_for(session_id, agent_id)
            if connection is None or connection.integration_id != integration_id:
                raise ExecutionClientError("browser.notConnected")
            timings["browser_id"] = str(connection.browser_id)
            payload = dict(arguments)
            expected_id = payload.pop("expectedBrowserId", None)
            expected_revision = payload.pop("expectedAttachmentRevision", None)
            expected_snapshot = payload.pop("expectedSnapshot", None)
            expected_run_id = payload.pop("expectedRunId", None)
            run_id = UUID(expected_run_id) if expected_run_id is not None else None
            action = BrowserActionArguments.model_validate(payload)
            timings["action"] = action.action
            with ExitStack() as waiting:
                waiting.enter_context(_browser_timing(timings, "lock_wait_ms"))
                async with connection.action_lock:
                    waiting.close()
                    with _browser_timing(timings, "access_ms"):
                        await connection.check_access()
                    def check_binding():
                        if (self.attached_for(session_id, agent_id) is not connection
                                or str(connection.browser_id) != expected_id
                                or connection.attachment_revision != expected_revision):
                            raise ExecutionClientError("browser.bindingChanged")
                    check_binding()
                    connection.busy = True
                    result = None
                    try:
                        await connection.state()
                        if run_id is not None:
                            controlled = await connection.request(
                                "integration.browser.control",
                                {"browserId": str(connection.browser_id), "manual": False,
                                 **connection.origin("model", run_id=run_id)},
                                BrowserControlResult,
                            )
                            if not controlled.ok:
                                raise ExecutionClientError("browser.actionFailed")
                        if expected_snapshot is not None:
                            try:
                                BrowserInspectionData(snapshotSha256=expected_snapshot)
                            except ValidationError as exc:
                                raise ExecutionClientError("browser.bindingChanged") from exc
                            with _browser_timing(timings, "reinspection_rpc_ms"):
                                digest, inspection = await self._snapshot(connection, action.tab_id)
                            check_binding()
                            if expected_snapshot != digest:
                                raise ExecutionClientError("browser.snapshotChanged")
                            if (
                                action.action == "inspect"
                                and action.selector is None
                                and action.depth is None
                                and BrowserInspectionData.model_validate(
                                    inspection.data
                                ).snapshot_characters
                                <= 32_768
                            ):
                                return inspection
                        check_binding()
                        read_action = action.action in {"tabs", "inspect", "screenshot", "diagnostics"}
                        wire = BrowserExecuteArguments(
                            browser_id=connection.browser_id,
                            settle=action.action == "inspect",
                            **action.model_dump(
                                by_alias=False, exclude={"observe"} if read_action else set()
                            ),
                        )
                        excluded = {"snapshot_sha256"} | ({"observe"} if read_action else set())
                        if action.action != "run_code":
                            excluded.add("code")
                        if action.action != "evaluate":
                            excluded.add("function")
                        if action.action != "fill":
                            excluded.update({"slowly", "submit"})
                        wire_payload = wire.model_dump(mode="json", exclude=excluded)
                        with _browser_timing(timings, "action_rpc_ms"):
                            if action.action == "screenshot":
                                result = await self._screenshot(connection, wire_payload, check_binding)
                            else:
                                result = await connection.client.request(
                                    "integration.browser.execute", wire_payload, BrowserActionResult,
                                    timeout=160 if action.action in {"run_code", "evaluate"} else 60,
                                )
                        if not result.matches_action(action):
                            raise ExecutionClientError("executionAgent.protocolError")
                        if result.snapshot_file is not None:
                            raise ExecutionClientError("executionAgent.protocolError")
                        if action.action == "inspect" and result.tab_id != action.tab_id:
                            raise ExecutionClientError("browser.bindingChanged")
                        if result.data is not None and "snapshotSha256" in result.data:
                            try:
                                observation = BrowserInspectionData.model_validate(result.data)
                            except ValidationError as exc:
                                raise ExecutionClientError("executionAgent.protocolError") from exc
                            digest = hashlib.sha256(
                                f"{result.url}\n{observation.snapshot_sha256}".encode()
                            ).hexdigest()
                            result = result.model_copy(update={
                                "data": {**result.data, "snapshotSha256": digest},
                            })
                        if result.snapshot is not None:
                            result._snapshot_stream = await self._read_browser_file(
                                connection, result.snapshot, check_binding
                            )
                    finally:
                        connection.busy = False
                        try:
                            with suppress(Exception):
                                await connection.state()
                        except BaseException:
                            if result is not None and result._snapshot_stream is not None:
                                result._snapshot_stream.close()
                            raise
                    if action.action == "screenshot":
                        await connection.check_access()
                        check_binding()
                    return result

    async def poll_downloads(self, connection: BrowserConnection) -> bool:
        listed = await connection.request(
            "integration.browser.files", {"browserId": str(connection.browser_id)}, BrowserFileListResult,
        )
        statuses = []
        previous = {item["id"]: item for item in connection.downloads}
        for download in listed.downloads:
            captured = connection.origins.get(download.origin or "")
            if captured is None or captured[1] != download.control_epoch:
                continue
            origin = captured[0]
            status = download.model_dump(mode="json")
            if download.status == "ready" and previous.get(download.id, {}).get("status") == "saved":
                statuses.append(previous[download.id])
                continue
            if download.status == "ready":
                home = await self._agent_home(connection)
                for index in range(DOWNLOAD_COLLISION_LIMIT):
                    destination = browser_download_path(home, download.filename, index)
                    decision = await self._download_decision(connection, origin, destination)
                    if decision == PermissionDecision.ASK:
                        status.update(
                            status="permission_required",
                            errorCode="browser.downloadPermissionRequired",
                            path=destination,
                        )
                        break
                    if decision != PermissionDecision.ALLOW:
                        status.update(status="failed", errorCode="permission.denied")
                        break
                    try:
                        published = await connection.request(
                            "integration.browser.publish",
                            {"browserId": str(connection.browser_id), "fileId": download.id,
                             "path": destination, "expectedOrigin": download.origin},
                            BrowserPublishedDownloadResult, timeout=60,
                        )
                        if (
                            published.path != destination or published.size != download.size
                            or published.sha256 != download.sha256
                        ):
                            raise ExecutionClientError("executionAgent.protocolError")
                        status.update(status="saved", path=published.path, sha256=published.sha256)
                        break
                    except ExecutionClientError as exc:
                        if exc.code != "file.pathAlreadyExists":
                            status.update(status="failed", errorCode=exc.code)
                            break
                else:
                    status.update(status="failed", errorCode="file.pathAlreadyExists")
            statuses.append(status)
        listed_ids = {download.id for download in listed.downloads}
        statuses.extend(
            item for item in connection.downloads
            if item.get("status") == "saved" and item.get("id") not in listed_ids
        )
        statuses = statuses[:32]
        changed = statuses != connection.downloads
        connection.downloads = statuses
        return changed

    async def _agent_home(self, connection: BrowserConnection) -> PurePosixPath:
        async with AsyncSessionFactory() as db:
            agent = await db.get(ExecutionAgent, connection.agent_id)
            if agent is None or agent.user_id != connection.owner_id or not agent.home_directory:
                raise ExecutionClientError("executionAgent.unavailable")
            return PurePosixPath(agent.home_directory)

    async def _download_decision(
        self, connection: BrowserConnection, origin: BrowserOrigin, destination: str,
    ) -> PermissionDecision:
        if origin.kind == "human":
            return PermissionDecision.ALLOW
        from mato_loop_backend.agent.runtime import AgentRuntime
        from mato_loop_backend.tools.registry import _model_revision

        async with AsyncSessionFactory() as db:
            session = await db.get(ChatSession, origin.session_id)
            run = await db.get(AgentRun, origin.run_id)
            agent = await db.get(ExecutionAgent, connection.agent_id)
            try:
                instance = await load_browser_integration(
                    db, origin.owner_id, origin.integration_id,
                )
            except ExecutionClientError:
                return PermissionDecision.DENY
            if (
                session is None or run is None or agent is None
                or session.user_id != origin.owner_id or run.session_id != session.id
                or run.execution_agent_id != connection.agent_id or agent.user_id != origin.owner_id
                or origin.integration_id != connection.integration_id
                or instance.execution_agent_id != connection.agent_id
                or not agent.home_directory
            ):
                return PermissionDecision.DENY
            operation = {
                "browserId": str(connection.browser_id),
                "originId": str(origin.id),
                "path": destination,
            }
            requests = (
                integration_operation_permission(
                    capability="browser.read",
                    integration_id=origin.integration_id,
                    binding_revision=_model_revision(instance),
                    operation_key=hashlib.sha256(
                        json.dumps(operation, sort_keys=True).encode()
                    ).hexdigest(),
                    title="Browser-Downloads lesen?",
                    summary="Der Agent möchte den Status eines Browser-Downloads lesen.",
                    resource_display=destination,
                    action_kind="browser_download_status",
                ),
                execution_directory_permission(
                    connection.agent_id, destination, capability="filesystem.write",
                    action_kind="browser_download", title="Browser-Download speichern?",
                    summary="Der Agent möchte einen Browser-Download auf dem ausgewählten Computer speichern.",
                ),
            )
            call = ToolCall(
                f"browser-download:{origin.id}", "browser_downloads", {"path": destination},
            )
            root_files = RootFileService(
                session.id, project_id=session.project_id, run_id=run.id,
            )
            decisions = [
                await AgentRuntime._authorize(
                    db, run, session, call, request, root_files, check_only=True,
                )
                for request in requests
            ]
            if any(decision in {PermissionDecision.DENY, PermissionDecision.USER_DENIED} for decision in decisions):
                return PermissionDecision.DENY
            return PermissionDecision.ASK if PermissionDecision.ASK in decisions else PermissionDecision.ALLOW

    async def _screenshot(self, connection: BrowserConnection, payload: dict, check_binding) -> BrowserActionResult:
        result = None
        pending = asyncio.create_task(connection.client.request(
            "integration.browser.execute", payload, BrowserActionResult, timeout=60,
        ))
        try:
            try:
                result = await asyncio.shield(pending)
            except asyncio.CancelledError:
                # Drain capture to recover its staging ID before releasing the shared action lock.
                with CancelScope(shield=True), suppress(Exception):
                    result = await pending
                raise
            if result.screenshot is None:
                raise ExecutionClientError("executionAgent.protocolError")
            with await self._read_browser_file(
                connection, result.screenshot, check_binding, delete=False
            ) as image:
                result._screenshot_bytes = image.read()
            return result
        finally:
            if result is not None and result.screenshot is not None:
                with CancelScope(shield=True), suppress(Exception):
                    wire = BrowserFileArguments(
                        browser_id=connection.browser_id, action="delete", file_id=result.screenshot.file_id,
                    )
                    await connection.request(
                        "integration.browser.file", wire.model_dump(mode="json"), BrowserControlResult, timeout=10,
                    )

    async def _read_browser_file(
        self, connection: BrowserConnection, descriptor, check_binding, *, delete: bool = True
    ):
        output = SpooledTemporaryFile(max_size=1024 * 1024, mode="w+b")
        try:
            async with asyncio.timeout(60 + descriptor.size / (256 * 1024)):
                offset = 0
                while offset < descriptor.size:
                    await connection.check_access()
                    check_binding()
                    wire = BrowserFileArguments(
                        browser_id=connection.browser_id,
                        action="read",
                        file_id=descriptor.file_id,
                        offset=offset,
                    )
                    chunk = await connection.request(
                        "integration.browser.file",
                        wire.model_dump(mode="json"),
                        BrowserFileResult,
                    )
                    try:
                        data = base64.b64decode(chunk.data or "", validate=True)
                    except (binascii.Error, ValueError) as exc:
                        raise ExecutionClientError("executionAgent.protocolError") from exc
                    if (
                        chunk.file_id != descriptor.file_id
                        or chunk.size != descriptor.size
                        or chunk.offset != offset + len(data)
                        or not data
                        or chunk.complete != (chunk.offset == chunk.size)
                    ):
                        raise ExecutionClientError("executionAgent.protocolError")
                    output.write(data)
                    offset = chunk.offset
                await connection.check_access()
                check_binding()
            output.seek(0)
            return output
        except BaseException:
            output.close()
            raise
        finally:
            if delete:
                with CancelScope(shield=True), suppress(Exception):
                    wire = BrowserFileArguments(
                        browser_id=connection.browser_id,
                        action="delete",
                        file_id=descriptor.file_id,
                    )
                    await connection.request(
                        "integration.browser.file",
                        wire.model_dump(mode="json"),
                        BrowserControlResult,
                        timeout=10,
                    )

    async def manual(self, owner_id, integration_id, session_id, browser_id,
                     view_id: UUID, login_id: UUID) -> BrowserConnection:
        connection = next((item for item in self.connections.values() if item.browser_id == browser_id), None)
        if (connection is None or connection.owner_id != owner_id
                or connection.integration_id != integration_id or connection.session_id != session_id
                or connection.view_id != view_id or connection.login_id != login_id):
            raise ExecutionClientError("browser.notConnected")
        await connection.check_manual()
        return connection

    async def viewer(self, owner_id, integration_id, session_id, browser_id,
                     view_id: UUID, login_id: UUID) -> BrowserConnection:
        connection = next((item for item in self.connections.values() if item.browser_id == browser_id), None)
        if (connection is None or connection.owner_id != owner_id
                or connection.integration_id != integration_id or connection.session_id != session_id
                or connection.view_id != view_id or connection.login_id != login_id
                or connection.closed or not connection.ready):
            raise ExecutionClientError("browser.notConnected")
        await connection.check_access()
        return connection

    async def shutdown(self) -> None:
        await asyncio.gather(*(self.close(item, reason="shutdown") for item in list(self.connections.values())))


browser_coordinator = BrowserCoordinator()
