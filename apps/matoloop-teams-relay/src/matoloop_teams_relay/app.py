from __future__ import annotations

import asyncio
import hmac
import logging
import sqlite3
import time
from collections import deque
from contextlib import asynccontextmanager
from typing import Annotated
from uuid import UUID

from fastapi import Depends, FastAPI, Header, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from matoloop_teams_protocol import (
    MAX_BODY_BYTES,
    MAX_FILE_BYTES,
    Delivery,
    PollResult,
    Receipt,
    RelayStatus,
    StreamPreview,
)
from microsoft_teams.apps import App, FastAPIAdapter

from .config import RelayConfig
from .spool import Spool, SpoolError
from .transport import TeamsTransport
from .welcome_queue import WelcomeQueue

logger = logging.getLogger(__name__)


def create_app(config: RelayConfig | None = None) -> FastAPI:
    config = config or RelayConfig()
    client_secret, relay_token = config.secrets()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        spool = Spool(
            config.spool_dir, max_bytes=config.max_spool_bytes, max_records=config.max_records
        )
        app.state.spool = spool
        bot = App(
            client_id=str(config.app_id),
            tenant_id=str(config.tenant_id),
            client_secret=client_secret.get_secret_value(),
            http_server_adapter=FastAPIAdapter(app=app),
            dangerously_allow_unauthenticated_requests=False,
            fetch_user_token=False,
        )
        try:
            directory = spool.root / "onboarding"
            directory.mkdir(exist_ok=True, mode=0o700)
            welcomes = WelcomeQueue(directory / "welcomes.sqlite3")
        except (OSError, sqlite3.Error):
            spool.close()
            raise
        transport = TeamsTransport(bot, spool, config, welcomes)
        app.state.transport = transport

        async def cleanup():
            while True:
                await asyncio.sleep(30)
                try:
                    spool.cleanup()
                    await transport.expire_handoffs()
                except (SpoolError, OSError):
                    logger.error(
                        "Relay maintenance failed; retrying on the next interval", exc_info=False
                    )

        cleanup_task = asyncio.create_task(cleanup())
        welcome_task = None
        try:
            await bot.initialize()
            welcome_task = asyncio.create_task(transport.run_welcomes(welcomes))
            if not config.pairing_origin:
                logger.warning("MATO_TEAMS_MATOLOOP_URL is unset; Teams pairing cards will have no button")
            yield
        finally:
            cleanup_task.cancel()
            if welcome_task is not None:
                welcome_task.cancel()
                await asyncio.gather(welcome_task, return_exceptions=True)
            await asyncio.gather(cleanup_task, return_exceptions=True)
            await transport.close()
            welcomes.close()
            spool.close()

    app = FastAPI(
        title="MatoLoop Teams relay",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    callback_slots, backend_slots = asyncio.Semaphore(8), asyncio.Semaphore(8)
    callback_arrivals: deque[float] = deque()
    backend_arrivals: deque[float] = deque()

    @app.exception_handler(SpoolError)
    async def spool_error(_request: Request, error: SpoolError):
        return JSONResponse(
            {"error": {"code": error.code, "message": "The relay request failed."}},
            status_code=error.status,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(_request: Request, _error: RequestValidationError):
        return JSONResponse(
            {
                "error": {
                    "code": "validation.invalid",
                    "message": "The request contains invalid data.",
                }
            },
            status_code=422,
        )

    @app.middleware("http")
    async def body_limit(request: Request, call_next):
        backend_request = request.url.path.startswith("/relay/")
        if backend_request:
            expected = "Bearer " + relay_token.get_secret_value()
            supplied = request.headers.get("authorization", "")
            if not hmac.compare_digest(supplied.encode(), expected.encode()):
                return JSONResponse(
                    {
                        "error": {
                            "code": "relay.authenticationFailed",
                            "message": "Authentication failed.",
                        }
                    },
                    status_code=401,
                )
        slots = backend_slots if backend_request else callback_slots
        arrivals = backend_arrivals if backend_request else callback_arrivals
        now = time.monotonic()
        while arrivals and arrivals[0] <= now - 1:
            arrivals.popleft()
        if len(arrivals) >= 100 or slots.locked():
            return JSONResponse(
                {"error": {"code": "relay.busy", "message": "Try again later."}},
                status_code=429,
                headers={"Retry-After": "1"},
            )
        arrivals.append(now)
        async with slots:
            return await limited_body(request, call_next)

    async def limited_body(request: Request, call_next):
        limit = (
            MAX_FILE_BYTES if request.url.path.startswith("/relay/v1/blobs/") else MAX_BODY_BYTES
        )
        raw_length = request.headers.get("content-length")
        if raw_length and (not raw_length.isdecimal() or int(raw_length) > limit):
            return JSONResponse(
                {"error": {"code": "attachment.tooLarge", "message": "The request is too large."}},
                status_code=413,
            )
        # Check streaming/chunked bodies before the SDK or JSON parser buffers them.
        if request.method in {"POST", "PUT"}:
            size = 0
            chunks = []
            try:
                async with asyncio.timeout(30):
                    async for chunk in request.stream():
                        size += len(chunk)
                        if size > limit:
                            return JSONResponse(
                                {
                                    "error": {
                                        "code": "attachment.tooLarge",
                                        "message": "The request is too large.",
                                    }
                                },
                                status_code=413,
                            )
                        chunks.append(chunk)
            except TimeoutError:
                return JSONResponse(
                    {"error": {"code": "relay.timeout", "message": "The request timed out."}},
                    status_code=408,
                )
            request._body = b"".join(chunks)
        return await call_next(request)

    async def authenticate(authorization: Annotated[str | None, Header()] = None) -> None:
        expected = "Bearer " + relay_token.get_secret_value()
        if authorization is None or not hmac.compare_digest(
            authorization.encode(), expected.encode()
        ):
            raise SpoolError("relay.authenticationFailed", 401)

    protected = [Depends(authenticate)]

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    @app.get("/relay/v1/status", dependencies=protected, response_model_exclude_none=True)
    async def status(include_pairing_origin: bool = False) -> RelayStatus:
        return RelayStatus(
            app_id=config.app_id, tenant_id=config.tenant_id, online=app.state.spool.online,
            pairing_origin=config.pairing_origin if include_pairing_origin else None,
        )

    @app.get("/relay/v1/events", dependencies=protected)
    async def events(receiver: UUID):
        spool: Spool = app.state.spool
        generation = spool.lease(receiver)
        spool.changed.clear()
        pending = spool.pending()
        if not pending:
            try:
                await asyncio.wait_for(spool.changed.wait(), timeout=25)
            except TimeoutError:
                pass
        return PollResult(generation=generation, events=spool.pending())

    @app.post("/relay/v1/receipts", dependencies=protected)
    async def receipt(value: Receipt):
        app.state.spool.receipt(value.event_id, value.generation, value.status)
        return {"ok": True}

    @app.post("/relay/v1/deliveries", dependencies=protected)
    async def deliver(value: Delivery):
        return await app.state.transport.send(value)

    @app.post("/relay/v1/streams", dependencies=protected)
    async def stream_preview(value: StreamPreview):
        return {"active": await app.state.transport.preview(value)}

    @app.get("/relay/v1/deliveries/{delivery_id}", dependencies=protected)
    async def delivery_status(delivery_id: UUID):
        spool = app.state.spool
        return spool.delivery_result(spool.get(delivery_id, "delivery"))

    @app.post("/relay/v1/deliveries/{delivery_id}/accept", dependencies=protected)
    async def authorize_file(delivery_id: UUID):
        return app.state.transport.authorize_upload(delivery_id)

    @app.put("/relay/v1/blobs/{blob_id}", dependencies=protected)
    async def upload(blob_id: UUID, operation: UUID, request: Request):
        data = await request.body()
        if not data:
            raise SpoolError("relay.emptyFile", 422)
        app.state.spool.put_blob(blob_id, data, operation)
        return {"ok": True}

    @app.get("/relay/v1/blobs/{blob_id}", dependencies=protected)
    async def blob(blob_id: UUID, operation: UUID):
        return FileResponse(
            app.state.spool.blob(blob_id, operation), media_type="application/octet-stream"
        )

    return app
