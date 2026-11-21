from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

import yaml

from living_kb.identity import normalize_url


@dataclass(frozen=True)
class Publication:
    slug: str
    name: str
    feed_url: str
    homepage: str
    archive_urls: list[str] = field(default_factory=list)
    notes: str | None = None


class NotAllowedError(ValueError):
    pass


def load_publications(path: str | Path) -> list[Publication]:
    with Path(path).open(encoding="utf-8") as handle:
        raw: object = yaml.safe_load(handle)
    if not isinstance(raw, dict):
        raise ValueError("publication registry must be a mapping")
    entries: object = raw.get("publications")
    if not isinstance(entries, list):
        raise ValueError("publication registry must contain a 'publications' list")
    return [_parse_publication(entry) for entry in entries]


def _parse_publication(entry: object) -> Publication:
    if not isinstance(entry, dict):
        raise ValueError("each publication must be a mapping")
    values: dict[str, str] = {}
    for name in ("slug", "name", "feed_url", "homepage"):
        value: object = entry.get(name)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"publication is missing a valid '{name}'")
        values[name] = value.strip()

    archives: object = entry.get("archive_urls", [])
    if archives is None:
        archives = []
    if not isinstance(archives, list) or not all(
        isinstance(url, str) and url.strip() for url in archives
    ):
        raise ValueError("archive_urls must be a list of URLs")
    archive_urls = [url.strip() for url in archives if isinstance(url, str)]

    notes: object = entry.get("notes")
    if notes is not None and not isinstance(notes, str):
        raise ValueError("notes must be a string")

    return Publication(
        slug=values["slug"],
        name=values["name"],
        feed_url=values["feed_url"],
        homepage=values["homepage"],
        archive_urls=archive_urls,
        notes=notes.strip() if isinstance(notes, str) else None,
    )


def _host(url: str) -> str | None:
    try:
        return urlsplit(normalize_url(url)).hostname
    except ValueError:
        return None


def find_publication(url: str, publications: Sequence[Publication]) -> Publication | None:
    host = _host(url)
    if host is None:
        return None
    for publication in publications:
        candidates = (publication.homepage, publication.feed_url, *publication.archive_urls)
        if any(_host(candidate) == host for candidate in candidates):
            return publication
    return None


def assert_allowed(url: str, publications: Sequence[Publication]) -> Publication:
    publication = find_publication(url, publications)
    if publication is None:
        host = urlsplit(url).hostname or url
        raise NotAllowedError(f"{host!r} is not in the publications allowlist")
    return publication
