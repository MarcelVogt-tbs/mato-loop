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
from mato_loop_backend.memory.episode_indexing import EpisodeIndexService
from mato_loop_backend.memory.episode_retrieval import EpisodeRetrievalService
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
    MessageSearchChunk,
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
        user = UserAccount(username=f"memory-{uuid4().hex}", password_hash="test")
        db.add(user)
        await db.flush()
        for content, audience, vector in zip(
            contents,
            ["both", "private_only", "both"],
            vectors,
            strict=True,
        ):
            _, content_hash = normalize_content(content)
            memory = MemoryRecord(
                id=uuid4(),
                user_id=user.id,
                source_type="manual",
                pinned=False,
                audience=audience,
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
            owner_id=user.id,
            mode=MemoryRetrieveMode.SEARCH,
            query="How does the user prefer answers while working?",
            readable_by=TrustLevel.PUBLIC,
            limit=2,
        )

        assert result.items[0].content == public_content
        assert all(item.audience in {"both", "public_only"} for item in result.items)
        await db.rollback()


async def test_public_model_episode_rag_can_use_authorized_private_session() -> None:
    async with AsyncSessionFactory() as db:
        user = UserAccount(username=f"episode-{uuid4().hex}", password_hash="test")
        db.add(user)
        await db.flush()
        public_session = ChatSession(user_id=user.id, title="Architektur")
        private_session = ChatSession(user_id=user.id, title="Vertraulich")
        current_session = ChatSession(user_id=user.id, title="Aktuell")
        db.add_all([public_session, private_session, current_session])
        await db.flush()
        public_message = Message(
            session_id=public_session.id,
            role="assistant",
            content="Wir entschieden uns für PostgreSQL als zentrale Datenbank.",
            trust_level=TrustLevel.PUBLIC.value,
            created_at=datetime(2026, 8, 1, 10, tzinfo=UTC),
        )
        latest_user = Message(
            session_id=public_session.id,
            role="user",
            content="Die Migration ist noch nicht gestartet.",
            trust_level=TrustLevel.PUBLIC.value,
            created_at=datetime(2026, 8, 2, 10, tzinfo=UTC),
        )
        latest_assistant = Message(
            session_id=public_session.id,
            role="assistant",
            content="Als Nächstes erstellen wir zuerst ein Backup.",
            trust_level=TrustLevel.PUBLIC.value,
            created_at=datetime(2026, 8, 2, 11, tzinfo=UTC),
        )
        private_message = Message(
            session_id=private_session.id,
            role="assistant",
            content="Die geheime Alternative wäre eine private Oracle-Instanz.",
            trust_level=TrustLevel.PRIVATE.value,
        )
        db.add_all([public_message, latest_user, latest_assistant, private_message])
        await db.flush()
        await EpisodeIndexService.enqueue(db, public_message)
        await EpisodeIndexService.enqueue(db, private_message)
        chunks = list(
            (
                await db.scalars(
                    select(MessageSearchChunk).where(
                        MessageSearchChunk.message_id.in_(
                            [public_message.id, private_message.id]
                        )
                    )
                )
            ).all()
        )
        vectors = await embedding_client.embed_documents(
            [chunk.content for chunk in chunks]
        )
        for chunk, vector in zip(chunks, vectors, strict=True):
            chunk.status = MemoryEmbeddingStatus.READY.value
            chunk.vector = vector
        await db.flush()

        result = await EpisodeRetrievalService.retrieve(
            db,
            owner_id=user.id,
            current_session_id=current_session.id,
            readable_by=TrustLevel.PUBLIC,
            query="What was the secret Oracle alternative?",
            limit=2,
            max_semantic_distance=0.25,
            latest_messages=2,
        )

        assert result.references
        private_reference = next(
            reference
            for reference in result.references
            if reference.session_id == private_session.id
        )
        assert "Oracle" in " ".join(
            message.content for message in private_reference.excerpt
        )
        await db.rollback()


async def test_episode_rag_is_immediate_without_embedding_or_dreaming() -> None:
    marker = f"Sofortkennung-{uuid4().hex}"
    async with AsyncSessionFactory() as db:
        user = UserAccount(username=f"immediate-{uuid4().hex}", password_hash="test")
        db.add(user)
        await db.flush()
        prior_session = ChatSession(user_id=user.id, title="Frueher")
        current_session = ChatSession(user_id=user.id, title="Aktuell")
        db.add_all([prior_session, current_session])
        await db.flush()
        message = Message(
            session_id=prior_session.id,
            role="user",
            content=f"Die projektweite Referenz lautet {marker}.",
            trust_level=TrustLevel.PUBLIC.value,
        )
        db.add(message)
        await db.flush()
        await EpisodeIndexService.enqueue(db, message)
        chunk = await db.scalar(
            select(MessageSearchChunk).where(MessageSearchChunk.message_id == message.id)
        )

        result = await EpisodeRetrievalService.retrieve(
            db,
            owner_id=user.id,
            current_session_id=current_session.id,
            readable_by=TrustLevel.PUBLIC,
            query=marker,
            semantic_enabled=False,
            limit=1,
        )

        assert chunk is not None
        assert chunk.status == MemoryEmbeddingStatus.QUEUED.value
        assert result.references[0].session_id == prior_session.id
        assert marker in result.references[0].anchor_content
        await db.rollback()


async def test_local_rag_falls_back_to_postgres_fts(monkeypatch) -> None:
    content = f"Fallbackkennung {uuid4().hex}"
    query = content.split()[-1]
    _, content_hash = normalize_content(content)

    async def unavailable(_text: str) -> list[float]:
        raise EmbeddingError("memory.embeddingUnavailable", transient=True)

    monkeypatch.setattr(embedding_client, "embed_query", unavailable)
    async with AsyncSessionFactory() as db:
        user = UserAccount(username=f"fallback-{uuid4().hex}", password_hash="test")
        db.add(user)
        await db.flush()
        memory = MemoryRecord(
            id=uuid4(),
            user_id=user.id,
            source_type="manual",
            pinned=False,
            audience="both",
            category="fact",
            content=content,
            normalized_content_hash=content_hash,
        )
        db.add(memory)
        await db.flush()

        result = await MemoryRetrievalService.retrieve(
            db,
            owner_id=user.id,
            mode=MemoryRetrieveMode.SEARCH,
            query=f"{'-' * 40} {query}",
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
        user = UserAccount(username=f"distance-{uuid4().hex}", password_hash="test")
        db.add(user)
        await db.flush()
        memory_ids = []
        for content, vector in zip(
            [relevant_content, unrelated_content], vectors, strict=True
        ):
            _, content_hash = normalize_content(content)
            memory = MemoryRecord(
                id=uuid4(),
                user_id=user.id,
                source_type="manual",
                pinned=False,
                audience="both",
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
            owner_id=user.id,
            mode=MemoryRetrieveMode.SEARCH,
            query="Wie alt ist mein Sohn?",
            readable_by=TrustLevel.PUBLIC,
            limit=5,
            max_semantic_distance=0.21,
        )

        result_ids = {item.id for item in result.items}
        assert memory_ids[0] in result_ids
        assert memory_ids[1] not in result_ids
        await db.rollback()


async def test_browse_lists_pinned_and_unpinned_memories() -> None:
    async with AsyncSessionFactory() as db:
        user = UserAccount(username=f"browse-{uuid4().hex}", password_hash="test")
        db.add(user)
        await db.flush()
        created = []
        for pinned in [True, False, False]:
            content = f"Browse-{pinned}-{uuid4().hex}"
            _, content_hash = normalize_content(content)
            memory = MemoryRecord(
                id=uuid4(),
                user_id=user.id,
                source_type="manual",
                pinned=pinned,
                audience="both",
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
            owner_id=user.id,
            mode=MemoryRetrieveMode.BROWSE,
            readable_by=TrustLevel.PUBLIC,
            limit=2,
        )

        assert result.has_more is True
        assert result.next_cursor_updated_at is not None
        assert result.next_cursor_id is not None
        continued = await MemoryRetrievalService.retrieve(
            db,
            owner_id=user.id,
            mode=MemoryRetrieveMode.BROWSE,
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
        session = ChatSession(user_id=user.id)
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
            user_id=user.id,
            source_session_id=session.id,
            source_message_id=source_message.id,
            source_type="dreaming",
            pinned=False,
            audience="private_only",
            category="fact",
            revision=1,
            content=stale_content,
            normalized_content_hash=stale_hash,
        )
        canonical = MemoryRecord(
            user_id=user.id,
            source_session_id=session.id,
            source_message_id=correction_message.id,
            source_type="tool_call",
            pinned=False,
            audience="both",
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
            owner_id=user.id,
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
        )

        assert result.item is canonical
        assert result.updated is True
        assert canonical.revision == 3
        assert canonical.audience == "private_only"
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
