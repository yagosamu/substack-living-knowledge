from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

import yaml

_REQUIRED_FIELDS = ("slug", "name", "feed_url", "homepage")


class NotAllowedError(ValueError):
    pass


@dataclass(frozen=True)
class Publication:
    slug: str
    name: str
    feed_url: str
    homepage: str
    archive_urls: list[str] = field(default_factory=list)
    notes: str | None = None


def _required_string(entry: dict[str, object], key: str, index: int) -> str:
    value = entry.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"publications[{index}] is missing required field {key!r}")
    return value


def load_publications(path: str | Path) -> list[Publication]:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("publications registry must be a mapping with a 'publications' list")
    entries = data.get("publications")
    if not isinstance(entries, list):
        raise ValueError("publications registry must contain a 'publications' list")

    publications: list[Publication] = []
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise ValueError(f"publications[{index}] must be a mapping")
        fields = {str(key): value for key, value in entry.items()}
        values = {key: _required_string(fields, key, index) for key in _REQUIRED_FIELDS}

        archive_urls = fields.get("archive_urls", [])
        if archive_urls is None:
            archive_urls = []
        if not isinstance(archive_urls, list) or not all(
            isinstance(url, str) and url for url in archive_urls
        ):
            raise ValueError(f"publications[{index}].archive_urls must be a list of URLs")

        notes = fields.get("notes")
        if notes is not None and not isinstance(notes, str):
            raise ValueError(f"publications[{index}].notes must be a string")

        publications.append(
            Publication(
                slug=values["slug"],
                name=values["name"],
                feed_url=values["feed_url"],
                homepage=values["homepage"],
                archive_urls=list(archive_urls),
                notes=notes,
            )
        )
    return publications


def _host(url: str) -> str:
    host = urlsplit(url.strip()).hostname
    if not host:
        raise ValueError(f"URL has no host: {url!r}")
    return host.lower()


def _hosts(publication: Publication) -> set[str]:
    return {
        _host(url)
        for url in (publication.homepage, publication.feed_url, *publication.archive_urls)
    }


def find_publication(url: str, publications: Iterable[Publication]) -> Publication | None:
    host = _host(url)
    return next(
        (publication for publication in publications if host in _hosts(publication)),
        None,
    )


def assert_allowed(url: str, publications: Iterable[Publication]) -> Publication:
    publication = find_publication(url, publications)
    if publication is None:
        raise NotAllowedError(f"host {_host(url)!r} is not in the publications allowlist: {url}")
    return publication
