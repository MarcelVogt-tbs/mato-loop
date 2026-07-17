from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend import __version__
from mato_loop_backend.db.session import get_db_session

router = APIRouter(prefix="/health")


@router.get("/live")
async def liveness() -> dict[str, str]:
    return {"status": "ok", "version": __version__}


@router.get("", response_model=None)
async def readiness(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict[str, str] | JSONResponse:
    try:
        await session.execute(text("SELECT 1"))
    except Exception:
        return JSONResponse(
            status_code=503,
            content={"status": "unavailable", "database": "unavailable"},
        )
    return {"status": "ok", "database": "ok", "version": __version__}
