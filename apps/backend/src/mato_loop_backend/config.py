from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PACKAGE_FILE = Path(__file__).resolve()
PROJECT_ROOT = PACKAGE_FILE.parents[2]
for parent in PACKAGE_FILE.parents:
    if (parent / "docker-compose.dev.yaml").exists():
        PROJECT_ROOT = parent
        break


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
    data_dir: Path = PROJECT_ROOT / "data"
    session_cookie_name: str = "mato_loop_session"
    session_duration_days: int = 30
    secure_cookies: bool = False
    static_dir: Path | None = None
    embedding_enabled: bool = True
    embedding_base_url: str = "http://embedding:7997"
    embedding_query_timeout_seconds: float = Field(default=5.0, gt=0, le=60)
    embedding_index_timeout_seconds: float = Field(default=60.0, gt=0, le=300)
    embedding_batch_size: int = Field(default=16, ge=1, le=64)

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def agent_home(self) -> Path:
        return self.data_dir / "home"

    @property
    def secrets_dir(self) -> Path:
        return self.data_dir / "secrets"

    @property
    def sessions_dir(self) -> Path:
        return self.data_dir / "sessions"


@lru_cache
def get_runtime_config() -> RuntimeConfig:
    return RuntimeConfig(_env_file=_environment_files(), _env_file_encoding="utf-8")
