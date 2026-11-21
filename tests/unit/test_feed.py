from datetime import UTC, datetime
from pathlib import Path

import pytest

from living_kb.sources.feed import FeedEntry, parse_feed

FIXTURES = Path(__file__).parents[1] / "fixtures" / "feeds"


def test_parse_feed_reads_every_entry_of_a_recorded_substack_feed() -> None:
    entries = parse_feed((FIXTURES / "decoding-ai.xml").read_bytes())

    assert entries == [
        FeedEntry(
            title="Ship Agent Changes Safely in 5 Steps",
            url="https://www.decodingai.com/p/ship-agent-changes-safely",
            published_at=datetime(2026, 11, 19, 12, 2, 46, tzinfo=UTC),
        ),
        FeedEntry(
            title="Build Durable, Secure Long-Running Agents",
            url="https://www.decodingai.com/p/build-durable-secure-long-running-agents",
            published_at=datetime(2026, 11, 17, 7, 2, 53, tzinfo=UTC),
        ),
        FeedEntry(
            title="Don't Let Claude Code Design Your LLM Judges",
            url="https://www.decodingai.com/p/write-your-own-llm-judges",
            published_at=datetime(2026, 11, 10, 12, 21, 59, tzinfo=UTC),
        ),
    ]


def rss(*items: str) -> bytes:
    return (
        '<?xml version="1.0"?><rss version="2.0"><channel><title>Demo</title>'
        + "".join(f"<item>{item}</item>" for item in items)
        + "</channel></rss>"
    ).encode()


def item(slug: str, pub_date: str | None = "Tue, 10 Nov 2026 09:00:00 GMT") -> str:
    date = f"<pubDate>{pub_date}</pubDate>" if pub_date else ""
    return f"<title>{slug}</title><link>https://www.example.com/p/{slug}</link>{date}"


def test_parse_feed_returns_entries_newest_first_whatever_the_feed_order() -> None:
    entries = parse_feed(
        rss(
            item("middle", "Wed, 11 Nov 2026 09:00:00 GMT"),
            item("oldest", "Tue, 10 Nov 2026 09:00:00 GMT"),
            item("newest", "Thu, 12 Nov 2026 09:00:00 GMT"),
        )
    )

    assert [entry.title for entry in entries] == ["newest", "middle", "oldest"]


def test_parse_feed_skips_entries_without_a_link_or_a_date() -> None:
    no_link = "<title>no-link</title><pubDate>Tue, 10 Nov 2026 09:00:00 GMT</pubDate>"
    entries = parse_feed(rss(item("kept"), no_link, item("no-date", pub_date=None)))

    assert [entry.title for entry in entries] == ["kept"]


def test_parse_feed_converts_dates_with_offsets_to_utc() -> None:
    (entry,) = parse_feed(rss(item("offset", "Tue, 10 Nov 2026 09:00:00 -0300")))

    assert entry.published_at == datetime(2026, 11, 10, 12, 0, 0, tzinfo=UTC)
    assert entry.published_at.utcoffset() is not None


def test_parse_feed_rejects_bytes_that_are_not_a_feed() -> None:
    with pytest.raises(ValueError):
        parse_feed(b"<html><body>Not found</body></html>")


def test_parse_feed_returns_no_entries_for_a_valid_feed_without_items() -> None:
    assert parse_feed(rss()) == []
