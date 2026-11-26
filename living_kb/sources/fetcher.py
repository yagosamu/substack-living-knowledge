"""Polite, bounded HTTP fetching of publisher pages."""

from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import urlsplit

import httpx

from living_kb.config import settings
from living_kb.durable import PermanentError
from living_kb.pipelines.ingest import FetchedPage

_RETRIABLE_CLIENT_ERRORS = (408, 429)


@dataclass(frozen=True)
class _RobotsGroup:
    agents: tuple[str, ...]
    disallows: tuple[str, ...]


@dataclass
class RobotsCache:
    """Disallow prefixes already learned for each host."""

    _disallows: dict[str, tuple[str, ...]] = field(default_factory=dict)

    def get(self, host: str) -> tuple[str, ...] | None:
        return self._disallows.get(host)

    def set(self, host: str, disallows: tuple[str, ...]) -> None:
        self._disallows[host] = tuple(disallows)


def parse_robots(text: str, *, user_agent: str) -> tuple[str, ...]:
    """Return the disallow prefixes of the groups that apply to our User-Agent."""
    groups: list[_RobotsGroup] = []
    agents: list[str] = []
    disallows: list[str] = []

    def flush() -> None:
        if agents and disallows:
            groups.append(_RobotsGroup(tuple(agents), tuple(disallows)))

    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        name, value = (part.strip() for part in line.split(":", 1))
        name = name.lower()
        if name == "user-agent":
            if disallows:
                flush()
                agents = []
                disallows = []
            agents.append(value.lower())
        elif name == "disallow" and agents and value:
            disallows.append(value)
    flush()

    agent = user_agent.lower()
    matched = [
        group.disallows
        for group in groups
        if any(name == "*" or name in agent for name in group.agents)
    ]
    return tuple(dict.fromkeys(prefix for group in matched for prefix in group))


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


class HttpxFetcher:
    """Fetch one article page politely and keep the URL the publisher finally served."""

    def __init__(
        self, client: httpx.AsyncClient, *, robots_cache: RobotsCache | None = None
    ) -> None:
        self._client = client
        self._robots_cache = robots_cache if robots_cache is not None else RobotsCache()

    async def _disallows(self, url: str) -> tuple[str, ...]:
        host = urlsplit(url).hostname
        if not host:
            raise ValueError(f"URL has no host: {url!r}")
        host = host.lower()
        cached = self._robots_cache.get(host)
        if cached is not None:
            return cached

        robots_url = _robots_url(url)
        response = await self._client.get(robots_url)
        if response.status_code == 404:
            disallows: tuple[str, ...] = ()
        else:
            _check_status(response, robots_url)
            disallows = parse_robots(response.text, user_agent=settings.user_agent)
        self._robots_cache.set(host, disallows)
        return disallows

    async def __call__(self, url: str) -> FetchedPage:
        target = urlsplit(url).path or "/"
        for prefix in await self._disallows(url):
            if target.startswith(prefix):
                raise PermanentError(f"robots.txt disallows {url}")

        async with self._client.stream("GET", url) as response:
            _check_status(response, url)
            size = 0
            async for chunk in response.aiter_bytes():
                size += len(chunk)
                if size > settings.max_article_bytes:
                    raise PermanentError(
                        f"article body from {url} exceeds {settings.max_article_bytes} bytes"
                    )
            return FetchedPage(url=str(response.url), html=response.text)
