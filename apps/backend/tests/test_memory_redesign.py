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
    memory = MemoryRecord(
        id=uuid4(),
        content="Pinned",
        category="fact",
        trust_level="public",
        pinned=False,
        revision=1,
        normalized_content_hash="hash",
    )
    scalar_result = Mock()
    scalar_result.all.return_value = [memory]
    db = AsyncMock(spec=AsyncSession)
    db.scalars.return_value = scalar_result

    records = await MemoryService.pinned_context(
        db, input_trust_level=TrustLevel.PUBLIC
    )

    assert records == [memory]
    statement = str(db.scalars.await_args.args[0])
    assert "memories.pinned IS true" in statement
    assert "memories.trust_level" in statement

    await MemoryService.set_pinned(db, memory, pinned=True)
    assert memory.pinned is True
    assert memory.revision == 2


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


def test_episode_ranking_deduplicates_sessions_and_applies_public_filter() -> None:
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
            readable_by=TrustLevel.PUBLIC,
        )
    )
    private_sql = " ".join(
        str(item)
        for item in _candidate_filters(
            owner_id=uuid4(),
            current_session_id=uuid4(),
            readable_by=TrustLevel.PRIVATE,
        )
    )
    assert "messages.trust_level" in public_sql
    assert "messages.trust_level" not in private_sql
