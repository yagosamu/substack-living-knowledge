import asyncio
from collections.abc import Awaitable, Callable, Coroutine
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, cast

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from living_kb.config import settings
from living_kb.durable import InjectedFailure, JournalRunner, StepRunner
from living_kb.pipelines.ingest import FetchedPage, IngestPorts, ingest_article
from living_kb.sources.embedder import EmbeddedTexts
from living_kb.sources.publications import Publication

SessionFactory = async_sessionmaker[AsyncSession]
ARTICLE_URL = "https://news.example.test/p/cost-of-embeddings"
ARTICLE_HTML = (Path(__file__).parent / "fixtures" / "articles" / "durable-agents.html").read_text()
SHORT_HTML = """<html><head>
<meta property="og:title" content="A short note about embedding costs">
<meta property="article:published_time" content="2026-11-26T10:00:00+00:00">
</head><body><article><h1>A short note about embedding costs</h1>
<p>Checkpoint the usage record with the vectors so a replay never needs a second paid call.</p>
</article></body></html>"""
EXAMPLE = Publication(
    slug="news-example",
    name="News Example",
    feed_url="https://news.example.test/feed",
    homepage="https://news.example.test",
)
MODEL = "fake-model-with-dashes-v3"
PRICE = 0.07
TOKENS = 12_345
USD = round(TOKENS / 1_000_000 * PRICE, 6)


@dataclass
class FakeEmbedder:
    model: str = MODEL
    input_tokens: int = TOKENS
    price: float = PRICE
    calls: int = 0

    async def __call__(self, texts: list[str]) -> EmbeddedTexts:
        self.calls += 1
        return {
            "vectors": [
                [0.001 * (index + 1)] * settings.embedding_dimensions for index in range(len(texts))
            ],
            "usage": {
                "model": self.model,
                "input_tokens": self.input_tokens,
                "estimated_usd": round(self.input_tokens / 1_000_000 * self.price, 6),
            },
        }


class FakeFetcher:
    def __init__(self, html: str) -> None:
        self.html = html
        self.calls: list[str] = []

    async def __call__(self, url: str) -> FetchedPage:
        self.calls.append(url)
        return FetchedPage(url=url, html=self.html)


class RecordingSender:
    def __init__(self) -> None:
        self.sent: list[tuple[str, dict[str, object]]] = []

    async def __call__(self, name: str, data: dict[str, object]) -> None:
        self.sent.append((name, data))


class CountingSessionFactory:
    def __init__(self, inner: SessionFactory) -> None:
        self.inner = inner
        self.opened = 0

    def __call__(self) -> AsyncSession:
        self.opened += 1
        return self.inner()


class MutatingRunner:
    def __init__(self, inner: JournalRunner, *, tokens: int) -> None:
        self.inner = inner
        self.tokens = tokens
        self.mutated = False

    async def run(self, name: str, fn: Callable[[], Any]) -> Any:
        result = await self.inner.run(name, fn)
        if name == "embed" and not self.mutated:
            self.mutated = True
            cast("EmbeddedTexts", result)["usage"]["input_tokens"] = self.tokens
        return result

    async def execute[T](self, pipeline: Callable[[StepRunner], Awaitable[T]]) -> T:
        return await self.inner.execute(pipeline)


def event(publication_id: int) -> dict[str, object]:
    return {
        "canonical_id": "pub:news-example:cost-of-embeddings",
        "publication_id": publication_id,
        "url": ARTICLE_URL,
        "title": "costs (feed title)",
        "published_at": "2026-11-20T08:00:00+00:00",
        "via": "rss",
    }


def make_ports(
    session_factory: SessionFactory,
    publication_id: int,
    embedder: FakeEmbedder,
    *,
    html: str = ARTICLE_HTML,
    sender: RecordingSender | None = None,
) -> IngestPorts:
    return IngestPorts(
        fetch_html=FakeFetcher(html),
        embed=embedder,
        send_event=sender or RecordingSender(),
        session_factory=session_factory,
        publications=[EXAMPLE],
    )


def pipeline(
    port_list: IngestPorts, publication_id: int
) -> Callable[[StepRunner], Coroutine[Any, Any, Any]]:
    return lambda runner: ingest_article(runner, event(publication_id), port_list)


async def seed_publication(session_factory: SessionFactory) -> int:
    async with session_factory() as session:
        publication_id = await session.scalar(
            text(
                "INSERT INTO publications (slug, name, feed_url, homepage) "
                "VALUES ('news-example', 'News Example', 'https://news.example.test/feed', "
                "'https://news.example.test') RETURNING id"
            )
        )
        await session.commit()
        assert isinstance(publication_id, int)
        return publication_id


async def rows(session_factory: SessionFactory, sql: str) -> list[Any]:
    async with session_factory() as session:
        return list((await session.execute(text(sql))).all())


async def counts(session_factory: SessionFactory) -> tuple[int, int, int]:
    (row,) = await rows(
        session_factory,
        "SELECT (SELECT count(*) FROM articles), (SELECT count(*) FROM passages), "
        "(SELECT count(*) FROM costs)",
    )
    return int(row[0]), int(row[1]), int(row[2])


COST_COLUMNS = "article_id, model, input_tokens, output_tokens, estimated_usd"


@pytest.fixture
def publication_id(session_factory: SessionFactory, clean_db: None) -> int:
    return asyncio.run(seed_publication(session_factory))


@pytest.mark.integration
def test_fake_embedder_usage_is_saved_as_one_cost_row_linked_to_the_article(
    session_factory: SessionFactory, publication_id: int
) -> None:
    embedder = FakeEmbedder()

    result = asyncio.run(
        JournalRunner(max_output_bytes=100_000).execute(
            pipeline(make_ports(session_factory, publication_id, embedder), publication_id)
        )
    )

    cost_rows = asyncio.run(rows(session_factory, f"SELECT {COST_COLUMNS} FROM costs"))
    assert len(cost_rows) == 1
    (cost,) = cost_rows
    assert cost.article_id == result["article_id"]
    assert cost.model == MODEL != settings.embedding_model
    assert cost.input_tokens == TOKENS
    assert cost.output_tokens == 0
    assert (
        float(cost.estimated_usd)
        == USD
        != round(TOKENS / 1_000_000 * settings.embedding_price_per_million_tokens, 6)
    )


@pytest.mark.integration
def test_a_fresh_redelivery_keeps_exactly_one_cost_row(
    session_factory: SessionFactory, publication_id: int
) -> None:
    first = FakeEmbedder()
    asyncio.run(
        JournalRunner().execute(
            pipeline(make_ports(session_factory, publication_id, first), publication_id)
        )
    )
    before = asyncio.run(rows(session_factory, f"SELECT {COST_COLUMNS} FROM costs"))

    changed = FakeEmbedder(model="a-later-run-model", input_tokens=1, price=0.02)
    asyncio.run(
        JournalRunner().execute(
            pipeline(make_ports(session_factory, publication_id, changed), publication_id)
        )
    )

    after = asyncio.run(rows(session_factory, f"SELECT {COST_COLUMNS} FROM costs"))
    assert len(before) == len(after) == 1
    assert after == before
    assert asyncio.run(counts(session_factory))[0] == 1
    assert changed.calls == 1


@pytest.mark.integration
def test_a_replay_after_a_successful_run_does_not_touch_the_cost_row(
    session_factory: SessionFactory, publication_id: int
) -> None:
    counting = CountingSessionFactory(session_factory)
    embedder = FakeEmbedder()
    ports = replace(
        make_ports(session_factory, publication_id, embedder),
        session_factory=cast("SessionFactory", counting),
    )
    runner = JournalRunner()

    asyncio.run(runner.execute(pipeline(ports, publication_id)))
    before = asyncio.run(rows(session_factory, f"SELECT {COST_COLUMNS} FROM costs"))
    runner.fail_next("emit", after_side_effect=True)

    with pytest.raises(InjectedFailure):
        asyncio.run(runner.execute(pipeline(ports, publication_id)))

    assert counting.opened == 1
    assert embedder.calls == 1
    assert asyncio.run(rows(session_factory, f"SELECT {COST_COLUMNS} FROM costs")) == before


@pytest.mark.integration
def test_usage_travels_through_the_journaled_embed_step(
    session_factory: SessionFactory, publication_id: int
) -> None:
    runner = JournalRunner(max_output_bytes=100_000)
    first = FakeEmbedder(model="usage-in-the-journal", input_tokens=111, price=PRICE)
    first_ports = make_ports(session_factory, publication_id, first, html=SHORT_HTML)
    runner.fail_next("persist")

    with pytest.raises(InjectedFailure):
        asyncio.run(runner.execute(pipeline(first_ports, publication_id)))

    changed = FakeEmbedder(model="usage-from-a-second-provider", input_tokens=999_999, price=0.02)
    asyncio.run(
        runner.execute(
            pipeline(
                make_ports(session_factory, publication_id, changed, html=SHORT_HTML),
                publication_id,
            )
        )
    )

    (cost,) = asyncio.run(rows(session_factory, f"SELECT {COST_COLUMNS} FROM costs"))
    assert changed.calls == 0
    assert cost.model == "usage-in-the-journal"
    assert cost.input_tokens == 111
    assert float(cost.estimated_usd) == round(111 / 1_000_000 * PRICE, 6)


@pytest.mark.integration
def test_mutating_a_journaled_usage_result_cannot_change_a_replay(
    session_factory: SessionFactory, publication_id: int
) -> None:
    inner = JournalRunner()
    runner = MutatingRunner(inner, tokens=777)
    ports = make_ports(session_factory, publication_id, FakeEmbedder(), html=SHORT_HTML)
    inner.fail_next("persist")

    with pytest.raises(InjectedFailure):
        asyncio.run(runner.execute(pipeline(ports, publication_id)))
    asyncio.run(runner.execute(pipeline(ports, publication_id)))

    (cost,) = asyncio.run(rows(session_factory, f"SELECT {COST_COLUMNS} FROM costs"))
    assert cost.input_tokens == TOKENS != 777


@pytest.mark.integration
def test_an_invalid_usage_record_rolls_back_the_article_and_its_passages(
    session_factory: SessionFactory, publication_id: int
) -> None:
    ports = make_ports(
        session_factory, publication_id, FakeEmbedder(model=cast("str", None)), html=SHORT_HTML
    )

    with pytest.raises(IntegrityError):
        asyncio.run(JournalRunner().execute(pipeline(ports, publication_id)))

    assert asyncio.run(counts(session_factory)) == (0, 0, 0)


@pytest.mark.integration
def test_zero_token_usage_records_a_zero_cost_row(
    session_factory: SessionFactory, publication_id: int
) -> None:
    ports = make_ports(
        session_factory, publication_id, FakeEmbedder(input_tokens=0), html=SHORT_HTML
    )

    asyncio.run(JournalRunner().execute(pipeline(ports, publication_id)))

    (cost,) = asyncio.run(rows(session_factory, f"SELECT {COST_COLUMNS} FROM costs"))
    assert cost.input_tokens == 0
    assert float(cost.estimated_usd) == 0.0
    assert cost.model == MODEL
