import asyncio
from dataclasses import dataclass, field

from living_kb.sources.embedder import OpenAIEmbedder


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
    """Mimics `client.embeddings.create`, returning items in reverse order like a provider
    that does not guarantee ordering. Each vector encodes its text, so order is checkable.
    Prompt and total tokens differ, so a usage field mix-up is checkable."""

    calls: list[dict[str, object]] = field(default_factory=list)
    prompt_tokens_per_item: int = 10_000
    shared_vectors: list[list[float]] | None = None

    async def create(self, *, input: list[str], model: str, dimensions: int) -> Response:
        self.calls.append({"input": list(input), "model": model, "dimensions": dimensions})
        items = [
            Item(
                index=i,
                embedding=self.shared_vectors[i]
                if self.shared_vectors is not None
                else [float(len(t))] * dimensions,
            )
            for i, t in enumerate(input)
        ]
        prompt_tokens = self.prompt_tokens_per_item * len(input)
        return Response(data=list(reversed(items)), usage=Usage(prompt_tokens, prompt_tokens + 7))


@dataclass
class FakeClient:
    embeddings: FakeEmbeddings = field(default_factory=FakeEmbeddings)


def embedder(
    client: FakeClient,
    batch_size: int = 96,
    *,
    model: str = "text-embedding-3-small",
    price: float = 0.02,
) -> OpenAIEmbedder:
    return OpenAIEmbedder(
        client, model=model, dimensions=4, batch_size=batch_size, price_per_million_tokens=price
    )


def test_vectors_come_back_in_input_order_even_when_the_provider_shuffles_them() -> None:
    texts = ["a", "bb", "ccc"]

    result = asyncio.run(embedder(FakeClient()).embed(texts))

    assert result["vectors"] == [[1.0] * 4, [2.0] * 4, [3.0] * 4]


def test_texts_are_sent_in_batches_of_the_configured_size_and_reassembled_in_order() -> None:
    client = FakeClient()
    texts = ["a", "bb", "ccc", "dddd", "eeeee"]

    result = asyncio.run(embedder(client, batch_size=2).embed(texts))

    assert [call["input"] for call in client.embeddings.calls] == [
        ["a", "bb"],
        ["ccc", "dddd"],
        ["eeeee"],
    ]
    assert all(
        call["model"] == "text-embedding-3-small" and call["dimensions"] == 4
        for call in client.embeddings.calls
    )
    assert result["vectors"] == [[float(n)] * 4 for n in range(1, 6)]


def test_usage_reports_the_requested_model_prompt_tokens_and_usd() -> None:
    client = FakeClient()

    result = asyncio.run(
        embedder(client, model="embedding-model-from-settings-v9", price=0.03).embed(["a", "bb"])
    )

    assert result["usage"] == {
        "model": "embedding-model-from-settings-v9",
        "input_tokens": 20_000,
        "estimated_usd": round(20_000 / 1_000_000 * 0.03, 6),
    }
    assert result["usage"]["estimated_usd"] == 0.0006
    assert "total" not in result["usage"]


def test_usage_sums_prompt_tokens_from_every_batch_including_the_last_partial_one() -> None:
    client = FakeClient()

    result = asyncio.run(embedder(client, batch_size=2).embed(["a", "bb", "ccc", "dddd", "eeeee"]))

    assert result["usage"]["input_tokens"] == 50_000
    assert result["usage"]["estimated_usd"] == 0.001


def test_estimated_usd_uses_the_given_price_and_is_rounded_to_six_decimals() -> None:
    client = FakeClient()
    embeddings = FakeEmbeddings(prompt_tokens_per_item=12_345)
    client.embeddings = embeddings
    raw = 12_345 / 1_000_000 * 0.03

    result = asyncio.run(embedder(client, price=0.03).embed(["a"]))

    assert raw != round(raw, 6)
    assert result["usage"]["estimated_usd"] == round(raw, 6)


def test_an_empty_list_returns_no_vectors_and_zero_usage_without_calling_the_provider() -> None:
    client = FakeClient()

    result = asyncio.run(embedder(client).embed([]))

    assert result["vectors"] == []
    assert result["usage"] == {
        "model": "text-embedding-3-small",
        "input_tokens": 0,
        "estimated_usd": 0.0,
    }
    assert client.embeddings.calls == []


def test_returning_plain_lists_keeps_each_result_independent_of_the_provider_response() -> None:
    client = FakeClient()
    shared = [[1.0] * 4]
    client.embeddings.shared_vectors = shared

    first = asyncio.run(embedder(client).embed(["a"]))
    first["vectors"][0][0] = 99.0
    first["usage"]["input_tokens"] = 1
    second = asyncio.run(embedder(client).embed(["a"]))

    assert shared == [[1.0] * 4]
    assert second["vectors"] == [[1.0] * 4]
    assert second["usage"]["input_tokens"] == 10_000
