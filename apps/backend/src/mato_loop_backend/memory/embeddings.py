from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from mato_loop_backend.config import get_runtime_config

EMBEDDING_MODEL_ID = "intfloat/multilingual-e5-small"
EMBEDDING_MODEL_REVISION = "614241f622f53c4eeff9890bdc4f31cfecc418b3"
EMBEDDING_SERVED_MODEL = "matoloop-memory"
EMBEDDING_DIMENSIONS = 384
QUERY_PREFIX = "query: "
DOCUMENT_PREFIX = "passage: "


@dataclass(frozen=True, slots=True)
class EmbeddingProfile:
    model_id: str = EMBEDDING_MODEL_ID
    revision: str = EMBEDDING_MODEL_REVISION
    dimensions: int = EMBEDDING_DIMENSIONS
    query_prefix: str = QUERY_PREFIX
    document_prefix: str = DOCUMENT_PREFIX

    @property
    def fingerprint(self) -> str:
        value = "\x00".join(
            (
                self.model_id,
                self.revision,
                str(self.dimensions),
                self.query_prefix,
                self.document_prefix,
            )
        )
        return hashlib.sha256(value.encode("utf-8")).hexdigest()


ACTIVE_EMBEDDING_PROFILE = EmbeddingProfile()


class EmbeddingError(RuntimeError):
    def __init__(self, code: str, *, transient: bool) -> None:
        super().__init__(code)
        self.code = code
        self.transient = transient


class _EmbeddingItem(BaseModel):
    model_config = ConfigDict(extra="ignore")

    index: int = Field(ge=0)
    embedding: list[float]


class _EmbeddingResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    model: str
    data: list[_EmbeddingItem]


class _ModelItem(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    capabilities: set[str] = Field(default_factory=set)


class _ModelsResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    data: list[_ModelItem]


class InfinityEmbeddingClient:
    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        config = get_runtime_config()
        self._base_url = config.embedding_base_url.rstrip("/")
        self._query_timeout = config.embedding_query_timeout_seconds
        self._index_timeout = config.embedding_index_timeout_seconds
        self._client = client

    @property
    def _http(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient()
        return self._client

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def available(self) -> bool:
        try:
            response = await self._http.get(f"{self._base_url}/models", timeout=2.0)
            response.raise_for_status()
            payload = _ModelsResponse.model_validate(response.json())
        except (httpx.HTTPError, ValueError, ValidationError):
            return False
        return any(
            item.id in {EMBEDDING_MODEL_ID, EMBEDDING_SERVED_MODEL}
            and (not item.capabilities or "embed" in item.capabilities)
            for item in payload.data
        )

    async def embed_query(self, text: str) -> list[float]:
        vectors = await self._embed([f"{QUERY_PREFIX}{text.strip()}"], timeout=self._query_timeout)
        return vectors[0]

    async def embed_queries(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        return await self._embed(
            [f"{QUERY_PREFIX}{text.strip()}" for text in texts],
            timeout=self._index_timeout,
        )

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        return await self._embed(
            [f"{DOCUMENT_PREFIX}{text.strip()}" for text in texts],
            timeout=self._index_timeout,
        )

    async def _embed(self, inputs: list[str], *, timeout: float) -> list[list[float]]:
        try:
            response = await self._http.post(
                f"{self._base_url}/embeddings",
                json={
                    "model": EMBEDDING_SERVED_MODEL,
                    "input": inputs,
                    "encoding_format": "float",
                },
                timeout=timeout,
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            code = (
                "memory.embeddingUnavailable"
                if exc.response.status_code >= 500
                else "memory.embeddingInvalidResponse"
            )
            raise EmbeddingError(code, transient=exc.response.status_code >= 500) from exc
        except httpx.HTTPError as exc:
            raise EmbeddingError("memory.embeddingUnavailable", transient=True) from exc

        try:
            payload = _EmbeddingResponse.model_validate(response.json())
        except (ValueError, ValidationError) as exc:
            raise EmbeddingError("memory.embeddingInvalidResponse", transient=False) from exc
        if payload.model not in {EMBEDDING_MODEL_ID, EMBEDDING_SERVED_MODEL}:
            raise EmbeddingError("memory.embeddingProfileMismatch", transient=False)
        if len(payload.data) != len(inputs):
            raise EmbeddingError("memory.embeddingInvalidResponse", transient=False)

        ordered = sorted(payload.data, key=lambda item: item.index)
        if [item.index for item in ordered] != list(range(len(inputs))):
            raise EmbeddingError("memory.embeddingInvalidResponse", transient=False)
        vectors = [item.embedding for item in ordered]
        for vector in vectors:
            if len(vector) != EMBEDDING_DIMENSIONS:
                raise EmbeddingError("memory.embeddingInvalidResponse", transient=False)
            if not all(math.isfinite(value) for value in vector) or not any(vector):
                raise EmbeddingError("memory.embeddingInvalidResponse", transient=False)
        return vectors


embedding_client = InfinityEmbeddingClient()
