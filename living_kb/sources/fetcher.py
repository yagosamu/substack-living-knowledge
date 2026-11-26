"""Polite, bounded HTTP fetching of publisher pages."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from urllib.parse import unquote, urljoin, urlsplit
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


def _compile_rule(path: str) -> re.Pattern[str]:
    parts: list[str] = []
    for index, char in enumerate(path):
        if char == "*":
            parts.append(".*")
        elif char == "$" and index == len(path) - 1:
            parts.append("$")
        else:
            parts.append(re.escape(char))
    return re.compile("^" + "".join(parts))


class _RobotsParser(RobotFileParser):
    """RobotFileParser with RFC 9309 wildcards and longest-match rules."""

    def __init__(self) -> None:
        super().__init__()
        self._rules: list[tuple[int, bool, re.Pattern[str]]] = []

    def add_rule(self, allowance: bool, path: str) -> None:
        if not path:
            return
        self._rules.append((len(path), allowance, _compile_rule(path)))

    def can_fetch(self, useragent: str, url: str) -> bool:
        parts = urlsplit(url)
        target = unquote(parts.path or "/")
        if parts.query:
            target = f"{target}?{parts.query}"
        matches = [
            (length, allowance)
            for length, allowance, pattern in self._rules
            if pattern.match(target)
        ]
        if not matches:
            return True
        _, allowance = max(matches, key=lambda match: (match[0], match[1]))
        return allowance


def parse_robots(text: str, *, user_agent: str) -> RobotFileParser:
    """Return rules from our agent's group, falling back to the wildcard group.

    Groups are selected before matching so a specific agent wins even when
    ``*`` is listed first. ``urllib.robotparser`` does not support wildcards on
    Python 3.12, so ``*`` and a trailing ``$`` are compiled here. Rules follow
    RFC 9309 longest match, with ``Allow`` winning a tie; empty rules are ignored.
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
    parser = _RobotsParser()
    for rules_for_group in selected:
        for rule in rules_for_group:
            name, _, value = rule.partition(":")
            parser.add_rule(name.strip().lower() == "allow", value.strip())
    return parser


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
