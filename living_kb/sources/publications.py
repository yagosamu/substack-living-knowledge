from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import yaml


@dataclass
class Publication:
    slug: str
    name: str
    feed_url: str
    homepage: str
    notes: str | None = None
    archive_urls: list[str] = field(default_factory=list)


class NotAllowedError(ValueError):
    pass


def _required_str(entry: dict[str, Any], key: str) -> str:
    value = entry.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Publication entry is missing a valid {key!r}")
    return value


def load_publications(path: Path | str) -> list[Publication]:
    try:
        with Path(path).open(encoding="utf-8") as stream:
            raw: Any = yaml.safe_load(stream)
    except yaml.YAMLError as exc:
        raise ValueError(f"Invalid publications registry {path}: {exc}") from exc

    if not isinstance(raw, dict) or not isinstance(raw.get("publications"), list):
        raise ValueError("Publications registry must contain a 'publications' list")

    publications: list[Publication] = []
    for entry in raw["publications"]:
        if not isinstance(entry, dict):
            raise ValueError("Each publication must be a mapping")
        slug = _required_str(entry, "slug")
        name = _required_str(entry, "name")
        feed_url = _required_str(entry, "feed_url")
        homepage = _required_str(entry, "homepage")
        notes = entry.get("notes")
        if notes is not None and not isinstance(notes, str):
            raise ValueError("Publication 'notes' must be a string")
        archive_urls = entry.get("archive_urls", [])
        if not isinstance(archive_urls, list) or not all(
            isinstance(url, str) and url.strip() for url in archive_urls
        ):
            raise ValueError("Publication 'archive_urls' must be a list of URLs")
        publications.append(
            Publication(
                slug=slug,
                name=name,
                feed_url=feed_url,
                homepage=homepage,
                notes=notes,
                archive_urls=list(archive_urls),
            )
        )
    return publications


def find_publication(url: str, publications: Iterable[Publication]) -> Publication | None:
    host = urlsplit(url).hostname
    if host is None:
        return None
    for publication in publications:
        hosts = (publication.homepage, publication.feed_url, *publication.archive_urls)
        if any(urlsplit(candidate).hostname == host for candidate in hosts):
            return publication
    return None


def assert_allowed(url: str, publications: Iterable[Publication]) -> Publication:
    publication = find_publication(url, publications)
    if publication is None:
        raise NotAllowedError(
            f"Host {urlsplit(url).hostname!r} is not in the publications allowlist"
        )
    return publication
