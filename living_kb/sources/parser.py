"""Extract article content and metadata from a fetched HTML page."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from html.parser import HTMLParser

import trafilatura

from living_kb.config import settings
from living_kb.durable import PermanentError


@dataclass(frozen=True)
class ParsedArticle:
    title: str | None
    author: str | None
    published_at: datetime
    text: str
    content_hash: str


class _MetadataParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.title: str | None = None
        self.author: str | None = None
        self.published_at_raw: str | None = None
        self._capture: str | None = None
        self._buffer: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key: value or "" for key, value in attrs}
        if tag == "meta":
            key = (values.get("property") or values.get("name") or "").lower()
            if key == "og:title":
                self.title = values.get("content") or None
            elif key in {"author", "article:author"} and self.author is None:
                self.author = values.get("content") or None
            elif key in {"article:published_time", "date", "dc.date"} and not self.published_at_raw:
                self.published_at_raw = values.get("content") or None
        elif tag == "time" and not self.published_at_raw:
            self.published_at_raw = values.get("datetime") or None
        elif tag in {"title", "h1"} and self._capture is None:
            self._capture = tag
            self._buffer = []

    def handle_data(self, data: str) -> None:
        if self._capture is not None:
            self._buffer.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag != self._capture:
            return
        value = " ".join("".join(self._buffer).split())
        if tag in {"title", "h1"} and self.title is None:
            self.title = value or None
        self._capture = None
        self._buffer = []


def _published_at(raw: str | None, fallback: datetime) -> datetime:
    if not raw:
        return fallback
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return fallback
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def parse_article(html: str, url: str, *, fallback_published_at: datetime) -> ParsedArticle:
    """Read the main content, journal-worthy metadata and a SHA-256 hash of the text."""
    extracted = trafilatura.extract(html, include_comments=False, include_tables=False) or ""
    text = "\n".join(line.strip() for line in extracted.splitlines() if line.strip())
    if len(text) < settings.min_article_chars:
        raise PermanentError(f"no extractable article content at {url}")

    metadata = _MetadataParser()
    metadata.feed(html)
    return ParsedArticle(
        title=metadata.title,
        author=metadata.author,
        published_at=_published_at(metadata.published_at_raw, fallback_published_at),
        text=text,
        content_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
    )
