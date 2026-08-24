from datetime import UTC, datetime
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.memory.episode_indexing import (
    MAX_CHUNK_CHARACTERS,
    chunk_message_content,
)
from mato_loop_backend.memory.episode_retrieval import (
    _Candidate,
    _candidate_filters,
    _rank_candidates,
)
from mato_loop_backend.memory.service import MemoryService
from mato_loop_backend.models import ChatSession, MemoryRecord, Message, MessageSearchChunk
from mato_loop_backend.settings import TrustLevel


def test_episode_chunking_is_deterministic_paragraph_aware_and_bounded() -> None:
    content = "\n\n".join(
        f"Paragraph {index}: " + (f"word-{index} " * 90)
        for index in range(8)
    )

    chunks = chunk_message_content(content)

    assert chunks == chunk_message_content(content.replace("\n", "\r\n"))
    assert len(chunks) > 1
    assert all(0 < len(chunk) <= MAX_CHUNK_CHARACTERS for chunk in chunks)
    assert chunks[0].startswith("Paragraph 0:")
    assert "Paragraph 7:" in chunks[-1]


async def test_pinned_context_filters_visibility_and_set_pinned_preserves_index() -> None:
    owner_id = uuid4()
    memory = MemoryRecord(
        id=uuid4(),
        user_id=owner_id,
        content="Pinned",
        category="fact",
        audience="both",
        pinned=False,
        revision=1,
        normalized_content_hash="hash",
    )
    scalar_result = Mock()
    scalar_result.all.return_value = [memory]
    db = AsyncMock(spec=AsyncSession)
    db.scalars.return_value = scalar_result

    records = await MemoryService.pinned_context(
        db, owner_id=owner_id, input_trust_level=TrustLevel.PUBLIC
    )

    assert records == [memory]
    statement = str(db.scalars.await_args.args[0])
    assert "memories.pinned IS true" in statement
    assert "memories.audience" in statement
    assert "memories.user_id" in statement

    await MemoryService.set_pinned(db, memory, owner_id=owner_id, pinned=True)
    assert memory.pinned is True
    assert memory.revision == 2


async def test_pinned_context_prefers_audience_specific_identity() -> None:
    owner_id = uuid4()
    shared = MemoryRecord(
        id=uuid4(),
        user_id=owner_id,
        content="Shared name",
        category="identity",
        audience="both",
        pinned=True,
        pin_kind="preferred_name",
        normalized_content_hash="shared",
    )
    private = MemoryRecord(
        id=uuid4(),
        user_id=owner_id,
        content="Private name",
        category="identity",
        audience="private_only",
        pinned=True,
        pin_kind="preferred_name",
        normalized_content_hash="private",
    )
    fact = MemoryRecord(
        id=uuid4(),
        user_id=owner_id,
        content="Pinned fact",
        category="fact",
        audience="both",
        pinned=True,
        normalized_content_hash="fact",
    )
    scalar_result = Mock()
    scalar_result.all.return_value = [shared, private, fact]
    db = AsyncMock(spec=AsyncSession)
    db.scalars.return_value = scalar_result

    records = await MemoryService.pinned_context(
        db, owner_id=owner_id, input_trust_level=TrustLevel.PRIVATE
    )

    assert records == [private, fact]


def _candidate(session_id, message_id, chunk_id, created_at) -> _Candidate:
    session = ChatSession(id=session_id, user_id=uuid4(), title="Session")
    message = Message(
        id=message_id,
        session_id=session_id,
        role="user",
        content="Relevant content",
        created_at=created_at,
    )
    chunk = MessageSearchChunk(
        id=chunk_id,
        message_id=message_id,
        chunk_index=0,
        content="Relevant content",
        content_hash="hash",
        profile_fingerprint="profile",
    )
    return _Candidate(chunk, message, session, "public")


def test_episode_ranking_deduplicates_sessions_without_destination_trust_filter() -> None:
    now = datetime.now(UTC)
    first_session = uuid4()
    shared = _candidate(first_session, uuid4(), uuid4(), now)
    same_session = _candidate(first_session, uuid4(), uuid4(), now)
    other = _candidate(uuid4(), uuid4(), uuid4(), now)

    ranked = _rank_candidates(
        [same_session, shared, other],
        [shared],
        limit=3,
    )

    assert ranked[0] is shared
    assert len(ranked) == 2
    assert len({item.session.id for item in ranked}) == 2

    public_sql = " ".join(
        str(item)
        for item in _candidate_filters(
            owner_id=uuid4(),
            current_session_id=uuid4(),
            project_id=None,
            readable_by=TrustLevel.PUBLIC,
        )
    )
    private_sql = " ".join(
        str(item)
        for item in _candidate_filters(
            owner_id=uuid4(),
            current_session_id=uuid4(),
            project_id=None,
            readable_by=TrustLevel.PRIVATE,
        )
    )
    assert "messages.trust_level" not in public_sql
    assert "messages.trust_level" not in private_sql
    assert "private_integration_data_used" not in public_sql

    global_sql = " ".join(
        str(item)
        for item in _candidate_filters(
            owner_id=uuid4(),
            current_session_id=None,
            project_id=None,
            readable_by=TrustLevel.PRIVATE,
            all_projects=True,
        )
    )
    assert "chat_sessions.user_id" in global_sql
    assert "chat_sessions.id !=" not in global_sql
    assert "chat_sessions.project_id" not in global_sql
