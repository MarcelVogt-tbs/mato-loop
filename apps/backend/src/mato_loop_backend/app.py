from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select, update

from mato_loop_backend import __version__
from mato_loop_backend.agent.runtime import AgentRuntime
from mato_loop_backend.api.errors import register_error_handlers
from mato_loop_backend.api.request_body_limit import MediaRequestBodyLimitMiddleware
from mato_loop_backend.api.router import api_router
from mato_loop_backend.attachments import AttachmentService
from mato_loop_backend.automations import AutomationScheduler, AutomationService
from mato_loop_backend.config import get_runtime_config
from mato_loop_backend.db.session import AsyncSessionFactory, engine
from mato_loop_backend.execution.terminals import terminal_coordinator
from mato_loop_backend.memory.dreaming import DreamingScheduler, DreamingService
from mato_loop_backend.memory.embeddings import embedding_client
from mato_loop_backend.memory.episode_indexing import (
    EpisodeIndexScheduler,
    EpisodeIndexService,
)
from mato_loop_backend.memory.indexing import MemoryIndexScheduler, MemoryIndexService
from mato_loop_backend.messaging.telegram import TelegramScheduler
from mato_loop_backend.models import (
    ApprovalRequest,
    ChatSession,
    Project,
    QuestionRequest,
    TitleGenerationStatus,
)
from mato_loop_backend.project_coding import CodingScheduler
from mato_loop_backend.session_storage import SessionStorage
from mato_loop_backend.settings import InstanceLimits, SettingsStore

runtime_config = get_runtime_config()
logger = logging.getLogger(__name__)


def _static_path(static_root: Path, full_path: str) -> Path | None:
    requested = (static_root / full_path).resolve()
    try:
        requested.relative_to(static_root)
    except ValueError:
        return None
    return requested


def _static_headers(full_path: str) -> dict[str, str]:
    if full_path == "sw.js":
        return {
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Service-Worker-Allowed": "/",
        }
    if full_path in {"", "index.html", "manifest.webmanifest"}:
        return {"Cache-Control": "no-cache, no-store, must-revalidate"}
    return {}


def _mount_documentation(target: FastAPI, docs_dir: Path) -> None:
    @target.get("/docs", include_in_schema=False)
    async def documentation_redirect() -> RedirectResponse:
        return RedirectResponse("/docs/", status_code=308)

    target.mount(
        "/docs",
        StaticFiles(directory=docs_dir, html=True),
        name="documentation",
    )


@asynccontextmanager
async def lifespan(_app: FastAPI):
    runtime_config.secrets_dir.mkdir(parents=True, exist_ok=True)
    try:
        async with AsyncSessionFactory() as db:
            attachments = AttachmentService(settings=await SettingsStore.load(db, InstanceLimits))
            await attachments.backfill_derived_files(db)
            await AgentRuntime.recover(db)
            await db.commit()
            await AutomationService.recover(db)
            await db.execute(
                update(ApprovalRequest)
                .where(
                    ApprovalRequest.status == "pending",
                    ApprovalRequest.request_type == "agent_action",
                )
                .values(status="expired", resolved_at=datetime.now(UTC))
            )
            await db.execute(
                update(QuestionRequest)
                .where(QuestionRequest.status == "pending")
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
            session_locations = dict(
                (await db.execute(select(ChatSession.id, ChatSession.project_id))).all()
            )
            project_ids = set((await db.scalars(select(Project.id))).all())
            session_storage = SessionStorage()
            reconciliation = await asyncio.to_thread(
                session_storage.reconcile,
                session_locations,
                projects=project_ids,
                stale_staging_after_seconds=0,
            )
            if reconciliation.provisioned or reconciliation.restored or reconciliation.trashed:
                logger.info(
                    "Session storage reconciled: %s provisioned, %s restored, %s orphaned",
                    len(reconciliation.provisioned),
                    len(reconciliation.restored),
                    len(reconciliation.trashed),
                )
            if reconciliation.restored:
                await attachments.backfill_derived_files(db)
            removed_legacy_files = await asyncio.to_thread(
                session_storage.cleanup_legacy_attachment_storage,
                session_locations,
            )
            if removed_legacy_files:
                logger.info(
                    "Removed %s legacy attachment storage entries",
                    removed_legacy_files,
                )
            if runtime_config.embedding_enabled:
                await MemoryIndexService.recover(db)
                await MemoryIndexService.reconcile(db, retry_failed=True)
            await EpisodeIndexService.recover(db)
            await EpisodeIndexService.reconcile(db, retry_failed=True)
    except Exception:
        logger.exception("Failed to clean up stale runs and interactions during startup")
    DreamingScheduler.start()
    AutomationScheduler.start()
    MemoryIndexScheduler.start()
    EpisodeIndexScheduler.start()
    CodingScheduler.start()
    TelegramScheduler.start()
    try:
        yield
    finally:
        await DreamingScheduler.stop()
        await AutomationScheduler.stop()
        await MemoryIndexScheduler.stop()
        await EpisodeIndexScheduler.stop()
        await CodingScheduler.stop()
        await TelegramScheduler.stop()
        await asyncio.gather(terminal_coordinator.shutdown(), AgentRuntime.shutdown())
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
app.add_middleware(MediaRequestBodyLimitMiddleware)

app.include_router(api_router, prefix="/api")
register_error_handlers(app)

if runtime_config.static_dir and runtime_config.static_dir.exists():
    docs_dir = runtime_config.static_dir / "docs"
    if docs_dir.is_dir():
        _mount_documentation(app, docs_dir)

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
            return FileResponse(requested, headers=_static_headers(full_path))
        index = static_root / "index.html"
        if not index.exists():
            raise HTTPException(status_code=404)
        return FileResponse(index, headers=_static_headers("index.html"))
