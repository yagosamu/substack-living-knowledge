from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree as ET


@dataclass(frozen=True)
class FeedEntry:
    title: str
    url: str
    published_at: datetime


def parse_feed(payload: bytes) -> list[FeedEntry]:
    try:
        root = ET.fromstring(payload)
    except ET.ParseError as exc:
        raise ValueError("payload is not a valid feed") from exc

    kind = _local_name(root.tag)
    if kind == "rss":
        channel = next((child for child in root if _local_name(child.tag) == "channel"), None)
        if channel is None:
            raise ValueError("RSS feed has no channel")
        entries = [_parse_rss_entry(child) for child in channel if _local_name(child.tag) == "item"]
    elif kind == "feed":
        entries = [_parse_atom_entry(child) for child in root if _local_name(child.tag) == "entry"]
    else:
        raise ValueError("payload is not an RSS or Atom feed")

    return sorted(
        (entry for entry in entries if entry is not None),
        key=lambda entry: entry.published_at,
        reverse=True,
    )


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _text(parent: ET.Element, name: str) -> str | None:
    for child in parent:
        if _local_name(child.tag) == name:
            return (child.text or "").strip() or None
    return None


def _atom_link(parent: ET.Element) -> str | None:
    for child in parent:
        if _local_name(child.tag) != "link":
            continue
        href = child.get("href")
        if href and child.get("rel") in (None, "alternate"):
            return href.strip()
    return None


def _parse_rss_entry(element: ET.Element) -> FeedEntry | None:
    title = _text(element, "title")
    url = _text(element, "link")
    published = _text(element, "pubDate") or _text(element, "date")
    return _build_entry(title, url, published)


def _parse_atom_entry(element: ET.Element) -> FeedEntry | None:
    title = _text(element, "title")
    url = _atom_link(element)
    published = _text(element, "published") or _text(element, "updated")
    return _build_entry(title, url, published)


def _build_entry(title: str | None, url: str | None, published: str | None) -> FeedEntry | None:
    if not title or not url or not published:
        return None
    try:
        timestamp = parsedate_to_datetime(published)
    except (TypeError, ValueError):
        try:
            timestamp = datetime.fromisoformat(published.replace("Z", "+00:00"))
        except ValueError:
            return None
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=UTC)
    return FeedEntry(title=title, url=url, published_at=timestamp.astimezone(UTC))
