import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.memory.embeddings import (
    DOCUMENT_PREFIX,
    EMBEDDING_DIMENSIONS,
    EMBEDDING_SERVED_MODEL,
    QUERY_PREFIX,
    EmbeddingError,
    InfinityEmbeddingClient,
)
from mato_loop_backend.memory.indexing import MemoryIndexService
from mato_loop_backend.memory.service import MemoryService
from mato_loop_backend.models import (
    MemoryCategory,
    MemoryEmbedding,
    MemoryEmbeddingStatus,
    MemorySourceType,
)
from mato_loop_backend.settings import TrustLevel


def _vector(value: float = 1.0) -> list[float]:
    return [value, *([0.0] * (EMBEDDING_DIMENSIONS - 1))]


async def test_infinity_client_applies_e5_prefixes_and_preserves_batch_order() -> None:
    requests: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        requests.append(payload)
        return httpx.Response(
            200,
            json={
                "model": EMBEDDING_SERVED_MODEL,
                "data": [
                    {"index": index, "embedding": _vector(index + 1.0)}
                    for index, _text in enumerate(payload["input"])
                ],
            },
        )

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = InfinityEmbeddingClient(http)

    query = await client.embed_query("Familie")
    documents = await client.embed_documents(["Erster Fakt", "Zweiter Fakt"])
    await client.close()

    assert requests[0]["input"] == [f"{QUERY_PREFIX}Familie"]
    assert requests[1]["input"] == [
        f"{DOCUMENT_PREFIX}Erster Fakt",
        f"{DOCUMENT_PREFIX}Zweiter Fakt",
    ]
    assert query[0] == 1.0
    assert [vector[0] for vector in documents] == [1.0, 2.0]


async def test_infinity_client_rejects_invalid_dimensions() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "model": EMBEDDING_SERVED_MODEL,
                "data": [{"index": 0, "embedding": [1.0]}],
            },
        )

    client = InfinityEmbeddingClient(
        httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )

    with pytest.raises(EmbeddingError) as exc_info:
        await client.embed_query("Test")
    await client.close()

    assert exc_info.value.code == "memory.embeddingInvalidResponse"
    assert exc_info.value.transient is False


async def test_transient_index_failure_is_requeued_with_backoff() -> None:
    row = MemoryEmbedding(
        memory_id=uuid4(),
        profile_fingerprint="profile",
        content_hash="hash",
        status=MemoryEmbeddingStatus.RUNNING.value,
        attempts=1,
        claimed_at=datetime.now(UTC),
    )
    db = AsyncMock(spec=AsyncSession)
    db.get.return_value = row

    await MemoryIndexService._record_failure(
        db,
        [(row.memory_id, "hash", "profile")],
        EmbeddingError("memory.embeddingUnavailable", transient=True),
    )

    assert row.status == MemoryEmbeddingStatus.QUEUED.value
    assert row.vector is None
    assert row.next_attempt_at is not None
    assert row.error_code == "memory.embeddingUnavailable"
    db.commit.assert_awaited_once()


async def test_memory_creation_enqueues_embedding_in_same_service_transaction(
    monkeypatch,
) -> None:
    db = AsyncMock(spec=AsyncSession)
    enqueue = AsyncMock()
    monkeypatch.setattr(MemoryIndexService, "enqueue", enqueue)

    memory = await MemoryService._create(
        db,
        content="Ein neuer Fakt",
        category=MemoryCategory.FACT,
        source_type=MemorySourceType.MANUAL,
        trust_level=TrustLevel.PRIVATE,
    )

    enqueue.assert_awaited_once_with(db, memory)
    assert memory.pinned is False
    assert memory.seen_at is not None
    db.commit.assert_not_awaited()


@pytest.mark.parametrize(
    "source_type", [MemorySourceType.TOOL_CALL, MemorySourceType.DREAMING]
)
async def test_automatic_memory_creation_starts_unseen(
    monkeypatch, source_type: MemorySourceType
) -> None:
    db = AsyncMock(spec=AsyncSession)
    monkeypatch.setattr(MemoryIndexService, "enqueue", AsyncMock())

    memory = await MemoryService._create(
        db,
        content="Automatisch erkannter Fakt",
        category=MemoryCategory.FACT,
        source_type=source_type,
        trust_level=TrustLevel.PRIVATE,
    )

    assert memory.seen_at is None
