from __future__ import annotations

import asyncio
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from mato_loop_backend.agent.runtime import AgentRuntime
from mato_loop_backend.files.storage import SessionStorageError
from mato_loop_backend.models import (
    AgentRun,
    ChatSession,
    Message,
    TitleGenerationStatus,
    UserAccount,
)
from mato_loop_backend.sessions.cleanup import deleting_child_sessions

_ACTIVE_RUN_STATUSES = ("queued", "running", "waiting_approval", "waiting_input")


class ChatHistoryError(RuntimeError):
    def __init__(self, code: str, status_code: int) -> None:
        super().__init__(code)
        self.code = code
        self.status_code = status_code


@dataclass(frozen=True, slots=True)
class UndoUserMessageResult:
    content: str


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
        if session.parent_session_id is not None:
            raise ChatHistoryError("session.readOnly", 403)
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
                AgentRun.parent_run_id.is_(None),
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
            await AgentRuntime.cancel_descendants(session_id)
            session = await cls._owned_session(db, session_id, user_id)
            if await cls._active_run(db, session_id) is None:
                return session
            await db.rollback()
        raise ChatHistoryError("session.runInactive", 409)

    @classmethod
    async def _delete_message_suffix(
        cls,
        db: AsyncSession,
        messages: list[Message],
        boundary_index: int,
        *,
        include_boundary: bool,
    ) -> None:
        start = boundary_index if include_boundary else boundary_index + 1
        removed = messages[start:]
        if not removed:
            return
        await db.execute(
            delete(Message)
            .where(Message.id.in_([message.id for message in removed]))
            .execution_options(synchronize_session=False)
        )

    @classmethod
    async def _commit(cls, db: AsyncSession) -> None:
        async def commit() -> None:
            await db.commit()

        task = asyncio.create_task(commit())
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            await task
            raise
        except BaseException:
            await db.rollback()
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
        run_ids = None if target_index == 0 else list(
            (await db.scalars(
                select(AgentRun.id).where(
                    AgentRun.session_id == session_id,
                    AgentRun.input_message_id.in_([message.id for message in messages[target_index:]]),
                )
            )).all()
        )
        try:
            async with deleting_child_sessions(db, session_id, spawned_by_run_ids=run_ids):
                await cls._delete_message_suffix(
                    db, messages, target_index, include_boundary=True
                )
                if target_index == 0 and session.title_generation_status != TitleGenerationStatus.SKIPPED.value:
                    session.title = "Neue Session"
                    session.title_generation_status = TitleGenerationStatus.PENDING.value
                await cls._commit(db)
        except (OSError, SessionStorageError) as exc:
            raise ChatHistoryError("session.storageUnavailable", 500) from exc
        return UndoUserMessageResult(content)

    @classmethod
    async def retry_failed_message(
        cls,
        db: AsyncSession,
        *,
        session_id: UUID,
        user_id: UUID,
        message_id: UUID,
    ) -> RetryFailedMessageResult:
        await cls._owned_session(db, session_id, user_id, for_update=False)
        if await cls._active_run(db, session_id) is not None:
            raise ChatHistoryError("session.runAlreadyActive", 409)
        await db.rollback()
        await AgentRuntime.cancel_descendants(session_id)
        await db.execute(select(UserAccount.id).where(UserAccount.id == user_id).with_for_update(key_share=True))
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
        before_execution = (messages[-1].provider_data or {}).get("_matoloop", {}).get("beforeExecution") is True

        try:
            async with deleting_child_sessions(
                db, session_id, spawned_by_run_ids=[failed_run.id]
            ):
                await cls._delete_message_suffix(
                    db, messages, user_index, include_boundary=False
                )
                run = await AgentRuntime.retry(db, session, failed_run, before_execution=before_execution)
        except (OSError, SessionStorageError) as exc:
            raise ChatHistoryError("session.storageUnavailable", 500) from exc
        except BaseException:
            await db.rollback()
            raise
        return RetryFailedMessageResult(run)
