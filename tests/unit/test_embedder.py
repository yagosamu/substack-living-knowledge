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
    that does not guarantee ordering. Each vector encodes its text, so order is checkable."""

    calls: list[dict[str, object]] = field(default_factory=list)

    async def create(self, *, input: list[str], model: str, dimensions: int) -> Response:
        self.calls.append({"input": list(input), "model": model, "dimensions": dimensions})
        items = [Item(index=i, embedding=[float(len(t))] * dimensions) for i, t in enumerate(input)]
        return Response(data=list(reversed(items)), usage=Usage(len(input), len(input)))


@dataclass
class FakeClient:
    embeddings: FakeEmbeddings = field(default_factory=FakeEmbeddings)


def embedder(client: FakeClient, batch_size: int = 96) -> OpenAIEmbedder:
    return OpenAIEmbedder(
        client, model="text-embedding-3-small", dimensions=4, batch_size=batch_size
    )


def test_vectors_come_back_in_input_order_even_when_the_provider_shuffles_them() -> None:
    texts = ["a", "bb", "ccc"]

    vectors = asyncio.run(embedder(FakeClient()).embed(texts))

    assert vectors == [[1.0] * 4, [2.0] * 4, [3.0] * 4]


def test_texts_are_sent_in_batches_of_the_configured_size_and_reassembled_in_order() -> None:
    client = FakeClient()
    texts = ["a", "bb", "ccc", "dddd", "eeeee", "ffffff", "ggggggg"]

    vectors = asyncio.run(embedder(client, batch_size=3).embed(texts))

    assert [call["input"] for call in client.embeddings.calls] == [
        ["a", "bb", "ccc"],
        ["dddd", "eeeee", "ffffff"],
        ["ggggggg"],
    ]
    assert all(
        call["model"] == "text-embedding-3-small" and call["dimensions"] == 4
        for call in client.embeddings.calls
    )
    assert vectors == [[float(n)] * 4 for n in range(1, 8)]


def test_an_empty_list_returns_no_vectors_without_calling_the_provider() -> None:
    client = FakeClient()

    assert asyncio.run(embedder(client).embed([])) == []
    assert client.embeddings.calls == []
