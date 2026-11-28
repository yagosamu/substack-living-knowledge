import asyncio
import subprocess
import sys

import httpx

from living_kb.config import settings
from living_kb.inngest_app import HttpxFetcher
from living_kb.sources.publications import Publication

EXAMPLE = Publication(
    slug="example",
    name="Example Newsletter",
    feed_url="https://www.example.com/feed",
    homepage="https://www.example.com",
)


def test_fetcher_keeps_the_final_url_after_a_redirect() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        assert request.headers["user-agent"] == settings.user_agent
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\n")
        if request.url.path == "/p/old":
            return httpx.Response(302, headers={"Location": "https://www.example.com/p/new"})
        return httpx.Response(200, text="<article>a full post</article>")

    async def fetch() -> tuple[str, str]:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            headers={"User-Agent": settings.user_agent},
            timeout=settings.request_timeout_seconds,
            follow_redirects=False,
        ) as client:
            page = await HttpxFetcher(client, publications=[EXAMPLE])(
                "https://www.example.com/p/old"
            )
        return page.url, page.html

    url, html = asyncio.run(fetch())

    assert seen == [
        "https://www.example.com/robots.txt",
        "https://www.example.com/p/old",
        "https://www.example.com/p/new",
    ]
    assert (url, html) == ("https://www.example.com/p/new", "<article>a full post</article>")


def test_importing_the_app_does_not_import_the_paid_sdk() -> None:
    script = "import sys, living_kb.inngest_app; print('openai' in sys.modules)"
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, check=True
    )

    assert result.stdout.strip() == "False"
