from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.memory.embeddings import EMBEDDING_DIMENSIONS, embedding_client
from mato_loop_backend.memory.retrieval import (
    MemoryRetrievalService,
    MemoryRetrievalStrategy,
    MemoryRetrieveMode,
)
from mato_loop_backend.memory.service import MemoryService
from mato_loop_backend.models import MemoryRecord
from mato_loop_backend.settings import TrustLevel


class _NestedTransaction:
    async def __aenter__(self):
        return None

    async def __aexit__(self, *_args):
        return None


def _record(content: str, *, trust: str = "public") -> MemoryRecord:
    return MemoryRecord(
        id=uuid4(),
        content=content,
        category="fact",
        retrieval_level="retrieve",
        trust_level=trust,
        normalized_content_hash=content,
        updated_at=datetime.now(UTC),
    )


async def test_hybrid_retrieval_boosts_candidates_found_by_both_strategies(
    monkeypatch,
) -> None:
    shared = _record("Gemeinsamer Treffer")
    lexical_only = _record("Lexikalischer Treffer")
    semantic_only = _record("Semantischer Treffer")
    lexical_result = Mock()
    lexical_result.all.return_value = [lexical_only, shared]
    semantic_result = Mock()
    semantic_result.all.return_value = [shared, semantic_only]
    db = AsyncMock(spec=AsyncSession)
    db.scalars.side_effect = [lexical_result, semantic_result]
    db.begin_nested = Mock(return_value=_NestedTransaction())
    monkeypatch.setattr(
        "mato_loop_backend.memory.retrieval.get_runtime_config",
        lambda: SimpleNamespace(embedding_enabled=True),
    )
    monkeypatch.setattr(
        embedding_client,
        "embed_query",
        AsyncMock(return_value=[1.0, *([0.0] * (EMBEDDING_DIMENSIONS - 1))]),
    )

    result = await MemoryRetrievalService.retrieve(
        db,
        mode=MemoryRetrieveMode.SEARCH,
        query="profile information",
        readable_by=TrustLevel.PUBLIC,
        limit=3,
    )

    assert result.strategy == MemoryRetrievalStrategy.HYBRID
    assert result.items[0].id == shared.id
    assert "memories.trust_level" in str(db.scalars.await_args_list[0].args[0])
    assert "memories.trust_level" in str(db.scalars.await_args_list[1].args[0])


async def test_search_uses_lexical_fallback_when_embeddings_are_disabled(
    monkeypatch,
) -> None:
    item = _record("Treffer")
    lexical_result = Mock()
    lexical_result.all.return_value = [item]
    db = AsyncMock(spec=AsyncSession)
    db.scalars.return_value = lexical_result
    monkeypatch.setattr(
        "mato_loop_backend.memory.retrieval.get_runtime_config",
        lambda: SimpleNamespace(embedding_enabled=False),
    )

    result = await MemoryRetrievalService.retrieve(
        db,
        mode=MemoryRetrieveMode.SEARCH,
        query="Treffer",
        readable_by=TrustLevel.PRIVATE,
    )

    assert result.strategy == MemoryRetrievalStrategy.LEXICAL
    assert result.items == [item]
    assert "memories.trust_level =" not in str(db.scalars.await_args.args[0])


async def test_browse_returns_stable_cursor_for_the_last_visible_item(
    monkeypatch,
) -> None:
    cursor_time = datetime(2026, 7, 23, 12, tzinfo=UTC)
    cursor_id = uuid4()
    items = [_record("Eins"), _record("Zwei"), _record("Drei")]
    list_memories = AsyncMock(
        return_value=SimpleNamespace(items=items, total=8)
    )
    monkeypatch.setattr(MemoryService, "list", list_memories)

    result = await MemoryRetrievalService.retrieve(
        AsyncMock(spec=AsyncSession),
        mode=MemoryRetrieveMode.BROWSE,
        query=None,
        readable_by=TrustLevel.PRIVATE,
        cursor_updated_at=cursor_time,
        cursor_id=cursor_id,
        limit=2,
    )

    assert result.items == items[:2]
    assert result.has_more is True
    assert result.next_cursor_updated_at == items[1].updated_at
    assert result.next_cursor_id == items[1].id
    assert list_memories.await_args.kwargs["cursor_updated_at"] == cursor_time
    assert list_memories.await_args.kwargs["cursor_id"] == cursor_id
    assert list_memories.await_args.kwargs["limit"] == 3
