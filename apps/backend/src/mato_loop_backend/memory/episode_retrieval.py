from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import JSON, and_, func, or_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.config import get_runtime_config
from mato_loop_backend.memory.embeddings import (
    ACTIVE_EMBEDDING_PROFILE,
    EmbeddingError,
    embedding_client,
)
from mato_loop_backend.memory.episode_indexing import _eligible_filters
from mato_loop_backend.memory.retrieval import MemoryRetrievalStrategy
from mato_loop_backend.memory.service import memory_lexical_terms
from mato_loop_backend.models import (
    ChatSession,
    MemoryEmbeddingStatus,
    Message,
    MessageSearchChunk,
)
from mato_loop_backend.settings import TrustLevel

logger = logging.getLogger(__name__)
RRF_K = 60
MAX_CANDIDATES = 100


@dataclass(frozen=True, slots=True)
class EpisodeExcerptMessage:
    id: UUID
    role: str
    content: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class EpisodeReference:
    session_id: UUID
    session_title: str | None
    anchor_message_id: UUID
    anchor_created_at: datetime
    anchor_role: str
    anchor_content: str
    excerpt: tuple[EpisodeExcerptMessage, ...]
    content_is_untrusted: bool = True


@dataclass(frozen=True, slots=True)
class EpisodeRetrieval:
    references: list[EpisodeReference]
    strategy: MemoryRetrievalStrategy


@dataclass(frozen=True, slots=True)
class _Candidate:
    chunk: MessageSearchChunk
    message: Message
    session: ChatSession
    title_trust: str | None


def _title_trust():
    return (
        select(Message.trust_level)
        .where(Message.session_id == ChatSession.id, Message.role == "user")
        .order_by(Message.created_at, Message.id)
        .limit(1)
        .correlate(ChatSession)
        .scalar_subquery()
    )


def _candidate_filters(
    *,
    owner_id: UUID,
    current_session_id: UUID,
    project_id: UUID | None,
    readable_by: TrustLevel,
) -> list[object]:
    filters = [
        ChatSession.user_id == owner_id,
        ChatSession.id != current_session_id,
        ChatSession.session_type == "chat",
        ChatSession.project_id == project_id,
        *_eligible_filters(),
    ]
    if readable_by == TrustLevel.PUBLIC:
        filters.append(Message.trust_level == TrustLevel.PUBLIC.value)
        filters.append(ChatSession.private_integration_data_used.is_(False))
    return filters


def _rank_candidates(
    lexical: list[_Candidate], semantic: list[_Candidate], limit: int
) -> list[_Candidate]:
    records: dict[UUID, _Candidate] = {}
    scores: dict[UUID, float] = {}
    for candidates in (lexical, semantic):
        for rank, candidate in enumerate(candidates, start=1):
            records[candidate.chunk.id] = candidate
            scores[candidate.chunk.id] = scores.get(candidate.chunk.id, 0.0) + 1.0 / (
                RRF_K + rank
            )
    ranked = sorted(
        records.values(),
        key=lambda candidate: (
            scores[candidate.chunk.id],
            candidate.message.created_at or datetime.min.replace(tzinfo=UTC),
            str(candidate.chunk.id),
        ),
        reverse=True,
    )
    result: list[_Candidate] = []
    seen_sessions: set[UUID] = set()
    for candidate in ranked:
        if candidate.session.id in seen_sessions:
            continue
        seen_sessions.add(candidate.session.id)
        result.append(candidate)
        if len(result) == limit:
            break
    return result


class EpisodeRetrievalService:
    @staticmethod
    async def recent(
        db: AsyncSession,
        *,
        owner_id: UUID,
        current_session_id: UUID,
        project_id: UUID | None = None,
        readable_by: TrustLevel,
        limit: int = 2,
        excerpt_messages: int = 6,
        max_excerpt_characters: int = 6_000,
    ) -> EpisodeRetrieval:
        if limit <= 0:
            raise ValueError("episode retrieval limit must be positive")
        title_trust = _title_trust().label("title_trust")
        recent_candidates = (
            select(
                Message.id.label("message_id"),
                func.row_number()
                .over(
                    partition_by=Message.session_id,
                    order_by=(Message.created_at.desc(), Message.id.desc()),
                )
                .label("session_position"),
            )
            .join(ChatSession, ChatSession.id == Message.session_id)
            .where(
                *_candidate_filters(
                    owner_id=owner_id,
                    current_session_id=current_session_id,
                    project_id=project_id,
                    readable_by=readable_by,
                )
            )
            .subquery()
        )
        rows = (
            await db.execute(
                select(Message, ChatSession, title_trust)
                .join(
                    recent_candidates,
                    recent_candidates.c.message_id == Message.id,
                )
                .join(ChatSession, ChatSession.id == Message.session_id)
                .where(recent_candidates.c.session_position == 1)
                .order_by(Message.created_at.desc(), Message.id.desc())
                .limit(limit)
            )
        ).all()
        references = [
            await EpisodeRetrievalService._reference_for(
                db,
                message=message,
                session=session,
                title_trust=visible_title_trust,
                anchor_content=message.content,
                readable_by=readable_by,
                excerpt_messages=excerpt_messages,
                max_excerpt_characters=max_excerpt_characters,
            )
            for message, session, visible_title_trust in rows
        ]
        return EpisodeRetrieval(
            references=references,
            strategy=MemoryRetrievalStrategy.BROWSE,
        )

    @staticmethod
    async def retrieve(
        db: AsyncSession,
        *,
        owner_id: UUID,
        current_session_id: UUID,
        project_id: UUID | None = None,
        readable_by: TrustLevel,
        query: str,
        limit: int = 5,
        query_embedding: list[float] | None = None,
        semantic_enabled: bool = True,
        max_semantic_distance: float | None = None,
        excerpt_messages: int = 6,
        latest_messages: int = 0,
        max_excerpt_characters: int = 6_000,
    ) -> EpisodeRetrieval:
        cleaned_query = query.strip()
        if not cleaned_query:
            raise ValueError("episode search requires a query")
        if (
            limit <= 0
            or excerpt_messages <= 0
            or latest_messages < 0
            or max_excerpt_characters <= 0
        ):
            raise ValueError("episode retrieval limits must be positive")
        candidate_limit = min(MAX_CANDIDATES, max(20, limit * 3))
        filters = _candidate_filters(
            owner_id=owner_id,
            current_session_id=current_session_id,
            project_id=project_id,
            readable_by=readable_by,
        )
        title_trust = _title_trust().label("title_trust")
        tsquery = func.plainto_tsquery(
            "simple", " ".join(memory_lexical_terms(cleaned_query))
        )
        lexical_rank = func.ts_rank_cd(MessageSearchChunk.search_vector, tsquery)
        lexical_candidates = (
            select(
                MessageSearchChunk.id.label("chunk_id"),
                lexical_rank.label("score"),
                func.row_number()
                .over(
                    partition_by=Message.session_id,
                    order_by=(
                        lexical_rank.desc(),
                        Message.created_at.desc(),
                        MessageSearchChunk.id.desc(),
                    ),
                )
                .label("session_position"),
            )
            .join(Message, Message.id == MessageSearchChunk.message_id)
            .join(ChatSession, ChatSession.id == Message.session_id)
            .where(MessageSearchChunk.search_vector.op("@@")(tsquery), *filters)
            .subquery()
        )
        lexical_rows = (
            await db.execute(
                select(MessageSearchChunk, Message, ChatSession, title_trust)
                .join(
                    lexical_candidates,
                    lexical_candidates.c.chunk_id == MessageSearchChunk.id,
                )
                .join(Message, Message.id == MessageSearchChunk.message_id)
                .join(ChatSession, ChatSession.id == Message.session_id)
                .where(lexical_candidates.c.session_position == 1)
                .order_by(
                    lexical_candidates.c.score.desc(),
                    Message.created_at.desc(),
                    MessageSearchChunk.id.desc(),
                )
                .limit(candidate_limit)
            )
        ).all()
        lexical = [_Candidate(*row) for row in lexical_rows]

        semantic: list[_Candidate] = []
        semantic_used = False
        if semantic_enabled and get_runtime_config().embedding_enabled:
            try:
                vector = (
                    query_embedding
                    if query_embedding is not None
                    else await embedding_client.embed_query(cleaned_query)
                )
                distance = MessageSearchChunk.vector.cosine_distance(vector)
                semantic_filters = [
                    *filters,
                    MessageSearchChunk.status == MemoryEmbeddingStatus.READY.value,
                    MessageSearchChunk.profile_fingerprint
                    == ACTIVE_EMBEDDING_PROFILE.fingerprint,
                ]
                if max_semantic_distance is not None:
                    semantic_filters.append(distance <= max_semantic_distance)
                semantic_candidates = (
                    select(
                        MessageSearchChunk.id.label("chunk_id"),
                        distance.label("distance"),
                        func.row_number()
                        .over(
                            partition_by=Message.session_id,
                            order_by=(
                                distance,
                                Message.created_at.desc(),
                                MessageSearchChunk.id.desc(),
                            ),
                        )
                        .label("session_position"),
                    )
                    .join(Message, Message.id == MessageSearchChunk.message_id)
                    .join(ChatSession, ChatSession.id == Message.session_id)
                    .where(*semantic_filters)
                    .subquery()
                )
                async with db.begin_nested():
                    semantic_rows = (
                        await db.execute(
                            select(MessageSearchChunk, Message, ChatSession, title_trust)
                            .join(
                                semantic_candidates,
                                semantic_candidates.c.chunk_id
                                == MessageSearchChunk.id,
                            )
                            .join(Message, Message.id == MessageSearchChunk.message_id)
                            .join(ChatSession, ChatSession.id == Message.session_id)
                            .where(semantic_candidates.c.session_position == 1)
                            .order_by(
                                semantic_candidates.c.distance,
                                Message.created_at.desc(),
                                MessageSearchChunk.id.desc(),
                            )
                            .limit(candidate_limit)
                        )
                    ).all()
                semantic = [_Candidate(*row) for row in semantic_rows]
                semantic_used = True
            except EmbeddingError:
                semantic = []
            except SQLAlchemyError:
                logger.warning("Semantic episode query failed; using lexical fallback")
                semantic = []

        anchors = _rank_candidates(lexical, semantic, limit)
        references = [
            await EpisodeRetrievalService._reference(
                db,
                candidate,
                readable_by=readable_by,
                excerpt_messages=excerpt_messages,
                latest_messages=latest_messages,
                max_excerpt_characters=max_excerpt_characters,
            )
            for candidate in anchors
        ]
        return EpisodeRetrieval(
            references=references,
            strategy=(
                MemoryRetrievalStrategy.HYBRID
                if semantic_used
                else MemoryRetrievalStrategy.LEXICAL
            ),
        )

    @staticmethod
    async def _reference(
        db: AsyncSession,
        candidate: _Candidate,
        *,
        readable_by: TrustLevel,
        excerpt_messages: int,
        latest_messages: int,
        max_excerpt_characters: int,
    ) -> EpisodeReference:
        return await EpisodeRetrievalService._reference_for(
            db,
            message=candidate.message,
            session=candidate.session,
            title_trust=candidate.title_trust,
            anchor_content=candidate.chunk.content,
            readable_by=readable_by,
            excerpt_messages=excerpt_messages,
            latest_messages=latest_messages,
            max_excerpt_characters=max_excerpt_characters,
        )

    @staticmethod
    async def _reference_for(
        db: AsyncSession,
        *,
        message: Message,
        session: ChatSession,
        title_trust: str | None,
        anchor_content: str,
        readable_by: TrustLevel,
        excerpt_messages: int,
        max_excerpt_characters: int,
        latest_messages: int = 0,
    ) -> EpisodeReference:
        anchor = message
        visible = [
            Message.session_id == session.id,
            Message.role.in_(("user", "assistant")),
            Message.generation_status == "completed",
            Message.error_code.is_(None),
            func.length(func.btrim(Message.content)) > 0,
            or_(
                Message.role == "user",
                Message.tool_calls.is_(None),
                Message.tool_calls == JSON.NULL,
            ),
        ]
        if readable_by == TrustLevel.PUBLIC:
            visible.append(Message.trust_level == TrustLevel.PUBLIC.value)
        before_limit = (excerpt_messages + 1) // 2
        before = list(
            (
                await db.scalars(
                    select(Message)
                    .where(
                        *visible,
                        or_(
                            Message.created_at < anchor.created_at,
                            and_(
                                Message.created_at == anchor.created_at,
                                Message.id <= anchor.id,
                            ),
                        ),
                    )
                    .order_by(Message.created_at.desc(), Message.id.desc())
                    .limit(before_limit)
                )
            ).all()
        )
        after = list(
            (
                await db.scalars(
                    select(Message)
                    .where(
                        *visible,
                        or_(
                            Message.created_at > anchor.created_at,
                            and_(
                                Message.created_at == anchor.created_at,
                                Message.id > anchor.id,
                            ),
                        ),
                    )
                    .order_by(Message.created_at, Message.id)
                    .limit(excerpt_messages - len(before))
                )
            ).all()
        )
        messages = [*before, *after]
        if latest_messages:
            latest = list(
                (
                    await db.scalars(
                        select(Message)
                        .where(*visible)
                        .order_by(Message.created_at.desc(), Message.id.desc())
                        .limit(latest_messages)
                    )
                ).all()
            )
            messages = list({item.id: item for item in (*messages, *latest)}.values())
        messages.sort(key=lambda item: (item.created_at, item.id))
        per_message = max(1, max_excerpt_characters // max(1, len(messages)))
        excerpt = tuple(
            EpisodeExcerptMessage(
                id=message.id,
                role=message.role,
                content=message.content[:per_message],
                created_at=message.created_at,
            )
            for message in messages
        )
        title_visible = (
            readable_by == TrustLevel.PRIVATE
            or title_trust == TrustLevel.PUBLIC.value
        )
        return EpisodeReference(
            session_id=session.id,
            session_title=session.title if title_visible else None,
            anchor_message_id=anchor.id,
            anchor_created_at=anchor.created_at,
            anchor_role=anchor.role,
            anchor_content=anchor_content,
            excerpt=excerpt,
        )
