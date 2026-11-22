"""The seven-step durable pipeline that ingests one discovered article."""

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
from living_kb.sources.parser import parse_article
from living_kb.sources.publications import NotAllowedError, Publication, assert_allowed


@dataclass(frozen=True)
class FetchedPage:
    url: str
    html: str


class FetchHtml(Protocol):
    async def __call__(self, url: str) -> FetchedPage: ...


class Embed(Protocol):
    async def __call__(self, texts: list[str]) -> list[list[float]]: ...


class SendEvent(Protocol):
    async def __call__(self, name: str, data: dict[str, object]) -> None: ...


@dataclass(frozen=True)
class IngestPorts:
    fetch_html: FetchHtml
    embed: Embed
    send_event: SendEvent
    session_factory: async_sessionmaker[AsyncSession]
    publications: Sequence[Publication]


class FetchedPayload(TypedDict):
    url: str
    html: str


class ParsedPayload(TypedDict):
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


async def _fetch(url: str, fetch_html: FetchHtml) -> FetchedPayload:
    page = await fetch_html(url)
    return {"url": page.url, "html": page.html}


def _parse(page: FetchedPage, fallback: datetime) -> ParsedPayload:
    parsed = parse_article(page.html, page.url, fallback_published_at=fallback)
    return {
        "title": parsed.title,
        "author": parsed.author,
        "published_at": parsed.published_at.isoformat(),
        "text": parsed.text,
        "content_hash": parsed.content_hash,
    }


async def _persist(
    session_factory: async_sessionmaker[AsyncSession],
    event: ArticleDiscovered,
    final_url: str,
    parsed: ParsedPayload,
    chunks: list[str],
    vectors: list[list[float]],
) -> PersistPayload:
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
                "url": final_url,
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
                    ON CONFLICT (article_id, chunk_index) DO UPDATE SET
                        text = EXCLUDED.text,
                        embedding = EXCLUDED.embedding
                    """
                ),
                {
                    "article_id": article_pk,
                    "chunk_index": index,
                    "body": chunk,
                    "embedding": embedding,
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
    fetched = await runner.run("fetch-html", lambda: _fetch(str(discovered.url), ports.fetch_html))
    page = FetchedPage(url=fetched["url"], html=fetched["html"])
    parsed = await runner.run("parse", lambda: _parse(page, discovered.published_at))
    chunks = await runner.run(
        "chunk",
        lambda: chunk_text(parsed["text"], settings.chunk_size, settings.chunk_overlap),
    )
    vectors = await runner.run("embed", lambda: ports.embed(chunks))
    persisted = await runner.run(
        "persist",
        lambda: _persist(ports.session_factory, discovered, page.url, parsed, chunks, vectors),
    )
    await runner.run("emit", lambda: _emit(discovered, persisted, ports.send_event))
    return {"article_id": persisted["article_id"], "passage_count": persisted["passage_count"]}
