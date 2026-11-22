from datetime import UTC, datetime
from pathlib import Path

import pytest

from living_kb.durable import PermanentError
from living_kb.sources.parser import parse_article

FIXTURE = Path(__file__).parents[1] / "fixtures" / "articles" / "durable-agents.html"
URL = "https://www.example.com/p/durable-agents"
FEED_DATE = datetime(2026, 11, 9, 8, 0, tzinfo=UTC)


def test_parser_keeps_the_article_body_and_drops_page_boilerplate() -> None:
    article = parse_article(FIXTURE.read_text(), URL, fallback_published_at=FEED_DATE)

    # Headings (title, subtitle) may lead the text; what matters is the full body is there.
    assert "Every agent that runs for more than a few seconds" in article.text
    assert article.text.rstrip().endswith("an agent you can trust in production.")
    for boilerplate in ("Subscribe", "Like", "Share", "Comments", "Privacy", "Substack is"):
        assert boilerplate not in article.text


def test_parser_reads_title_author_and_publish_time_from_the_page() -> None:
    article = parse_article(FIXTURE.read_text(), URL, fallback_published_at=FEED_DATE)

    assert article.title == "Durable Agents in Practice"
    assert article.author == "Ada Example"
    assert article.published_at == datetime(2026, 11, 10, 9, 30, tzinfo=UTC)


BARE_PAGE = """<html><head><title>Plain post</title></head><body><article>
<p>A post whose page carries no author and no publish date, only a body long enough to be
extracted as the main content of the page by any reasonable readability algorithm.</p>
</article></body></html>"""


def test_parser_falls_back_to_the_feed_date_when_the_page_has_none() -> None:
    article = parse_article(BARE_PAGE, URL, fallback_published_at=FEED_DATE)

    assert article.published_at == FEED_DATE
    assert article.author is None


def test_content_hash_is_stable_for_the_same_text_and_changes_when_it_changes() -> None:
    first = parse_article(BARE_PAGE, URL, fallback_published_at=FEED_DATE)
    again = parse_article(BARE_PAGE, URL, fallback_published_at=FEED_DATE)
    edited = parse_article(
        BARE_PAGE.replace("no author", "no byline"), URL, fallback_published_at=FEED_DATE
    )

    assert first.content_hash == again.content_hash
    assert first.content_hash != edited.content_hash
    assert len(first.content_hash) == 64 and int(first.content_hash, 16) >= 0


def test_a_page_with_no_extractable_content_fails_permanently() -> None:
    # Re-fetching the same empty page will not produce content: retrying is pointless.
    empty = "<html><head><title>Paywalled</title></head><body><nav>Sign in</nav></body></html>"

    with pytest.raises(PermanentError, match=URL):
        parse_article(empty, URL, fallback_published_at=FEED_DATE)
