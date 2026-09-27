from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass, field
from time import monotonic
from typing import TYPE_CHECKING
from uuid import UUID, uuid4
from weakref import WeakValueDictionary

from anyio import CancelScope
from fastapi import WebSocket

from mato_loop_backend.db.session import AsyncSessionFactory
from mato_loop_backend.execution.browsers import (
    BrowserConnection,
    browser_coordinator,
)
from mato_loop_backend.execution.client import ExecutionClientError
from mato_loop_backend.execution.manager import execution_agent_connections
from mato_loop_backend.execution.protocol import WireModel
from mato_loop_backend.files.service import RootFileService
from mato_loop_backend.integrations.browser.adapter import load_browser_integration
from mato_loop_backend.models import AgentRun, ChatSession, UserAccount
from mato_loop_backend.permissions.policy import (
    PermissionDecision,
    PermissionRequest,
    integration_operation_permission,
)
from mato_loop_backend.providers.base import ToolCall
from mato_loop_backend.settings import TrustLevel
from mato_loop_backend.settings.effective import AgentProfileUnavailable, load_agent_profile_tools

if TYPE_CHECKING:
    from mato_loop_backend.tools.context import ToolContext

CONTROL_TIMEOUT = 90


class ControlRequestInfo(WireModel):
    id: UUID
    session_id: UUID
    integration_id: UUID
    agent_id: UUID
    run_id: UUID


class ControlRequestResponse(WireModel):
    request: ControlRequestInfo | None = None


def _binding(connection: BrowserConnection | None) -> tuple:
    return () if connection is None else (
        connection.browser_id, connection.view_id, connection.attachment_revision,
        connection.generation, connection.closed,
    )


@dataclass(slots=True, weakref_slot=True)
class ControlRequest:
    info: ControlRequestInfo
    owner_id: UUID
    revision: str
    generation: int
    expected: tuple
    permission: PermissionRequest
    call: ToolCall
    root_files: RootFileService
    profile_id: UUID | None
    session_profile_id: UUID | None
    trust: str
    proof: dict[str, str] = field(default_factory=dict)
    socket: WebSocket | None = None
    connection: BrowserConnection | None = None
    task: asyncio.Task | None = None
    deadline: float | None = None
    done: asyncio.Event = field(default_factory=asyncio.Event)
    error: str | None = None

    def check_binding(self) -> None:
        if self.done.is_set() or (self.task is not None and (self.task.done() or self.task.cancelling())):
            raise ExecutionClientError(self.error or "browser.bindingChanged")
        if self.deadline is not None and monotonic() >= self.deadline:
            raise ExecutionClientError("browser.controlTimeout")
        current = browser_coordinator.connections.get(self.info.agent_id)
        if (execution_agent_connections.generation(self.info.agent_id) != self.generation
                or _binding(current) != self.expected):
            raise ExecutionClientError("browser.bindingChanged")

    async def validate(self, *, authorized: bool = True) -> None:
        from mato_loop_backend.agent.runtime import AgentRuntime
        from mato_loop_backend.tools.registry import _model_revision, _tool_available

        self.check_binding()
        async with asyncio.timeout(5), AsyncSessionFactory() as db:
            user = await db.get(UserAccount, self.owner_id)
            session = await db.get(ChatSession, self.info.session_id)
            run = await db.get(AgentRun, self.info.run_id)
            if (user is None or not user.is_active or session is None
                    or session.user_id != self.owner_id or session.session_type != "chat"
                    or session.parent_session_id is not None or run is None
                    or run.session_id != session.id or run.parent_run_id is not None
                    or run.status != "running" or run.completed_at is not None):
                raise ExecutionClientError("browser.notConnected")
            if (session.preferred_execution_agent_id != self.info.agent_id
                    or run.execution_agent_id != self.info.agent_id
                    or (run.agent_profile_id or session.agent_profile_id) != self.profile_id
                    or session.agent_profile_id != self.session_profile_id
                    or run.trust_level != self.trust
                    or "integration.browser.execute" not in (run.execution_operations or [])
                    or "integration.browser.execute" not in (execution_agent_connections.snapshot(self.info.agent_id) or ())):
                raise ExecutionClientError("browser.bindingChanged")
            instance = await load_browser_integration(db, self.owner_id, self.info.integration_id)
            if instance.execution_agent_id != self.info.agent_id or _model_revision(instance) != self.revision:
                raise ExecutionClientError("browser.bindingChanged")
            try:
                tools, profile = await load_agent_profile_tools(db, self.owner_id, self.profile_id, TrustLevel(self.trust))
            except AgentProfileUnavailable as exc:
                raise ExecutionClientError("tool.unavailable") from exc
            if (not _tool_available("browser_request_control", tools)
                    or not profile.integration_availability.get(str(instance.id), profile.integrations_enabled_by_default)):
                raise ExecutionClientError("tool.unavailable")
            if authorized and await AgentRuntime._authorize(
                db, run, session, self.call, self.permission, self.root_files,
                approval_resolution=self.proof, check_only=True,
            ) != PermissionDecision.ALLOW:
                raise ExecutionClientError("permission.denied")
        self.check_binding()

    def bind(self, connection: BrowserConnection) -> None:
        self.connection = connection
        self.expected = _binding(connection)

    def binding_ready(self) -> None:
        self.check_binding()
        connection = self.connection
        if connection is None or not connection.ready:
            raise ExecutionClientError("browser.notConnected")


async def capture_control_request(reason: str | None, context: ToolContext) -> ControlRequest:
    client = context.execution_client
    if client is None:
        raise ExecutionClientError("tool.unavailable")
    generation = execution_agent_connections.generation(client.agent_id)
    if generation is None:
        raise ExecutionClientError("executionAgent.unavailable")
    integration_id = UUID(context.bound_options["integration_id"])
    current = browser_coordinator.connections.get(client.agent_id)
    if current is not None and (current.owner_id != context.session.user_id
                               or current.integration_id != integration_id or current.closed or not current.ready):
        raise ExecutionClientError("browser.busy")
    info = ControlRequestInfo(id=uuid4(), session_id=context.session.id, integration_id=integration_id,
                              agent_id=client.agent_id, run_id=context.run_id)
    operation = {
        "reason": reason, "sessionId": str(info.session_id), "generation": generation,
        "browserId": str(current.browser_id) if current else None,
        "viewId": str(current.view_id) if current else None,
        "attachmentRevision": current.attachment_revision if current else None,
    }
    permission = integration_operation_permission(
        capability="browser.control", integration_id=integration_id,
        binding_revision=context.bound_options["binding_revision"],
        operation_key=hashlib.sha256(json.dumps(operation, sort_keys=True).encode()).hexdigest(),
        grant_resource_key=(
            f"browser:{current.browser_id}:attachment:{current.attachment_revision}"
            if current is not None else f"control:{client.agent_id}:{generation}"
        ),
        title="Browser-Steuerung erlauben?",
        summary="Der Agent möchte den Browser in diesem Chat öffnen, gegebenenfalls übernehmen und die Modellsteuerung aktivieren.",
        resource_display="Freigegebener Browser (alle Tabs)", action_kind="browser_control",
    )
    request = ControlRequest(
        info, context.session.user_id, context.bound_options["binding_revision"], generation,
        _binding(current),
        permission, context.tool_call, context.root_files,
        context.agent_profile_id or context.session.agent_profile_id, context.session.agent_profile_id,
        context.model_trust_level.value,
    )
    # Observe opens even during approval, without retaining finished tool contexts.
    browser_control_requests.preflights[info.id] = request
    await request.validate(authorized=False)
    return request


class BrowserControlRequests:
    def __init__(self) -> None:
        self.requests: dict[UUID, ControlRequest] = {}
        self.preflights: WeakValueDictionary[UUID, ControlRequest] = WeakValueDictionary()

    def finish(self, request: ControlRequest, error: str | None = None) -> None:
        if not request.done.is_set():
            request.error = error
            request.done.set()
            self.requests.pop(request.info.id, None)
            self.preflights.pop(request.info.id, None)

    def invalidate_agent(self, agent_id: UUID, current: ControlRequest | None = None) -> None:
        for request in list(self.preflights.values()):
            if request.info.agent_id == agent_id and request is not current:
                self.finish(request, "browser.bindingChanged")

    async def wait(self, request: ControlRequest, context: ToolContext):
        from mato_loop_backend.tools.contracts import ToolResult

        request.proof = dict(context.approval_resolution)
        request.task = asyncio.current_task()
        request.deadline = monotonic() + CONTROL_TIMEOUT
        await context.db.refresh(context.session, with_for_update=True)
        control_center = dict(context.session.control_center or {})
        tabs = list(control_center.get("tabs", []))
        if not any(
            tab.get("app_id") == "browser" and tab.get("kind", "app") == "app"
            for tab in tabs
        ):
            if len(tabs) >= 21:
                raise ExecutionClientError("browser.busy")
            control_center["tabs"] = [*tabs, {"app_id": "browser", "pinned": False}]
            context.session.control_center = control_center
        # Persist the app before publishing; the request may attach without a mounted viewer.
        await context.db.commit()
        await request.validate()
        if any(item.info.agent_id == request.info.agent_id for item in self.requests.values()):
            raise ExecutionClientError("browser.busy")
        self.requests[request.info.id] = request
        try:
            current = browser_coordinator.connections.get(request.info.agent_id)

            async def bind() -> None:
                try:
                    connection = await browser_coordinator.open(
                        request.owner_id,
                        request.info.integration_id,
                        request.info.session_id,
                        None,
                        None,
                        expected_browser_id=current.browser_id if current else None,
                        expected_view_id=current.view_id if current else None,
                        control_request=request,
                    )
                    await browser_coordinator.attachment(
                        connection, True, control_request=request,
                    )
                except BaseException:
                    if request.connection is not None:
                        await browser_coordinator.close(request.connection)
                    raise

            binding = asyncio.create_task(bind())
            try:
                async with asyncio.timeout(max(0, request.deadline - monotonic())):
                    await asyncio.shield(binding)
            except TimeoutError:
                binding.cancel()
                with CancelScope(shield=True):
                    await asyncio.gather(binding, return_exceptions=True)
                    if request.connection is not None:
                        await browser_coordinator.close(request.connection)
                raise ExecutionClientError("browser.controlTimeout") from None
            except asyncio.CancelledError:
                with CancelScope(shield=True):
                    await asyncio.gather(binding, return_exceptions=True)
                    if request.connection is not None:
                        await browser_coordinator.close(request.connection)
                raise
            return ToolResult.json({"ok": True})
        finally:
            self.finish(request, "browser.notConnected")

    async def active(self, owner_id: UUID, session_id: UUID) -> ControlRequestInfo | None:
        async with AsyncSessionFactory() as db:
            session = await db.get(ChatSession, session_id)
            if (session is None or session.user_id != owner_id or session.session_type != "chat"
                    or session.parent_session_id is not None):
                raise ExecutionClientError("session.notFound")
        # Existing frontends still reconcile this endpoint, but binding is backend-owned.
        return None

    async def claim(self, request_id: UUID, owner_id: UUID, session_id: UUID,
                    integration_id: UUID, login_id: UUID, socket: WebSocket,
                    *, color_scheme: str = "light") -> BrowserConnection:
        request = self.requests.get(request_id)
        if (request is None or request.owner_id != owner_id or request.info.session_id != session_id
                or request.info.integration_id != integration_id or request.socket is not None):
            raise ExecutionClientError("browser.bindingChanged")
        # Claim without an await so two controllers can never consume the same authority.
        request.socket = socket
        try:
            await request.validate()
            current = browser_coordinator.connections.get(request.info.agent_id)
            return await browser_coordinator.open(
                owner_id, integration_id, session_id, login_id, socket,
                expected_browser_id=current.browser_id if current else None,
                expected_view_id=current.view_id if current else None,
                control_request=request,
                color_scheme=color_scheme,
            )
        except BaseException:
            self.finish(request, "browser.bindingChanged")
            raise

    async def attach(self, request_id: UUID, connection: BrowserConnection, socket: WebSocket) -> None:
        request = self.requests.get(request_id)
        if request is None or request.socket is not socket or request.connection is not connection:
            raise ExecutionClientError("browser.bindingChanged")
        try:
            await browser_coordinator.attachment(connection, True, control_request=request)
        except BaseException:
            self.finish(request, "browser.bindingChanged")
            raise

    def cancel_socket(self, socket: WebSocket) -> None:
        for request in list(self.requests.values()):
            if request.socket is socket:
                self.finish(request, "browser.notConnected")

    def cancel_session(self, owner_id: UUID, session_id: UUID) -> None:
        for request in list(self.requests.values()):
            if request.owner_id == owner_id and request.info.session_id == session_id:
                self.finish(request, "browser.notConnected")


browser_control_requests = BrowserControlRequests()
