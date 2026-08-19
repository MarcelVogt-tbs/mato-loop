from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.db.session import get_db_session
from mato_loop_backend.models import UserAccount
from mato_loop_backend.security import require_admin, require_user
from mato_loop_backend.settings import InstanceLimits, SettingsStore

router = APIRouter(prefix="/instance-limits")


def _payload(settings: InstanceLimits) -> dict:
    return settings.model_dump(mode="json", by_alias=True)


@router.get("")
async def get_instance_limits(
    _user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict:
    return _payload(await SettingsStore.load(db, InstanceLimits))


@router.put("")
async def update_instance_limits(
    payload: InstanceLimits,
    _admin: Annotated[UserAccount, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict:
    settings = await SettingsStore.load(db, InstanceLimits, for_update=True)
    settings = settings.model_copy(update=payload.model_dump())
    await SettingsStore.save(db, settings)
    await db.commit()
    return _payload(settings)
