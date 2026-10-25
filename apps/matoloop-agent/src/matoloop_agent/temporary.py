"""Shared scratch directory for authorized model-side checks."""

import logging
import os
import tempfile
from pathlib import Path

from .errors import AgentError

logger = logging.getLogger(__name__)


def temporary_directory() -> str:
    base = Path(tempfile.gettempdir()) if os.name == "nt" else Path("/tmp")
    return (base / "mato-loop").as_posix()


def temporary_environment(
    filesystem, session_id: str, environment: dict[str, str] | None = None
) -> dict[str, str]:
    try:
        path = f"{temporary_directory()}/{session_id}"
        filesystem.mkdir(path, True, inherit_ownership=False)
    except (AgentError, OSError):
        logger.warning(
            "Shared temporary directory is unavailable; retaining the process environment"
        )
        return {}
    explicit = {name.upper() if os.name == "nt" else name for name in environment or {}}
    return {name: path for name in ("TMPDIR", "TEMP", "TMP") if name not in explicit}
