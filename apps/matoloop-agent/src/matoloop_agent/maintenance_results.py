"""Replay durable installer outcomes through the existing completion endpoint."""

import logging
import os
import stat
from itertools import islice
from typing import Literal
from urllib.parse import urlsplit, urlunsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .config import UUID_PATTERN, AgentConfig

logger = logging.getLogger(__name__)


class MaintenanceResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    backend_url: str = Field(alias="backendUrl", max_length=4096)
    agent_id: str = Field(alias="agentId", pattern=UUID_PATTERN)
    job_id: str = Field(alias="jobId", pattern=UUID_PATTERN)
    status: Literal["succeeded", "failed"]


async def replay_maintenance_results(config: AgentConfig, access_key: str) -> None:
    state = config.data_dir / "agent-state"
    if state.is_symlink() or not state.is_dir():
        return
    pending = list(islice(state.glob("maintenance-*.json"), 64))
    if not pending:
        return
    async with httpx.AsyncClient(timeout=10, trust_env=False, follow_redirects=False) as client:
        for path in pending:
            try:
                if path.is_symlink():
                    continue
                descriptor = os.open(
                    path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
                )
                with os.fdopen(descriptor, "rb") as stream:
                    if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                        continue
                    result = MaintenanceResult.model_validate_json(stream.read(8193))
                if (
                    result.backend_url != config.backend_url
                    or result.agent_id != config.agent_id
                    or path.name != f"maintenance-{result.job_id}.json"
                ):
                    continue
                address = urlsplit(config.backend_url)
                endpoint = urlunsplit(
                    (
                        "https" if address.scheme == "wss" else "http",
                        address.netloc,
                        f"{address.path.removesuffix('/connect')}/{result.agent_id}/maintenance/{result.job_id}/complete",
                        "",
                        "",
                    )
                )
                response = await client.post(
                    endpoint,
                    json={"status": result.status},
                    headers={"Authorization": f"Bearer {access_key}"},
                )
                if response.status_code == 204 or (
                    response.status_code == 409
                    and response.json().get("error", {}).get("code") == "maintenance.jobUnavailable"
                ):
                    path.unlink(missing_ok=True)
                else:
                    return
            except (OSError, ValidationError, ValueError, TypeError, AttributeError):
                logger.warning("A stored maintenance outcome could not be read or acknowledged")
            except httpx.HTTPError:
                return
