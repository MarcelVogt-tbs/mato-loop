from __future__ import annotations

import asyncio
import logging
import re
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend import __version__
from mato_loop_backend.api.errors import ApiError
from mato_loop_backend.build_info import build_revision
from mato_loop_backend.db.session import AsyncSessionFactory
from mato_loop_backend.execution import ExecutionAgentError, execution_agent_connections
from mato_loop_backend.models import ExecutionAgent
from mato_loop_backend.personal_variables import (
    PersonalVariableError,
    get_personal_variable,
    personal_variable_value,
)
from mato_loop_backend.settings import GeneralSettings, SettingsStore

logger = logging.getLogger(__name__)
MAINTENANCE_LIMIT = timedelta(minutes=65)
_auto_update_tasks: set[asyncio.Task] = set()


class InstallationTarget(BaseModel):
    revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    version: str


def installation_target() -> InstallationTarget:
    revision = build_revision()
    if revision is None or not re.fullmatch(
        rf"[0-9]{{4}}-[0-9]{{2}}-[0-9]{{2}}-{revision[:8]}", __version__
    ):
        raise ApiError(409, "maintenance.targetUnavailable", headers={"Cache-Control": "no-store"})
    return InstallationTarget(revision=revision, version=__version__)


def maintenance_state(agent: ExecutionAgent) -> dict | None:
    state = agent.maintenance
    if state and state["status"] == "running":
        started = datetime.fromisoformat(state["startedAt"])
        if datetime.now(UTC) - started > MAINTENANCE_LIMIT:
            return {**state, "status": "unknown"}
    return state


async def start_maintenance(
    db: AsyncSession, agent: ExecutionAgent, action: Literal["update", "uninstall"],
    *, sudo_password: str | None = None, target: InstallationTarget | None = None,
    automatic: bool = False,
) -> dict:
    # Callers hold the agent row lock until the running job has been committed.
    agent_id = agent.id
    if agent.revoked_at is not None:
        raise ApiError(409, "executionAgent.revoked")
    if (
        agent.maintenance
        and agent.maintenance["status"] in {"running", "unknown"}
        and datetime.now(UTC) - datetime.fromisoformat(agent.maintenance["startedAt"])
        <= MAINTENANCE_LIMIT
    ):
        raise ApiError(409, "maintenance.busy")
    if not execution_agent_connections.is_connected(agent_id):
        raise ApiError(503, "executionAgent.unavailable")
    generation = execution_agent_connections.generation(agent_id)
    operation = (
        "maintenance.update"
        if action == "update" and "maintenance.update" in agent.operations
        else "maintenance.start"
    )
    if operation not in agent.operations:
        raise ApiError(409, "maintenance.unavailable")
    if action == "update" and not automatic and "maintenance.update.interrupt" in agent.operations:
        operation = "maintenance.update.interrupt"
    revision = (target or installation_target()).revision if action == "update" else None
    previous = agent.maintenance
    job_id = str(uuid4())
    agent.maintenance = {
        "jobId": job_id,
        "action": action,
        "status": "running",
        "startedAt": datetime.now(UTC).isoformat(),
    }
    await db.commit()
    try:
        if (
            action == "update" and agent.platform == "linux" and "maintenance.update" not in agent.operations
            and sudo_password is None and agent.sudo_credential_id
        ):
            credential = await get_personal_variable(db, agent.user_id, agent.sudo_credential_id)
            if credential is None or credential.metadata_json.get("secret") is not True:
                raise ApiError(409, "personalVariable.notFound")
            try:
                sudo_password = personal_variable_value(credential)
            except PersonalVariableError as exc:
                raise ApiError(409, exc.code) from exc
            if not 1 <= len(sudo_password) <= 4096 or any(c in sudo_password for c in "\r\n\x00"):
                raise ApiError(409, "personalVariable.valueInvalid")
        result = await execution_agent_connections.request(
            agent_id, operation,
            {
                "action": action, "jobId": job_id,
                **({"revision": revision} if revision else {}),
                **({"sudoPassword": sudo_password} if sudo_password else {}),
            },
            timeout=120 if operation == "maintenance.update.interrupt" else 35, expected_generation=generation,
        )
        if not result.ok:
            code = result.error.code if result.error else "maintenance.failed"
            if code not in {
                "process.sudoAuthRequired", "process.sudoAuthFailed", "maintenance.unavailable",
                "maintenance.busy",
            }:
                code = "maintenance.failed"
            raise ApiError(409 if code != "maintenance.failed" else 502, code)
    except (ExecutionAgentError, ApiError) as exc:
        code = exc.code
        unknown = code in {
            "executionAgent.outcomeUnknown", "executionAgent.timeout", "executionAgent.replaced"
        }
        current = await db.get(ExecutionAgent, agent_id, with_for_update=True, populate_existing=True)
        if current and current.maintenance and current.maintenance["jobId"] == job_id:
            if code == "maintenance.busy" or (
                not automatic and code in {"process.sudoAuthRequired", "process.sudoAuthFailed"}
            ):
                current.maintenance = previous
            elif current.maintenance["status"] == "running":
                current.maintenance = {
                    **current.maintenance,
                    "status": "unknown" if unknown else "failed",
                    "errorCode": "maintenance.failed" if code.startswith("executionAgent.") else code,
                }
            await db.commit()
        if isinstance(exc, ApiError):
            raise
        raise ApiError(
            503, code if unknown or code == "executionAgent.unavailable" else "maintenance.failed",
        ) from exc
    await db.refresh(agent)
    return {"maintenance": maintenance_state(agent)}


async def auto_update_agents(agent_id: UUID | None = None) -> None:
    try:
        target = installation_target()
        async with AsyncSessionFactory() as db:
            statement = select(ExecutionAgent.id).where(ExecutionAgent.revoked_at.is_(None))
            statement = statement.where(
                ExecutionAgent.id == agent_id if agent_id else ExecutionAgent.auto_update_enabled.is_(None)
            )
            agent_ids = list(await db.scalars(statement))
        for identifier in agent_ids:
            if not execution_agent_connections.is_connected(identifier):
                continue
            try:
                async with AsyncSessionFactory() as db:
                    agent = await db.get(ExecutionAgent, identifier, with_for_update=True)
                    if agent is None or agent.revoked_at is not None or agent.token_hash is None:
                        continue
                    settings = await SettingsStore.load(db, GeneralSettings)
                    enabled = agent.auto_update_enabled if agent.auto_update_enabled is not None else settings.agent_auto_update_enabled
                    if (
                        not enabled or agent.agent_version == target.version
                        or not {"maintenance.start", "maintenance.update"}.intersection(agent.operations)
                        or (agent_id is None and agent.auto_update_enabled is not None)
                    ):
                        continue
                    await start_maintenance(db, agent, "update", target=target, automatic=True)
            except ApiError as exc:
                logger.info("Automatic Agent update for %s was not started: %s", identifier, exc.code)
    except ApiError as exc:
        logger.info("Automatic Agent updates unavailable: %s", exc.code)
    except Exception:
        # Never log exception details from credential handling.
        logger.error("Automatic Agent update check failed")


def schedule_auto_updates(agent_id: UUID | None = None) -> None:
    task = asyncio.create_task(auto_update_agents(agent_id))
    _auto_update_tasks.add(task)
    task.add_done_callback(_auto_update_tasks.discard)


async def stop_auto_updates() -> None:
    tasks = list(_auto_update_tasks)
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
