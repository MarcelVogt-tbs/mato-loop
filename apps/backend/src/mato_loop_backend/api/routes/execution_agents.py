from __future__ import annotations

import asyncio
import json
import secrets
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal
from uuid import UUID

from fastapi import (
    APIRouter,
    Body,
    Depends,
    Header,
    Response,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from fastapi.responses import FileResponse
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    ValidationError,
    field_validator,
    model_validator,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend import __version__
from mato_loop_backend.api.errors import ApiError
from mato_loop_backend.config import PROJECT_ROOT
from mato_loop_backend.db.session import AsyncSessionFactory, get_db_session
from mato_loop_backend.execution import (
    ExecutionAgentError,
    HelloMessage,
    WelcomeMessage,
    execution_agent_connections,
    transfer_tickets,
)
from mato_loop_backend.execution.maintenance import (
    InstallationTarget,
    installation_target,
    maintenance_state,
    schedule_auto_updates,
    start_maintenance,
)
from mato_loop_backend.execution.protocol import MAX_MESSAGE_BYTES, SUPPORTED_OPERATIONS
from mato_loop_backend.models import ExecutionAgent, UserAccount
from mato_loop_backend.personal_variables import get_personal_variable
from mato_loop_backend.security import hash_token, require_user
from mato_loop_backend.settings import GeneralSettings, SettingsStore, UserPreferences

router = APIRouter(prefix="/execution-agents")
PAIRING_DURATION_DEFAULT_SECONDS = 1800
PAIRING_DURATION_MAXIMUM_SECONDS = 86400


@router.get("/install.sh", include_in_schema=False)
async def download_installer() -> FileResponse:
    return FileResponse(
        PROJECT_ROOT / "scripts/install-matoloop-agent.sh",
        media_type="text/x-shellscript",
        headers={"Cache-Control": "no-cache"},
    )


@router.get("/install.ps1", include_in_schema=False)
async def download_windows_installer() -> FileResponse:
    return FileResponse(
        PROJECT_ROOT / "scripts/install-matoloop-agent.ps1",
        media_type="text/plain",
        headers={"Cache-Control": "no-cache"},
    )


def _camel(value: str) -> str:
    first, *rest = value.split("_")
    return first + "".join(part.capitalize() for part in rest)


class ApiModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=_camel,
        populate_by_name=True,
        serialize_by_alias=True,
        extra="forbid",
    )


@router.get("/installation-target")
async def get_installation_target(response: Response) -> InstallationTarget:
    # Public build metadata lets an unpaired installer select the exact source.
    response.headers["Cache-Control"] = "no-store"
    return installation_target()


class SudoPasswordRequest(ApiModel):
    sudo_password: str | None = Field(default=None, min_length=1, max_length=4096, repr=False)

    @field_validator("sudo_password")
    @classmethod
    def valid_password(cls, value: str | None) -> str | None:
        if value is not None and any(character in value for character in "\r\n\x00"):
            raise ValueError("invalid sudo password")
        return value


class ExecutionAgentAliasRequest(ApiModel):
    alias: str = Field(default="", max_length=160)

    @field_validator("alias")
    @classmethod
    def normalize_alias(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None


ExecutionPolicy = Literal["auto", "ask", "deny"]


class ExecutionAgentRequest(ExecutionAgentAliasRequest):
    sudo_credential_id: UUID | None = None
    pairing_token_duration_seconds: int = Field(
        default=PAIRING_DURATION_DEFAULT_SECONDS,
        ge=PAIRING_DURATION_DEFAULT_SECONDS,
        le=PAIRING_DURATION_MAXIMUM_SECONDS,
    )
    home_filesystem_policy: ExecutionPolicy = "auto"
    external_filesystem_policy: ExecutionPolicy = "ask"
    home_shell_enabled: bool = True
    external_shell_enabled: bool = True
    auto_update_enabled: StrictBool | None = None


class PairingTokenRequest(ApiModel):
    pairing_token_duration_seconds: int = Field(
        default=PAIRING_DURATION_DEFAULT_SECONDS,
        ge=PAIRING_DURATION_DEFAULT_SECONDS,
        le=PAIRING_DURATION_MAXIMUM_SECONDS,
    )


class PairExecutionAgentRequest(ApiModel):
    agent_id: UUID
    access_key_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class DefaultExecutionAgentRequest(ApiModel):
    default_execution_agent_id: UUID | None = None


class ExecutionAgentUpdateRequest(ApiModel):
    sudo_credential_id: UUID | None = None
    alias: str | None = Field(default=None, max_length=160)
    home_filesystem_policy: ExecutionPolicy | None = None
    external_filesystem_policy: ExecutionPolicy | None = None
    home_shell_enabled: bool | None = None
    external_shell_enabled: bool | None = None
    auto_update_enabled: StrictBool | None = None

    _normalize_alias = field_validator("alias")(ExecutionAgentAliasRequest.normalize_alias.__func__)

    @model_validator(mode="after")
    def require_change(self) -> ExecutionAgentUpdateRequest:
        if not self.model_fields_set:
            raise ValueError("at least one field is required")
        return self


class MaintenanceRequest(SudoPasswordRequest):
    action: Literal["update", "uninstall"]


class MaintenanceCompletion(ApiModel):
    status: Literal["succeeded", "failed"]


def _valid_registration(agent: ExecutionAgent, now: datetime) -> bool:
    return (
        agent.revoked_at is None
        and agent.token_hash is None
        and agent.registration_token_hash is not None
        and agent.registration_expires_at is not None
        and agent.registration_expires_at > now
    )


def _agent_payload(agent: ExecutionAgent) -> dict:
    now = datetime.now(UTC)
    pending = _valid_registration(agent, now)
    pairing_state = "pending" if pending else "paired" if agent.token_hash is not None else "unpaired"
    payload = {
        "id": str(agent.id),
        "alias": agent.alias,
        "displayName": agent.display_name,
        "computerName": agent.computer_name,
        "agentVersion": agent.agent_version,
        "platform": agent.platform,
        "revision": agent.revision,
        "operations": agent.operations,
        "maintenance": maintenance_state(agent),
        "autoUpdateEnabled": agent.auto_update_enabled,
        "sudoCredentialId": str(agent.sudo_credential_id) if agent.sudo_credential_id else None,
        "homeDirectory": agent.home_directory,
        "homeFilesystemPolicy": agent.home_filesystem_policy,
        "externalFilesystemPolicy": agent.external_filesystem_policy,
        "homeShellEnabled": agent.home_shell_enabled,
        "externalShellEnabled": agent.external_shell_enabled,
        "pairingState": pairing_state,
        "connected": pairing_state == "paired"
        and agent.revoked_at is None
        and execution_agent_connections.is_connected(agent.id),
        "lastSeenAt": agent.last_connected_at,
        "revokedAt": agent.revoked_at,
        "createdAt": agent.created_at,
        "updatedAt": agent.updated_at,
    }
    if pending:
        payload["pairingTokenExpiresAt"] = agent.registration_expires_at
    return payload


def _issue_pairing_token(agent: ExecutionAgent, duration_seconds: int) -> str:
    token = secrets.token_urlsafe(48)
    agent.registration_token_hash = hash_token(token)
    agent.registration_expires_at = datetime.now(UTC) + timedelta(seconds=duration_seconds)
    return token


def _pairing_envelope(agent: ExecutionAgent, token: str) -> dict:
    return {
        "agent": _agent_payload(agent),
        "pairingToken": token,
        "pairingTokenExpiresAt": agent.registration_expires_at,
        "websocketUrl": "/api/execution-agents/connect",
    }


def _http_bearer_token(authorization: str | None) -> str:
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not token or " " in token:
        raise ApiError(status.HTTP_401_UNAUTHORIZED, "executionAgent.pairingTokenInvalid")
    return token


async def _commit_and_revoke_live_capabilities(db: AsyncSession, agent_id: UUID) -> None:
    async def cleanup() -> None:
        await db.commit()
        try:
            await execution_agent_connections.close(agent_id)
        finally:
            transfer_tickets.revoke_owner(agent_id)

    task = asyncio.create_task(cleanup())
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
    task.result()
    if cancelled:
        raise asyncio.CancelledError


async def _owned_agent(
    db: AsyncSession, user_id: UUID, agent_id: UUID, *, for_update: bool = False
) -> ExecutionAgent:
    statement = select(ExecutionAgent).where(
        ExecutionAgent.id == agent_id, ExecutionAgent.user_id == user_id
    )
    if for_update:
        statement = statement.with_for_update()
    agent = await db.scalar(statement)
    if agent is None:
        raise ApiError(status.HTTP_404_NOT_FOUND, "executionAgent.notFound")
    return agent


async def _validate_sudo_credential(db: AsyncSession, owner_id: UUID, credential_id: UUID | None) -> None:
    if credential_id is not None:
        variable = await get_personal_variable(db, owner_id, credential_id)
        if variable is None or variable.metadata_json.get("secret") is not True:
            raise ApiError(422, "personalVariable.notFound", field="sudoCredentialId")


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_execution_agent(
    payload: ExecutionAgentRequest,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
    response: Response,
) -> dict:
    await _validate_sudo_credential(db, user.id, payload.sudo_credential_id)
    agent = ExecutionAgent(
        user_id=user.id,
        alias=payload.alias,
        token_hash=None,
        token_is_legacy=None,
        home_filesystem_policy=payload.home_filesystem_policy,
        external_filesystem_policy=payload.external_filesystem_policy,
        home_shell_enabled=payload.home_shell_enabled,
        external_shell_enabled=payload.external_shell_enabled,
        auto_update_enabled=payload.auto_update_enabled,
        sudo_credential_id=payload.sudo_credential_id,
    )
    token = _issue_pairing_token(agent, payload.pairing_token_duration_seconds)
    db.add(agent)
    await db.flush()
    await db.commit()
    response.headers["Cache-Control"] = "no-store"
    return _pairing_envelope(agent, token)


@router.get("/pairing-policy")
async def execution_agent_pairing_policy(
    _user: Annotated[UserAccount, Depends(require_user)],
) -> dict[str, int]:
    return {
        "defaultDurationSeconds": PAIRING_DURATION_DEFAULT_SECONDS,
        "maximumDurationSeconds": PAIRING_DURATION_MAXIMUM_SECONDS,
    }


@router.post("/pair", status_code=status.HTTP_204_NO_CONTENT)
async def pair_execution_agent(
    payload: PairExecutionAgentRequest,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    authorization: Annotated[str | None, Header()] = None,
) -> Response:
    presented_hash = hash_token(_http_bearer_token(authorization))
    agent = await db.scalar(
        select(ExecutionAgent)
        .where(ExecutionAgent.id == payload.agent_id)
        .with_for_update()
    )
    if agent is None or agent.revoked_at is not None:
        raise ApiError(status.HTTP_401_UNAUTHORIZED, "executionAgent.pairingTokenInvalid")
    if agent.token_hash == payload.access_key_hash:
        await db.commit()
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    migrated_legacy_token = False
    if agent.token_hash is not None:
        if agent.token_is_legacy is not True or not secrets.compare_digest(
            agent.token_hash, presented_hash
        ):
            raise ApiError(status.HTTP_409_CONFLICT, "executionAgent.alreadyPaired")
        migrated_legacy_token = True
    else:
        if agent.registration_token_hash is None or not secrets.compare_digest(
            agent.registration_token_hash, presented_hash
        ):
            raise ApiError(status.HTTP_401_UNAUTHORIZED, "executionAgent.pairingTokenInvalid")
        if (
            agent.registration_expires_at is None
            or agent.registration_expires_at <= datetime.now(UTC)
        ):
            raise ApiError(status.HTTP_410_GONE, "executionAgent.pairingTokenExpired")
    agent.token_hash = payload.access_key_hash
    agent.token_is_legacy = False
    agent.registration_token_hash = None
    agent.registration_expires_at = None
    agent.revoked_at = None
    agent.revision += 1
    agent.updated_at = datetime.now(UTC)
    if migrated_legacy_token:
        await _commit_and_revoke_live_capabilities(db, agent.id)
    else:
        await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("")
async def list_execution_agents(
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict:
    agents = list(
        (
            await db.scalars(
                select(ExecutionAgent).where(
                    ExecutionAgent.user_id == user.id,
                    ExecutionAgent.revoked_at.is_(None),
                )
                .order_by(ExecutionAgent.created_at, ExecutionAgent.id)
            )
        ).all()
    )
    preferences = await SettingsStore.load(db, UserPreferences, user_id=user.id)
    general = await SettingsStore.load(db, GeneralSettings)
    try:
        target = installation_target().model_dump()
    except ApiError:
        target = None
    return {
        "items": [_agent_payload(agent) for agent in agents],
        "backendVersion": __version__,
        "installationTarget": target,
        "autoUpdateDefault": general.agent_auto_update_enabled,
        "defaultExecutionAgentId": (
            str(preferences.default_execution_agent_id)
            if preferences.default_execution_agent_id
            else None
        ),
    }


@router.put("/default")
async def set_default_execution_agent(
    payload: DefaultExecutionAgentRequest,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict[str, str | None]:
    if payload.default_execution_agent_id is not None:
        agent = await _owned_agent(db, user.id, payload.default_execution_agent_id)
        if agent.revoked_at is not None:
            raise ApiError(status.HTTP_409_CONFLICT, "executionAgent.revoked")
    preferences = await SettingsStore.load(
        db, UserPreferences, user_id=user.id, for_update=True
    )
    preferences.default_execution_agent_id = payload.default_execution_agent_id
    await SettingsStore.save(db, preferences, user_id=user.id)
    await db.commit()
    return {
        "defaultExecutionAgentId": (
            str(preferences.default_execution_agent_id)
            if preferences.default_execution_agent_id
            else None
        )
    }


@router.get("/{agent_id}")
async def get_execution_agent(
    agent_id: UUID,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict:
    return _agent_payload(await _owned_agent(db, user.id, agent_id))


@router.post("/{agent_id}/maintenance")
async def start_execution_agent_maintenance(
    agent_id: UUID,
    payload: MaintenanceRequest,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
    response: Response,
) -> dict:
    agent = await _owned_agent(db, user.id, agent_id, for_update=True)
    response.headers["Cache-Control"] = "no-store"
    return await start_maintenance(db, agent, payload.action, sudo_password=payload.sudo_password)


@router.post("/{agent_id}/maintenance/{job_id}/complete", status_code=204)
async def complete_execution_agent_maintenance(
    agent_id: UUID,
    job_id: UUID,
    payload: MaintenanceCompletion,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    authorization: Annotated[str | None, Header()] = None,
) -> Response:
    token_hash = hash_token(_http_bearer_token(authorization))
    agent = await db.get(ExecutionAgent, agent_id, with_for_update=True)
    if (
        agent is None or agent.revoked_at is not None or not agent.token_hash
        or not secrets.compare_digest(agent.token_hash, token_hash)
    ):
        raise ApiError(401, "executionAgent.pairingTokenInvalid")
    state = agent.maintenance
    if (
        not state or state["jobId"] != str(job_id)
        or state["status"] not in {"running", "unknown", payload.status}
    ):
        raise ApiError(409, "maintenance.jobUnavailable")
    if state["status"] in {"running", "unknown"}:
        agent.maintenance = {
            **{key: value for key, value in state.items() if key != "errorCode"},
            "status": payload.status,
            "finishedAt": datetime.now(UTC).isoformat(),
            **({"errorCode": "maintenance.failed"} if payload.status == "failed" else {}),
        }
    await db.commit()
    return Response(status_code=204)


@router.patch("/{agent_id}")
async def update_execution_agent(
    agent_id: UUID,
    payload: ExecutionAgentUpdateRequest,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict:
    agent = await _owned_agent(db, user.id, agent_id, for_update=True)
    if agent.revoked_at is not None:
        raise ApiError(status.HTTP_409_CONFLICT, "executionAgent.revoked")
    changed = False
    auto_update_changed = False
    for field in payload.model_fields_set:
        if field == "sudo_credential_id":
            await _validate_sudo_credential(db, user.id, payload.sudo_credential_id)
        value = getattr(payload, field)
        if (value is not None or field in {"auto_update_enabled", "sudo_credential_id"}) and getattr(agent, field) != value:
            setattr(agent, field, value)
            changed = True
            auto_update_changed |= field in {"auto_update_enabled", "sudo_credential_id"}
    if changed:
        agent.revision += 1
        agent.updated_at = datetime.now(UTC)
    await db.commit()
    if auto_update_changed:
        schedule_auto_updates(agent.id)
    return _agent_payload(agent)


@router.post("/{agent_id}/pairing-token")
async def create_execution_agent_pairing_token(
    agent_id: UUID,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
    response: Response,
    payload: Annotated[PairingTokenRequest | None, Body()] = None,
) -> dict:
    agent = await _owned_agent(db, user.id, agent_id, for_update=True)
    if agent.revoked_at is not None:
        raise ApiError(status.HTTP_409_CONFLICT, "executionAgent.revoked")
    if agent.token_hash is not None:
        raise ApiError(status.HTTP_409_CONFLICT, "executionAgent.alreadyPaired")
    duration_seconds = (
        payload.pairing_token_duration_seconds
        if payload is not None
        else PAIRING_DURATION_DEFAULT_SECONDS
    )
    token = _issue_pairing_token(agent, duration_seconds)
    agent.revision += 1
    agent.updated_at = datetime.now(UTC)
    await db.commit()
    response.headers["Cache-Control"] = "no-store"
    return _pairing_envelope(agent, token)


@router.delete("/{agent_id}/pairing")
async def unpair_execution_agent(
    agent_id: UUID,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict:
    agent = await _owned_agent(db, user.id, agent_id, for_update=True)
    preferences = await SettingsStore.load(
        db, UserPreferences, user_id=user.id, for_update=True
    )
    changed = any(
        value is not None
        for value in (
            agent.token_hash,
            agent.token_is_legacy,
            agent.registration_token_hash,
            agent.registration_expires_at,
        )
    )
    agent.token_hash = None
    agent.token_is_legacy = None
    agent.registration_token_hash = None
    agent.registration_expires_at = None
    if changed:
        agent.revision += 1
        agent.updated_at = datetime.now(UTC)
    if preferences.default_execution_agent_id == agent.id:
        preferences.default_execution_agent_id = None
        await SettingsStore.save(db, preferences, user_id=user.id)
    agent.sudo_credential_id = None
    await _commit_and_revoke_live_capabilities(db, agent.id)
    return _agent_payload(agent)


@router.delete("/{agent_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_execution_agent(
    agent_id: UUID,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> None:
    agent = await _owned_agent(db, user.id, agent_id, for_update=True)
    preferences = await SettingsStore.load(
        db, UserPreferences, user_id=user.id, for_update=True
    )
    if agent.revoked_at is None:
        agent.revoked_at = datetime.now(UTC)
        agent.revision += 1
    agent.sudo_credential_id = None
    if preferences.default_execution_agent_id == agent.id:
        preferences.default_execution_agent_id = None
        await SettingsStore.save(db, preferences, user_id=user.id)
    await _commit_and_revoke_live_capabilities(db, agent.id)


def _bearer_token(websocket: WebSocket) -> str | None:
    scheme, _, token = websocket.headers.get("authorization", "").partition(" ")
    return token if scheme.lower() == "bearer" and token and " " not in token else None


async def _authenticate(token: str | None) -> tuple[UUID, str] | None:
    if token is None:
        return None
    token_hash = hash_token(token)
    async with AsyncSessionFactory() as db:
        agent_id = await db.scalar(
            select(ExecutionAgent.id).where(
                ExecutionAgent.token_hash == token_hash,
                ExecutionAgent.revoked_at.is_(None),
            )
        )
    return (agent_id, token_hash) if agent_id is not None else None


async def _register_snapshot(
    agent_id: UUID, authenticated_token_hash: str, hello: HelloMessage
) -> bool:
    operations = sorted(set(hello.operations) & SUPPORTED_OPERATIONS)
    async with AsyncSessionFactory() as db:
        agent = await db.scalar(
            select(ExecutionAgent)
            .where(
                ExecutionAgent.id == agent_id,
                ExecutionAgent.token_hash == authenticated_token_hash,
            )
            .with_for_update()
        )
        if agent is None or agent.revoked_at is not None:
            return False
        changed = (
            agent.agent_version != hello.agent_version
            or agent.computer_name != hello.computer_name
            or agent.platform != hello.platform
            or agent.operations != operations
            or agent.home_directory != hello.home_directory
        )
        agent.agent_version = hello.agent_version
        agent.computer_name = hello.computer_name
        agent.platform = hello.platform
        agent.operations = operations
        agent.home_directory = hello.home_directory
        agent.last_connected_at = datetime.now(UTC)
        if changed:
            agent.revision += 1
        await db.commit()
        return True


@router.websocket("/connect")
async def connect_execution_agent(websocket: WebSocket) -> None:
    authentication = await _authenticate(_bearer_token(websocket))
    if authentication is None:
        await websocket.close(code=4401, reason="Authentication required")
        return
    authenticated_id, authenticated_token_hash = authentication
    await websocket.accept()
    registered = False
    try:
        raw = await websocket.receive_text()
        if len(raw.encode("utf-8")) > MAX_MESSAGE_BYTES:
            raise ExecutionAgentError("executionAgent.protocolError")
        hello = HelloMessage.model_validate_json(raw)
        if hello.agent_id != authenticated_id or not await _register_snapshot(
            authenticated_id, authenticated_token_hash, hello
        ):
            await websocket.close(code=4403, reason="Agent access rejected")
            return
        registered = True
        await websocket.send_text(
            WelcomeMessage(agent_id=authenticated_id).model_dump_json(by_alias=True)
        )
        await execution_agent_connections.register(hello, websocket)
        registered = True
        schedule_auto_updates(authenticated_id)
        while True:
            await execution_agent_connections.receive(
                authenticated_id, websocket, await websocket.receive_text()
            )
    except WebSocketDisconnect:
        pass
    except (ExecutionAgentError, ValidationError, ValueError, json.JSONDecodeError) as exc:
        code = exc.code if isinstance(exc, ExecutionAgentError) else "executionAgent.protocolError"
        if registered:
            await execution_agent_connections.unregister(
                authenticated_id, websocket, error_code=code
            )
            registered = False
        try:
            await websocket.close(code=4400, reason="Invalid MatoLoop Agent protocol")
        except RuntimeError:
            pass
    finally:
        if registered:
            await execution_agent_connections.unregister(authenticated_id, websocket)
