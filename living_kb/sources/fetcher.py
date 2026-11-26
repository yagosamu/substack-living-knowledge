"""Polite, bounded HTTP fetching of publisher pages."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, cast
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import httpx

from living_kb.config import settings
from living_kb.durable import PermanentError
from living_kb.pipelines.ingest import FetchedPage
from living_kb.sources.publications import NotAllowedError, Publication, assert_allowed

_RETRIABLE_CLIENT_ERRORS = (408, 429)
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})


@dataclass
class RobotsCache:
    """Parsed robots rules already learned for each host."""

    _parsers: dict[str, RobotFileParser] = field(default_factory=dict)

    def get(self, host: str) -> RobotFileParser | None:
        return self._parsers.get(host)

    def set(self, host: str, parser: RobotFileParser) -> None:
        self._parsers[host] = parser


def _agent_product(agent: str) -> str:
    return agent.split("/", 1)[0].strip().lower()


def _matches_agent(declared: str, product: str) -> bool:
    name = declared.strip().lower()
    return name == "*" or name == product or product.startswith(name)


def _empty_robots() -> RobotFileParser:
    parser = RobotFileParser()
    parser.parse([])
    return parser


def _apply_longest_match(parser: RobotFileParser) -> RobotFileParser:
    entries = cast("list[Any]", getattr(parser, "entries", []))
    default = getattr(parser, "default_entry", None)
    if default is not None:
        entries.append(default)
    for entry in entries:
        for attribute in ("rulelines", "rules"):
            rules = getattr(entry, attribute, None)
            if rules is not None:
                cast("list[Any]", rules).sort(
                    key=lambda rule: (-len(getattr(rule, "path", "")), not rule.allowance)
                )
    return parser


def parse_robots(text: str, *, user_agent: str) -> RobotFileParser:
    """Return rules from our agent's group, falling back to the wildcard group.

    ``urllib.robotparser`` parses and matches rules: it understands ``Allow``,
    wildcards and empty rules without a new dependency. Its ``can_fetch`` uses
    insertion order, so rules are sorted by longest path (with ``Allow`` winning
    a tie) to follow RFC 9309. Groups are selected before parsing so a specific
    agent wins even when a wildcard group appears first in the file.
    """
    groups: list[tuple[tuple[str, ...], tuple[str, ...]]] = []
    agents: list[str] = []
    rules: list[str] = []

    def flush() -> None:
        if agents:
            groups.append((tuple(agents), tuple(rules)))

    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        name, value = (part.strip() for part in line.split(":", 1))
        name = name.lower()
        if name == "user-agent":
            if agents:
                flush()
                agents = []
                rules = []
            agents.append(value)
        elif name in ("allow", "disallow") and agents:
            rules.append(f"{name.capitalize()}: {value}")
    flush()

    product = _agent_product(user_agent)
    specific = [
        rules_for_group
        for group_agents, rules_for_group in groups
        if any(
            _matches_agent(declared, product) and declared.strip().lower() != "*"
            for declared in group_agents
        )
    ]
    wildcard = [
        rules_for_group
        for group_agents, rules_for_group in groups
        if any(declared.strip().lower() == "*" for declared in group_agents)
    ]
    selected = specific or wildcard
    parser = RobotFileParser()
    lines = ["User-agent: *"]
    for rules_for_group in selected:
        lines.extend(rules_for_group)
    parser.parse(lines)
    return _apply_longest_match(parser)


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


def _robots_from_response(response: httpx.Response, url: str) -> RobotFileParser:
    status = response.status_code
    if status in _RETRIABLE_CLIENT_ERRORS or status >= 500:
        response.raise_for_status()
    if status >= 400:
        return _empty_robots()
    return parse_robots(response.text, user_agent=settings.user_agent)


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

    async def _robots(self, url: str) -> RobotFileParser:
        host = urlsplit(url).hostname
        if not host:
            raise ValueError(f"URL has no host: {url!r}")
        host = host.lower()
        cached = self._robots_cache.get(host)
        if cached is not None:
            return cached

        robots_url = _robots_url(url)
        response = await self._client.get(robots_url, follow_redirects=False)
        parser = _robots_from_response(response, robots_url)
        self._robots_cache.set(host, parser)
        return parser

    async def _is_allowed_by_robots(self, url: str) -> bool:
        parser = await self._robots(url)
        return parser.can_fetch(settings.user_agent, url)

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
