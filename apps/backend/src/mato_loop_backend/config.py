from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[4]


def _environment_files() -> tuple[Path, ...]:
    environment = os.getenv("MATO_LOOP_ENVIRONMENT", "dev")
    return (
        PROJECT_ROOT / ".env.default",
        PROJECT_ROOT / f".env.{environment}",
        PROJECT_ROOT / f".env.{environment}.override",
    )


class RuntimeConfig(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="MATO_LOOP_",
        case_sensitive=False,
        extra="ignore",
    )

    environment: str = "dev"
    app_name: str = "MatoLoop"
    bind_backend_host: str = "0.0.0.0"
    bind_backend_port: int = 6020
    database_url: str = "postgresql+asyncpg://matoloop:matoloop@localhost:5432/matoloop"
    cors_origins: str = Field(default="http://localhost:10020")
    log_level: str = "INFO"

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_runtime_config() -> RuntimeConfig:
    return RuntimeConfig(_env_file=_environment_files(), _env_file_encoding="utf-8")
