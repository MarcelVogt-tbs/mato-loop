from pathlib import Path
from uuid import UUID

from pydantic import Field, HttpUrl, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class RelayConfig(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MATO_TEAMS_", extra="ignore")

    app_id: UUID
    tenant_id: UUID
    client_secret_file: Path
    relay_token_file: Path
    spool_dir: Path = Path("/spool")
    max_spool_bytes: int = Field(default=256 * 1024 * 1024, ge=1024 * 1024)
    max_records: int = Field(default=2000, ge=20, le=100_000)
    matoloop_url: HttpUrl | None = None

    @field_validator("matoloop_url", mode="before")
    @classmethod
    def empty_matoloop_url(cls, value: str | None) -> str | None:
        return value or None

    @field_validator("matoloop_url")
    @classmethod
    def validate_matoloop_url(cls, url: HttpUrl | None) -> HttpUrl | None:
        if url is not None and (
            url.scheme != "https"
            or url.username is not None
            or url.password is not None
            or url.query is not None
            or url.fragment is not None
            or url.path not in {None, "/"}
        ):
            raise ValueError("Use the MatoLoop HTTPS origin without a path or credentials")
        return url

    def secrets(self) -> tuple[SecretStr, SecretStr]:
        client = self.client_secret_file.read_text().strip()
        token = self.relay_token_file.read_text().strip()
        if not client or len(token) < 43:
            raise ValueError("Configure a client secret and a relay token with at least 256 bits")
        return SecretStr(client), SecretStr(token)
