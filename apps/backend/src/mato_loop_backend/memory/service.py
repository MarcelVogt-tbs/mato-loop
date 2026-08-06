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
from sqlalchemy.orm import Load, selectinload

from mato_loop_backend.memory.indexing import MemoryIndexService
from mato_loop_backend.models import (
    MemoryCategory,
    MemoryRecord,
    MemorySourceMessage,
    MemorySourceType,
    Message,
)
from mato_loop_backend.settings import TrustLevel

_MAX_LEXICAL_TERMS = 20
_MAX_LEXICAL_TERM_CHARACTERS = 256


def normalize_content(content: str) -> tuple[str, str]:
    normalized = re.sub(r"\s+", " ", content.strip()).casefold()
    return normalized, hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def memory_lexical_terms(content: str) -> list[str]:
    terms: list[str] = []
    seen: set[str] = set()
    for term in re.findall(r"\w+", content.casefold()):
        if len(term) > _MAX_LEXICAL_TERM_CHARACTERS or term in seen:
            continue
        seen.add(term)
        terms.append(term)
        if len(terms) == _MAX_LEXICAL_TERMS:
            break
    return terms


def memory_reference_options() -> tuple[Load, Load]:
    return (
        selectinload(MemoryRecord.source_session),
        selectinload(MemoryRecord.source_message).load_only(Message.created_at),
    )


def memory_reference_timestamps(item: MemoryRecord) -> dict[str, str | None]:
    return {
        "sourceMessageAt": (
            item.source_message.created_at.isoformat()
            if item.source_message and item.source_message.created_at
            else None
        ),
        "memoryCreatedAt": item.created_at.isoformat() if item.created_at else None,
        "memoryUpdatedAt": item.updated_at.isoformat() if item.updated_at else None,
    }


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
    pinned: int


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
    ) -> MemoryRecord:
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
                evidence_message_ids=[evidence_message_id],
                source_type=MemorySourceType.TOOL_CALL,
                source_session_id=session_id,
                source_message_id=evidence_message_id,
                source_run_id=run_id,
                source_tool_call_id=tool_call_id,
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
            duplicate.source_type = MemorySourceType.TOOL_CALL.value
            duplicate.source_session_id = session_id
            duplicate.source_message_id = evidence_message_id
            duplicate.source_run_id = run_id
            duplicate.source_dream_run_id = None
            duplicate.source_tool_call_id = tool_call_id
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
                item = await MemoryService._create(
                    db,
                    content=content,
                    category=category,
                    source_type=MemorySourceType.DREAMING,
                    trust_level=trust_level,
                    session_id=session_id,
                    message_id=(evidence_message_ids[0] if evidence_message_ids else None),
                    dream_run_id=run_id,
                    evidence_message_ids=evidence_message_ids,
                )
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
                evidence_message_ids=evidence_message_ids,
                source_type=MemorySourceType.DREAMING,
                source_session_id=session_id,
                source_message_id=(evidence_message_ids[0] if evidence_message_ids else None),
                source_dream_run_id=dream_run_id,
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
                ).options(*memory_reference_options())
            )
            if exact is not None:
                selected[exact.id] = exact

            terms = memory_lexical_terms(content)
            if terms:
                tsquery = func.to_tsquery("simple", " | ".join(terms))
                lexical = list(
                    (
                        await db.scalars(
                            select(MemoryRecord)
                            .options(*memory_reference_options())
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
                        .options(*memory_reference_options())
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
            pinned=False,
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
        pinned: bool | None = None,
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
                if pinned is not None:
                    item.pinned = pinned
                item.normalized_content_hash = content_hash
                item.revision = (item.revision or 1) + 1
                if content_changed:
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
        pinned: bool | None = None,
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
        if pinned is not None:
            filters.append(MemoryRecord.pinned.is_(pinned))
        if readable_by == TrustLevel.PUBLIC:
            filters.append(MemoryRecord.trust_level == TrustLevel.PUBLIC.value)
        cleaned_query = (query or "").strip()
        rank = None
        if cleaned_query:
            tsquery = func.plainto_tsquery(
                "simple", " ".join(memory_lexical_terms(cleaned_query))
            )
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
                *memory_reference_options(),
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
        total, pinned = (
            await db.execute(
                select(
                    func.count(MemoryRecord.id),
                    func.count(MemoryRecord.id).filter(MemoryRecord.pinned.is_(True)),
                ).where(*filters)
            )
        ).one()
        return MemoryScope(int(total), int(pinned))

    @staticmethod
    async def pinned_context(
        db: AsyncSession,
        *,
        input_trust_level: TrustLevel,
    ) -> list[MemoryRecord]:
        filters = [MemoryRecord.pinned.is_(True)]
        if input_trust_level == TrustLevel.PUBLIC:
            filters.append(MemoryRecord.trust_level == TrustLevel.PUBLIC.value)
        return list(
            (
                await db.scalars(
                    select(MemoryRecord)
                    .where(*filters)
                    .options(*memory_reference_options())
                    .order_by(MemoryRecord.created_at, MemoryRecord.id)
                )
            ).all()
        )

    @staticmethod
    async def set_pinned(
        db: AsyncSession, item: MemoryRecord, *, pinned: bool
    ) -> MemoryRecord:
        if item.pinned != pinned:
            item.pinned = pinned
            item.revision = (item.revision or 1) + 1
            await db.flush()
        return item
