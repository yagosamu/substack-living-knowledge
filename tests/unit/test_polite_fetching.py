import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any, cast

import httpx
import inngest
import pytest

from living_kb import inngest_app
from living_kb.config import settings
from living_kb.durable import PermanentError
from living_kb.inngest_app import article_fetcher, handle_ingest_article
from living_kb.sources.fetcher import RobotsCache

USER_AGENT = settings.user_agent


def _robots(status: int = 404, text: str = "") -> httpx.Response:
    return httpx.Response(status, text=text)


def _fetch(
    url: str,
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    cache: RobotsCache | None = None,
    use_production_cache: bool = False,
) -> tuple[str, str]:
    async def run() -> tuple[str, str]:
        async with article_fetcher(
            transport=httpx.MockTransport(handler),
            robots_cache=(
                None if use_production_cache else (cache if cache is not None else RobotsCache())
            ),
        ) as fetcher:
            page = await fetcher(url)
        return page.url, page.html

    return asyncio.run(run())


def test_every_request_carries_the_configured_user_agent_and_timeout() -> None:
    seen: list[tuple[str, dict[str, Any], dict[str, int]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((str(request.url), dict(request.headers), dict(request.extensions["timeout"])))
        if request.url.path == "/robots.txt":
            return _robots()
        return httpx.Response(200, text="<article>a full post</article>")

    _fetch("https://news.example.com/p/post", handler)

    assert [path for path, _, _ in seen] == [
        "https://news.example.com/robots.txt",
        "https://news.example.com/p/post",
    ]
    for _, headers, timeout in seen:
        assert headers["user-agent"] == USER_AGENT
        assert timeout == {
            "connect": settings.request_timeout_seconds,
            "read": settings.request_timeout_seconds,
            "write": settings.request_timeout_seconds,
            "pool": settings.request_timeout_seconds,
        }


def test_robots_is_cached_per_host_and_a_404_allows_every_path() -> None:
    cache = RobotsCache()
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, text="<article>post body</article>")

    for url in (
        "https://news.example.com/p/private",
        "https://news.example.com/p/public",
        "https://other.example.net/p/private",
        "https://other.example.net/p/public",
    ):
        _fetch(url, handler, cache=cache)

    assert [url for url in seen if url.endswith("/robots.txt")] == [
        "https://news.example.com/robots.txt",
        "https://other.example.net/robots.txt",
    ]
    assert len(seen) == 6


def test_a_robots_disallow_fails_permanently_without_requesting_the_article() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return _robots(200, "User-agent: *\nDisallow: /private\n")

    with pytest.raises(PermanentError, match="robots.txt disallows"):
        _fetch("https://news.example.com/private/post", handler)

    assert seen == ["https://news.example.com/robots.txt"]


def test_robots_rules_apply_only_to_matching_user_agents() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return _robots(200, "User-agent: OtherBot\nDisallow: /only-for-other-bot\n")
        return httpx.Response(200, text="<article>a full post</article>")

    assert _fetch("https://news.example.com/only-for-other-bot/post", handler)[0] == (
        "https://news.example.com/only-for-other-bot/post"
    )


def test_a_disallow_prefix_does_not_block_an_unrelated_path() -> None:
    cache = RobotsCache()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return _robots(200, "User-agent: *\nDisallow: /p/private\n")
        return httpx.Response(200, text="<article>a full post</article>")

    assert _fetch("https://news.example.com/p/public/post", handler, cache=cache)[0] == (
        "https://news.example.com/p/public/post"
    )
    with pytest.raises(PermanentError, match="disallows"):
        _fetch("https://news.example.com/p/private/deep", handler, cache=cache)


class _ExplodingStream(httpx.AsyncByteStream):
    def __init__(self, chunk: bytes) -> None:
        self.chunk = chunk

    async def __aiter__(self) -> AsyncIterator[bytes]:
        yield self.chunk
        raise AssertionError("the stream was read past the size cap")

    async def aread(self) -> bytes:
        raise AssertionError("the body was buffered before the size cap was checked")


def test_an_oversized_body_fails_permanently_while_streaming(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "max_article_bytes", 24)
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if request.url.path == "/robots.txt":
            return _robots()
        return httpx.Response(200, stream=_ExplodingStream(b"x" * 25))

    with pytest.raises(PermanentError, match="exceeds 24 bytes"):
        _fetch("https://news.example.com/p/huge", handler)

    assert len(seen) == 2


def test_a_body_at_the_size_cap_is_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    html = "<article>exactly at the cap</article>"
    monkeypatch.setattr(settings, "max_article_bytes", len(html.encode()))

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return _robots()
        return httpx.Response(200, text=html)

    assert _fetch("https://news.example.com/p/exact", handler) == (
        "https://news.example.com/p/exact",
        html,
    )


def test_redirects_are_followed_and_the_final_url_is_returned() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if request.url.path == "/robots.txt":
            return _robots()
        if request.url.path == "/p/old":
            return httpx.Response(302, headers={"Location": "https://news.example.com/p/final"})
        return httpx.Response(200, text="<article>the final post</article>")

    assert _fetch("https://news.example.com/p/old", handler) == (
        "https://news.example.com/p/final",
        "<article>the final post</article>",
    )
    assert seen == [
        "https://news.example.com/robots.txt",
        "https://news.example.com/p/old",
        "https://news.example.com/p/final",
    ]


@pytest.mark.parametrize(
    ("status", "permanent"),
    [(403, True), (404, True), (408, False), (429, False), (503, False)],
    ids=["403", "404", "408", "429", "503"],
)
def test_http_status_classes_are_permanent_only_for_other_4xx(status: int, permanent: bool) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return _robots()
        return httpx.Response(status, text="failure body")

    if permanent:
        with pytest.raises(PermanentError, match=f"HTTP {status}"):
            _fetch("https://news.example.com/p/post", handler)
    else:
        with pytest.raises(httpx.HTTPStatusError) as raised:
            _fetch("https://news.example.com/p/post", handler)
        assert raised.value.response.status_code == status
        assert not isinstance(raised.value, PermanentError)


@pytest.mark.parametrize("where", ["robots", "article"], ids=["robots", "article"])
def test_timeouts_stay_retriable(where: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if (request.url.path == "/robots.txt") == (where == "robots"):
            raise httpx.TimeoutException("publisher is slow")
        return httpx.Response(200, text="<article>a full post</article>")

    with pytest.raises(httpx.TimeoutException) as raised:
        _fetch("https://news.example.com/p/post", handler)

    assert not isinstance(raised.value, PermanentError)


def test_the_production_fetcher_shares_one_robots_cache_between_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(inngest_app, "_robots_cache", RobotsCache())
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, text="<article>a full post</article>")

    for path in ("/p/first", "/p/second"):
        _fetch(f"https://across-runs.example.com{path}", handler, use_production_cache=True)

    assert [url for url in seen if url.endswith("/robots.txt")] == [
        "https://across-runs.example.com/robots.txt"
    ]


class _FakeStep:
    async def run(self, name: str, fn: Callable[[], Awaitable[object]]) -> object:
        return await fn()


class _FakeEvent:
    def __init__(self, data: dict[str, object]) -> None:
        self.data = data


class _FakeContext:
    run_id = "run-7"

    def __init__(self) -> None:
        self.step = _FakeStep()
        self.event = _FakeEvent(
            {
                "canonical_id": "pub:decoding-ai:test",
                "publication_id": 1,
                "url": "https://www.decodingai.com/p/test",
                "title": "A post about agents",
                "published_at": "2026-11-10T09:30:00+00:00",
                "via": "rss",
            }
        )


def _context() -> inngest.Context:
    return cast(inngest.Context, _FakeContext())


def test_a_permanent_fetch_failure_reaches_the_registered_handler_as_non_retriable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(inngest_app, "_robots_cache", RobotsCache())
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        assert request.headers["user-agent"] == USER_AGENT
        if request.url.path == "/robots.txt":
            return _robots()
        return httpx.Response(403, text="forbidden")

    with pytest.raises(inngest.NonRetriableError, match="HTTP 403") as raised:
        asyncio.run(handle_ingest_article(_context(), transport=httpx.MockTransport(handler)))

    assert isinstance(raised.value.__cause__, PermanentError)
    assert seen == [
        "https://www.decodingai.com/robots.txt",
        "https://www.decodingai.com/p/test",
    ]


def test_a_transient_fetch_failure_reaches_the_registered_handler_as_retriable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(inngest_app, "_robots_cache", RobotsCache())

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return _robots()
        return httpx.Response(503, text="try later")

    with pytest.raises(httpx.HTTPStatusError) as raised:
        asyncio.run(handle_ingest_article(_context(), transport=httpx.MockTransport(handler)))

    assert raised.value.response.status_code == 503
    assert not isinstance(raised.value, inngest.NonRetriableError)
