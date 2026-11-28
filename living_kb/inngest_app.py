"""The Inngest client and the functions registered with it."""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from typing import Any, cast

import httpx
import inngest

from living_kb.config import settings
from living_kb.db import SessionFactory
from living_kb.durable import InngestRunner, StepRunner, run_durable
from living_kb.events import ARTICLE_DISCOVERED
from living_kb.pipelines.ingest import IngestPorts, ingest_article
from living_kb.sources.embedder import EmbeddedTexts, EmbeddingClient, OpenAIEmbedder
from living_kb.sources.fetcher import HttpxFetcher as HttpxFetcher
from living_kb.sources.fetcher import RobotsCache
from living_kb.sources.publications import Publication, load_publications

logger = logging.getLogger("living_kb.inngest")
publications = load_publications(settings.publications_registry_path)
_robots_cache = RobotsCache()

inngest_client = inngest.Inngest(
    app_id=settings.inngest_app_id,
    api_base_url=settings.inngest_base_url,
    event_api_base_url=settings.inngest_base_url,
    event_key=settings.inngest_event_key or None,
    signing_key=settings.inngest_signing_key or None,
    is_production=not settings.inngest_dev,
    logger=logging.getLogger("uvicorn.inngest"),
)


def _log(message: str, run_id: str, **fields: object) -> None:
    logger.info(json.dumps({"message": message, "run_id": run_id, **fields}))


@asynccontextmanager
async def article_fetcher(
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    robots_cache: RobotsCache | None = None,
    allowed_publications: Sequence[Publication] | None = None,
) -> AsyncIterator[HttpxFetcher]:
    """Build the production fetcher: one polite client, shared robots cache."""
    async with httpx.AsyncClient(
        headers={"User-Agent": settings.user_agent},
        timeout=settings.request_timeout_seconds,
        follow_redirects=False,
        transport=transport,
    ) as client:
        yield HttpxFetcher(
            client,
            publications=publications if allowed_publications is None else allowed_publications,
            robots_cache=robots_cache or _robots_cache,
        )


class LazyEmbedder:
    """Import and construct the paid SDK only when the embed step actually runs."""

    def __init__(self) -> None:
        self._inner: OpenAIEmbedder | None = None

    def _get(self) -> OpenAIEmbedder:
        if self._inner is None:
            import openai

            self._inner = OpenAIEmbedder(
                cast(
                    "EmbeddingClient",
                    openai.AsyncOpenAI(api_key=settings.openai_api_key),
                ),
                model=settings.embedding_model,
                dimensions=settings.embedding_dimensions,
                batch_size=settings.embedding_batch_size,
                price_per_million_tokens=settings.embedding_price_per_million_tokens,
            )
        return self._inner

    async def __call__(self, texts: list[str]) -> EmbeddedTexts:
        return await self._get().embed(texts)


class InngestEventSender:
    """Send an announcement through the same Inngest client that serves the function."""

    async def __call__(self, name: str, data: dict[str, object]) -> None:
        await inngest_client.send(cast("inngest.Event", {"name": name, "data": data}))


async def demo_pipeline(runner: StepRunner, *, name: str) -> dict[str, str]:
    """Prove the seam with two named steps and no Inngest dependency."""
    greeting = await runner.run("greet", lambda: f"hello, {name}")
    shouted = await runner.run("shout", lambda: greeting.upper())
    return {"greeting": shouted}


@inngest_client.create_function(
    fn_id="demo-durable",
    trigger=inngest.TriggerEvent(event="kb/demo.requested"),
    retries=settings.step_retries,
)
async def demo_durable(ctx: inngest.Context) -> dict[str, str]:
    """Run the demo pipeline with the real engine's step runner."""
    return await demo_pipeline(InngestRunner(ctx), name=str(ctx.event.data["name"]))


async def handle_ingest_article(
    ctx: inngest.Context, *, transport: httpx.AsyncBaseTransport | None = None
) -> dict[str, int]:
    """Wire the production ports and run the six durable steps for one article."""
    run_id = str(getattr(ctx, "run_id", "unknown"))
    _log("ingest-article started", run_id, canonical_id=ctx.event.data.get("canonical_id"))

    async def pipeline(runner: StepRunner) -> dict[str, int]:
        async with article_fetcher(
            transport=transport, allowed_publications=publications
        ) as fetcher:
            ports = IngestPorts(
                fetch_html=fetcher,
                embed=LazyEmbedder(),
                send_event=InngestEventSender(),
                session_factory=SessionFactory,
                publications=publications,
            )
            return await ingest_article(runner, dict(ctx.event.data), ports)

    try:
        result = await run_durable(ctx, pipeline)
    except Exception as exc:
        _log("ingest-article failed", run_id, error=str(exc))
        raise

    _log(
        "ingest-article completed",
        run_id,
        article_id=result["article_id"],
        passage_count=result["passage_count"],
    )
    return result


@inngest_client.create_function(
    fn_id="ingest-article",
    trigger=inngest.TriggerEvent(event=ARTICLE_DISCOVERED),
    retries=settings.step_retries,
    concurrency=[
        inngest.Concurrency(
            key="event.data.publication_id",
            limit=settings.ingest_concurrency,
        )
    ],
)
async def ingest_article_function(ctx: inngest.Context) -> dict[str, int]:
    """Let the production entry point translate permanent failures anywhere in the run."""
    return await handle_ingest_article(ctx)


functions: list[inngest.Function[Any]] = [demo_durable, ingest_article_function]
