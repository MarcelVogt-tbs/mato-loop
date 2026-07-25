from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from mato_loop_backend.memory.indexing import MemoryIndexService
from mato_loop_backend.models import (
    MemoryCategory,
    MemoryRecord,
    MemoryRetrievalLevel,
    MemorySourceMessage,
    MemorySourceType,
)
from mato_loop_backend.settings import TrustLevel


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


class MemoryService:
    @staticmethod
    async def create_manual(
        db: AsyncSession,
        *,
        content: str,
        category: MemoryCategory,
        trust_level: TrustLevel,
        retrieval_level: MemoryRetrievalLevel,
    ) -> MemoryRecord:
        _, content_hash = normalize_content(content)
        if await MemoryService._duplicate_by_hash(db, content_hash) is not None:
            raise MemoryDuplicateError
        try:
            async with db.begin_nested():
                return await MemoryService._create(
                    db,
                    content=content,
                    category=category,
                    source_type=MemorySourceType.MANUAL,
                    trust_level=trust_level,
                    retrieval_level=retrieval_level,
                )
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
        retrieval_level: MemoryRetrievalLevel,
        trust_level: TrustLevel,
        session_id: UUID,
        message_id: UUID,
        run_id: UUID,
        tool_call_id: str,
    ) -> MemoryMutation:
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
                return MemoryMutation(
                    await MemoryService._create(
                        db,
                        content=content,
                        category=category,
                        source_type=MemorySourceType.TOOL_CALL,
                        retrieval_level=retrieval_level,
                        trust_level=trust_level,
                        session_id=session_id,
                        message_id=message_id,
                        run_id=run_id,
                        tool_call_id=tool_call_id,
                        evidence_message_ids=[message_id],
                    ),
                    created=True,
                )
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
        retrieval_level: MemoryRetrievalLevel,
        readable_by: TrustLevel,
        write_trust_level: TrustLevel,
        evidence_message_id: UUID,
    ) -> MemoryMutation:
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
                retrieval_level=retrieval_level,
                evidence_message_ids=[evidence_message_id],
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
            await db.delete(item)
            await db.flush()
            return MemoryMutation(duplicate, updated=True)
        return MemoryMutation(item, updated=True)

    @staticmethod
    async def create_dreaming(
        db: AsyncSession,
        *,
        content: str,
        category: MemoryCategory,
        retrieval_level: MemoryRetrievalLevel,
        trust_level: TrustLevel,
        session_id: UUID,
        run_id: UUID,
        evidence_message_ids: list[UUID],
    ) -> MemoryMutation:
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
                return MemoryMutation(
                    await MemoryService._create(
                        db,
                        content=content,
                        category=category,
                        source_type=MemorySourceType.DREAMING,
                        retrieval_level=retrieval_level,
                        trust_level=trust_level,
                        session_id=session_id,
                        dream_run_id=run_id,
                        evidence_message_ids=evidence_message_ids,
                    ),
                    created=True,
                )
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
        retrieval_level: MemoryRetrievalLevel,
        trust_level: TrustLevel,
        evidence_message_ids: list[UUID],
    ) -> MemoryMutation:
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
                retrieval_level=retrieval_level,
                evidence_message_ids=evidence_message_ids,
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
                )
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
        retrieval_level: MemoryRetrievalLevel,
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
            retrieval_level=retrieval_level.value,
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
        retrieval_level: MemoryRetrievalLevel,
        evidence_message_ids: list[UUID] | None = None,
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
                item.retrieval_level = retrieval_level.value
                item.normalized_content_hash = content_hash
                item.revision = (item.revision or 1) + 1
                await db.flush()
                await MemoryService._add_evidence(
                    db, item.id, evidence_message_ids or []
                )
                if content_changed:
                    await MemoryIndexService.enqueue(db, item)
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
        max_items: int,
        max_characters: int,
    ) -> list[MemoryRecord]:
        if max_items <= 0 or max_characters <= 0:
            return []
        filters = [MemoryRecord.retrieval_level == MemoryRetrievalLevel.INSTANT.value]
        if input_trust_level == TrustLevel.PUBLIC:
            filters.append(MemoryRecord.trust_level == TrustLevel.PUBLIC.value)
        records = list(
            (
                await db.scalars(
                    select(MemoryRecord)
                    .where(*filters)
                    .order_by(MemoryRecord.updated_at.desc(), MemoryRecord.id.desc())
                    .limit(max(100, max_items * 10))
                )
            ).all()
        )
        selected: list[MemoryRecord] = []
        used = 0
        for item in records:
            separator = 2 if selected else 0
            if used + separator + len(item.content) > max_characters:
                continue
            selected.append(item)
            used += separator + len(item.content)
            if len(selected) >= max_items:
                break
        return selected
