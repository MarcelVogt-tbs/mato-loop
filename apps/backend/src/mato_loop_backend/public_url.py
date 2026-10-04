from fastapi import Request
from pydantic import HttpUrl

from mato_loop_backend.config import get_runtime_config


def normalize_public_base_url(value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("The public URL must be a string")
    if not value.strip():
        return None
    url = HttpUrl(value.strip())
    if (
        url.username is not None
        or url.password is not None
        or url.query is not None
        or url.fragment is not None
        or url.path not in {None, "/"}
    ):
        raise ValueError("The public URL must be an HTTP(S) origin without credentials or a path")
    return str(url).rstrip("/")


def resolve_public_base_url(configured: str | None, request: Request) -> tuple[str, str]:
    environment = (get_runtime_config().public_base_url or "").strip()
    value, source = (
        (configured, "settings")
        if configured
        else (environment, "environment")
        if environment
        else (str(request.base_url), "automatic")
    )
    # Request.base_url uses the server's trusted proxy handling, not arbitrary forwarded headers.
    return normalize_public_base_url(value), source
