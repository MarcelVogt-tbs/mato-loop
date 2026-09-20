from __future__ import annotations

from typing import TypeVar
from uuid import UUID

from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.models.setting import SettingRecord
from mato_loop_backend.settings.models import SettingsDocument, SettingsScope

SettingsType = TypeVar("SettingsType", bound=SettingsDocument)


class SettingsStore:
    @staticmethod
    async def load(
        session: AsyncSession,
        settings_type: type[SettingsType],
        *,
        user_id: UUID | None = None,
        for_update: bool = False,
    ) -> SettingsType:
        SettingsStore._validate_scope(settings_type, user_id)
        if for_update:
            await SettingsStore._lock_scope(
                session, settings_type.settings_key, user_id
            )
        statement = (
            select(SettingRecord)
            .where(
                SettingRecord.key == settings_type.settings_key,
                SettingRecord.user_id == user_id,
            )
            .execution_options(populate_existing=True)
        )
        if for_update:
            statement = statement.with_for_update()
        record = await session.scalar(
            statement
        )
        if record is None:
            return settings_type()
        return settings_type.model_validate(record.data)

    @staticmethod
    async def load_optional(
        session: AsyncSession,
        settings_type: type[SettingsType],
        *,
        user_id: UUID | None = None,
        for_update: bool = False,
    ) -> SettingsType | None:
        SettingsStore._validate_scope(settings_type, user_id)
        if for_update:
            await SettingsStore._lock_scope(session, settings_type.settings_key, user_id)
        statement = (
            select(SettingRecord)
            .where(
                SettingRecord.key == settings_type.settings_key,
                SettingRecord.user_id == user_id,
            )
            .execution_options(populate_existing=True)
        )
        if for_update:
            statement = statement.with_for_update()
        record = await session.scalar(statement)
        return settings_type.model_validate(record.data) if record is not None else None

    @staticmethod
    async def save(
        session: AsyncSession,
        settings: SettingsDocument,
        *,
        user_id: UUID | None = None,
    ) -> SettingRecord:
        SettingsStore._validate_scope(type(settings), user_id)
        await SettingsStore._lock_scope(session, settings.settings_key, user_id)
        record = await session.scalar(
            select(SettingRecord).where(
                SettingRecord.key == settings.settings_key,
                SettingRecord.user_id == user_id,
            )
        )
        payload = settings.model_dump(mode="json")
        if record is None:
            record = SettingRecord(
                key=settings.settings_key, user_id=user_id, data=payload
            )
            session.add(record)
        else:
            record.data = payload
        await session.flush()
        return record

    @staticmethod
    async def delete(
        session: AsyncSession,
        settings_type: type[SettingsDocument],
        *,
        user_id: UUID | None = None,
    ) -> None:
        SettingsStore._validate_scope(settings_type, user_id)
        await SettingsStore._lock_scope(session, settings_type.settings_key, user_id)
        await session.execute(
            delete(SettingRecord).where(
                SettingRecord.key == settings_type.settings_key,
                SettingRecord.user_id == user_id,
            )
        )

    @staticmethod
    async def list_user_scopes(
        session: AsyncSession, settings_type: type[SettingsDocument]
    ) -> list[UUID]:
        if settings_type.settings_scope != SettingsScope.USER:
            raise ValueError(f"{settings_type.settings_key} is not user-scoped")
        result = await session.scalars(
            select(SettingRecord.user_id)
            .where(
                SettingRecord.key == settings_type.settings_key,
                SettingRecord.user_id.is_not(None),
            )
            .order_by(SettingRecord.user_id)
        )
        return list(result.all())

    @staticmethod
    def _validate_scope(
        settings_type: type[SettingsDocument], user_id: UUID | None
    ) -> None:
        if settings_type.settings_scope == SettingsScope.USER and user_id is None:
            raise ValueError(f"{settings_type.settings_key} requires a user_id")
        if settings_type.settings_scope == SettingsScope.GLOBAL and user_id is not None:
            raise ValueError(f"{settings_type.settings_key} does not accept a user_id")

    @staticmethod
    async def _lock_scope(
        session: AsyncSession, settings_key: str, user_id: UUID | None
    ) -> None:
        scope = f"{settings_key}:{user_id or 'global'}"
        await session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:scope, 0))"),
            {"scope": scope},
        )
