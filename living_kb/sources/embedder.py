"""OpenAI embeddings behind a small port that tests can fake."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, TypedDict, cast


class EmbeddingUsage(TypedDict):
    """The small, JSON-serializable usage record journaled with the vectors."""

    model: str
    input_tokens: int
    estimated_usd: float


class EmbeddedTexts(TypedDict):
    vectors: list[list[float]]
    usage: EmbeddingUsage


class EmbeddingTokenUsage(Protocol):
    @property
    def prompt_tokens(self) -> int: ...


class EmbeddingItem(Protocol):
    @property
    def index(self) -> int: ...

    @property
    def embedding(self) -> Sequence[float]: ...


class EmbeddingResponse(Protocol):
    @property
    def data(self) -> Sequence[EmbeddingItem]: ...

    @property
    def usage(self) -> EmbeddingTokenUsage: ...


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
        price_per_million_tokens: float,
    ) -> None:
        if batch_size < 1:
            raise ValueError("batch_size must be positive")
        if dimensions < 1:
            raise ValueError("dimensions must be positive")
        self._client = client
        self._model = model
        self._dimensions = dimensions
        self._batch_size = batch_size
        self._price_per_million_tokens = price_per_million_tokens

    async def embed(self, texts: list[str]) -> EmbeddedTexts:
        if not texts:
            return {
                "vectors": [],
                "usage": {"model": self._model, "input_tokens": 0, "estimated_usd": 0.0},
            }

        vectors: list[list[float] | None] = [None] * len(texts)
        input_tokens = 0
        for start in range(0, len(texts), self._batch_size):
            batch = texts[start : start + self._batch_size]
            response = await self._client.embeddings.create(
                input=batch,
                model=self._model,
                dimensions=self._dimensions,
            )
            input_tokens += response.usage.prompt_tokens
            items = sorted(response.data, key=lambda item: item.index)
            if len(items) != len(batch):
                raise ValueError("embedding response does not match the input batch")
            for offset, item in enumerate(items):
                vectors[start + offset] = list(item.embedding)
        return {
            "vectors": cast("list[list[float]]", vectors),
            "usage": {
                "model": self._model,
                "input_tokens": input_tokens,
                "estimated_usd": round(
                    input_tokens / 1_000_000 * self._price_per_million_tokens, 6
                ),
            },
        }
