from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from mato_loop_backend.agent.runtime import AgentRuntime
from mato_loop_backend.attachments import AttachmentService
from mato_loop_backend.models import (
    AgentRun,
    ChatSession,
    Message,
    MessageAttachment,
)
from mato_loop_backend.session_storage import SessionStorage, SessionStorageError

logger = logging.getLogger(__name__)
_ACTIVE_RUN_STATUSES = ("queued", "running", "waiting_approval", "waiting_input")


class ChatHistoryError(RuntimeError):
    def __init__(self, code: str, status_code: int) -> None:
        super().__init__(code)
        self.code = code
        self.status_code = status_code


@dataclass(frozen=True, slots=True)
class UndoUserMessageResult:
    content: str
    session_deleted: bool


@dataclass(frozen=True, slots=True)
class RetryFailedMessageResult:
    run: AgentRun


class ChatHistoryService:
    @staticmethod
    async def _owned_session(
        db: AsyncSession,
        session_id: UUID,
        user_id: UUID,
        *,
        for_update: bool = True,
    ) -> ChatSession:
        statement = select(ChatSession).where(
            ChatSession.id == session_id, ChatSession.user_id == user_id
        )
        if for_update:
            statement = statement.with_for_update()
        session = await db.scalar(statement)
        if session is None:
            raise ChatHistoryError("session.notFound", 404)
        return session

    @staticmethod
    async def _messages(db: AsyncSession, session_id: UUID) -> list[Message]:
        return list(
            (
                await db.scalars(
                    select(Message)
                    .where(Message.session_id == session_id)
                    .options(selectinload(Message.attachments))
                    .order_by(Message.created_at, Message.id)
                )
            ).all()
        )

    @staticmethod
    async def _active_run(db: AsyncSession, session_id: UUID) -> AgentRun | None:
        return await db.scalar(
            select(AgentRun)
            .where(
                AgentRun.session_id == session_id,
                AgentRun.status.in_(_ACTIVE_RUN_STATUSES),
            )
            .order_by(AgentRun.created_at.desc(), AgentRun.id.desc())
            .execution_options(populate_existing=True)
        )

    @staticmethod
    async def retry_source(
        db: AsyncSession,
        *,
        session_id: UUID,
        messages: list[Message],
        message_id: UUID,
    ) -> tuple[AgentRun, int] | None:
        if not messages or messages[-1].id != message_id:
            return None
        target = messages[-1]
        if target.role != "assistant" or not target.error_code or target.error_run_id is None:
            return None
        failed_run = await db.get(AgentRun, target.error_run_id)
        if (
            failed_run is None
            or failed_run.session_id != session_id
            or failed_run.status != "failed"
        ):
            return None
        user_index = next(
            (index for index in range(len(messages) - 2, -1, -1) if messages[index].role == "user"),
            None,
        )
        return (failed_run, user_index) if user_index is not None else None

    @classmethod
    async def _lock_after_cancelling_active_run(
        cls, db: AsyncSession, session_id: UUID, user_id: UUID
    ) -> ChatSession:
        for _attempt in range(3):
            await cls._owned_session(db, session_id, user_id, for_update=False)
            active_run = await cls._active_run(db, session_id)
            if active_run is not None:
                run_id = active_run.id
                await db.rollback()
                await AgentRuntime.cancel_and_wait(run_id)
                continue

            await db.rollback()
            session = await cls._owned_session(db, session_id, user_id)
            if await cls._active_run(db, session_id) is None:
                return session
            await db.rollback()
        raise ChatHistoryError("session.runInactive", 409)

    @staticmethod
    async def _discard_attachments(
        attachments: list[MessageAttachment], session_id: UUID
    ) -> None:
        service = AttachmentService()
        for attachment in attachments:
            try:
                await service.discard(attachment, session_id)
            except BaseException:
                logger.exception("Could not discard attachment %s after history update", attachment.id)

    @classmethod
    async def _delete_message_suffix(
        cls,
        db: AsyncSession,
        messages: list[Message],
        boundary_index: int,
        *,
        include_boundary: bool,
    ) -> list[MessageAttachment]:
        start = boundary_index if include_boundary else boundary_index + 1
        removed = messages[start:]
        if not removed:
            return []
        attachments = [attachment for message in removed for attachment in message.attachments]
        await db.execute(
            delete(Message)
            .where(Message.id.in_([message.id for message in removed]))
            .execution_options(synchronize_session=False)
        )
        return attachments

    @classmethod
    async def _commit_and_discard(
        cls,
        db: AsyncSession,
        attachments: list[MessageAttachment],
        session_id: UUID,
    ) -> None:
        async def commit_and_discard() -> None:
            await db.commit()
            await cls._discard_attachments(attachments, session_id)

        task = asyncio.create_task(commit_and_discard())
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            await task
            raise
        except BaseException:
            await db.rollback()
            raise

    @staticmethod
    async def _delete_session(db: AsyncSession, session: ChatSession) -> None:
        storage = SessionStorage()
        trash_task = asyncio.create_task(
            asyncio.to_thread(storage.trash_session, session.id, session.project_id)
        )
        try:
            trashed = await asyncio.shield(trash_task)
        except asyncio.CancelledError:
            try:
                trashed = await trash_task
            except (OSError, SessionStorageError):
                raise
            if trashed is not None:
                try:
                    await asyncio.to_thread(
                        storage.restore_session,
                        session.id,
                        trashed,
                        session.project_id,
                    )
                except BaseException:
                    logger.exception("Could not restore session storage after cancelled undo")
            raise
        except (OSError, SessionStorageError) as exc:
            raise ChatHistoryError("session.storageUnavailable", 500) from exc

        async def restore() -> None:
            if trashed is None:
                return
            try:
                await asyncio.to_thread(
                    storage.restore_session,
                    session.id,
                    trashed,
                    session.project_id,
                )
            except BaseException:
                logger.exception("Could not restore session storage after undo failure")

        async def commit_and_purge() -> None:
            await db.delete(session)
            await db.commit()
            try:
                await asyncio.to_thread(storage.purge_trashed, trashed)
            except BaseException:
                logger.exception("Could not purge session storage after undo")

        task = asyncio.create_task(commit_and_purge())
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            try:
                await task
            except BaseException:
                await db.rollback()
                await restore()
            raise
        except BaseException:
            await db.rollback()
            await restore()
            raise

    @classmethod
    async def undo_user_message(
        cls,
        db: AsyncSession,
        *,
        session_id: UUID,
        user_id: UUID,
        message_id: UUID,
    ) -> UndoUserMessageResult:
        session = await cls._lock_after_cancelling_active_run(db, session_id, user_id)
        messages = await cls._messages(db, session_id)
        target = next((message for message in messages if message.id == message_id), None)
        if target is None or target.role != "user":
            raise ChatHistoryError("session.messageNotFound", 404)

        target_index = messages.index(target)
        content = target.content
        if target_index == 0:
            await cls._delete_session(db, session)
            return UndoUserMessageResult(content, True)

        attachments = await cls._delete_message_suffix(
            db, messages, target_index, include_boundary=True
        )
        await cls._commit_and_discard(db, attachments, session_id)
        return UndoUserMessageResult(content, False)

    @classmethod
    async def retry_failed_message(
        cls,
        db: AsyncSession,
        *,
        session_id: UUID,
        user_id: UUID,
        message_id: UUID,
    ) -> RetryFailedMessageResult:
        session = await cls._owned_session(db, session_id, user_id)
        if await cls._active_run(db, session_id) is not None:
            raise ChatHistoryError("session.runAlreadyActive", 409)
        messages = await cls._messages(db, session_id)
        source = await cls.retry_source(
            db,
            session_id=session_id,
            messages=messages,
            message_id=message_id,
        )
        if source is None:
            raise ChatHistoryError("session.retryUnavailable", 409)
        failed_run, user_index = source

        attachments = await cls._delete_message_suffix(
            db, messages, user_index, include_boundary=False
        )
        try:
            run = await AgentRuntime.retry(db, session, failed_run)
        except BaseException:
            await db.rollback()
            raise
        await cls._discard_attachments(attachments, session_id)
        return RetryFailedMessageResult(run)
