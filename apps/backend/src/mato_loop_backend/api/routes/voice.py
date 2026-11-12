from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.api.errors import ApiError
from mato_loop_backend.api.websocket_access import _browser_login, _origin_allowed
from mato_loop_backend.config import get_runtime_config
from mato_loop_backend.db.session import AsyncSessionFactory, get_db_session
from mato_loop_backend.models import UserAccount, VoiceConnection
from mato_loop_backend.privacy import PseudonymizationError
from mato_loop_backend.providers.base import ProviderError
from mato_loop_backend.providers.realtime.settings import realtime_model_available
from mato_loop_backend.security import require_user
from mato_loop_backend.sessions.service import SessionServiceError
from mato_loop_backend.settings import load_effective_ai
from mato_loop_backend.voice.admission import active_connection
from mato_loop_backend.voice.delegation import tasks_for
from mato_loop_backend.voice.service import owned_chat, voice_coordinator

router = APIRouter(prefix="/sessions/{session_id}/voice")


class VoiceStartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sdp_offer: str = Field(min_length=5, max_length=65_536)
    model_id: UUID | None = None


@router.get("")
async def state(
    session_id: UUID, user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict:
    try:
        session = await owned_chat(db, session_id, user.id)
        ai = await load_effective_ai(db, user.id)
        record = await active_connection(db, session_id)
        return {
            "connectionId": str(record.id) if record else None, "status": record.status if record else "ended",
            "defaultModelId": str(ai.realtime_model_id) if ai.realtime_model_id else None,
            "models": [{"id": str(model.id), "name": model.name or model.model_id, "voice": model.realtime_voice, "provider": provider.name}
                       for model in ai.models for provider in ai.providers if realtime_model_available(ai, model, provider)],
            "tasks": await tasks_for(db, session),
        }
    except SessionServiceError as exc:
        raise ApiError(exc.status_code, exc.code) from exc


@router.post("")
async def start(
    session_id: UUID, payload: VoiceStartRequest,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict:
    try:
        return await voice_coordinator.start(db, session_id, user.id, payload.sdp_offer, payload.model_id)
    except SessionServiceError as exc:
        raise ApiError(exc.status_code, exc.code) from exc
    except ProviderError as exc:
        raise ApiError(502, exc.public_code) from exc
    except PseudonymizationError as exc:
        raise ApiError(502, str(exc)) from exc


@router.delete("/{connection_id}")
async def stop(
    session_id: UUID, connection_id: UUID,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict:
    record = await db.get(VoiceConnection, connection_id)
    if record is None or record.user_id != user.id or record.session_id != session_id:
        raise ApiError(404, "voice.notActive")
    try:
        await voice_coordinator.stop(connection_id)
    except SessionServiceError as exc:
        raise ApiError(exc.status_code, exc.code) from exc
    await db.refresh(record)
    return {"status": record.status, "errorCode": record.error_code}


@router.websocket("/{connection_id}/control")
async def control(websocket: WebSocket, session_id: UUID, connection_id: UUID) -> None:
    if not _origin_allowed(websocket, get_runtime_config().cors_origin_list):
        await websocket.close(code=1008)
        return
    login = await _browser_login(websocket)
    live = voice_coordinator.live.get(connection_id)
    if login is None or live is None or live.user_id != login.user.id or live.session_id != session_id or live.browser_attached:
        await websocket.close(code=1008)
        return
    live.browser_attached = True
    await websocket.accept()
    error_code = None
    try:
        while live.phase not in {"ended", "closing"}:
            current_login = await _browser_login(websocket)
            if current_login is None or current_login.login_id != login.login_id or current_login.expires_at <= datetime.now(UTC):
                raise SessionServiceError(401, "auth.notAuthenticated")
            async with AsyncSessionFactory() as db:
                await voice_coordinator.validate(db, live)
                session = await owned_chat(db, session_id, login.user.id)
                tasks = await tasks_for(db, session)
            await websocket.send_json({"phase": live.phase, "errorCode": live.error_code, "tasks": tasks})
            try:
                async with asyncio.timeout(2):
                    raw = await websocket.receive_text()
                if len(raw) > 1024:
                    raise SessionServiceError(422, "validation.invalid")
                if raw not in {"heartbeat", "interrupt", "end"}:
                    raise SessionServiceError(422, "validation.invalid")
                live.heartbeat = asyncio.get_running_loop().time()
                if raw == "interrupt":
                    await voice_coordinator.interrupt(live)
                elif raw == "end":
                    break
            except TimeoutError:
                pass
        if live.error_code:
            await websocket.send_json({"phase": "ended", "errorCode": live.error_code})
    except WebSocketDisconnect:
        pass
    except (SessionServiceError, ProviderError) as exc:
        error_code = exc.public_code if isinstance(exc, ProviderError) else exc.code
        await websocket.send_json({"phase": "ended", "errorCode": error_code})
    finally:
        await voice_coordinator.stop(connection_id, error_code=error_code)
        try:
            await websocket.close()
        except RuntimeError:
            pass
