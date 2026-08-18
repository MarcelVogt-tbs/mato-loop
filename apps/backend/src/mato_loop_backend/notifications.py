from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from mato_loop_backend.models import (
    Automation,
    AutomationRun,
    ChatSession,
    Notification,
    UserAccount,
)


class NotificationDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    user_id: UUID
    kind: Annotated[str, Field(min_length=3, max_length=80, pattern=r"^[a-z][A-Za-z]*(?:\.[a-z][A-Za-z]*)+$")]
    title: Annotated[str, Field(min_length=1, max_length=240)]
    body: Annotated[str | None, Field(default=None, max_length=4_000)]
    deduplication_key: Annotated[str, Field(min_length=1, max_length=255)]
    session_id: UUID | None = None
    automation_run_id: UUID | None = None

    @field_validator("title", "deduplication_key")
    @classmethod
    def normalize_required_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("value must not be blank")
        return normalized

    @field_validator("body")
    @classmethod
    def normalize_body(cls, value: str | None) -> str | None:
        return value.strip() or None if value is not None else None


class NotificationError(ValueError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class NotificationService:
    @staticmethod
    async def create(db: AsyncSession, draft: NotificationDraft) -> UUID | None:
        if draft.session_id is not None:
            owned_session = await db.scalar(
                select(ChatSession.id)
                .where(
                    ChatSession.id == draft.session_id,
                    ChatSession.user_id == draft.user_id,
                )
                .with_for_update()
            )
            if owned_session is None:
                raise NotificationError("notification.targetNotFound")
        if draft.automation_run_id is not None:
            owned_run = await db.scalar(
                select(AutomationRun.id)
                .join(Automation, Automation.id == AutomationRun.automation_id)
                .where(
                    AutomationRun.id == draft.automation_run_id,
                    Automation.user_id == draft.user_id,
                )
                .with_for_update()
            )
            if owned_run is None:
                raise NotificationError("notification.targetNotFound")
        notification_id = uuid4()
        statement = (
            insert(Notification)
            .values(id=notification_id, **draft.model_dump())
            .on_conflict_do_nothing(constraint="uq_notifications_user_deduplication_key")
            .returning(Notification.id)
        )
        return await db.scalar(statement)

    @staticmethod
    async def list_page(
        db: AsyncSession,
        user_id: UUID,
        *,
        page_size: int,
        cursor: tuple[datetime, UUID] | None = None,
    ) -> list[Notification]:
        statement = (
            select(Notification)
            .options(
                selectinload(Notification.automation_run).selectinload(
                    AutomationRun.automation
                )
            )
            .where(Notification.user_id == user_id)
        )
        if cursor is not None:
            created_at, notification_id = cursor
            statement = statement.where(
                or_(
                    Notification.created_at < created_at,
                    and_(
                        Notification.created_at == created_at,
                        Notification.id < notification_id,
                    ),
                )
            )
        return list(
            (
                await db.scalars(
                    statement.order_by(
                        Notification.created_at.desc(), Notification.id.desc()
                    ).limit(page_size + 1)
                )
            ).all()
        )

    @staticmethod
    async def unread_count(db: AsyncSession, user_id: UUID) -> int:
        return int(
            await db.scalar(
                select(func.count())
                .select_from(Notification)
                .where(Notification.user_id == user_id, Notification.read_at.is_(None))
            )
            or 0
        )

    @staticmethod
    async def mark_read(
        db: AsyncSession, user_id: UUID, notification_id: UUID
    ) -> Notification:
        await db.scalar(
            select(UserAccount.id).where(UserAccount.id == user_id).with_for_update()
        )
        item = await db.scalar(
            select(Notification)
            .options(
                selectinload(Notification.automation_run).selectinload(
                    AutomationRun.automation
                )
            )
            .where(Notification.id == notification_id, Notification.user_id == user_id)
            .with_for_update()
        )
        if item is None:
            raise NotificationError("notification.notFound")
        if item.read_at is None:
            item.read_at = datetime.now(UTC)
        return item

    @staticmethod
    async def mark_all_read(db: AsyncSession, user_id: UUID) -> int:
        await db.scalar(
            select(UserAccount.id).where(UserAccount.id == user_id).with_for_update()
        )
        result = await db.execute(
            update(Notification)
            .where(Notification.user_id == user_id, Notification.read_at.is_(None))
            .values(read_at=func.now())
        )
        return int(result.rowcount or 0)
