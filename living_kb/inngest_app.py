"""The Inngest client and the functions registered with it."""

from __future__ import annotations

import logging
from typing import Any, cast

import httpx
import inngest

from living_kb.config import settings
from living_kb.db import SessionFactory
from living_kb.durable import StepRunner, run_durable
from living_kb.events import ARTICLE_DISCOVERED
from living_kb.pipelines.ingest import FetchedPage, IngestPorts, ingest_article
from living_kb.sources.embedder import EmbeddingClient, OpenAIEmbedder
from living_kb.sources.publications import load_publications

logger = logging.getLogger("living_kb.inngest")
publications = load_publications(settings.publications_registry_path)

inngest_client = inngest.Inngest(
    app_id=settings.inngest_app_id,
    api_base_url=settings.inngest_base_url,
    event_api_base_url=settings.inngest_base_url,
    event_key=settings.inngest_event_key or None,
    signing_key=settings.inngest_signing_key or None,
    is_production=not settings.inngest_dev,
    logger=logging.getLogger("uvicorn.inngest"),
)


class HttpxFetcher:
    """Fetch one article page and keep the URL the publisher finally served."""

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def __call__(self, url: str) -> FetchedPage:
        response = await self._client.get(url)
        response.raise_for_status()
        return FetchedPage(url=str(response.url), html=response.text)


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
            )
        return self._inner

    async def __call__(self, texts: list[str]) -> list[list[float]]:
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
    return await run_durable(
        ctx,
        lambda runner: demo_pipeline(runner, name=str(ctx.event.data["name"])),
        function_name="demo-durable",
    )


async def handle_ingest_article(ctx: inngest.Context) -> dict[str, int]:
    """Wire the production ports and run the six durable steps for one article."""

    async def pipeline(runner: StepRunner) -> dict[str, int]:
        logger.info(
            "ingest-article started", extra={"canonical_id": ctx.event.data.get("canonical_id")}
        )
        try:
            async with httpx.AsyncClient(
                headers={"User-Agent": settings.user_agent},
                timeout=settings.request_timeout_seconds,
                follow_redirects=True,
            ) as client:
                ports = IngestPorts(
                    fetch_html=HttpxFetcher(client),
                    embed=LazyEmbedder(),
                    send_event=InngestEventSender(),
                    session_factory=SessionFactory,
                    publications=publications,
                )
                result = await ingest_article(runner, dict(ctx.event.data), ports)
        except Exception as exc:
            logger.exception("ingest-article failed", extra={"error": str(exc)})
            raise

        logger.info(
            "ingest-article completed",
            extra={
                "article_id": result["article_id"],
                "passage_count": result["passage_count"],
            },
        )
        return result

    return await run_durable(ctx, pipeline, function_name="ingest-article")


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
