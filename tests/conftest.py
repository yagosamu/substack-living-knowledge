from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable, Iterator
from typing import cast

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from living_kb.config import settings

SessionFactory = async_sessionmaker[AsyncSession]
ZERO_VECTOR = "[" + ",".join("0" for _ in range(settings.embedding_dimensions)) + "]"


@pytest.fixture(scope="session")
def session_factory() -> Iterator[SessionFactory]:
    engine = create_async_engine(settings.database_url, pool_pre_ping=True, poolclass=NullPool)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        asyncio.run(engine.dispose())


@pytest.fixture
def session_override(
    session_factory: SessionFactory,
) -> Callable[[], AsyncIterator[AsyncSession]]:
    async def override() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            yield session

    return override


@pytest.fixture
def clean_db(session_factory: SessionFactory) -> None:
    async def clear() -> None:
        async with session_factory() as session:
            for table in ("costs", "passages", "jobs", "articles", "publications"):
                await session.execute(text(f"DELETE FROM {table}"))
            await session.commit()

    asyncio.run(clear())


@pytest.fixture
def populated_db(session_factory: SessionFactory, clean_db: None) -> None:
    async def seed() -> None:
        async with session_factory() as session:
            publication_id = cast(
                int,
                await session.scalar(
                    text(
                        "INSERT INTO publications (slug, name, feed_url, homepage) "
                        "VALUES ('fixture', 'Fixture', 'https://example.test/feed', "
                        "'https://example.test') RETURNING id"
                    )
                ),
            )
            article_id = cast(
                int,
                await session.scalar(
                    text(
                        "INSERT INTO articles (canonical_id, publication_id, url) "
                        "VALUES ('fixture-1', :publication_id, 'https://example.test/1') "
                        "RETURNING id"
                    ),
                    {"publication_id": publication_id},
                ),
            )
            for chunk_index in (0, 1):
                await session.execute(
                    text(
                        "INSERT INTO passages (article_id, chunk_index, text, embedding) "
                        "VALUES (:article_id, :chunk_index, 'fixture text', "
                        "CAST(:embedding AS vector))"
                    ),
                    {
                        "article_id": article_id,
                        "chunk_index": chunk_index,
                        "embedding": ZERO_VECTOR,
                    },
                )
            await session.commit()

    asyncio.run(seed())
