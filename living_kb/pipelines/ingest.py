"""The six-step durable pipeline that ingests one discovered article."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, TypedDict, cast

from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from living_kb.config import settings
from living_kb.durable import PermanentError, StepRunner
from living_kb.events import (
    ARTICLE_DISCOVERED,
    ARTICLE_INGESTED,
    ArticleDiscovered,
    ArticleIngested,
)
from living_kb.sources.chunking import chunk_text
from living_kb.sources.embedder import EmbeddedTexts
from living_kb.sources.parser import parse_article
from living_kb.sources.publications import NotAllowedError, Publication, assert_allowed


@dataclass(frozen=True)
class FetchedPage:
    url: str
    html: str


class FetchHtml(Protocol):
    async def __call__(self, url: str) -> FetchedPage: ...


class Embed(Protocol):
    async def __call__(self, texts: list[str]) -> EmbeddedTexts: ...


class SendEvent(Protocol):
    async def __call__(self, name: str, data: dict[str, object]) -> None: ...


@dataclass(frozen=True)
class IngestPorts:
    fetch_html: FetchHtml
    embed: Embed
    send_event: SendEvent
    session_factory: async_sessionmaker[AsyncSession]
    publications: Sequence[Publication]


class ArticlePayload(TypedDict):
    url: str
    title: str | None
    author: str | None
    published_at: str
    text: str
    content_hash: str


class PersistPayload(TypedDict):
    article_id: int
    content_hash: str
    passage_count: int


def _check_allowlist(event: ArticleDiscovered, publications: Sequence[Publication]) -> str:
    try:
        publication = assert_allowed(str(event.url), publications)
    except (NotAllowedError, ValueError) as exc:
        raise PermanentError(str(exc)) from exc
    return publication.slug


async def _fetch_article(url: str, fetch_html: FetchHtml, fallback: datetime) -> ArticlePayload:
    page = await fetch_html(url)
    parsed = parse_article(page.html, page.url, fallback_published_at=fallback)
    return {
        "url": page.url,
        "title": parsed.title,
        "author": parsed.author,
        "published_at": parsed.published_at.isoformat(),
        "text": parsed.text,
        "content_hash": parsed.content_hash,
    }


async def _persist(
    session_factory: async_sessionmaker[AsyncSession],
    event: ArticleDiscovered,
    parsed: ArticlePayload,
    chunks: list[str],
    embedded: EmbeddedTexts,
) -> PersistPayload:
    vectors = embedded["vectors"]
    usage = embedded["usage"]
    if len(chunks) != len(vectors):
        raise ValueError("each chunk needs exactly one embedding")

    embedding_literals = [json.dumps(vector) for vector in vectors]
    published_at = datetime.fromisoformat(parsed["published_at"])
    async with session_factory() as session, session.begin():
        article_id = await session.scalar(
            text(
                """
                INSERT INTO articles (
                    canonical_id, publication_id, url, title, author, published_at,
                    content_hash, "text", status
                ) VALUES (
                    :canonical_id, :publication_id, :url, :title, :author, :published_at,
                    :content_hash, :body, 'ingested'
                )
                ON CONFLICT (canonical_id) DO UPDATE SET
                    publication_id = EXCLUDED.publication_id,
                    url = EXCLUDED.url,
                    title = EXCLUDED.title,
                    author = EXCLUDED.author,
                    published_at = EXCLUDED.published_at,
                    content_hash = EXCLUDED.content_hash,
                    "text" = EXCLUDED."text",
                    status = 'ingested',
                    updated_at = now()
                RETURNING id
                """
            ),
            {
                "canonical_id": str(event.canonical_id),
                "publication_id": event.publication_id,
                "url": parsed["url"],
                "title": parsed["title"],
                "author": parsed["author"],
                "published_at": published_at,
                "content_hash": parsed["content_hash"],
                "body": parsed["text"],
            },
        )
        if article_id is None:
            raise RuntimeError("upserting the article returned no id")
        article_pk = int(article_id)

        await session.execute(
            text("DELETE FROM passages WHERE article_id = :article_id"),
            {"article_id": article_pk},
        )
        for index, (chunk, embedding) in enumerate(zip(chunks, embedding_literals, strict=True)):
            await session.execute(
                text(
                    """
                    INSERT INTO passages (article_id, chunk_index, text, embedding)
                    VALUES (:article_id, :chunk_index, :body, CAST(:embedding AS vector))
                    """
                ),
                {
                    "article_id": article_pk,
                    "chunk_index": index,
                    "body": chunk,
                    "embedding": embedding,
                },
            )
        await session.execute(
            text(
                """
                INSERT INTO costs (
                    article_id, model, input_tokens, output_tokens, estimated_usd
                ) VALUES (
                    :article_id, :model, :input_tokens, 0, :estimated_usd
                )
                ON CONFLICT (article_id) WHERE article_id IS NOT NULL DO NOTHING
                """
            ),
            {
                "article_id": article_pk,
                "model": usage["model"],
                "input_tokens": usage["input_tokens"],
                "estimated_usd": usage["estimated_usd"],
            },
        )
    return {
        "article_id": article_pk,
        "content_hash": parsed["content_hash"],
        "passage_count": len(chunks),
    }


async def _emit(event: ArticleDiscovered, persisted: PersistPayload, sender: SendEvent) -> None:
    announcement = ArticleIngested(
        canonical_id=event.canonical_id,
        article_id=persisted["article_id"],
        content_hash=persisted["content_hash"],
        passage_count=persisted["passage_count"],
    )
    await sender(
        ARTICLE_INGESTED,
        cast("dict[str, object]", announcement.model_dump(mode="json")),
    )


async def ingest_article(
    runner: StepRunner,
    event: dict[str, object],
    ports: IngestPorts,
) -> dict[str, int]:
    """Ingest one article, checkpointing every fragile, paid or side-effecting step."""
    try:
        discovered = ArticleDiscovered.model_validate(event)
    except ValidationError as exc:
        raise PermanentError(f"malformed {ARTICLE_DISCOVERED} event: {exc}") from exc

    await runner.run("check-allowlist", lambda: _check_allowlist(discovered, ports.publications))
    parsed = await runner.run(
        "fetch-article",
        lambda: _fetch_article(str(discovered.url), ports.fetch_html, discovered.published_at),
    )
    chunks = await runner.run(
        "chunk",
        lambda: chunk_text(parsed["text"], settings.chunk_size, settings.chunk_overlap),
    )
    embedded = await runner.run("embed", lambda: ports.embed(chunks))
    persisted = await runner.run(
        "persist",
        lambda: _persist(ports.session_factory, discovered, parsed, chunks, embedded),
    )
    await runner.run("emit", lambda: _emit(discovered, persisted, ports.send_event))
    return {"article_id": persisted["article_id"], "passage_count": persisted["passage_count"]}
