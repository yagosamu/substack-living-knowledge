"""Contract tests for ingest-article (#6): the pipeline seam against the real test database.

The pipeline runs through the JournalRunner fake with fake ports (HTTP, embedder, event
sender), so durable behaviour (resume, at-least-once, idempotency) is tested without the
Inngest engine and without paid APIs.
"""

import asyncio
import dataclasses
from collections.abc import Callable, Coroutine, Mapping
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from living_kb.durable import InjectedFailure, JournalRunner, PermanentError, StepRunner
from living_kb.pipelines.ingest import FetchedPage, IngestPorts, ingest_article
from living_kb.sources.embedder import EmbeddedTexts
from living_kb.sources.publications import Publication

SessionFactory = async_sessionmaker[AsyncSession]

ARTICLE_URL = "https://www.example.com/p/durable-agents"
ARTICLE_HTML = (Path(__file__).parent / "fixtures" / "articles" / "durable-agents.html").read_text()
EXAMPLE = Publication(
    slug="example",
    name="Example Newsletter",
    feed_url="https://www.example.com/feed",
    homepage="https://www.example.com",
)


class FakeFetcher:
    def __init__(self, pages: Mapping[str, FetchedPage]) -> None:
        self.pages = dict(pages)
        self.calls: list[str] = []

    async def __call__(self, url: str) -> FetchedPage:
        self.calls.append(url)
        return self.pages[url]


class FakeEmbedder:
    """Deterministic 1536-dimensional vectors; counts successful calls."""

    def __init__(self) -> None:
        self.calls = 0

    async def __call__(self, texts: list[str]) -> EmbeddedTexts:
        self.calls += 1
        return {
            "vectors": [[round(0.001 * (i + 1), 6)] * 1536 for i in range(len(texts))],
            "usage": {
                "model": "test-embed-v1",
                "input_tokens": 100 * len(texts),
                "estimated_usd": 0.0,
            },
        }


class RecordingSender:
    def __init__(self) -> None:
        self.sent: list[tuple[str, dict[str, object]]] = []

    async def __call__(self, name: str, data: dict[str, object]) -> None:
        self.sent.append((name, data))


class Harness:
    def __init__(self, session_factory: SessionFactory, publication_id: int) -> None:
        self.publication_id = publication_id
        self.fetcher = FakeFetcher({ARTICLE_URL: FetchedPage(url=ARTICLE_URL, html=ARTICLE_HTML)})
        self.embedder = FakeEmbedder()
        self.sender = RecordingSender()
        self.ports = IngestPorts(
            fetch_html=self.fetcher,
            embed=self.embedder,
            send_event=self.sender,
            session_factory=session_factory,
            publications=[EXAMPLE],
        )

    def event(self, **overrides: object) -> dict[str, object]:
        event: dict[str, object] = {
            "canonical_id": "pub:example:durable-agents",
            "publication_id": self.publication_id,
            "url": ARTICLE_URL,
            # Deliberately different from the page title: the page is the source of truth.
            "title": "durable agents (as titled in the feed)",
            "published_at": "2026-11-09T08:00:00+00:00",
            "via": "rss",
        }
        event.update(overrides)
        return event

    def pipeline(
        self, event: dict[str, object]
    ) -> Callable[[StepRunner], Coroutine[Any, Any, Any]]:
        return lambda runner: ingest_article(runner, event, self.ports)


async def _seed_publication(session_factory: SessionFactory) -> int:
    async with session_factory() as session:
        publication_id = await session.scalar(
            text(
                "INSERT INTO publications (slug, name, feed_url, homepage) "
                "VALUES ('example', 'Example Newsletter', 'https://www.example.com/feed', "
                "'https://www.example.com') RETURNING id"
            )
        )
        await session.commit()
        assert isinstance(publication_id, int)
        return publication_id


async def _rows(session_factory: SessionFactory, sql: str) -> list[Any]:
    async with session_factory() as session:
        return list((await session.execute(text(sql))).all())


@pytest.fixture
def harness(session_factory: SessionFactory, clean_db: None) -> Harness:
    return Harness(session_factory, asyncio.run(_seed_publication(session_factory)))


@pytest.mark.integration
def test_ingesting_an_article_stores_it_with_embedded_passages_and_announces_it(
    harness: Harness, session_factory: SessionFactory
) -> None:
    result = asyncio.run(JournalRunner().execute(harness.pipeline(harness.event())))

    (article,) = asyncio.run(
        _rows(
            session_factory,
            "SELECT id, canonical_id, publication_id, url, title, author, published_at, "
            "content_hash, status FROM articles",
        )
    )
    passages = asyncio.run(
        _rows(
            session_factory,
            "SELECT chunk_index, text, vector_dims(embedding), search_vector IS NOT NULL "
            "FROM passages ORDER BY chunk_index",
        )
    )

    assert article.canonical_id == "pub:example:durable-agents"
    assert article.publication_id == harness.publication_id
    assert article.url == ARTICLE_URL
    assert article.title == "Durable Agents in Practice"
    assert article.author == "Ada Example"
    assert article.published_at.isoformat() == "2026-11-10T09:30:00+00:00"
    assert len(article.content_hash) == 64
    assert article.status == "ingested"

    assert len(passages) > 1
    assert [p.chunk_index for p in passages] == list(range(len(passages)))
    assert all(p.vector_dims == 1536 and p[3] for p in passages)
    assert "Every agent that runs" in passages[0].text
    assert not any("Subscribe" in p.text for p in passages)

    assert harness.sender.sent == [
        (
            "kb/article.ingested",
            {
                "canonical_id": "pub:example:durable-agents",
                "article_id": article.id,
                "content_hash": article.content_hash,
                "passage_count": len(passages),
            },
        )
    ]
    assert result == {"article_id": article.id, "passage_count": len(passages)}


class RecordingRunner:
    """Wraps a JournalRunner and records the step names the pipeline asks for, in order."""

    def __init__(self, inner: JournalRunner) -> None:
        self.inner = inner
        self.names: list[str] = []

    async def run(self, name: str, fn: Callable[[], Any]) -> Any:
        self.names.append(name)
        return await self.inner.run(name, fn)


@pytest.mark.integration
def test_ingestion_runs_six_named_steps_in_order(harness: Harness) -> None:
    runner = RecordingRunner(JournalRunner())

    asyncio.run(ingest_article(runner, harness.event(), harness.ports))

    assert runner.names == [
        "check-allowlist",
        "fetch-article",
        "chunk",
        "embed",
        "persist",
        "emit",
    ]


async def _counts(session_factory: SessionFactory) -> tuple[int, int]:
    (row,) = await _rows(
        session_factory,
        "SELECT (SELECT count(*) FROM articles), (SELECT count(*) FROM passages)",
    )
    return row[0], row[1]


@pytest.mark.integration
def test_delivering_the_same_event_twice_creates_no_new_rows(
    harness: Harness, session_factory: SessionFactory
) -> None:
    # A re-delivered event is a new run (fresh journal), not a replay.
    asyncio.run(JournalRunner().execute(harness.pipeline(harness.event())))
    first = asyncio.run(_counts(session_factory))

    asyncio.run(JournalRunner().execute(harness.pipeline(harness.event())))

    assert asyncio.run(_counts(session_factory)) == first
    assert first[0] == 1


@pytest.mark.integration
def test_a_failure_at_embed_resumes_there_without_refetching_or_re_embedding(
    harness: Harness, session_factory: SessionFactory
) -> None:
    runner = JournalRunner()
    runner.fail_next("embed")

    with pytest.raises(InjectedFailure):
        asyncio.run(runner.execute(harness.pipeline(harness.event())))
    assert asyncio.run(_counts(session_factory)) == (0, 0)

    asyncio.run(runner.execute(harness.pipeline(harness.event())))

    assert harness.fetcher.calls == [ARTICLE_URL]
    assert harness.embedder.calls == 1
    articles, passages = asyncio.run(_counts(session_factory))
    assert articles == 1 and passages > 1
    assert len(harness.sender.sent) == 1


@pytest.mark.integration
def test_persist_dying_after_its_commit_leaves_no_duplicates_on_retry(
    harness: Harness, session_factory: SessionFactory
) -> None:
    # At-least-once (ADR 0003, rule 1): the rows were committed, then the process died
    # before the journal recorded persist, so the retry runs persist a second time.
    runner = JournalRunner()
    runner.fail_next("persist", after_side_effect=True)

    with pytest.raises(InjectedFailure):
        asyncio.run(runner.execute(harness.pipeline(harness.event())))
    committed = asyncio.run(_counts(session_factory))
    assert committed[0] == 1

    asyncio.run(runner.execute(harness.pipeline(harness.event())))

    assert asyncio.run(_counts(session_factory)) == committed


@pytest.mark.integration
def test_an_article_outside_the_allowlist_fails_permanently_without_being_fetched(
    harness: Harness, session_factory: SessionFactory
) -> None:
    event = harness.event(url="https://evil.example.io/p/durable-agents")

    with pytest.raises(PermanentError, match="evil.example.io"):
        asyncio.run(JournalRunner().execute(harness.pipeline(event)))

    assert harness.fetcher.calls == []
    assert asyncio.run(_counts(session_factory)) == (0, 0)


class FailingPassageSessionFactory:
    """Commit the article insert, then fail on the first passage insert."""

    def __init__(self, inner: SessionFactory) -> None:
        self.inner = inner

    def __call__(self) -> AsyncSession:
        session = self.inner()
        original = session.execute

        async def execute(statement: Any, params: Any = None) -> Any:
            if "INSERT INTO passages" in str(statement):
                raise RuntimeError("passage write failed")
            return await original(statement, params)

        session.execute = execute  # type: ignore[assignment]
        return session


@pytest.mark.integration
def test_a_failure_while_writing_passages_leaves_no_article(
    harness: Harness, session_factory: SessionFactory
) -> None:
    ports = dataclasses.replace(
        harness.ports,
        session_factory=FailingPassageSessionFactory(session_factory),  # type: ignore[arg-type]
    )

    with pytest.raises(RuntimeError, match="passage write failed"):
        asyncio.run(
            JournalRunner().execute(lambda runner: ingest_article(runner, harness.event(), ports))
        )

    assert asyncio.run(_counts(session_factory)) == (0, 0)


@pytest.mark.integration
@pytest.mark.parametrize(
    "broken",
    [
        {"url": None},
        {"via": "carrier-pigeon"},
        {"published_at": "last tuesday"},
    ],
    ids=["missing-url", "unknown-via", "bad-date"],
)
def test_a_malformed_event_fails_permanently_before_any_step(
    harness: Harness, broken: dict[str, object]
) -> None:
    runner = RecordingRunner(JournalRunner())

    with pytest.raises(PermanentError):
        asyncio.run(ingest_article(runner, harness.event(**broken), harness.ports))

    assert runner.names == []


SHORT_HTML = """<html><head>
<meta property="og:title" content="Durable Agents in Practice">
<meta property="article:published_time" content="2026-11-10T09:30:00+00:00">
</head><body><article><h1>Durable Agents in Practice</h1>
<p>An edited, much shorter version of the post: checkpoint every side effect, and test
the resume path by failing a step on purpose before trusting it in production.</p>
</article></body></html>"""


@pytest.mark.integration
def test_re_ingesting_a_shorter_article_replaces_its_passages(
    harness: Harness, session_factory: SessionFactory
) -> None:
    asyncio.run(JournalRunner().execute(harness.pipeline(harness.event())))
    harness.fetcher.pages[ARTICLE_URL] = FetchedPage(url=ARTICLE_URL, html=SHORT_HTML)

    asyncio.run(JournalRunner().execute(harness.pipeline(harness.event())))

    passages = asyncio.run(_rows(session_factory, "SELECT text FROM passages"))
    assert len(passages) == 1
    assert "much shorter version" in passages[0].text


@pytest.mark.integration
def test_the_final_url_after_a_redirect_is_the_one_stored(
    harness: Harness, session_factory: SessionFactory
) -> None:
    final_url = "https://www.example.com/p/durable-agents-in-practice"
    harness.fetcher.pages[ARTICLE_URL] = FetchedPage(url=final_url, html=ARTICLE_HTML)

    asyncio.run(JournalRunner().execute(harness.pipeline(harness.event())))

    assert asyncio.run(_rows(session_factory, "SELECT url FROM articles")) == [(final_url,)]


@pytest.mark.integration
def test_a_heavy_page_never_puts_its_raw_html_in_the_journal(
    harness: Harness, session_factory: SessionFactory
) -> None:
    # ADR 0003, rule 4: step outputs stay small. A page with 300 KB of scripts around a
    # normal body must ingest under a 100 KB per-step budget, because only the extracted
    # article is journaled, never the HTML.
    heavy = ARTICLE_HTML.replace(
        "</head>", "<script>" + "var tracking = 1;" * 20_000 + "</script></head>"
    )
    assert len(heavy) > 300_000
    harness.fetcher.pages[ARTICLE_URL] = FetchedPage(url=ARTICLE_URL, html=heavy)

    asyncio.run(JournalRunner(max_output_bytes=100_000).execute(harness.pipeline(harness.event())))

    assert asyncio.run(_counts(session_factory))[0] == 1


class CountingSessionFactory:
    """Wraps the session factory to count how many times the pipeline opens the database."""

    def __init__(self, inner: SessionFactory) -> None:
        self.inner = inner
        self.opened = 0

    def __call__(self) -> AsyncSession:
        self.opened += 1
        return self.inner()


@pytest.mark.integration
def test_a_failure_at_emit_retries_only_emit_without_persisting_again(
    harness: Harness, session_factory: SessionFactory
) -> None:
    counting = CountingSessionFactory(session_factory)
    ports = dataclasses.replace(harness.ports, session_factory=counting)  # type: ignore[arg-type]
    runner = JournalRunner()
    runner.fail_next("emit")

    def pipeline(r: StepRunner) -> Coroutine[Any, Any, Any]:
        return ingest_article(r, harness.event(), ports)

    with pytest.raises(InjectedFailure):
        asyncio.run(runner.execute(pipeline))
    asyncio.run(runner.execute(pipeline))

    assert counting.opened == 1
    assert len(harness.sender.sent) == 1
