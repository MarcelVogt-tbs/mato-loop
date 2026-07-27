import os
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from mato_loop_backend.db.session import AsyncSessionFactory, engine
from mato_loop_backend.memory.embeddings import (
    ACTIVE_EMBEDDING_PROFILE,
    EmbeddingError,
    embedding_client,
)
from mato_loop_backend.memory.retrieval import (
    MemoryRetrievalService,
    MemoryRetrievalStrategy,
    MemoryRetrieveMode,
)
from mato_loop_backend.memory.service import MemoryService, normalize_content
from mato_loop_backend.models import (
    AgentRun,
    ChatSession,
    MemoryCategory,
    MemoryEmbedding,
    MemoryEmbeddingStatus,
    MemoryRecord,
    MemorySourceMessage,
    Message,
    UserAccount,
)
from mato_loop_backend.settings import TrustLevel

pytestmark = pytest.mark.skipif(
    os.getenv("MATO_LOOP_RAG_INTEGRATION") != "1",
    reason="requires the local pgvector and Infinity stack",
)


@pytest.fixture(autouse=True)
async def _dispose_database_pool_after_test():
    yield
    await embedding_client.close()
    await engine.dispose()


async def test_local_rag_finds_cross_language_memory_without_leaking_private() -> None:
    public_content = "Der Nutzer bevorzugt bei der Arbeit kurze und direkte Antworten."
    private_content = "The user prefers detailed private financial reports."
    unrelated_content = "Der Nutzer interessiert sich für das Wetter in Bayern."
    contents = [public_content, private_content, unrelated_content]
    vectors = await embedding_client.embed_documents(contents)

    async with AsyncSessionFactory() as db:
        for content, trust_level, vector in zip(
            contents,
            ["public", "private", "public"],
            vectors,
            strict=True,
        ):
            _, content_hash = normalize_content(content)
            memory = MemoryRecord(
                id=uuid4(),
                source_type="manual",
                retrieval_level="retrieve",
                trust_level=trust_level,
                category="preference",
                content=content,
                normalized_content_hash=content_hash,
            )
            db.add(memory)
            await db.flush()
            db.add(
                MemoryEmbedding(
                    memory_id=memory.id,
                    profile_fingerprint=ACTIVE_EMBEDDING_PROFILE.fingerprint,
                    content_hash=content_hash,
                    status=MemoryEmbeddingStatus.READY.value,
                    vector=vector,
                )
            )
        await db.flush()

        result = await MemoryRetrievalService.retrieve(
            db,
            mode=MemoryRetrieveMode.SEARCH,
            query="How does the user prefer answers while working?",
            readable_by=TrustLevel.PUBLIC,
            limit=2,
        )

        assert result.items[0].content == public_content
        assert all(item.trust_level == "public" for item in result.items)
        await db.rollback()


async def test_local_rag_falls_back_to_postgres_fts(monkeypatch) -> None:
    content = f"Fallbackkennung {uuid4().hex}"
    query = content.split()[-1]
    _, content_hash = normalize_content(content)

    async def unavailable(_text: str) -> list[float]:
        raise EmbeddingError("memory.embeddingUnavailable", transient=True)

    monkeypatch.setattr(embedding_client, "embed_query", unavailable)
    async with AsyncSessionFactory() as db:
        memory = MemoryRecord(
            id=uuid4(),
            source_type="manual",
            retrieval_level="retrieve",
            trust_level="public",
            category="fact",
            content=content,
            normalized_content_hash=content_hash,
        )
        db.add(memory)
        await db.flush()

        result = await MemoryRetrievalService.retrieve(
            db,
            mode=MemoryRetrieveMode.SEARCH,
            query=query,
            readable_by=TrustLevel.PUBLIC,
            limit=1,
        )

        assert result.strategy == MemoryRetrievalStrategy.LEXICAL
        assert result.items[0].id == memory.id
        await db.rollback()


async def test_automatic_rag_distance_excludes_unrelated_memory() -> None:
    relevant_content = "Das Kind des Testnutzers ist genau neun Monate alt."
    unrelated_content = "Eine Anleitung beschreibt das Backen von Sauerteigbrot."
    vectors = await embedding_client.embed_documents(
        [relevant_content, unrelated_content]
    )

    async with AsyncSessionFactory() as db:
        memory_ids = []
        for content, vector in zip(
            [relevant_content, unrelated_content], vectors, strict=True
        ):
            _, content_hash = normalize_content(content)
            memory = MemoryRecord(
                id=uuid4(),
                source_type="manual",
                retrieval_level="retrieve",
                trust_level="public",
                category="fact",
                content=content,
                normalized_content_hash=content_hash,
            )
            memory_ids.append(memory.id)
            db.add(memory)
            await db.flush()
            db.add(
                MemoryEmbedding(
                    memory_id=memory.id,
                    profile_fingerprint=ACTIVE_EMBEDDING_PROFILE.fingerprint,
                    content_hash=content_hash,
                    status=MemoryEmbeddingStatus.READY.value,
                    vector=vector,
                )
            )
        await db.flush()

        result = await MemoryRetrievalService.retrieve(
            db,
            mode=MemoryRetrieveMode.SEARCH,
            query="Wie alt ist mein Sohn?",
            retrieval_level=None,
            readable_by=TrustLevel.PUBLIC,
            limit=5,
            max_semantic_distance=0.21,
        )

        result_ids = {item.id for item in result.items}
        assert memory_ids[0] in result_ids
        assert memory_ids[1] not in result_ids
        await db.rollback()


async def test_browse_lists_instant_and_retrieve_memories() -> None:
    async with AsyncSessionFactory() as db:
        created = []
        for level in ["instant", "retrieve", "retrieve"]:
            content = f"Browse-{level}-{uuid4().hex}"
            _, content_hash = normalize_content(content)
            memory = MemoryRecord(
                id=uuid4(),
                source_type="manual",
                retrieval_level=level,
                trust_level="public",
                category="fact",
                content=content,
                normalized_content_hash=content_hash,
                updated_at=datetime(2100, 1, 1, tzinfo=UTC),
            )
            created.append(memory.id)
            db.add(memory)
        await db.flush()

        result = await MemoryRetrievalService.retrieve(
            db,
            mode=MemoryRetrieveMode.BROWSE,
            retrieval_level=None,
            readable_by=TrustLevel.PUBLIC,
            limit=2,
        )

        assert result.has_more is True
        assert result.next_cursor_updated_at is not None
        assert result.next_cursor_id is not None
        continued = await MemoryRetrievalService.retrieve(
            db,
            mode=MemoryRetrieveMode.BROWSE,
            retrieval_level=None,
            readable_by=TrustLevel.PUBLIC,
            cursor_updated_at=result.next_cursor_updated_at,
            cursor_id=result.next_cursor_id,
            limit=2,
        )

        first_ids = {item.id for item in result.items}
        continued_ids = {item.id for item in continued.items}
        assert first_ids.isdisjoint(continued_ids)
        assert set(created).issubset(first_ids | continued_ids)
        assert result.total is not None
        assert continued.total == result.total
        await db.rollback()


async def test_tool_update_merges_exact_duplicate_and_removes_stale_embedding() -> None:
    stale_content = f"Alter Herkunftsort {uuid4().hex}"
    canonical_content = f"Neuer Herkunftsort {uuid4().hex}"
    _, stale_hash = normalize_content(stale_content)
    _, canonical_hash = normalize_content(canonical_content)

    async with AsyncSessionFactory() as db:
        user = UserAccount(username=f"merge-{uuid4().hex}", password_hash="test")
        db.add(user)
        await db.flush()
        session = ChatSession(user_id=user.id, trust_level=TrustLevel.PRIVATE.value)
        db.add(session)
        await db.flush()
        source_message = Message(
            session_id=session.id,
            role="user",
            content=stale_content,
            trust_level=TrustLevel.PRIVATE.value,
        )
        correction_message = Message(
            session_id=session.id,
            role="user",
            content=canonical_content,
            trust_level=TrustLevel.PRIVATE.value,
        )
        db.add_all([source_message, correction_message])
        await db.flush()
        agent_run = AgentRun(
            session_id=session.id,
            status="completed",
            trust_level=TrustLevel.PRIVATE.value,
        )
        db.add(agent_run)
        await db.flush()
        stale = MemoryRecord(
            source_session_id=session.id,
            source_message_id=source_message.id,
            source_type="dreaming",
            retrieval_level="retrieve",
            trust_level="private",
            category="fact",
            revision=1,
            content=stale_content,
            normalized_content_hash=stale_hash,
        )
        canonical = MemoryRecord(
            source_session_id=session.id,
            source_message_id=correction_message.id,
            source_type="tool_call",
            retrieval_level="retrieve",
            trust_level="public",
            category="context",
            revision=2,
            content=canonical_content,
            normalized_content_hash=canonical_hash,
        )
        db.add_all([stale, canonical])
        await db.flush()
        db.add(
            MemorySourceMessage(memory_id=stale.id, message_id=source_message.id)
        )
        db.add(
            MemoryEmbedding(
                memory_id=stale.id,
                profile_fingerprint=ACTIVE_EMBEDDING_PROFILE.fingerprint,
                content_hash=stale_hash,
                status=MemoryEmbeddingStatus.READY.value,
                vector=[0.0] * ACTIVE_EMBEDDING_PROFILE.dimensions,
            )
        )
        await db.flush()

        result = await MemoryService.update_tool(
            db,
            memory_id=stale.id,
            expected_revision=1,
            content=canonical_content,
            category=MemoryCategory.FACT,
            readable_by=TrustLevel.PRIVATE,
            write_trust_level=TrustLevel.PRIVATE,
            session_id=session.id,
            evidence_message_id=correction_message.id,
            run_id=agent_run.id,
            tool_call_id="correction",
            max_context_characters=20_000,
        )

        assert result.item is canonical
        assert result.updated is True
        assert canonical.revision == 3
        assert canonical.trust_level == TrustLevel.PRIVATE.value
        remaining_ids = set(
            (
                await db.scalars(
                    select(MemoryRecord.id).where(
                        MemoryRecord.id.in_([stale.id, canonical.id])
                    )
                )
            ).all()
        )
        assert remaining_ids == {canonical.id}
        evidence_ids = set(
            (
                await db.scalars(
                    select(MemorySourceMessage.message_id).where(
                        MemorySourceMessage.memory_id == canonical.id
                    )
                )
            ).all()
        )
        assert evidence_ids == {source_message.id, correction_message.id}
        stale_embedding_count = await db.scalar(
            select(func.count())
            .select_from(MemoryEmbedding)
            .where(MemoryEmbedding.memory_id == stale.id)
        )
        assert stale_embedding_count == 0
        await db.rollback()
