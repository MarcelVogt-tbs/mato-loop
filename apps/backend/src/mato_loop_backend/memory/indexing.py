from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from time import monotonic
from uuid import UUID

from sqlalchemy import or_, select, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.config import get_runtime_config
from mato_loop_backend.db.session import AsyncSessionFactory
from mato_loop_backend.memory.embeddings import (
    ACTIVE_EMBEDDING_PROFILE,
    EmbeddingError,
    embedding_client,
)
from mato_loop_backend.models import (
    MemoryEmbedding,
    MemoryEmbeddingStatus,
    MemoryRecord,
)

logger = logging.getLogger(__name__)
STALE_CLAIM_AFTER = timedelta(minutes=5)
RETRY_DELAYS = (5, 30, 120, 600, 3600)


class MemoryIndexService:
    @staticmethod
    async def enqueue(db: AsyncSession, memory: MemoryRecord) -> None:
        statement = postgresql_insert(MemoryEmbedding).values(
            memory_id=memory.id,
            profile_fingerprint=ACTIVE_EMBEDDING_PROFILE.fingerprint,
            content_hash=memory.normalized_content_hash,
            status=MemoryEmbeddingStatus.QUEUED.value,
            vector=None,
            attempts=0,
            next_attempt_at=None,
            claimed_at=None,
            error_code=None,
        )
        await db.execute(
            statement.on_conflict_do_update(
                index_elements=[MemoryEmbedding.memory_id],
                set_={
                    "profile_fingerprint": statement.excluded.profile_fingerprint,
                    "content_hash": statement.excluded.content_hash,
                    "status": MemoryEmbeddingStatus.QUEUED.value,
                    "embedding": None,
                    "attempts": 0,
                    "next_attempt_at": None,
                    "claimed_at": None,
                    "error_code": None,
                    "updated_at": datetime.now(UTC),
                },
            )
        )

    @staticmethod
    async def reconcile(db: AsyncSession) -> int:
        memories = list(
            (await db.execute(select(MemoryRecord.id, MemoryRecord.normalized_content_hash))).all()
        )
        changed = 0
        for memory_id, content_hash in memories:
            row = await db.get(MemoryEmbedding, memory_id)
            if (
                row is not None
                and row.profile_fingerprint == ACTIVE_EMBEDDING_PROFILE.fingerprint
                and row.content_hash == content_hash
            ):
                continue
            memory = MemoryRecord(id=memory_id, normalized_content_hash=content_hash)
            await MemoryIndexService.enqueue(db, memory)
            changed += 1
        await db.commit()
        return changed

    @staticmethod
    async def recover(db: AsyncSession) -> int:
        result = await db.execute(
            update(MemoryEmbedding)
            .where(
                MemoryEmbedding.status == MemoryEmbeddingStatus.RUNNING.value,
                or_(
                    MemoryEmbedding.claimed_at.is_(None),
                    MemoryEmbedding.claimed_at <= datetime.now(UTC) - STALE_CLAIM_AFTER,
                ),
            )
            .values(
                status=MemoryEmbeddingStatus.QUEUED.value,
                vector=None,
                claimed_at=None,
                next_attempt_at=None,
                error_code="memory.embeddingInterrupted",
                updated_at=datetime.now(UTC),
            )
        )
        await db.commit()
        return int(result.rowcount or 0)

    @staticmethod
    async def process_next(db: AsyncSession) -> bool:
        config = get_runtime_config()
        if not config.embedding_enabled:
            return False
        now = datetime.now(UTC)
        rows = list(
            (
                await db.scalars(
                    select(MemoryEmbedding)
                    .where(
                        MemoryEmbedding.status == MemoryEmbeddingStatus.QUEUED.value,
                        or_(
                            MemoryEmbedding.next_attempt_at.is_(None),
                            MemoryEmbedding.next_attempt_at <= now,
                        ),
                    )
                    .order_by(MemoryEmbedding.next_attempt_at, MemoryEmbedding.updated_at)
                    .with_for_update(skip_locked=True)
                    .limit(config.embedding_batch_size)
                )
            ).all()
        )
        if not rows:
            await db.rollback()
            return False

        snapshots = [
            (row.memory_id, row.content_hash, row.profile_fingerprint) for row in rows
        ]
        for row in rows:
            row.status = MemoryEmbeddingStatus.RUNNING.value
            row.vector = None
            row.claimed_at = now
            row.next_attempt_at = None
            row.error_code = None
            row.attempts += 1
        await db.commit()

        memories = {
            item.id: item
            for item in (
                await db.scalars(
                    select(MemoryRecord).where(
                        MemoryRecord.id.in_([memory_id for memory_id, _, _ in snapshots])
                    )
                )
            ).all()
        }
        valid = []
        for snapshot in snapshots:
            memory = memories.get(snapshot[0])
            if (
                memory is not None
                and memory.normalized_content_hash == snapshot[1]
                and snapshot[2] == ACTIVE_EMBEDDING_PROFILE.fingerprint
            ):
                valid.append(snapshot)
            elif memory is not None:
                await MemoryIndexService.enqueue(db, memory)
        await db.commit()
        if not valid:
            return True

        try:
            vectors = await embedding_client.embed_documents(
                [memories[memory_id].content for memory_id, _, _ in valid]
            )
        except EmbeddingError as exc:
            await db.rollback()
            await MemoryIndexService._record_failure(db, valid, exc)
            return True

        for (memory_id, content_hash, profile), vector in zip(valid, vectors, strict=True):
            row = await db.get(MemoryEmbedding, memory_id, with_for_update=True)
            memory = await db.get(MemoryRecord, memory_id)
            if row is None or memory is None:
                continue
            if (
                row.content_hash != content_hash
                or row.profile_fingerprint != profile
                or memory.normalized_content_hash != content_hash
            ):
                await MemoryIndexService.enqueue(db, memory)
                continue
            row.status = MemoryEmbeddingStatus.READY.value
            row.vector = vector
            row.claimed_at = None
            row.next_attempt_at = None
            row.error_code = None
            row.updated_at = datetime.now(UTC)
        await db.commit()
        return True

    @staticmethod
    async def _record_failure(
        db: AsyncSession,
        snapshots: list[tuple[UUID, str, str]],
        error: EmbeddingError,
    ) -> None:
        now = datetime.now(UTC)
        for memory_id, content_hash, profile in snapshots:
            row = await db.get(MemoryEmbedding, memory_id, with_for_update=True)
            if (
                row is None
                or row.content_hash != content_hash
                or row.profile_fingerprint != profile
            ):
                continue
            row.vector = None
            row.claimed_at = None
            row.error_code = error.code
            row.updated_at = now
            if error.transient:
                delay = RETRY_DELAYS[min(max(row.attempts - 1, 0), len(RETRY_DELAYS) - 1)]
                row.status = MemoryEmbeddingStatus.QUEUED.value
                row.next_attempt_at = now + timedelta(seconds=delay)
            else:
                row.status = MemoryEmbeddingStatus.FAILED.value
                row.next_attempt_at = None
        await db.commit()


class MemoryIndexScheduler:
    _task: asyncio.Task[None] | None = None

    @classmethod
    def start(cls) -> None:
        if cls._task is None or cls._task.done():
            cls._task = asyncio.create_task(cls._loop())

    @classmethod
    async def stop(cls) -> None:
        if cls._task is None:
            return
        cls._task.cancel()
        await asyncio.gather(cls._task, return_exceptions=True)
        cls._task = None

    @classmethod
    async def _loop(cls) -> None:
        last_reconcile = 0.0
        while True:
            try:
                async with AsyncSessionFactory() as db:
                    if monotonic() - last_reconcile >= 300:
                        await MemoryIndexService.recover(db)
                        await MemoryIndexService.reconcile(db)
                        last_reconcile = monotonic()
                    while await MemoryIndexService.process_next(db):
                        pass
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Memory embedding scheduler iteration failed")
            await asyncio.sleep(5)
