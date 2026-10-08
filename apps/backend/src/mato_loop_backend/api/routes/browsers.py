from __future__ import annotations

import asyncio
import base64
import hmac
import logging
import os
from contextlib import suppress
from typing import Annotated, Literal
from urllib.parse import quote
from uuid import UUID

from anyio import CancelScope
from fastapi import APIRouter, Depends, Query, Request, UploadFile, WebSocket, WebSocketDisconnect
from pydantic import Field, TypeAdapter, ValidationError
from starlette.datastructures import UploadFile as StarletteUploadFile
from starlette.responses import StreamingResponse

from mato_loop_backend.api.browser_errors import browser_api_error
from mato_loop_backend.api.errors import ApiError
from mato_loop_backend.api.websocket_access import _browser_login, _origin_allowed
from mato_loop_backend.api.workspace_service import _normalize_agent_path
from mato_loop_backend.config import get_runtime_config
from mato_loop_backend.db.session import AsyncSessionFactory
from mato_loop_backend.error_contract import describe_error
from mato_loop_backend.execution.browser_contracts import (
    MAX_BROWSER_FILE_BYTES,
    BrowserClipboard,
    BrowserControlResult,
    BrowserFileArguments,
    BrowserFileResult,
    BrowserNavigationArguments,
    BrowserResizeResult,
    BrowserSetupReadResult,
    BrowserSetupResizeArguments,
    BrowserSetupWriteArguments,
    BrowserStatusResult,
)
from mato_loop_backend.execution.browser_control import (
    ControlRequestResponse,
    browser_control_requests,
)
from mato_loop_backend.execution.browsers import (
    ACCESS_INTERVAL,
    BrowserConnection,
    BrowserRelay,
    browser_coordinator,
    validate_browser_access,
)
from mato_loop_backend.execution.client import ExecutionClientError
from mato_loop_backend.execution.protocol import WireModel
from mato_loop_backend.files.service import FileRoot, RootFileError, RootFileService
from mato_loop_backend.integrations.browser.adapter import browser_client
from mato_loop_backend.models import ChatSession

router = APIRouter()
logger = logging.getLogger(__name__)
SessionId = Annotated[UUID, Query(alias="sessionId")]
BrowserId = Annotated[UUID, Query(alias="browserId")]
ViewId = Annotated[UUID, Query(alias="viewId")]
_setup_viewers: dict[UUID, WebSocket] = {}
_setup_starts: dict[UUID, asyncio.Task] = {}


class BrowserControl(WireModel):
    type: Literal["attach", "detach", "close", "release"]


class BrowserTakeover(WireModel):
    type: Literal["takeover"]
    browser_id: UUID = Field(strict=False)
    view_id: UUID = Field(strict=False)


class BrowserRequestedAttachment(WireModel):
    type: Literal["attach.requested"]
    request_id: UUID = Field(strict=False)


class BrowserResize(BrowserResizeResult):
    type: Literal["resize"]


class BrowserNavigation(BrowserNavigationArguments):
    type: Literal["navigation"]


class BrowserUploadSource(WireModel):
    scope: Literal["session", "filesystem"]
    path: str = Field(min_length=1, max_length=4096)


class BrowserUploadRequest(WireModel):
    source: BrowserUploadSource


_BROWSER_CONTROL = TypeAdapter(Annotated[BrowserControl | BrowserResize | BrowserNavigation | BrowserTakeover | BrowserRequestedAttachment, Field(discriminator="type")])


class SetupControl(WireModel):
    type: Literal["start", "status"]


class SetupInput(WireModel):
    type: Literal["input"]
    data: str = Field(max_length=87384, repr=False)


class SetupResize(WireModel):
    type: Literal["resize"]
    rows: int = Field(ge=1, le=1000)
    columns: int = Field(ge=1, le=1000)


_SETUP_CONTROL = TypeAdapter(Annotated[SetupControl | SetupInput | SetupResize, Field(discriminator="type")])


async def _login(websocket: WebSocket):
    if not _origin_allowed(websocket, get_runtime_config().cors_origin_list):
        await websocket.close(code=4403)
        return None
    login = await _browser_login(websocket)
    if login is None:
        await websocket.close(code=4401)
    return login


async def _error(websocket: WebSocket, exc: Exception, *, operation: str | None = None) -> None:
    code = exc.code if isinstance(exc, ExecutionClientError) else "browser.unavailable"
    if not isinstance(exc, (ExecutionClientError, WebSocketDisconnect, ValidationError)):
        logger.error("Browser transport failed: %s", type(exc).__name__)
    with suppress(Exception):
        async with asyncio.timeout(2):
            await websocket.send_json({"type": "error", "error": describe_error(code).payload(),
                                       **({"operation": operation} if operation else {})})


async def _receive_control(websocket: WebSocket, adapter):
    raw = await websocket.receive_text()
    if len(raw.encode()) > 100_000:
        raise ExecutionClientError("browser.invalidTarget")
    try:
        return adapter.validate_json(raw) if isinstance(adapter, TypeAdapter) else adapter.model_validate_json(raw)
    except ValidationError as exc:
        raise ExecutionClientError("browser.invalidTarget") from exc


async def _close_socket(websocket: WebSocket, code: int = 1000) -> None:
    with suppress(Exception):
        async with asyncio.timeout(1):
            await websocket.close(code=code)


@router.get("/browsers/control-requests", response_model=ControlRequestResponse)
async def browser_pending_control(request: Request, session_id: SessionId):
    login = await _browser_login(request)
    if login is None:
        raise ApiError(401, "auth.notAuthenticated")
    try:
        return ControlRequestResponse(request=await browser_control_requests.active(login.user.id, session_id))
    except ExecutionClientError as exc:
        raise browser_api_error(exc) from exc


@router.websocket("/browsers/{integration_id}")
async def browser_control(websocket: WebSocket, integration_id: UUID) -> None:
    connection = None
    tasks = []
    attachment_task = None
    resize_pending = None
    resize_changed = asyncio.Event()
    try:
        login = await _login(websocket)
        if login is None:
            return
        try:
            session_id = UUID(websocket.query_params.get("sessionId", ""))
        except ValueError as exc:
            raise ExecutionClientError("session.notFound") from exc
        try:
            control_request_id = UUID(websocket.query_params["controlRequestId"]) if "controlRequestId" in websocket.query_params else None
        except ValueError as exc:
            raise ExecutionClientError("browser.invalidTarget") from exc
        color_scheme = websocket.query_params.get("colorScheme", "light")
        if color_scheme not in {"light", "dark"}:
            raise ExecutionClientError("browser.invalidTarget")
        instance, _ = await validate_browser_access(login.user.id, integration_id, login_id=login.login_id, session_id=session_id)
        await websocket.accept()
        if control_request_id is not None:
            candidate = await browser_control_requests.claim(
                control_request_id, login.user.id, session_id, integration_id, login.login_id,
                websocket, color_scheme=color_scheme,
            )
        else:
            candidate = await browser_coordinator.open(
                login.user.id, integration_id, session_id, login.login_id, websocket,
                color_scheme=color_scheme,
            )
        last_offer = None
        while candidate.websocket is not websocket:
            offer = await browser_coordinator.availability(candidate)
            if offer != last_offer:
                await websocket.send_json(offer)
                last_offer = offer
            try:
                message = await asyncio.wait_for(_receive_control(websocket, _BROWSER_CONTROL), ACCESS_INTERVAL)
            except TimeoutError:
                await validate_browser_access(login.user.id, integration_id, login_id=login.login_id, session_id=session_id)
                candidate = browser_coordinator.connections.get(instance.execution_agent_id)
                if candidate is None:
                    raise ExecutionClientError("browser.notConnected") from None
                continue
            if isinstance(message, BrowserControl) and message.type in {"close", "release"}:
                return
            if not isinstance(message, BrowserTakeover):
                raise ExecutionClientError("browser.invalidTarget")
            candidate = await browser_coordinator.open(
                login.user.id, integration_id, session_id, login.login_id, websocket,
                expected_browser_id=message.browser_id, expected_view_id=message.view_id,
                color_scheme=color_scheme,
            )
        connection = candidate
        await connection.state()

        def control_pending():
            return control_request_id in browser_control_requests.requests or (
                attachment_task is not None and not attachment_task.done()
            )

        async def requested_attachment(request_id):
            try:
                await browser_control_requests.attach(request_id, connection, websocket)
            except Exception as exc:
                if not connection.closed:
                    await _error(websocket, exc, operation="control")

        async def controls():
            nonlocal resize_pending, attachment_task
            while not connection.closed and connection.websocket is websocket:
                message = await _receive_control(websocket, _BROWSER_CONTROL)
                if connection.closed:
                    return False
                if isinstance(message, (BrowserResize, BrowserNavigation)) and control_pending():
                    await _error(websocket, ExecutionClientError("browser.busy"),
                                 operation="resize" if isinstance(message, BrowserResize) else "navigation")
                    continue
                if isinstance(message, BrowserResize):
                    resize_pending = message
                    resize_changed.set()
                elif isinstance(message, BrowserNavigation):
                    try:
                        arguments = BrowserNavigationArguments.model_validate(
                            message.model_dump(exclude={"type"}, exclude_unset=True),
                        )
                        await browser_coordinator.navigate(connection, arguments, completed=True)
                    except ExecutionClientError as exc:
                        await connection.send({"type": "error", "operation": "navigation",
                                               "error": describe_error(exc.code).payload()})
                elif message.type == "close":
                    browser_control_requests.cancel_socket(websocket)
                    return True
                elif message.type == "release":
                    browser_control_requests.cancel_socket(websocket)
                    return False
                elif isinstance(message, BrowserTakeover):
                    raise ExecutionClientError("browser.invalidTarget")
                else:
                    try:
                        if isinstance(message, BrowserRequestedAttachment):
                            if message.request_id != control_request_id:
                                raise ExecutionClientError("browser.bindingChanged")
                            if attachment_task is not None and not attachment_task.done():
                                raise ExecutionClientError("browser.busy")
                            # Keep reading close/detach while the attachment RPC is in flight.
                            attachment_task = asyncio.create_task(requested_attachment(message.request_id))
                        else:
                            pending = control_request_id in browser_control_requests.requests
                            browser_control_requests.cancel_socket(websocket)
                            if pending and message.type == "attach":
                                raise ExecutionClientError("browser.bindingChanged")
                            await browser_coordinator.attachment(connection, message.type == "attach")
                    except ExecutionClientError as exc:
                        if not connection.closed:
                            await _error(websocket, exc, operation=(
                                "control" if isinstance(message, BrowserRequestedAttachment) else None
                            ))

        async def resizes():
            nonlocal resize_pending
            while True:
                await resize_changed.wait()
                # At most one dispatched resize and one newest pending geometry.
                resize_changed.clear()
                message, resize_pending = resize_pending, None
                try:
                    if control_pending():
                        raise ExecutionClientError("browser.busy")
                    result = await browser_coordinator.resize(connection, message.width, message.height)
                    await connection.send({"type": "resized", "browserId": str(connection.browser_id), **result.model_dump()})
                except ExecutionClientError as exc:
                    await connection.send({"type": "error", "operation": "resize", "error": describe_error(exc.code).payload()})

        tasks = [asyncio.create_task(controls()), asyncio.create_task(resizes())]
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            task.result()
        if tasks[0] in done and tasks[0].result() is True:
            await browser_coordinator.close(connection)
    except (WebSocketDisconnect, asyncio.CancelledError):
        pass
    except Exception as exc:
        if connection is None or not connection.closed:
            await _error(websocket, exc)
    finally:
        with CancelScope(shield=True):
            browser_control_requests.cancel_socket(websocket)
            if attachment_task is not None:
                tasks.append(attachment_task)
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            if connection is not None:
                await browser_coordinator.release(connection, websocket)
            current = browser_coordinator.connections.get(connection.agent_id) if connection is not None else None
            await _close_socket(websocket, 4001 if current is not None and current is not connection else 1000)


async def _pump_socket(websocket: WebSocket, incoming, outgoing, *, connection=None, relay=None) -> None:
    async def receive():
        while True:
            message = await websocket.receive()
            if message["type"] == "websocket.disconnect":
                return
            data = message.get("bytes")
            if not isinstance(data, bytes) or len(data) > 1024 * 1024:
                raise ExecutionClientError("browser.invalidTarget")
            if relay is not None and relay.connection.closed:
                continue
            # Backpressure keeps frame traffic off the command channel and bounds memory.
            if connection is None:
                async with asyncio.timeout(10):
                    await outgoing.put(data)
            else:
                if connection.closed:
                    continue
                async with asyncio.timeout(10):
                    await outgoing.put(data)

    async def send():
        while True:
            data = await incoming.get()
            if (connection is not None and connection.closed) or (relay is not None and relay.connection.closed):
                continue
            async with asyncio.timeout(10):
                await websocket.send_bytes(data)

    tasks = [asyncio.create_task(receive()), asyncio.create_task(send())]
    try:
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            task.result()
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


@router.websocket("/browsers/{integration_id}/view")
async def browser_view(websocket: WebSocket, integration_id: UUID) -> None:
    relay = connection = None
    tasks = []
    try:
        login = await _login(websocket)
        if login is None:
            return
        session_id = UUID(websocket.query_params.get("sessionId", ""))
        browser_id = UUID(websocket.query_params.get("browserId", ""))
        view_id = UUID(websocket.query_params.get("viewId", ""))
        connection = await browser_coordinator.viewer(
            login.user.id, integration_id, session_id, browser_id, view_id, login.login_id
        )
        if connection.login_id != login.login_id or connection.view_socket is not None:
            raise ExecutionClientError("browser.busy")
        connection.view_socket = websocket
        await websocket.accept()
        relay = BrowserRelay(connection)
        browser_coordinator.relays[relay.relay_id] = relay
        async with connection.action_lock:
            if connection.attached:
                await connection.check_access()
            else:
                await connection.check_manual()
            result = await connection.request(
                "integration.browser.view",
                {"browserId": str(browser_id), "relayId": str(relay.relay_id), "relayToken": relay.token},
                BrowserControlResult, timeout=20,
            )
            await connection.check_access()
        if not result.ok or not relay.accepted:
            raise ExecutionClientError("browser.notConnected")
        relay.ready = True
        tasks = [
            asyncio.create_task(_pump_socket(websocket, relay.incoming, relay.outgoing, connection=connection)),
            asyncio.create_task(relay.ended.wait()),
        ]
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            task.result()
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        # The RFB socket must contain only protocol bytes, never JSON errors.
        if connection is not None:
            with suppress(Exception):
                await connection.send({"type": "error", "error": describe_error(
                    exc.code if isinstance(exc, ExecutionClientError) else "browser.unavailable",
                ).payload()})
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if relay is not None:
            relay.ended.set()
            browser_coordinator.relays.pop(relay.relay_id, None)
        if connection is not None and not connection.closed and connection.view_socket is websocket:
            if not connection.attached and connection.websocket is not None:
                browser_control_requests.cancel_socket(connection.websocket)
            connection.view_socket = None
            if not connection.attached:
                connection.detach()
            if not connection.resetting_view:
                with suppress(Exception):
                    await connection.state()
        await _close_socket(websocket)


@router.websocket("/browser-relay/{relay_id}")
async def browser_relay(websocket: WebSocket, relay_id: UUID) -> None:
    relay = browser_coordinator.relays.get(relay_id)
    supplied = websocket.headers.get("authorization", "")
    if (relay is None or relay.accepted or relay.ended.is_set() or relay.connection.closed
            or not hmac.compare_digest(supplied, "Bearer " + relay.token)):
        await websocket.close(code=4403)
        return
    relay.accepted = True
    tasks = []
    try:
        await relay.connection.check_access()
        if relay.ended.is_set():
            raise ExecutionClientError("browser.notConnected")
        await websocket.accept()
        relay.connected = True
        tasks = [asyncio.create_task(_pump_socket(websocket, relay.outgoing, relay.incoming, relay=relay)),
                 asyncio.create_task(relay.ended.wait())]
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            task.result()
    except (WebSocketDisconnect, ExecutionClientError):
        pass
    finally:
        relay.ended.set()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await _close_socket(websocket)


@router.websocket("/browsers/{integration_id}/setup")
async def browser_setup(websocket: WebSocket, integration_id: UUID) -> None:
    agent_id = None
    tasks = []
    send_lock = asyncio.Lock()
    setup_id = None
    setup_changed = asyncio.Event()

    async def send(payload):
        async with send_lock, asyncio.timeout(5):
            if isinstance(payload, bytes):
                await websocket.send_bytes(payload)
            else:
                await websocket.send_json(payload)

    try:
        login = await _login(websocket)
        if login is None:
            return
        instance, _ = await validate_browser_access(
            login.user.id, integration_id, login_id=login.login_id, require_enabled=False,
        )
        agent_id = instance.execution_agent_id
        if agent_id in _setup_viewers:
            raise ExecutionClientError("browser.busy")
        _setup_viewers[agent_id] = websocket
        client = browser_client(agent_id)
        await websocket.accept()

        async def check():
            current, _ = await validate_browser_access(
                login.user.id, integration_id, login_id=login.login_id, require_enabled=False,
            )
            if current.execution_agent_id != agent_id:
                raise ExecutionClientError("browser.bindingChanged")

        async def status(start=False):
            nonlocal setup_id
            await check()
            if start:
                task = _setup_starts.get(agent_id)
                if task is None:
                    task = asyncio.create_task(client.request(
                        "integration.browser.setup", {"ownerId": str(login.user.id)},
                        BrowserStatusResult, timeout=60,
                    ))
                    _setup_starts[agent_id] = task

                    def finished(task):
                        if _setup_starts.get(agent_id) is task:
                            _setup_starts.pop(agent_id)
                        if not task.cancelled():
                            task.exception()

                    task.add_done_callback(finished)
                # Viewer lifetime must not cancel a dispatched package-install start.
                result = await asyncio.shield(task)
            else:
                result = await client.request("integration.browser.status", {}, BrowserStatusResult, timeout=60)
            setup_id = result.setup_id
            if setup_id is not None:
                setup_changed.set()
            await send({"type": "state", **result.model_dump(mode="json")})

        async def controls():
            while True:
                message = await _receive_control(websocket, _SETUP_CONTROL)
                await check()
                if isinstance(message, SetupControl):
                    await status(message.type == "start")
                elif setup_id is not None:
                    if isinstance(message, SetupInput):
                        arguments = BrowserSetupWriteArguments(setup_id=setup_id, data=message.data)
                        operation = "setupwrite"
                    else:
                        arguments = BrowserSetupResizeArguments(setup_id=setup_id, rows=message.rows, columns=message.columns)
                        operation = "setupresize"
                    result = await client.request(
                        f"integration.browser.{operation}", arguments.model_dump(mode="json"), BrowserControlResult,
                    )
                    if not result.ok:
                        raise ExecutionClientError("browser.setupFailed")

        async def output():
            previous_id, offset, reported_exit = None, 0, False
            while True:
                await setup_changed.wait()
                if previous_id != setup_id:
                    previous_id, offset, reported_exit = setup_id, 0, False
                result = await client.request(
                    "integration.browser.setupread",
                    {"setupId": str(previous_id), "offset": offset, "maxBytes": 65536, "waitSeconds": 2},
                    BrowserSetupReadResult, timeout=10,
                )
                data = base64.b64decode(result.data, validate=True)
                if result.offset < offset or result.offset - offset < len(data):
                    raise ExecutionClientError("executionAgent.protocolError")
                offset = result.offset
                if data:
                    await send(data)
                if not result.running and not data:
                    if not reported_exit:
                        await send({"type": "exit", "exitCode": result.exit_code})
                        reported_exit = True
                    setup_changed.clear()
                elif not data:
                    await asyncio.sleep(0.05)

        async def watch():
            while True:
                await asyncio.sleep(ACCESS_INTERVAL)
                await check()

        tasks = [asyncio.create_task(controls()), asyncio.create_task(output()), asyncio.create_task(watch())]
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            task.result()
    except (WebSocketDisconnect, asyncio.CancelledError):
        pass
    except Exception as exc:
        await _error(websocket, exc)
    finally:
        with CancelScope(shield=True):
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            if agent_id is not None and _setup_viewers.get(agent_id) is websocket:
                _setup_viewers.pop(agent_id)
            # Closing a viewer must never cancel a package-manager transaction.
            await _close_socket(websocket)


async def _file_request(connection, **arguments):
    wire = BrowserFileArguments(browser_id=connection.browser_id, **arguments)
    result = await connection.request(
        "integration.browser.file", wire.model_dump(mode="json"),
        BrowserControlResult if arguments["action"] in {"delete", "choose"} else BrowserFileResult,
    )
    if isinstance(result, BrowserFileResult) and arguments.get("file_id", result.file_id) != result.file_id:
        raise ExecutionClientError("executionAgent.protocolError")
    if isinstance(result, BrowserControlResult) and not result.ok:
        raise ExecutionClientError("browser.actionFailed")
    return result


async def _manual_browser(request: Request, integration_id: UUID, session_id: SessionId,
                          browser_id: BrowserId, view_id: ViewId) -> BrowserConnection:
    login = await _browser_login(request)
    if login is None:
        raise ApiError(401, "auth.notAuthenticated")
    try:
        return await browser_coordinator.manual(
            login.user.id, integration_id, session_id, browser_id, view_id, login.login_id,
        )
    except ExecutionClientError as exc:
        raise browser_api_error(exc) from exc


ManualBrowser = Annotated[BrowserConnection, Depends(_manual_browser)]


@router.get("/browsers/{integration_id}/clipboard")
async def read_browser_clipboard(connection: ManualBrowser):
    return await _clipboard(connection)


@router.put("/browsers/{integration_id}/clipboard")
async def write_browser_clipboard(connection: ManualBrowser, payload: BrowserClipboard):
    return await _clipboard(connection, payload.text)


@router.post("/browsers/{integration_id}/clipboard/copy")
async def copy_browser_clipboard(connection: ManualBrowser):
    return await _clipboard(connection, action="copy")


async def _clipboard(connection, text=None, action=None):
    try:
        async with connection.action_lock:
            await connection.check_manual()
            arguments = {"browserId": str(connection.browser_id), "action": action or ("read" if text is None else "write")}
            if text is not None:
                arguments["text"] = text
            result = await connection.request("integration.browser.clipboard", arguments, BrowserClipboard)
            await connection.check_manual()
            return result
    except ExecutionClientError as exc:
        raise browser_api_error(exc) from exc


@router.get("/browsers/{integration_id}/downloads")
async def browser_downloads(connection: ManualBrowser):
    try:
        async with connection.action_lock:
            await connection.check_manual()
            await browser_coordinator.poll_downloads(connection)
            await connection.check_manual()
            return {"downloads": connection.downloads}
    except ExecutionClientError as exc:
        raise browser_api_error(exc) from exc


@router.post("/browsers/{integration_id}/upload")
async def browser_upload(connection: ManualBrowser, request: Request):
    file = request if isinstance(request, UploadFile) else None
    try:
        if file is None and request.headers.get("content-type", "").split(";", 1)[0].strip().lower() == "multipart/form-data":
            candidate = (await request.form()).get("file")
            if not isinstance(candidate, StarletteUploadFile):
                raise ApiError(422, "validation.invalid")
            file = candidate
        if file is not None:
            return await _upload_stream(connection, file, file.filename or "upload", file.size)
        try:
            payload = BrowserUploadRequest.model_validate(await request.json())
        except (ValidationError, ValueError) as exc:
            raise ApiError(422, "validation.invalid") from exc
        return await _browser_upload_source(connection, payload)
    except ValidationError as exc:
        raise ApiError(400, "browser.invalidTarget") from exc
    except ExecutionClientError as exc:
        raise browser_api_error(exc) from exc
    finally:
        if file is not None:
            await file.close()


@router.post("/browsers/{integration_id}/upload-source")
async def browser_upload_source(connection: ManualBrowser, payload: BrowserUploadRequest):
    try:
        return await _browser_upload_source(connection, payload)
    except ValidationError as exc:
        raise ApiError(400, "browser.invalidTarget") from exc
    except ExecutionClientError as exc:
        raise browser_api_error(exc) from exc


async def _browser_upload_source(connection: BrowserConnection, payload: BrowserUploadRequest):
    if payload.source.scope == "filesystem":
        path = _normalize_agent_path(payload.source.path)
        async with connection.action_lock:
            await connection.check_manual()
            result = await connection.request(
                "integration.browser.upload",
                {"browserId": str(connection.browser_id), "path": path},
                BrowserFileResult,
            )
            if not result.complete or result.offset != result.size:
                raise ExecutionClientError("executionAgent.protocolError")
            await connection.check_manual()
            await _file_request(connection, action="choose", file_id=result.file_id)
            await connection.check_manual()
        return {"uploadId": result.file_id, "chosen": True, "size": result.size}
    async with AsyncSessionFactory() as db:
        session = await db.get(ChatSession, connection.session_id)
        if session is None or session.user_id != connection.owner_id:
            raise ExecutionClientError("session.notFound")
        root_files = RootFileService(session.id, project_id=session.project_id)
    try:
        handle = await asyncio.to_thread(root_files.open_file, FileRoot.SESSION, payload.source.path)
    except RootFileError as exc:
        raise ApiError(404 if exc.code.endswith("NotFound") else 400, exc.code) from exc
    with handle:
        size = os.fstat(handle.fileno()).st_size
        return await _upload_stream(
            connection, handle, payload.source.path.rsplit("/", 1)[-1], size,
        )


async def _upload_stream(connection: BrowserConnection, file, filename: str, size: int | None):
    file_id = None
    completed = False
    if size is None or not 0 <= size <= MAX_BROWSER_FILE_BYTES:
        raise ExecutionClientError("browser.fileTooLarge")
    async with connection.action_lock:
        try:
            await connection.check_manual()
            beginning = asyncio.create_task(_file_request(
                connection, action="begin", filename=filename, size=size,
            ))
            try:
                result = await asyncio.shield(beginning)
            except asyncio.CancelledError:
                with CancelScope(shield=True), suppress(Exception):
                    file_id = (await beginning).file_id
                raise
            file_id = result.file_id
            if result.offset != 0 or result.size != size or result.complete:
                raise ExecutionClientError("executionAgent.protocolError")
            offset = 0
            while chunk := (
                await file.read(65536)
                if isinstance(file, StarletteUploadFile)
                else await asyncio.to_thread(file.read, 65536)
            ):
                await connection.check_manual()
                result = await _file_request(connection, action="write", file_id=file_id, offset=offset,
                                             data=base64.b64encode(chunk).decode("ascii"))
                offset += len(chunk)
                if result.offset != offset or result.size != size or result.complete:
                    raise ExecutionClientError("executionAgent.protocolError")
            await connection.check_manual()
            result = await _file_request(connection, action="finish", file_id=file_id)
            if not result.complete or result.size != size or result.offset != size:
                raise ExecutionClientError("executionAgent.protocolError")
            completed = True
            chosen = False
            try:
                await connection.check_manual()
                await _file_request(connection, action="choose", file_id=file_id)
                chosen = True
            except ExecutionClientError as exc:
                if exc.code != "browser.invalidTarget":
                    raise
            await connection.check_manual()
            return {"uploadId": file_id, "chosen": chosen}
        finally:
            if file_id is not None and not completed:
                with CancelScope(shield=True), suppress(Exception):
                    await _file_request(connection, action="delete", file_id=file_id)


@router.post("/browsers/{integration_id}/upload/{upload_id}/choose")
async def browser_choose(upload_id: UUID, connection: ManualBrowser):
    try:
        async with connection.action_lock:
            await connection.check_manual()
            result = await _file_request(connection, action="choose", file_id=str(upload_id))
            await connection.check_manual()
            return result
    except ExecutionClientError as exc:
        raise browser_api_error(exc) from exc


@router.get("/browsers/{integration_id}/download/{download_id}")
async def browser_download(download_id: UUID, connection: ManualBrowser):
    try:
        async with connection.action_lock:
            await connection.check_manual()
            first = await _file_request(connection, action="read", file_id=str(download_id))
            await connection.check_manual()
        if first.filename is None or first.data is None:
            raise ExecutionClientError("executionAgent.protocolError")
        first_data = base64.b64decode(first.data, validate=True)
        if first.offset != len(first_data):
            raise ExecutionClientError("executionAgent.protocolError")
    except ExecutionClientError as exc:
        raise browser_api_error(exc) from exc

    async def chunks():
        result = first
        offset = 0
        while True:
            data = base64.b64decode(result.data or "", validate=True)
            if (result.size != first.size or result.filename != first.filename
                    or result.offset != offset + len(data) or (not data and not result.complete)):
                raise ExecutionClientError("executionAgent.protocolError")
            await connection.check_manual()
            yield data
            if result.complete:
                break
            offset = result.offset
            async with connection.action_lock:
                await connection.check_manual()
                result = await _file_request(connection, action="read", file_id=str(download_id), offset=offset)
                await connection.check_manual()

    return StreamingResponse(chunks(), media_type="application/octet-stream", headers={
        "Content-Disposition": f"attachment; filename*=UTF-8''{quote(first.filename, safe='')}",
        "Content-Length": str(first.size), "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
    })
