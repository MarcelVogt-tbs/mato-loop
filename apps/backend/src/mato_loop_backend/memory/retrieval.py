from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.config import get_runtime_config
from mato_loop_backend.memory.embeddings import (
    ACTIVE_EMBEDDING_PROFILE,
    EmbeddingError,
    embedding_client,
)
from mato_loop_backend.memory.service import (
    MemoryService,
    memory_lexical_terms,
    memory_reference_options,
    memory_visibility_filter,
)
from mato_loop_backend.models import (
    MemoryCategory,
    MemoryEmbedding,
    MemoryEmbeddingStatus,
    MemoryRecord,
)
from mato_loop_backend.settings import TrustLevel

logger = logging.getLogger(__name__)
RRF_K = 60
MAX_CANDIDATES = 100


class MemoryRetrieveMode(StrEnum):
    SEARCH = "search"
    BROWSE = "browse"


class MemoryRetrievalStrategy(StrEnum):
    HYBRID = "hybrid"
    LEXICAL = "lexical"
    BROWSE = "browse"


@dataclass(frozen=True, slots=True)
class MemoryRetrieval:
    items: list[MemoryRecord]
    mode: MemoryRetrieveMode
    strategy: MemoryRetrievalStrategy
    readable_by: TrustLevel
    offset: int
    limit: int
    has_more: bool
    total: int | None = None
    next_cursor_updated_at: datetime | None = None
    next_cursor_id: UUID | None = None


def _filters(
    *,
    owner_id: UUID,
    readable_by: TrustLevel,
    category: MemoryCategory | None,
    pinned: bool | None,
    source_types: tuple[str, ...] | None = None,
) -> list[object]:
    filters: list[object] = [MemoryRecord.user_id == owner_id]
    if category is not None:
        filters.append(MemoryRecord.category == category.value)
    if pinned is not None:
        filters.append(MemoryRecord.pinned.is_(pinned))
    if source_types is not None:
        filters.append(MemoryRecord.source_type.in_(source_types))
    filters.append(memory_visibility_filter(readable_by))
    return filters


class MemoryRetrievalService:
    @staticmethod
    async def retrieve(
        db: AsyncSession,
        *,
        owner_id: UUID,
        mode: MemoryRetrieveMode,
        readable_by: TrustLevel,
        query: str | None = None,
        category: MemoryCategory | None = None,
        pinned: bool | None = None,
        offset: int = 0,
        limit: int = 10,
        query_embedding: list[float] | None = None,
        semantic_enabled: bool = True,
        max_semantic_distance: float | None = None,
        cursor_updated_at: datetime | None = None,
        cursor_id: UUID | None = None,
        source_types: tuple[str, ...] | None = None,
    ) -> MemoryRetrieval:
        if (cursor_updated_at is None) != (cursor_id is None):
            raise ValueError("both cursor fields are required together")
        if mode == MemoryRetrieveMode.BROWSE:
            if (query or "").strip():
                raise ValueError("browse mode does not accept a query")
            result = await MemoryService.list(
                db,
                owner_id=owner_id,
                category=category,
                pinned=pinned,
                readable_by=readable_by,
                cursor_updated_at=cursor_updated_at,
                cursor_id=cursor_id,
                offset=0 if cursor_updated_at is not None else offset,
                limit=limit + 1,
            )
            items = result.items[:limit]
            has_more = len(result.items) > limit
            cursor_item = items[-1] if has_more and items else None
            return MemoryRetrieval(
                items=items,
                mode=mode,
                strategy=MemoryRetrievalStrategy.BROWSE,
                readable_by=readable_by,
                offset=offset,
                limit=limit,
                has_more=has_more,
                total=result.total,
                next_cursor_updated_at=(
                    cursor_item.updated_at if cursor_item is not None else None
                ),
                next_cursor_id=(cursor_item.id if cursor_item is not None else None),
            )

        cleaned_query = (query or "").strip()
        if cursor_updated_at is not None:
            raise ValueError("search mode does not accept a cursor")
        if not cleaned_query:
            raise ValueError("search mode requires a query")
        candidate_limit = min(MAX_CANDIDATES, max(20, limit * 3))
        filters = _filters(
            owner_id=owner_id,
            readable_by=readable_by,
            category=category,
            pinned=pinned,
            source_types=source_types,
        )
        tsquery = func.plainto_tsquery(
            "simple", " ".join(memory_lexical_terms(cleaned_query))
        )
        lexical = list(
            (
                await db.scalars(
                    select(MemoryRecord)
                    .options(*memory_reference_options(owner_id))
                    .where(MemoryRecord.search_vector.op("@@")(tsquery), *filters)
                    .order_by(
                        func.ts_rank_cd(MemoryRecord.search_vector, tsquery).desc(),
                        MemoryRecord.updated_at.desc(),
                        MemoryRecord.id.desc(),
                    )
                    .limit(candidate_limit)
                )
            ).all()
        )

        semantic: list[MemoryRecord] = []
        semantic_used = False
        if semantic_enabled and get_runtime_config().embedding_enabled:
            try:
                vector = (
                    query_embedding
                    if query_embedding is not None
                    else await embedding_client.embed_query(cleaned_query)
                )
                distance = MemoryEmbedding.vector.cosine_distance(vector)
                semantic_filters = [
                    *filters,
                    MemoryEmbedding.status == MemoryEmbeddingStatus.READY.value,
                    MemoryEmbedding.profile_fingerprint
                    == ACTIVE_EMBEDDING_PROFILE.fingerprint,
                    MemoryEmbedding.content_hash
                    == MemoryRecord.normalized_content_hash,
                ]
                if max_semantic_distance is not None:
                    semantic_filters.append(distance <= max_semantic_distance)
                async with db.begin_nested():
                    semantic = list(
                        (
                            await db.scalars(
                                select(MemoryRecord)
                                .options(*memory_reference_options(owner_id))
                                .join(
                                    MemoryEmbedding,
                                    MemoryEmbedding.memory_id == MemoryRecord.id,
                                )
                                .where(*semantic_filters)
                                .order_by(
                                    distance,
                                    MemoryRecord.updated_at.desc(),
                                    MemoryRecord.id.desc(),
                                )
                                .limit(candidate_limit)
                            )
                        ).all()
                    )
                semantic_used = True
            except EmbeddingError:
                semantic = []
            except SQLAlchemyError:
                logger.warning("Semantic memory query failed; using lexical fallback")
                semantic = []

        records: dict[object, MemoryRecord] = {}
        scores: dict[object, float] = {}
        for candidates in (lexical, semantic):
            for rank, item in enumerate(candidates, start=1):
                records[item.id] = item
                scores[item.id] = scores.get(item.id, 0.0) + 1.0 / (RRF_K + rank)
        ranked = sorted(
            records.values(),
            key=lambda item: (
                scores[item.id],
                item.updated_at or datetime.min.replace(tzinfo=UTC),
                str(item.id),
            ),
            reverse=True,
        )
        return MemoryRetrieval(
            items=ranked[:limit],
            mode=mode,
            strategy=(
                MemoryRetrievalStrategy.HYBRID
                if semantic_used
                else MemoryRetrievalStrategy.LEXICAL
            ),
            readable_by=readable_by,
            offset=0,
            limit=limit,
            has_more=(
                len(ranked) > limit
                or len(lexical) == candidate_limit
                or len(semantic) == candidate_limit
            ),
        )
