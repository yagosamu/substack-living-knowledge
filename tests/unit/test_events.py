from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from living_kb.events import (
    ARTICLE_DISCOVERED,
    ARTICLE_INGESTED,
    PUBLICATION_ADDED,
    ArticleDiscovered,
    ArticleIngested,
    PublicationAdded,
)


def test_event_names_are_stable() -> None:
    assert PUBLICATION_ADDED == "kb/publication.added"
    assert ARTICLE_DISCOVERED == "kb/article.discovered"
    assert ARTICLE_INGESTED == "kb/article.ingested"


def test_article_discovered_accepts_a_well_formed_payload() -> None:
    event = ArticleDiscovered.model_validate(
        {
            "canonical_id": "pub:example:durable-agents",
            "publication_id": 7,
            "url": "https://www.example.com/p/durable-agents",
            "title": "Durable Agents in Practice",
            "published_at": "2026-11-09T08:00:00+00:00",
            "via": "rss",
        }
    )

    assert event.canonical_id == "pub:example:durable-agents"
    assert event.publication_id == 7
    assert str(event.url) == "https://www.example.com/p/durable-agents"
    assert event.published_at == datetime(2026, 11, 9, 8, 0, tzinfo=UTC)
    assert event.via == "rss"


@pytest.mark.parametrize(
    "broken",
    [
        {"url": None},
        {"url": "ftp://www.example.com/p/durable-agents"},
        {"via": "carrier-pigeon"},
        {"published_at": "last tuesday"},
        {"publication_id": 0},
    ],
    ids=["missing-url", "bad-scheme", "unknown-via", "bad-date", "bad-publication-id"],
)
def test_article_discovered_rejects_invalid_payloads(broken: dict[str, object]) -> None:
    payload: dict[str, object] = {
        "canonical_id": "pub:example:durable-agents",
        "publication_id": 7,
        "url": "https://www.example.com/p/durable-agents",
        "title": "Durable Agents in Practice",
        "published_at": "2026-11-09T08:00:00+00:00",
        "via": "rss",
    }
    payload.update(broken)

    with pytest.raises(ValidationError):
        ArticleDiscovered.model_validate(payload)


def test_article_ingested_requires_a_sha256_hash() -> None:
    announcement = ArticleIngested(
        canonical_id="pub:example:durable-agents",
        article_id=3,
        content_hash="a" * 64,
        passage_count=5,
    )

    assert announcement.model_dump(mode="json") == {
        "canonical_id": "pub:example:durable-agents",
        "article_id": 3,
        "content_hash": "a" * 64,
        "passage_count": 5,
    }
    with pytest.raises(ValidationError):
        ArticleIngested(
            canonical_id="pub:example:durable-agents",
            article_id=3,
            content_hash="not-a-hash",
            passage_count=5,
        )


def test_publication_added_is_a_typed_event() -> None:
    publication = PublicationAdded.model_validate(
        {
            "slug": "example",
            "name": "Example Newsletter",
            "feed_url": "https://www.example.com/feed",
            "homepage": "https://www.example.com",
        }
    )

    assert (publication.slug, str(publication.feed_url)) == (
        "example",
        "https://www.example.com/feed",
    )
