from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree


@dataclass(frozen=True)
class FeedEntry:
    title: str
    url: str
    published_at: datetime


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _text(element: ElementTree.Element, tag: str) -> str:
    child = element.find(tag)
    return (child.text or "").strip() if child is not None else ""


def parse_feed(data: bytes) -> list[FeedEntry]:
    try:
        root = ElementTree.fromstring(data)
    except ElementTree.ParseError as exc:
        raise ValueError("Feed is not valid XML") from exc

    if _local_name(root.tag) != "rss":
        raise ValueError("Feed root element is not rss")
    channel = next((child for child in root if _local_name(child.tag) == "channel"), None)
    if channel is None:
        raise ValueError("Feed has no channel")

    entries: list[FeedEntry] = []
    for item in channel:
        if _local_name(item.tag) != "item":
            continue
        url = _text(item, "link")
        published_text = _text(item, "pubDate")
        if not url or not published_text:
            continue
        try:
            published_at = parsedate_to_datetime(published_text)
        except (TypeError, ValueError):
            continue
        if published_at.tzinfo is None:
            published_at = published_at.replace(tzinfo=UTC)
        entries.append(
            FeedEntry(
                title=_text(item, "title"),
                url=url,
                published_at=published_at.astimezone(UTC),
            )
        )

    entries.sort(key=lambda entry: entry.published_at, reverse=True)
    return entries
