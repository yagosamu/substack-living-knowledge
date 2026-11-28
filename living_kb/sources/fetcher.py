"""Polite, bounded HTTP fetching of publisher pages."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlsplit

import httpx
from protego import Protego

from living_kb.config import settings
from living_kb.durable import PermanentError
from living_kb.pipelines.ingest import FetchedPage
from living_kb.sources.publications import NotAllowedError, Publication, assert_allowed

_RETRIABLE_CLIENT_ERRORS = (408, 429)
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})


@dataclass
class RobotsCache:
    """Parsed robots rules already learned for each host."""

    _parsers: dict[str, Protego] = field(default_factory=dict)

    def get(self, host: str) -> Protego | None:
        return self._parsers.get(host)

    def set(self, host: str, parser: Protego) -> None:
        self._parsers[host] = parser


def _empty_robots() -> Protego:
    return Protego.parse("")


def _robots_url(url: str) -> str:
    parts = urlsplit(url)
    if not parts.scheme or not parts.netloc:
        raise ValueError(f"URL has no scheme or host: {url!r}")
    return f"{parts.scheme}://{parts.netloc}/robots.txt"


def _check_status(response: httpx.Response, url: str) -> None:
    status = response.status_code
    if status < 400:
        return
    if status in _RETRIABLE_CLIENT_ERRORS or status >= 500:
        response.raise_for_status()
        return
    raise PermanentError(f"{url} returned HTTP {status}")


def _robots_from_response(response: httpx.Response, url: str) -> Protego:
    status = response.status_code
    if status in _RETRIABLE_CLIENT_ERRORS or status >= 500:
        response.raise_for_status()
    if status >= 400:
        return _empty_robots()
    return Protego.parse(response.text)


class HttpxFetcher:
    """Fetch one allowlisted article page, checking every redirect hop first."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        publications: Sequence[Publication],
        robots_cache: RobotsCache | None = None,
    ) -> None:
        self._client = client
        self._publications = tuple(publications)
        self._robots_cache = robots_cache if robots_cache is not None else RobotsCache()

    def _assert_hop_allowed(self, url: str) -> None:
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.netloc:
            raise PermanentError(f"only http(s) URLs may be fetched: {url!r}")
        try:
            assert_allowed(url, self._publications)
        except (NotAllowedError, ValueError) as exc:
            raise PermanentError(str(exc)) from exc

    async def _robots(self, url: str) -> Protego:
        host = urlsplit(url).hostname
        if not host:
            raise ValueError(f"URL has no host: {url!r}")
        host = host.lower()
        cached = self._robots_cache.get(host)
        if cached is not None:
            return cached

        current = _robots_url(url)
        for hop in range(settings.max_redirects + 1):
            self._assert_hop_allowed(current)
            response = await self._client.get(current, follow_redirects=False)
            location = response.headers.get("location")
            if response.status_code in _REDIRECT_STATUSES and location:
                if hop == settings.max_redirects:
                    raise httpx.TooManyRedirects(
                        "robots.txt redirect chain from "
                        f"{current} exceeds {settings.max_redirects} hops"
                    )
                current = urljoin(str(response.url), location)
                continue
            parser = _robots_from_response(response, current)
            self._robots_cache.set(host, parser)
            return parser
        raise RuntimeError("unreachable robots redirect loop")

    async def _is_allowed_by_robots(self, url: str) -> bool:
        parser = await self._robots(url)
        return parser.can_fetch(url, settings.user_agent)

    async def __call__(self, url: str) -> FetchedPage:
        current = url
        for hop in range(settings.max_redirects + 1):
            self._assert_hop_allowed(current)
            if not await self._is_allowed_by_robots(current):
                raise PermanentError(f"robots.txt disallows {current}")

            async with self._client.stream("GET", current, follow_redirects=False) as response:
                _check_status(response, current)
                location = response.headers.get("location")
                if response.status_code in _REDIRECT_STATUSES and location:
                    if hop == settings.max_redirects:
                        raise PermanentError(
                            f"redirect chain from {url} exceeds {settings.max_redirects} hops"
                        )
                    current = urljoin(str(response.url), location)
                    continue

                size = 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > settings.max_article_bytes:
                        raise PermanentError(
                            f"article body from {current} exceeds "
                            f"{settings.max_article_bytes} bytes"
                        )
                return FetchedPage(url=str(response.url), html=response.text)
        raise RuntimeError("unreachable redirect loop")
