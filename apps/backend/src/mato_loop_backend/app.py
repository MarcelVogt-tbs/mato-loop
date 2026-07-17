from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from mato_loop_backend import __version__
from mato_loop_backend.api import api_router
from mato_loop_backend.config import get_runtime_config

runtime_config = get_runtime_config()

app = FastAPI(
    title=runtime_config.app_name,
    version=__version__,
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=runtime_config.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router, prefix="/api")
