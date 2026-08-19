from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.api.errors import ApiError
from mato_loop_backend.attachments import (
    AttachmentError,
    processing_limits,
    validate_audio,
)
from mato_loop_backend.db.session import get_db_session
from mato_loop_backend.models import UserAccount
from mato_loop_backend.providers.base import ProviderError
from mato_loop_backend.providers.media import MediaService
from mato_loop_backend.security import require_user
from mato_loop_backend.settings import (
    InstanceLimits,
    SettingsStore,
    ToolExecutionPolicy,
    load_effective_tools,
)

router = APIRouter(prefix="/media", dependencies=[Depends(require_user)])


def _provider_api_error(exc: ProviderError) -> ApiError:
    code = exc.public_code
    if code in {
        "provider.modelCapabilityUnavailable",
        "provider.configurationInvalid",
    }:
        return ApiError(409, code, params=exc.params)
    if code == "provider.rateLimited":
        return ApiError(429, code, params=exc.params)
    if code == "provider.timeout":
        return ApiError(504, code, params=exc.params)
    return ApiError(502, code, params=exc.params)


@router.post("/transcriptions")
async def create_transcription(
    audio: Annotated[UploadFile, File()],
    db: Annotated[AsyncSession, Depends(get_db_session)],
    user: Annotated[UserAccount, Depends(require_user)],
    language: Annotated[str | None, Form(max_length=35)] = None,
) -> dict[str, str]:
    settings = await load_effective_tools(db, user.id)
    instance_limits = await SettingsStore.load(db, InstanceLimits)
    if settings.transcribe_audio.policy == ToolExecutionPolicy.DISABLED:
        await audio.close()
        raise ApiError(409, "tool.unavailable")
    try:
        data = await audio.read(instance_limits.max_upload_bytes + 1)
    finally:
        await audio.close()
    mime_type = (audio.content_type or "").split(";", 1)[0].strip().lower()
    try:
        validate_audio(
            data,
            mime_type,
            max_bytes=instance_limits.max_upload_bytes,
            limits=processing_limits(instance_limits),
        )
    except AttachmentError as exc:
        raise ApiError(413 if exc.code == "attachment.tooLarge" else 400, exc.code) from exc
    try:
        result = await MediaService.transcribe_audio(
            db,
            audio=data,
            language=language,
            timeout=settings.transcribe_audio.timeout_seconds,
            user_id=user.id,
        )
    except ProviderError as exc:
        raise _provider_api_error(exc) from exc
    return {"text": result.text}
