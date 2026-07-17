from __future__ import annotations

from typing import TypeVar

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.models.setting import SettingRecord
from mato_loop_backend.settings.models import SettingsDocument

SettingsType = TypeVar("SettingsType", bound=SettingsDocument)


class SettingsStore:
    @staticmethod
    async def load(session: AsyncSession, settings_type: type[SettingsType]) -> SettingsType:
        record = await session.scalar(
            select(SettingRecord).where(SettingRecord.key == settings_type.settings_key)
        )
        if record is None:
            return settings_type()
        return settings_type.model_validate(record.data)

    @staticmethod
    async def save(session: AsyncSession, settings: SettingsDocument) -> SettingRecord:
        record = await session.scalar(
            select(SettingRecord).where(SettingRecord.key == settings.settings_key)
        )
        payload = settings.model_dump(mode="json")
        if record is None:
            record = SettingRecord(key=settings.settings_key, data=payload)
            session.add(record)
        else:
            record.data = payload
        await session.flush()
        return record
