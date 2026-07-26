from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import and_, delete, func, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from mato_loop_backend.memory.indexing import MemoryIndexService
from mato_loop_backend.models import (
    AgentRun,
    ChatSession,
    MemoryCategory,
    MemoryEmbedding,
    MemoryExposure,
    MemoryExposureStatus,
    MemoryRecord,
    MemoryRetrievalLevel,
    MemorySourceMessage,
    MemorySourceType,
)
from mato_loop_backend.settings import MemorySettings, TrustLevel


def normalize_content(content: str) -> tuple[str, str]:
    normalized = re.sub(r"\s+", " ", content.strip()).casefold()
    return normalized, hashlib.sha256(normalized.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class MemoryList:
    items: list[MemoryRecord]
    total: int
    offset: int
    limit: int


@dataclass(frozen=True, slots=True)
class MemoryMutation:
    item: MemoryRecord | None
    created: bool = False
    updated: bool = False
    duplicate: bool = False
    conflict: bool = False


@dataclass(frozen=True, slots=True)
class MemoryScope:
    total: int
    instant: int
    retrieve: int


class MemoryDuplicateError(Exception):
    pass


class MemoryTierBudgetError(Exception):
    pass


class MemoryService:
    @staticmethod
    async def lock_capacity(db: AsyncSession) -> None:
        await db.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:scope, 0))"),
            {"scope": "memory:active-context-capacity"},
        )

    @staticmethod
    async def create_manual(
        db: AsyncSession,
        *,
        content: str,
        category: MemoryCategory,
        trust_level: TrustLevel,
        max_context_characters: int,
    ) -> MemoryRecord:
        await MemoryService.lock_capacity(db)
        _, content_hash = normalize_content(content)
        if await MemoryService._duplicate_by_hash(db, content_hash) is not None:
            raise MemoryDuplicateError
        try:
            async with db.begin_nested():
                item = await MemoryService._create(
                    db,
                    content=content,
                    category=category,
                    source_type=MemorySourceType.MANUAL,
                    trust_level=trust_level,
                )
                await MemoryService.enforce_capacity(db, max_context_characters)
                return item
        except IntegrityError as exc:
            if await MemoryService._duplicate_by_hash(db, content_hash) is None:
                raise
            raise MemoryDuplicateError from exc

    @staticmethod
    async def create_tool(
        db: AsyncSession,
        *,
        content: str,
        category: MemoryCategory,
        trust_level: TrustLevel,
        session_id: UUID,
        message_id: UUID,
        run_id: UUID,
        tool_call_id: str,
        max_context_characters: int,
    ) -> MemoryMutation:
        await MemoryService.lock_capacity(db)
        existing = await db.scalar(
            select(MemoryRecord).where(
                MemoryRecord.source_type == MemorySourceType.TOOL_CALL.value,
                MemoryRecord.source_run_id == run_id,
                MemoryRecord.source_tool_call_id == tool_call_id,
            )
        )
        if existing is not None:
            return MemoryMutation(existing, created=True)
        _, content_hash = normalize_content(content)
        duplicate = await MemoryService._duplicate_by_hash(db, content_hash)
        if duplicate is not None:
            return MemoryMutation(duplicate, duplicate=True)
        try:
            async with db.begin_nested():
                item = await MemoryService._create(
                        db,
                        content=content,
                        category=category,
                        source_type=MemorySourceType.TOOL_CALL,
                        trust_level=trust_level,
                        session_id=session_id,
                        message_id=message_id,
                        run_id=run_id,
                        tool_call_id=tool_call_id,
                        evidence_message_ids=[message_id],
                    )
                await MemoryService.enforce_capacity(db, max_context_characters)
                return MemoryMutation(item, created=True)
        except IntegrityError:
            replay = await db.scalar(
                select(MemoryRecord).where(
                    MemoryRecord.source_type == MemorySourceType.TOOL_CALL.value,
                    MemoryRecord.source_run_id == run_id,
                    MemoryRecord.source_tool_call_id == tool_call_id,
                )
            )
            if replay is not None:
                return MemoryMutation(replay, created=True)
            duplicate = await MemoryService._duplicate_by_hash(db, content_hash)
            if duplicate is None:
                raise
            return MemoryMutation(duplicate, duplicate=True)

    @staticmethod
    async def update_tool(
        db: AsyncSession,
        *,
        memory_id: UUID,
        expected_revision: int,
        content: str,
        category: MemoryCategory,
        readable_by: TrustLevel,
        write_trust_level: TrustLevel,
        session_id: UUID,
        evidence_message_id: UUID,
        run_id: UUID,
        tool_call_id: str,
        max_context_characters: int,
    ) -> MemoryMutation:
        await MemoryService.lock_capacity(db)
        item = await MemoryService._get_for_automatic_update(
            db, memory_id, readable_by=readable_by
        )
        if item is None:
            return MemoryMutation(None)
        if item.revision != expected_revision:
            return MemoryMutation(item, conflict=True)
        target_trust = (
            TrustLevel.PRIVATE
            if write_trust_level == TrustLevel.PRIVATE
            else TrustLevel(item.trust_level)
        )
        try:
            await MemoryService.update(
                db,
                item,
                content=content,
                category=category,
                trust_level=target_trust,
                evidence_message_ids=[evidence_message_id],
                source_type=MemorySourceType.TOOL_CALL,
                source_session_id=session_id,
                source_message_id=evidence_message_id,
                source_run_id=run_id,
                source_tool_call_id=tool_call_id,
                max_context_characters=max_context_characters,
            )
        except MemoryDuplicateError:
            _, content_hash = normalize_content(content)
            duplicate = await MemoryService._duplicate_by_hash(
                db, content_hash, exclude_id=item.id, for_update=True
            )
            if duplicate is None:
                raise
            source_evidence = list(
                (
                    await db.scalars(
                        select(MemorySourceMessage.message_id).where(
                            MemorySourceMessage.memory_id == item.id
                        )
                    )
                ).all()
            )
            if item.source_message_id is not None:
                source_evidence.append(item.source_message_id)
            source_evidence.append(evidence_message_id)
            merged_trust = (
                TrustLevel.PRIVATE
                if TrustLevel.PRIVATE.value
                in {item.trust_level, duplicate.trust_level, target_trust.value}
                else TrustLevel.PUBLIC
            )
            await MemoryService._merge_automatic_metadata(
                db,
                duplicate,
                trust_level=merged_trust,
                evidence_message_ids=source_evidence,
            )
            await db.execute(
                delete(MemoryExposure).where(MemoryExposure.memory_id == duplicate.id)
            )
            duplicate.last_used_at = None
            duplicate.source_type = MemorySourceType.TOOL_CALL.value
            duplicate.source_session_id = session_id
            duplicate.source_message_id = evidence_message_id
            duplicate.source_run_id = run_id
            duplicate.source_dream_run_id = None
            duplicate.source_tool_call_id = tool_call_id
            if not duplicate.tier_locked:
                await MemoryService._change_retrieval_level(
                    db, duplicate, MemoryRetrievalLevel.INSTANT
                )
            await db.delete(item)
            await MemoryService.enforce_capacity(db, max_context_characters)
            await db.flush()
            return MemoryMutation(duplicate, updated=True)
        return MemoryMutation(item, updated=True)

    @staticmethod
    async def create_dreaming(
        db: AsyncSession,
        *,
        content: str,
        category: MemoryCategory,
        trust_level: TrustLevel,
        session_id: UUID,
        run_id: UUID,
        evidence_message_ids: list[UUID],
        max_context_characters: int,
    ) -> MemoryMutation:
        await MemoryService.lock_capacity(db)
        _, content_hash = normalize_content(content)
        existing = await MemoryService._duplicate_by_hash(
            db, content_hash, for_update=True
        )
        if existing is not None:
            await MemoryService._merge_automatic_metadata(
                db,
                existing,
                trust_level=trust_level,
                evidence_message_ids=evidence_message_ids,
            )
            return MemoryMutation(existing, duplicate=True)
        try:
            async with db.begin_nested():
                item = await MemoryService._create(
                        db,
                        content=content,
                        category=category,
                        source_type=MemorySourceType.DREAMING,
                        trust_level=trust_level,
                        session_id=session_id,
                        dream_run_id=run_id,
                        evidence_message_ids=evidence_message_ids,
                    )
                await MemoryService.enforce_capacity(db, max_context_characters)
                return MemoryMutation(item, created=True)
        except IntegrityError:
            duplicate = await MemoryService._duplicate_by_hash(
                db, content_hash, for_update=True
            )
            if duplicate is None:
                raise
            await MemoryService._merge_automatic_metadata(
                db,
                duplicate,
                trust_level=trust_level,
                evidence_message_ids=evidence_message_ids,
            )
            return MemoryMutation(duplicate, duplicate=True)

    @staticmethod
    async def update_dreaming(
        db: AsyncSession,
        *,
        memory_id: UUID,
        expected_revision: int,
        content: str,
        category: MemoryCategory,
        trust_level: TrustLevel,
        session_id: UUID,
        dream_run_id: UUID,
        evidence_message_ids: list[UUID],
        max_context_characters: int,
    ) -> MemoryMutation:
        await MemoryService.lock_capacity(db)
        item = await MemoryService._get_for_automatic_update(
            db, memory_id, readable_by=trust_level
        )
        if item is None:
            return MemoryMutation(None)
        if item.revision != expected_revision:
            return MemoryMutation(item, conflict=True)
        target_trust = (
            TrustLevel.PRIVATE
            if trust_level == TrustLevel.PRIVATE
            else TrustLevel(item.trust_level)
        )
        try:
            await MemoryService.update(
                db,
                item,
                content=content,
                category=category,
                trust_level=target_trust,
                evidence_message_ids=evidence_message_ids,
                source_type=MemorySourceType.DREAMING,
                source_session_id=session_id,
                source_message_id=(evidence_message_ids[0] if evidence_message_ids else None),
                source_dream_run_id=dream_run_id,
                max_context_characters=max_context_characters,
            )
        except MemoryDuplicateError:
            return MemoryMutation(item, duplicate=True)
        return MemoryMutation(item, updated=True)

    @staticmethod
    async def keep_dreaming(
        db: AsyncSession,
        *,
        memory_id: UUID,
        expected_revision: int,
        trust_level: TrustLevel,
        evidence_message_ids: list[UUID],
    ) -> MemoryMutation:
        await MemoryService.lock_capacity(db)
        item = await MemoryService._get_for_automatic_update(
            db, memory_id, readable_by=trust_level
        )
        if item is None:
            return MemoryMutation(None)
        if item.revision != expected_revision:
            return MemoryMutation(item, conflict=True)
        await MemoryService._merge_automatic_metadata(
            db,
            item,
            trust_level=trust_level,
            evidence_message_ids=evidence_message_ids,
        )
        return MemoryMutation(item)

    @staticmethod
    async def reconciliation_candidates(
        db: AsyncSession,
        *,
        candidates: list[tuple[str, MemoryCategory]],
        readable_by: TrustLevel,
        max_items: int = 50,
        max_characters: int = 40_000,
    ) -> list[MemoryRecord]:
        trust_filters = []
        if readable_by == TrustLevel.PUBLIC:
            trust_filters.append(MemoryRecord.trust_level == TrustLevel.PUBLIC.value)
        selected: dict[UUID, MemoryRecord] = {}
        for content, category in candidates:
            _, content_hash = normalize_content(content)
            exact = await db.scalar(
                select(MemoryRecord).where(
                    MemoryRecord.normalized_content_hash == content_hash,
                    *trust_filters,
                ).options(selectinload(MemoryRecord.source_session))
            )
            if exact is not None:
                selected[exact.id] = exact

            terms = list(dict.fromkeys(re.findall(r"\w+", content.casefold())))[:20]
            if terms:
                tsquery = func.to_tsquery("simple", " | ".join(terms))
                lexical = list(
                    (
                        await db.scalars(
                            select(MemoryRecord)
                            .options(selectinload(MemoryRecord.source_session))
                            .where(
                                MemoryRecord.search_vector.op("@@")(tsquery),
                                *trust_filters,
                            )
                            .order_by(
                                func.ts_rank_cd(MemoryRecord.search_vector, tsquery).desc(),
                                MemoryRecord.updated_at.desc(),
                            )
                            .limit(5)
                        )
                    ).all()
                )
                selected.update((item.id, item) for item in lexical)

            same_category = list(
                (
                    await db.scalars(
                        select(MemoryRecord)
                        .options(selectinload(MemoryRecord.source_session))
                        .where(MemoryRecord.category == category.value, *trust_filters)
                        .order_by(MemoryRecord.updated_at.desc(), MemoryRecord.id.desc())
                        .limit(5)
                    )
                ).all()
            )
            selected.update((item.id, item) for item in same_category)

        result: list[MemoryRecord] = []
        used_characters = 0
        for item in selected.values():
            if len(result) >= max_items:
                break
            item_characters = len(item.content) + 120
            if result and used_characters + item_characters > max_characters:
                continue
            result.append(item)
            used_characters += item_characters
        return result

    @staticmethod
    async def _create(
        db: AsyncSession,
        *,
        content: str,
        category: MemoryCategory,
        source_type: MemorySourceType,
        trust_level: TrustLevel,
        session_id: UUID | None = None,
        message_id: UUID | None = None,
        run_id: UUID | None = None,
        dream_run_id: UUID | None = None,
        tool_call_id: str | None = None,
        evidence_message_ids: list[UUID] | None = None,
    ) -> MemoryRecord:
        cleaned = content.strip()
        if not cleaned:
            raise ValueError("memory content must not be empty")
        _, content_hash = normalize_content(cleaned)
        item = MemoryRecord(
            source_session_id=session_id,
            source_message_id=message_id,
            source_run_id=run_id,
            source_dream_run_id=dream_run_id,
            source_tool_call_id=tool_call_id,
            source_type=source_type.value,
            retrieval_level=MemoryRetrievalLevel.INSTANT.value,
            trust_level=trust_level.value,
            category=category.value,
            content=cleaned,
            normalized_content_hash=content_hash,
            seen_at=datetime.now(UTC) if source_type == MemorySourceType.MANUAL else None,
        )
        db.add(item)
        await db.flush()
        await MemoryService._add_evidence(db, item.id, evidence_message_ids or [])
        await MemoryIndexService.enqueue(db, item)
        return item

    @staticmethod
    async def _duplicate_by_hash(
        db: AsyncSession,
        content_hash: str,
        *,
        exclude_id: UUID | None = None,
        for_update: bool = False,
    ) -> MemoryRecord | None:
        statement = select(MemoryRecord).where(
            MemoryRecord.normalized_content_hash == content_hash
        )
        if exclude_id is not None:
            statement = statement.where(MemoryRecord.id != exclude_id)
        if for_update:
            statement = statement.execution_options(populate_existing=True).with_for_update()
        return await db.scalar(statement)

    @staticmethod
    async def _get_for_automatic_update(
        db: AsyncSession,
        memory_id: UUID,
        *,
        readable_by: TrustLevel,
    ) -> MemoryRecord | None:
        statement = select(MemoryRecord).where(MemoryRecord.id == memory_id)
        if readable_by == TrustLevel.PUBLIC:
            statement = statement.where(
                MemoryRecord.trust_level == TrustLevel.PUBLIC.value
            )
        return await db.scalar(
            statement.execution_options(populate_existing=True).with_for_update()
        )

    @staticmethod
    async def _add_evidence(
        db: AsyncSession, memory_id: UUID, message_ids: list[UUID]
    ) -> None:
        unique_ids = list(dict.fromkeys(message_ids))
        if not unique_ids:
            return
        await db.execute(
            postgresql_insert(MemorySourceMessage)
            .values(
                [
                    {"memory_id": memory_id, "message_id": message_id}
                    for message_id in unique_ids
                ]
            )
            .on_conflict_do_nothing(index_elements=["memory_id", "message_id"])
        )

    @staticmethod
    async def _merge_automatic_metadata(
        db: AsyncSession,
        item: MemoryRecord,
        *,
        trust_level: TrustLevel,
        evidence_message_ids: list[UUID],
    ) -> None:
        if trust_level == TrustLevel.PRIVATE:
            item.trust_level = TrustLevel.PRIVATE.value
        item.revision = (item.revision or 1) + 1
        await MemoryService._add_evidence(db, item.id, evidence_message_ids)
        await db.flush()

    @staticmethod
    async def get(
        db: AsyncSession, memory_id: UUID, *, for_update: bool = False
    ) -> MemoryRecord | None:
        statement = (
            select(MemoryRecord)
            .where(MemoryRecord.id == memory_id)
            .options(
                selectinload(MemoryRecord.source_session),
                selectinload(MemoryRecord.source_messages),
            )
        )
        if for_update:
            statement = statement.execution_options(populate_existing=True).with_for_update()
        return await db.scalar(statement)

    @staticmethod
    async def update(
        db: AsyncSession,
        item: MemoryRecord,
        *,
        content: str,
        category: MemoryCategory,
        trust_level: TrustLevel,
        evidence_message_ids: list[UUID] | None = None,
        source_type: MemorySourceType | None = None,
        source_session_id: UUID | None = None,
        source_message_id: UUID | None = None,
        source_run_id: UUID | None = None,
        source_dream_run_id: UUID | None = None,
        source_tool_call_id: str | None = None,
        max_context_characters: int,
    ) -> MemoryRecord:
        cleaned = content.strip()
        if not cleaned:
            raise ValueError("memory content must not be empty")
        _, content_hash = normalize_content(cleaned)
        content_changed = content_hash != item.normalized_content_hash
        if await MemoryService._duplicate_by_hash(
            db, content_hash, exclude_id=item.id
        ) is not None:
            raise MemoryDuplicateError
        item_id = item.id
        try:
            async with db.begin_nested():
                item.content = cleaned
                item.category = category.value
                item.trust_level = trust_level.value
                item.normalized_content_hash = content_hash
                item.revision = (item.revision or 1) + 1
                if content_changed:
                    await db.execute(
                        delete(MemoryExposure).where(MemoryExposure.memory_id == item.id)
                    )
                    item.last_used_at = None
                    if not item.tier_locked:
                        item.retrieval_level = MemoryRetrievalLevel.INSTANT.value
                        item.retrieval_level_changed_at = datetime.now(UTC)
                    if source_type is not None:
                        item.source_type = source_type.value
                        item.source_session_id = source_session_id
                        item.source_message_id = source_message_id
                        item.source_run_id = source_run_id
                        item.source_dream_run_id = source_dream_run_id
                        item.source_tool_call_id = source_tool_call_id
                await db.flush()
                await MemoryService._add_evidence(
                    db, item.id, evidence_message_ids or []
                )
                if content_changed:
                    await MemoryIndexService.enqueue(db, item)
                await MemoryService.enforce_capacity(db, max_context_characters)
                await db.flush()
        except IntegrityError as exc:
            if await MemoryService._duplicate_by_hash(
                db, content_hash, exclude_id=item_id
            ) is None:
                raise
            raise MemoryDuplicateError from exc
        return item

    @staticmethod
    async def delete(db: AsyncSession, item: MemoryRecord) -> None:
        await db.delete(item)
        await db.flush()

    @staticmethod
    async def mark_seen(db: AsyncSession, memory_ids: list[UUID]) -> int:
        unique_ids = list(dict.fromkeys(memory_ids))
        if not unique_ids:
            return 0
        result = await db.scalars(
            update(MemoryRecord)
            .where(
                MemoryRecord.id.in_(unique_ids),
                MemoryRecord.seen_at.is_(None),
            )
            .values(
                seen_at=func.now(),
                updated_at=MemoryRecord.updated_at,
            )
            .returning(MemoryRecord.id)
        )
        return len(result.all())

    @staticmethod
    async def list(
        db: AsyncSession,
        *,
        query: str | None = None,
        source_type: MemorySourceType | None = None,
        category: MemoryCategory | None = None,
        trust_level: TrustLevel | None = None,
        retrieval_level: MemoryRetrievalLevel | None = None,
        readable_by: TrustLevel | None = None,
        cursor_updated_at: datetime | None = None,
        cursor_id: UUID | None = None,
        offset: int = 0,
        limit: int = 20,
    ) -> MemoryList:
        if (cursor_updated_at is None) != (cursor_id is None):
            raise ValueError("both cursor fields are required together")
        filters = []
        if source_type is not None:
            filters.append(MemoryRecord.source_type == source_type.value)
        if category is not None:
            filters.append(MemoryRecord.category == category.value)
        if trust_level is not None:
            filters.append(MemoryRecord.trust_level == trust_level.value)
        if retrieval_level is not None:
            filters.append(MemoryRecord.retrieval_level == retrieval_level.value)
        if readable_by == TrustLevel.PUBLIC:
            filters.append(MemoryRecord.trust_level == TrustLevel.PUBLIC.value)
        cleaned_query = (query or "").strip()
        rank = None
        if cleaned_query:
            tsquery = func.websearch_to_tsquery("simple", cleaned_query)
            filters.append(MemoryRecord.search_vector.op("@@")(tsquery))
            rank = func.ts_rank_cd(MemoryRecord.search_vector, tsquery)
        total = int(
            await db.scalar(select(func.count(MemoryRecord.id)).where(*filters)) or 0
        )
        if cursor_updated_at is not None and cursor_id is not None:
            filters.append(
                or_(
                    MemoryRecord.updated_at < cursor_updated_at,
                    and_(
                        MemoryRecord.updated_at == cursor_updated_at,
                        MemoryRecord.id < cursor_id,
                    ),
                )
            )
        statement = (
            select(MemoryRecord)
            .where(*filters)
            .options(
                selectinload(MemoryRecord.source_session),
                selectinload(MemoryRecord.source_messages),
            )
        )
        if rank is not None:
            statement = statement.order_by(
                rank.desc(), MemoryRecord.updated_at.desc(), MemoryRecord.id.desc()
            )
        else:
            statement = statement.order_by(
                MemoryRecord.updated_at.desc(), MemoryRecord.id.desc()
            )
        items = list((await db.scalars(statement.offset(offset).limit(limit))).all())
        return MemoryList(items, total, offset, limit)

    @staticmethod
    async def scope(db: AsyncSession, *, readable_by: TrustLevel) -> MemoryScope:
        filters = []
        if readable_by == TrustLevel.PUBLIC:
            filters.append(MemoryRecord.trust_level == TrustLevel.PUBLIC.value)
        rows = (
            await db.execute(
                select(MemoryRecord.retrieval_level, func.count(MemoryRecord.id))
                .where(*filters)
                .group_by(MemoryRecord.retrieval_level)
            )
        ).all()
        counts = {str(level): int(count) for level, count in rows}
        instant = counts.get(MemoryRetrievalLevel.INSTANT.value, 0)
        retrieve = counts.get(MemoryRetrievalLevel.RETRIEVE.value, 0)
        return MemoryScope(instant + retrieve, instant, retrieve)

    @staticmethod
    async def instant_context(
        db: AsyncSession,
        *,
        input_trust_level: TrustLevel,
        max_characters: int,
    ) -> list[MemoryRecord]:
        if max_characters <= 0:
            return []
        filters = [MemoryRecord.retrieval_level == MemoryRetrievalLevel.INSTANT.value]
        if input_trust_level == TrustLevel.PUBLIC:
            filters.append(MemoryRecord.trust_level == TrustLevel.PUBLIC.value)
        records = list(
            (
                await db.scalars(
                    select(MemoryRecord)
                    .where(*filters)
                    .options(selectinload(MemoryRecord.source_session))
                    .order_by(MemoryRecord.created_at, MemoryRecord.id)
                )
            ).all()
        )
        selected: list[MemoryRecord] = []
        used = 0
        for item in records:
            item_cost = MemoryService.content_char_cost(item.content)
            if used + item_cost > max_characters:
                continue
            selected.append(item)
            used += item_cost
        return selected

    @staticmethod
    def content_char_cost(content: str) -> int:
        return len(content)

    @staticmethod
    async def _change_retrieval_level(
        db: AsyncSession,
        item: MemoryRecord,
        retrieval_level: MemoryRetrievalLevel,
        *,
        changed_at: datetime | None = None,
    ) -> None:
        if item.retrieval_level == retrieval_level.value:
            return
        item.retrieval_level = retrieval_level.value
        item.retrieval_level_changed_at = changed_at or datetime.now(UTC)
        if retrieval_level == MemoryRetrievalLevel.RETRIEVE:
            await db.flush()
            await MemoryIndexService.enqueue(db, item)
        else:
            await db.execute(
                delete(MemoryEmbedding).where(MemoryEmbedding.memory_id == item.id)
            )

    @staticmethod
    async def enforce_capacity(
        db: AsyncSession, max_context_characters: int
    ) -> None:
        await MemoryService.lock_capacity(db)
        items = list(
            (
                await db.scalars(
                    select(MemoryRecord)
                    .where(
                        MemoryRecord.retrieval_level
                        == MemoryRetrievalLevel.INSTANT.value
                    )
                    .order_by(
                        func.coalesce(
                            MemoryRecord.last_used_at,
                            MemoryRecord.retrieval_level_changed_at,
                            MemoryRecord.created_at,
                        ),
                        MemoryRecord.id,
                    )
                    .with_for_update()
                )
            ).all()
        )
        fixed_cost = sum(
            MemoryService.content_char_cost(item.content)
            for item in items
            if item.tier_locked
        )
        if fixed_cost > max_context_characters:
            raise MemoryTierBudgetError
        total_cost = sum(MemoryService.content_char_cost(item.content) for item in items)
        for item in items:
            if total_cost <= max_context_characters:
                break
            if item.tier_locked:
                continue
            total_cost -= MemoryService.content_char_cost(item.content)
            await MemoryService._change_retrieval_level(
                db, item, MemoryRetrievalLevel.RETRIEVE
            )
        await db.flush()

    @staticmethod
    async def set_tier(
        db: AsyncSession,
        item: MemoryRecord,
        *,
        retrieval_level: MemoryRetrievalLevel,
        locked: bool,
        max_context_characters: int,
    ) -> MemoryRecord:
        await MemoryService.lock_capacity(db)
        now = datetime.now(UTC)
        previous_level = item.retrieval_level
        await MemoryService._change_retrieval_level(
            db, item, retrieval_level, changed_at=now
        )
        item.tier_locked = locked
        item.tier_locked_at = now if locked else None
        if (
            not locked
            and retrieval_level == MemoryRetrievalLevel.INSTANT
            and previous_level == MemoryRetrievalLevel.INSTANT.value
        ):
            item.retrieval_level_changed_at = now
        await MemoryService.enforce_capacity(db, max_context_characters)
        return item

    @staticmethod
    async def record_exposures(
        db: AsyncSession,
        memories: list[MemoryRecord],
        *,
        run_id: UUID,
        assistant_message_id: UUID | None = None,
    ) -> None:
        unique_memories = {memory.id: memory for memory in memories}
        if not unique_memories:
            return
        statement = postgresql_insert(MemoryExposure).values(
            [
                {
                    "memory_id": memory_id,
                    "memory_content_hash": memory.normalized_content_hash,
                    "agent_run_id": run_id,
                    "assistant_message_id": assistant_message_id,
                    "status": MemoryExposureStatus.PENDING.value,
                }
                for memory_id, memory in unique_memories.items()
            ]
        )
        await db.execute(
            statement.on_conflict_do_update(
                index_elements=["memory_id", "agent_run_id"],
                set_={
                    "memory_content_hash": statement.excluded.memory_content_hash,
                    "assistant_message_id": statement.excluded.assistant_message_id,
                },
            )
        )

    @staticmethod
    async def link_pending_exposures(
        db: AsyncSession, *, run_id: UUID, assistant_message_id: UUID
    ) -> None:
        await db.execute(
            update(MemoryExposure)
            .where(
                MemoryExposure.agent_run_id == run_id,
                MemoryExposure.status == MemoryExposureStatus.PENDING.value,
            )
            .values(assistant_message_id=assistant_message_id)
        )

    @staticmethod
    async def usage_counts(
        db: AsyncSession, memory_ids: list[UUID], *, window_days: int
    ) -> dict[UUID, int]:
        if not memory_ids:
            return {}
        rows = (
            await db.execute(
                select(MemoryExposure.memory_id, func.count(MemoryExposure.id))
                .join(MemoryRecord, MemoryRecord.id == MemoryExposure.memory_id)
                .where(
                    MemoryExposure.memory_id.in_(memory_ids),
                    MemoryExposure.status == MemoryExposureStatus.USED.value,
                    MemoryExposure.exposed_at
                    >= datetime.now(UTC) - timedelta(days=window_days),
                    MemoryExposure.memory_content_hash
                    == MemoryRecord.normalized_content_hash,
                )
                .group_by(MemoryExposure.memory_id)
            )
        ).all()
        return {memory_id: int(count) for memory_id, count in rows}

    @staticmethod
    async def apply_tiering(
        db: AsyncSession,
        settings: MemorySettings,
        *,
        now: datetime | None = None,
    ) -> int:
        await MemoryService.lock_capacity(db)
        now = now or datetime.now(UTC)
        window_days = settings.usage_window_days
        grace_days = settings.short_term_grace_days
        required_count = settings.required_usage_count
        items = list(
            (
                await db.scalars(
                    select(MemoryRecord)
                    .where(MemoryRecord.tier_locked.is_(False))
                    .with_for_update()
                )
            ).all()
        )
        counts = await MemoryService.usage_counts(
            db, [item.id for item in items], window_days=window_days
        )
        pending_ids = (
            set(
                (
                    await db.scalars(
                        select(MemoryExposure.memory_id)
                        .join(AgentRun, AgentRun.id == MemoryExposure.agent_run_id)
                        .join(ChatSession, ChatSession.id == AgentRun.session_id)
                        .join(MemoryRecord, MemoryRecord.id == MemoryExposure.memory_id)
                        .where(
                            MemoryExposure.memory_id.in_([item.id for item in items]),
                            MemoryExposure.status
                            == MemoryExposureStatus.PENDING.value,
                            AgentRun.status == "completed",
                            ChatSession.memory_mode == "full",
                            MemoryExposure.memory_content_hash
                            == MemoryRecord.normalized_content_hash,
                        )
                    )
                ).all()
            )
            if items
            else set()
        )
        changed = 0
        for item in items:
            count = counts.get(item.id, 0)
            changed_at = item.retrieval_level_changed_at or item.created_at or now
            if (
                item.retrieval_level == MemoryRetrievalLevel.INSTANT.value
                and changed_at <= now - timedelta(days=grace_days)
                and count < required_count
                and item.id not in pending_ids
            ):
                await MemoryService._change_retrieval_level(
                    db, item, MemoryRetrievalLevel.RETRIEVE, changed_at=now
                )
                changed += 1
            elif (
                item.retrieval_level == MemoryRetrievalLevel.RETRIEVE.value
                and count >= required_count
            ):
                await MemoryService._change_retrieval_level(
                    db, item, MemoryRetrievalLevel.INSTANT, changed_at=now
                )
                changed += 1
        await MemoryService.enforce_capacity(
            db, settings.max_context_characters
        )
        return changed
