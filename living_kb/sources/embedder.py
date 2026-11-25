"""OpenAI embeddings behind a small port that tests can fake."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, cast


class EmbeddingItem(Protocol):
    @property
    def index(self) -> int: ...

    @property
    def embedding(self) -> Sequence[float]: ...


class EmbeddingResponse(Protocol):
    @property
    def data(self) -> Sequence[EmbeddingItem]: ...


class Embeddings(Protocol):
    async def create(
        self, *, input: list[str], model: str, dimensions: int
    ) -> EmbeddingResponse: ...


class EmbeddingClient(Protocol):
    @property
    def embeddings(self) -> Embeddings: ...


class OpenAIEmbedder:
    """Call the embeddings API in batches and restore the provider's shuffled order."""

    def __init__(
        self,
        client: EmbeddingClient,
        *,
        model: str,
        dimensions: int,
        batch_size: int,
    ) -> None:
        if batch_size < 1:
            raise ValueError("batch_size must be positive")
        if dimensions < 1:
            raise ValueError("dimensions must be positive")
        self._client = client
        self._model = model
        self._dimensions = dimensions
        self._batch_size = batch_size

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []

        vectors: list[list[float] | None] = [None] * len(texts)
        for start in range(0, len(texts), self._batch_size):
            batch = texts[start : start + self._batch_size]
            response = await self._client.embeddings.create(
                input=batch,
                model=self._model,
                dimensions=self._dimensions,
            )
            items = sorted(response.data, key=lambda item: item.index)
            if len(items) != len(batch):
                raise ValueError("embedding response does not match the input batch")
            for offset, item in enumerate(items):
                vectors[start + offset] = list(item.embedding)
        return cast("list[list[float]]", vectors)
