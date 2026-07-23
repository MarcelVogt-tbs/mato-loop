from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import update

from mato_loop_backend import __version__
from mato_loop_backend.agent.runtime import AgentRuntime
from mato_loop_backend.api.errors import register_error_handlers
from mato_loop_backend.api.router import api_router
from mato_loop_backend.attachments import AttachmentService
from mato_loop_backend.config import get_runtime_config
from mato_loop_backend.db.session import AsyncSessionFactory, engine
from mato_loop_backend.memory.dreaming import DreamingScheduler, DreamingService
from mato_loop_backend.memory.embeddings import embedding_client
from mato_loop_backend.memory.indexing import MemoryIndexScheduler, MemoryIndexService
from mato_loop_backend.models import (
    AgentRun,
    ApprovalRequest,
    ChatSession,
    TitleGenerationStatus,
)

runtime_config = get_runtime_config()
logger = logging.getLogger(__name__)


def _static_path(static_root: Path, full_path: str) -> Path | None:
    requested = (static_root / full_path).resolve()
    try:
        requested.relative_to(static_root)
    except ValueError:
        return None
    return requested


@asynccontextmanager
async def lifespan(_app: FastAPI):
    runtime_config.agent_home.mkdir(parents=True, exist_ok=True)
    runtime_config.secrets_dir.mkdir(parents=True, exist_ok=True)
    try:
        async with AsyncSessionFactory() as db:
            await db.execute(
                update(AgentRun)
                .where(AgentRun.status.in_(["queued", "running", "waiting_approval"]))
                .values(status="failed", error="agent.backendRestarted")
            )
            await db.execute(
                update(ApprovalRequest)
                .where(ApprovalRequest.status == "pending")
                .values(status="expired")
            )
            await db.execute(
                update(ChatSession)
                .where(
                    ChatSession.title_generation_status
                    == TitleGenerationStatus.RUNNING.value
                )
                .values(title_generation_status=TitleGenerationStatus.FAILED.value)
            )
            await DreamingService.recover(db)
            await db.commit()
            if runtime_config.embedding_enabled:
                await MemoryIndexService.recover(db)
                await MemoryIndexService.reconcile(db)
            await AttachmentService().reconcile(db)
    except Exception:
        logger.exception("Failed to clean up stale runs and approvals during startup")
    DreamingScheduler.start()
    MemoryIndexScheduler.start()
    try:
        yield
    finally:
        await DreamingScheduler.stop()
        await MemoryIndexScheduler.stop()
        await AgentRuntime.shutdown()
        await embedding_client.close()
        await engine.dispose()


app = FastAPI(
    title=runtime_config.app_name,
    version=__version__,
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=runtime_config.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router, prefix="/api")
register_error_handlers(app)

if runtime_config.static_dir and runtime_config.static_dir.exists():
    assets_dir = runtime_config.static_dir / "assets"
    if assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa_fallback(full_path: str) -> FileResponse:
        if full_path == "api" or full_path.startswith("api/"):
            raise HTTPException(status_code=404)
        static_root = runtime_config.static_dir.resolve()
        requested = _static_path(static_root, full_path)
        if requested is None:
            raise HTTPException(status_code=404)
        if full_path and requested.is_file():
            return FileResponse(requested)
        index = static_root / "index.html"
        if not index.exists():
            raise HTTPException(status_code=404)
        return FileResponse(index)
