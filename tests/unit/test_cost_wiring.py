import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

import openai
import pytest

from living_kb.config import Settings, settings
from living_kb.inngest_app import LazyEmbedder


@dataclass
class Item:
    index: int
    embedding: list[float]


@dataclass
class Usage:
    prompt_tokens: int
    total_tokens: int


@dataclass
class Response:
    data: list[Item]
    usage: Usage


@dataclass
class FakeEmbeddings:
    calls: list[dict[str, Any]] = field(default_factory=list)

    async def create(self, *, input: list[str], model: str, dimensions: int) -> Response:
        self.calls.append({"input": list(input), "model": model, "dimensions": dimensions})
        items = [Item(index=i, embedding=[1.0] * dimensions) for i in range(len(input))]
        return Response(data=list(reversed(items)), usage=Usage(20_000, 20_007))


@dataclass
class FakeClient:
    embeddings: FakeEmbeddings = field(default_factory=FakeEmbeddings)


def test_lazy_embedder_reports_usage_priced_with_the_configured_price(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configured_price = 0.07
    configured_model = "text-embedding-3-large-v2"
    monkeypatch.setattr(settings, "embedding_price_per_million_tokens", configured_price)
    monkeypatch.setattr(settings, "embedding_model", configured_model)
    client = FakeClient()
    monkeypatch.setattr(openai, "AsyncOpenAI", lambda **_: cast("Any", client))

    result = asyncio.run(LazyEmbedder()(["a", "bb"]))

    assert result["usage"] == {
        "model": configured_model,
        "input_tokens": 20_000,
        "estimated_usd": round(20_000 / 1_000_000 * configured_price, 6),
    }
    assert configured_model != "text-embedding-3-small"
    assert result["usage"]["estimated_usd"] != round(20_000 / 1_000_000 * 0.02, 6)
    assert client.embeddings.calls == [
        {
            "input": ["a", "bb"],
            "model": configured_model,
            "dimensions": settings.embedding_dimensions,
        }
    ]


def test_the_configured_price_is_read_from_an_env_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / ".env").write_text("EMBEDDING_PRICE_PER_MILLION_TOKENS=0.07\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    config = Settings()

    assert config.embedding_price_per_million_tokens == 0.07
