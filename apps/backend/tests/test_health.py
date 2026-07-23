from unittest.mock import AsyncMock

from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.api.routes.health import readiness
from mato_loop_backend.memory.embeddings import embedding_client


async def test_readiness_reports_embedding_degradation_without_failing(monkeypatch) -> None:
    db = AsyncMock(spec=AsyncSession)
    db.scalar.return_value = "0.8.5"
    monkeypatch.setattr(embedding_client, "available", AsyncMock(return_value=False))

    result = await readiness(db)

    assert result["status"] == "ok"
    assert result["vector"] == "ok"
    assert result["embedding"] == "degraded"
