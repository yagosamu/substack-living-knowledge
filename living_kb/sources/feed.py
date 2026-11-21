from dataclasses import dataclass
from datetime import UTC, datetime
from time import struct_time
from typing import cast

from feedparser import parse as parse_bytes


@dataclass(frozen=True)
class FeedEntry:
    title: str
    url: str
    published_at: datetime


def parse_feed(payload: bytes) -> list[FeedEntry]:
    document = parse_bytes(payload)
    if not cast(str, document.get("version", "")):
        raise ValueError("payload is not an RSS feed")

    entries: list[FeedEntry] = []
    raw_entries = cast(list[dict[str, object]], document.get("entries", []))
    for raw in raw_entries:
        title = raw.get("title")
        url = raw.get("link")
        published = raw.get("published_parsed")
        if not isinstance(title, str) or not title:
            continue
        if not isinstance(url, str) or not url:
            continue
        if not isinstance(published, struct_time):
            continue

        entries.append(
            FeedEntry(
                title=title,
                url=url,
                published_at=datetime(*published[:6], tzinfo=UTC),
            )
        )

    return sorted(entries, key=lambda entry: entry.published_at, reverse=True)
