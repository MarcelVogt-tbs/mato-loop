from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend import __version__
from mato_loop_backend.config import get_runtime_config
from mato_loop_backend.db.session import get_db_session
from mato_loop_backend.memory.embeddings import embedding_client

router = APIRouter(prefix="/health")


@router.get("/live")
async def liveness() -> dict[str, str]:
    return {"status": "ok", "version": __version__}


@router.get("", response_model=None)
async def readiness(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict[str, Any] | JSONResponse:
    try:
        await session.execute(text("SELECT 1"))
    except Exception:
        return JSONResponse(
            status_code=503,
            content={"status": "unavailable", "database": "unavailable"},
        )
    try:
        vector_version = await session.scalar(
            text("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
        )
    except Exception:
        vector_version = None
    if not vector_version:
        return JSONResponse(
            status_code=503,
            content={
                "status": "unavailable",
                "database": "ok",
                "vector": "unavailable",
            },
        )
    config = get_runtime_config()
    embedding_status = "disabled"
    if config.embedding_enabled:
        embedding_status = "ok" if await embedding_client.available() else "degraded"
    return {
        "status": "ok",
        "database": "ok",
        "vector": "ok",
        "vectorVersion": vector_version,
        "embedding": embedding_status,
        "version": __version__,
    }
