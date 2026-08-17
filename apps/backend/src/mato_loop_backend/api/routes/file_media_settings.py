from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.db.session import get_db_session
from mato_loop_backend.models import UserAccount
from mato_loop_backend.security import require_admin, require_user
from mato_loop_backend.settings import FileMediaSettings, SettingsStore

router = APIRouter(prefix="/file-media-settings")


def _payload(settings: FileMediaSettings) -> dict:
    return settings.model_dump(mode="json", by_alias=True)


@router.get("")
async def get_file_media_settings(
    _user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict:
    return _payload(await SettingsStore.load(db, FileMediaSettings))


@router.put("")
async def update_file_media_settings(
    payload: FileMediaSettings,
    _admin: Annotated[UserAccount, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict:
    settings = await SettingsStore.load(db, FileMediaSettings, for_update=True)
    settings = settings.model_copy(update=payload.model_dump())
    await SettingsStore.save(db, settings)
    await db.commit()
    return _payload(settings)
