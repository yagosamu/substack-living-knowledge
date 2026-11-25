import asyncio
import subprocess
import sys

import httpx

from living_kb.config import settings
from living_kb.inngest_app import HttpxFetcher


def test_fetcher_keeps_the_final_url_after_a_redirect() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if request.url.path == "/p/old":
            return httpx.Response(302, headers={"Location": "https://www.example.com/p/new"})
        assert request.headers["user-agent"] == settings.user_agent
        return httpx.Response(200, text="<article>a full post</article>")

    async def fetch() -> tuple[str, str]:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            headers={"User-Agent": settings.user_agent},
            timeout=settings.request_timeout_seconds,
            follow_redirects=True,
        ) as client:
            page = await HttpxFetcher(client)("https://www.example.com/p/old")
        return page.url, page.html

    url, html = asyncio.run(fetch())

    assert seen == [
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
