import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from typing import Any, cast

import httpx
import inngest
import pytest

from living_kb import inngest_app
from living_kb.config import settings
from living_kb.durable import PermanentError
from living_kb.inngest_app import article_fetcher, handle_ingest_article
from living_kb.sources.fetcher import RobotsCache
from living_kb.sources.publications import Publication

TEST_HOSTS = (
    "news.example.com",
    "other.example.net",
    "across-runs.example.com",
    "mirror.example.net",
)
TEST_PUBLICATIONS = [
    Publication(
        slug=host.split(".", 1)[0],
        name=f"{host} Newsletter",
        feed_url=f"https://{host}/feed",
        homepage=f"https://{host}",
    )
    for host in TEST_HOSTS
]


def _robots(status: int = 404, text: str = "") -> httpx.Response:
    return httpx.Response(status, text=text)


def _fetch(
    url: str,
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    cache: RobotsCache | None = None,
    use_production_cache: bool = False,
    publications: Sequence[Publication] = TEST_PUBLICATIONS,
) -> tuple[str, str]:
    async def run() -> tuple[str, str]:
        async with article_fetcher(
            transport=httpx.MockTransport(handler),
            robots_cache=(
                None if use_production_cache else (cache if cache is not None else RobotsCache())
            ),
            allowed_publications=publications,
        ) as fetcher:
            page = await fetcher(url)
        return page.url, page.html

    return asyncio.run(run())


def test_every_request_uses_the_configured_user_agent_and_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = "probe-bot/9 (+https://probe.example)"
    timeout = 3
    monkeypatch.setattr(settings, "user_agent", agent)
    monkeypatch.setattr(settings, "request_timeout_seconds", timeout)
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
    for _, headers, limits in seen:
        assert headers["user-agent"] == agent
        assert limits == {
            "connect": timeout,
            "read": timeout,
            "write": timeout,
            "pool": timeout,
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


def test_the_own_user_agent_group_wins_over_the_wildcard() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if request.url.path == "/robots.txt":
            return _robots(
                200,
                "User-agent: *\nDisallow: /\n\n"
                "User-agent: OtherBot\nDisallow: /only-for-other-bot\n\n"
                "User-agent: living-kb\nDisallow: /private\n",
            )
        return httpx.Response(200, text="<article>a full post</article>")

    assert _fetch("https://news.example.com/public/post", handler)[1] == (
        "<article>a full post</article>"
    )
    with pytest.raises(PermanentError, match="disallows"):
        _fetch("https://news.example.com/private/post", handler)
    assert seen == [
        "https://news.example.com/robots.txt",
        "https://news.example.com/public/post",
        "https://news.example.com/robots.txt",
    ]


def test_allow_rules_override_a_broader_disallow() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return _robots(200, "User-agent: *\nDisallow: /p/\nAllow: /p/public\n")
        return httpx.Response(200, text="<article>a full post</article>")

    assert _fetch("https://news.example.com/p/public/post", handler)[1] == (
        "<article>a full post</article>"
    )
    with pytest.raises(PermanentError, match="disallows"):
        _fetch("https://news.example.com/p/private/post", handler)


def test_allow_wins_an_equal_length_tie() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return _robots(200, "User-agent: *\nDisallow: /p/public\nAllow: /p/public\n")
        return httpx.Response(200, text="<article>a full post</article>")

    assert _fetch("https://news.example.com/p/public", handler)[1] == (
        "<article>a full post</article>"
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


@pytest.mark.parametrize("status", [401, 403, 404, 410], ids=["401", "403", "404", "410"])
def test_every_robots_4xx_except_408_and_429_means_no_rules(status: int) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return _robots(status)
        return httpx.Response(200, text="<article>a full post</article>")

    assert _fetch("https://news.example.com/p/post", handler) == (
        "https://news.example.com/p/post",
        "<article>a full post</article>",
    )


@pytest.mark.parametrize("status", [408, 429], ids=["408", "429"])
def test_robots_retriable_client_errors_stay_retriable(status: int) -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return _robots(status)

    with pytest.raises(httpx.HTTPStatusError) as raised:
        _fetch("https://news.example.com/p/post", handler)
    assert raised.value.response.status_code == status
    assert not isinstance(raised.value, PermanentError)
    assert seen == ["https://news.example.com/robots.txt"]


def test_robots_500_is_retriable_and_never_allows_the_article() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return _robots(500)

    with pytest.raises(httpx.HTTPStatusError) as raised:
        _fetch("https://news.example.com/p/post", handler)
    assert raised.value.response.status_code == 500
    assert not isinstance(raised.value, PermanentError)
    assert seen == ["https://news.example.com/robots.txt"]


def test_robots_txt_redirects_are_followed_and_the_final_rules_are_used() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if request.url.path == "/robots.txt":
            return httpx.Response(301, headers={"Location": "/robots-final.txt"})
        if request.url.path == "/robots-final.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /p/secret\n")
        return httpx.Response(200, text="<article>the final post</article>")

    with pytest.raises(PermanentError, match="robots.txt disallows"):
        _fetch("https://news.example.com/p/secret", handler)

    assert seen == [
        "https://news.example.com/robots.txt",
        "https://news.example.com/robots-final.txt",
    ]


def test_robots_redirect_is_cached_under_the_article_host() -> None:
    cache = RobotsCache()
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if request.url.path == "/robots.txt":
            return httpx.Response(301, headers={"Location": "/robots-final.txt"})
        if request.url.path == "/robots-final.txt":
            return _robots()
        return httpx.Response(200, text="<article>a full post</article>")

    _fetch("https://news.example.com/p/first", handler, cache=cache)
    _fetch("https://news.example.com/p/second", handler, cache=cache)

    assert [url for url in seen if "robots" in url] == [
        "https://news.example.com/robots.txt",
        "https://news.example.com/robots-final.txt",
    ]


def test_a_cross_host_robots_redirect_is_cached_under_the_article_host() -> None:
    cache = RobotsCache()
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if request.url.host == "news.example.com" and request.url.path == "/robots.txt":
            return httpx.Response(
                302, headers={"Location": "https://mirror.example.net/robots-final.txt"}
            )
        if request.url.path == "/robots-final.txt":
            return _robots()
        return httpx.Response(200, text="<article>a full post</article>")

    _fetch("https://news.example.com/p/first", handler, cache=cache)
    _fetch("https://news.example.com/p/second", handler, cache=cache)

    assert [url for url in seen if "robots" in url] == [
        "https://news.example.com/robots.txt",
        "https://mirror.example.net/robots-final.txt",
    ]


def test_an_off_allowlist_robots_redirect_is_never_requested() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(302, headers={"Location": "https://evil.example.org/robots.txt"})

    with pytest.raises(PermanentError, match="allowlist"):
        _fetch("https://news.example.com/p/post", handler)

    assert seen == ["https://news.example.com/robots.txt"]


def test_a_robots_redirect_chain_over_the_limit_stays_retriable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "max_redirects", 2)
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        locations = {
            "/robots.txt": "/robots-1.txt",
            "/robots-1.txt": "/robots-2.txt",
            "/robots-2.txt": "/robots-3.txt",
        }
        if request.url.path in locations:
            return httpx.Response(302, headers={"Location": locations[request.url.path]})
        return httpx.Response(200, text="<article>the final post</article>")

    with pytest.raises(httpx.TooManyRedirects) as raised:
        _fetch("https://news.example.com/p/post", handler)

    assert not isinstance(raised.value, PermanentError)
    assert seen == [
        "https://news.example.com/robots.txt",
        "https://news.example.com/robots-1.txt",
        "https://news.example.com/robots-2.txt",
    ]


def test_robots_400_with_a_disallow_body_still_means_no_rules() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if request.url.path == "/robots.txt":
            return _robots(400, "User-agent: *\nDisallow: /\n")
        return httpx.Response(200, text="<article>a full post</article>")

    assert _fetch("https://news.example.com/p/post", handler) == (
        "https://news.example.com/p/post",
        "<article>a full post</article>",
    )
    assert seen == [
        "https://news.example.com/robots.txt",
        "https://news.example.com/p/post",
    ]


@pytest.mark.parametrize(
    ("rule", "url", "allowed"),
    [
        ("Disallow: /*.pdf$", "https://news.example.com/report.pdf", False),
        ("Disallow: /*.pdf$", "https://news.example.com/report.pdf?download=1", True),
        ("Disallow: /p/*/draft", "https://news.example.com/p/2026/draft", False),
        ("Disallow: /p/*/draft", "https://news.example.com/p/public/post", True),
    ],
    ids=["suffix", "suffix-anchored", "middle", "no-match"],
)
def test_robots_wildcards_and_end_anchors_follow_rfc_9309(
    rule: str, url: str, allowed: bool
) -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if request.url.path == "/robots.txt":
            return _robots(200, f"User-agent: *\n{rule}\n")
        return httpx.Response(200, text="<article>a full post</article>")

    if allowed:
        assert _fetch(url, handler)[0] == url
    else:
        with pytest.raises(PermanentError, match="robots.txt disallows"):
            _fetch(url, handler)
    assert seen == ["https://news.example.com/robots.txt"] + ([] if not allowed else [url])


class _ChunkedStream(httpx.AsyncByteStream):
    def __init__(self, chunks: Sequence[bytes]) -> None:
        self.chunks = tuple(chunks)
        self.consumed = 0

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for chunk in self.chunks:
            self.consumed += 1
            yield chunk
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
        return httpx.Response(200, stream=_ChunkedStream([b"x" * 25]))

    with pytest.raises(PermanentError, match="exceeds 24 bytes"):
        _fetch("https://news.example.com/p/huge", handler)

    assert len(seen) == 2


def test_the_size_cap_accumulates_across_many_small_chunks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "max_article_bytes", 9)
    stream = _ChunkedStream([b"12345", b"67890", b"still-more"])

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return _robots()
        return httpx.Response(200, stream=stream)

    with pytest.raises(PermanentError, match="exceeds 9 bytes"):
        _fetch("https://news.example.com/p/chunked", handler)

    assert stream.consumed == 2


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


@pytest.mark.parametrize(
    "status", [301, 302, 303, 307, 308], ids=["301", "302", "303", "307", "308"]
)
def test_redirects_are_followed_and_the_final_url_is_returned(status: int) -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if request.url.path == "/robots.txt":
            return _robots()
        if request.url.path == "/p/old":
            return httpx.Response(status, headers={"Location": "https://news.example.com/p/final"})
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


def test_the_final_url_is_taken_from_the_response_not_the_redirect_string() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return _robots()
        if request.url.path == "/p/old":
            return httpx.Response(302, headers={"Location": "https://NEWS.example.com/p/final"})
        return httpx.Response(200, text="<article>the final post</article>")

    assert _fetch("https://news.example.com/p/old", handler)[0] == (
        "https://news.example.com/p/final"
    )


def test_a_relative_redirect_is_resolved_against_the_current_hop() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if request.url.path == "/robots.txt":
            return _robots()
        if request.url.path == "/p/old":
            return httpx.Response(
                302, headers={"Location": "https://news.example.com/p/a/b/middle"}
            )
        if request.url.path == "/p/a/b/middle":
            return httpx.Response(302, headers={"Location": "../up"})
        return httpx.Response(200, text="<article>the final post</article>")

    assert _fetch("https://news.example.com/p/old", handler)[0] == (
        "https://news.example.com/p/a/up"
    )
    assert seen == [
        "https://news.example.com/robots.txt",
        "https://news.example.com/p/old",
        "https://news.example.com/p/a/b/middle",
        "https://news.example.com/p/a/up",
    ]


def test_an_allowed_multi_hop_chain_checks_each_hosts_robots() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        host = request.url.host
        if request.url.path == "/robots.txt":
            if host == "news.example.com":
                return _robots(200, "User-agent: *\nDisallow: /private\n")
            return _robots()
        if host == "news.example.com" and request.url.path == "/p/start":
            return httpx.Response(302, headers={"Location": "https://mirror.example.net/q/next"})
        if host == "mirror.example.net" and request.url.path == "/q/next":
            return httpx.Response(302, headers={"Location": "https://news.example.com/p/final"})
        return httpx.Response(200, text="<article>the final post</article>")

    assert _fetch("https://news.example.com/p/start", handler) == (
        "https://news.example.com/p/final",
        "<article>the final post</article>",
    )
    assert seen == [
        "https://news.example.com/robots.txt",
        "https://news.example.com/p/start",
        "https://mirror.example.net/robots.txt",
        "https://mirror.example.net/q/next",
        "https://news.example.com/p/final",
    ]


def test_an_off_allowlist_redirect_is_never_requested() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if request.url.path == "/robots.txt":
            return _robots()
        return httpx.Response(302, headers={"Location": "https://evil.example.org/p/secret"})

    with pytest.raises(PermanentError, match="allowlist"):
        _fetch("https://news.example.com/p/open", handler)

    assert seen == [
        "https://news.example.com/robots.txt",
        "https://news.example.com/p/open",
    ]


def test_a_redirect_to_the_cloud_metadata_address_is_never_requested() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if request.url.path == "/robots.txt":
            return _robots()
        return httpx.Response(302, headers={"Location": "http://169.254.169.254/latest/meta-data"})

    with pytest.raises(PermanentError, match="allowlist"):
        _fetch("https://news.example.com/p/open", handler)

    assert seen == [
        "https://news.example.com/robots.txt",
        "https://news.example.com/p/open",
    ]


def test_a_same_host_redirect_into_a_disallowed_path_is_never_requested() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if request.url.path == "/robots.txt":
            return _robots(200, "User-agent: *\nDisallow: /private\n")
        return httpx.Response(302, headers={"Location": "https://news.example.com/private/x"})

    with pytest.raises(PermanentError, match="robots.txt disallows"):
        _fetch("https://news.example.com/p/open", handler)

    assert seen == [
        "https://news.example.com/robots.txt",
        "https://news.example.com/p/open",
    ]


def test_a_non_http_redirect_is_never_requested() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if request.url.path == "/robots.txt":
            return _robots()
        return httpx.Response(302, headers={"Location": "ftp://news.example.com/etc/passwd"})

    with pytest.raises(PermanentError, match="only http\\(s\\) URLs"):
        _fetch("https://news.example.com/p/open", handler)

    assert seen == [
        "https://news.example.com/robots.txt",
        "https://news.example.com/p/open",
    ]


def test_a_redirect_chain_over_the_hop_limit_stops_before_the_next_hop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "max_redirects", 2)
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if request.url.path == "/robots.txt":
            return _robots()
        index = int(request.url.path.rsplit("/", 1)[-1])
        return httpx.Response(302, headers={"Location": f"https://news.example.com/p/{index + 1}"})

    with pytest.raises(PermanentError, match="exceeds 2 hops"):
        _fetch("https://news.example.com/p/0", handler)

    assert seen == [
        "https://news.example.com/robots.txt",
        "https://news.example.com/p/0",
        "https://news.example.com/p/1",
        "https://news.example.com/p/2",
    ]


@pytest.mark.parametrize(
    ("status", "outcome"),
    [
        (399, "page"),
        (400, "permanent"),
        (407, "permanent"),
        (408, "retriable"),
        (409, "permanent"),
        (428, "permanent"),
        (429, "retriable"),
        (430, "permanent"),
        (499, "permanent"),
        (500, "retriable"),
        (599, "retriable"),
    ],
    ids=["399", "400", "407", "408", "409", "428", "429", "430", "499", "500", "599"],
)
def test_http_status_boundaries(status: int, outcome: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return _robots()
        return httpx.Response(status, text="response body")

    if outcome == "page":
        assert _fetch("https://news.example.com/p/post", handler) == (
            "https://news.example.com/p/post",
            "response body",
        )
        return

    if outcome == "permanent":
        with pytest.raises(PermanentError, match=f"HTTP {status}"):
            _fetch("https://news.example.com/p/post", handler)
        return

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
        _fetch(
            f"https://across-runs.example.com{path}",
            handler,
            use_production_cache=True,
        )

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
        assert request.headers["user-agent"] == settings.user_agent
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


def test_the_registered_handler_rejects_an_off_allowlist_redirect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(inngest_app, "_robots_cache", RobotsCache())
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if request.url.path == "/robots.txt":
            return _robots()
        return httpx.Response(302, headers={"Location": "https://evil.example.org/p/x"})

    with pytest.raises(inngest.NonRetriableError, match="allowlist"):
        asyncio.run(handle_ingest_article(_context(), transport=httpx.MockTransport(handler)))

    assert seen == [
        "https://www.decodingai.com/robots.txt",
        "https://www.decodingai.com/p/test",
    ]
