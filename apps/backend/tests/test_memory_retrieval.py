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
    _filters,
)
from mato_loop_backend.memory.service import (
    MemoryService,
    memory_audience_for_source,
    memory_audience_intersection,
    memory_lexical_terms,
    memory_visible_to,
)
from mato_loop_backend.models import MemoryAudience, MemoryRecord
from mato_loop_backend.settings import TrustLevel


class _NestedTransaction:
    async def __aenter__(self):
        return None

    async def __aexit__(self, *_args):
        return None


def _record(content: str, *, audience: str = "both") -> MemoryRecord:
    return MemoryRecord(
        id=uuid4(),
        content=content,
        category="fact",
        pinned=False,
        audience=audience,
        normalized_content_hash=content,
        updated_at=datetime.now(UTC),
    )


def test_memory_lexical_terms_are_safe_and_bounded() -> None:
    terms = memory_lexical_terms(
        f"{'-' * 40} First FIRST {'x' * 257} "
        + " ".join(f"term{index}" for index in range(30))
    )

    assert terms == ["first", *(f"term{index}" for index in range(19))]


def test_memory_audience_helpers_apply_source_defaults_and_intersections() -> None:
    assert memory_audience_for_source(TrustLevel.PUBLIC) == MemoryAudience.BOTH
    assert (
        memory_audience_for_source(TrustLevel.PRIVATE)
        == MemoryAudience.PRIVATE_ONLY
    )
    assert memory_visible_to(MemoryAudience.BOTH, TrustLevel.PUBLIC)
    assert memory_visible_to(MemoryAudience.BOTH, TrustLevel.PRIVATE)
    assert not memory_visible_to(MemoryAudience.PUBLIC_ONLY, TrustLevel.PRIVATE)
    assert (
        memory_audience_intersection(
            [MemoryAudience.BOTH, MemoryAudience.PRIVATE_ONLY]
        )
        == MemoryAudience.PRIVATE_ONLY
    )
    assert (
        memory_audience_intersection(
            [MemoryAudience.PUBLIC_ONLY, MemoryAudience.PRIVATE_ONLY]
        )
        is None
    )


def test_retrieval_filters_bind_each_owner() -> None:
    first_owner = uuid4()
    second_owner = uuid4()

    first = _filters(
        owner_id=first_owner,
        readable_by=TrustLevel.PRIVATE,
        category=None,
        pinned=None,
    )[0]
    second = _filters(
        owner_id=second_owner,
        readable_by=TrustLevel.PRIVATE,
        category=None,
        pinned=None,
    )[0]

    assert first.right.value == first_owner
    assert second.right.value == second_owner
    assert first.right.value != second.right.value


def test_retrieval_source_filter_is_optional_and_sql_level() -> None:
    common = {
        "owner_id": uuid4(),
        "readable_by": TrustLevel.PRIVATE,
        "category": None,
        "pinned": False,
    }

    unfiltered = _filters(**common)
    filtered = _filters(**common, source_types=("manual", "tool_call"))

    assert all("source_type" not in str(item) for item in unfiltered)
    source_filter = next(item for item in filtered if "source_type" in str(item))
    assert source_filter.right.value == ["manual", "tool_call"]


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

    owner_id = uuid4()
    result = await MemoryRetrievalService.retrieve(
        db,
        owner_id=owner_id,
        mode=MemoryRetrieveMode.SEARCH,
        query="profile information",
        readable_by=TrustLevel.PUBLIC,
        limit=3,
    )

    assert result.strategy == MemoryRetrievalStrategy.HYBRID
    assert result.items[0].id == shared.id
    assert "memories.audience" in str(db.scalars.await_args_list[0].args[0])
    assert "memories.audience" in str(db.scalars.await_args_list[1].args[0])
    assert "memories.user_id" in str(db.scalars.await_args_list[0].args[0])
    assert "memories.user_id" in str(db.scalars.await_args_list[1].args[0])


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
        owner_id=uuid4(),
        mode=MemoryRetrieveMode.SEARCH,
        query="Treffer",
        readable_by=TrustLevel.PRIVATE,
    )

    assert result.strategy == MemoryRetrievalStrategy.LEXICAL
    assert result.items == [item]
    statement = str(db.scalars.await_args.args[0])
    assert "memories.audience" in statement
    assert "plainto_tsquery" in statement
    assert "websearch_to_tsquery" not in statement


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
        owner_id=(owner_id := uuid4()),
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
    assert list_memories.await_args.kwargs["owner_id"] == owner_id
