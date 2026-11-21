from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterator, Callable, Coroutine, Iterator
from pathlib import Path
from typing import Any, cast

import pytest
from alembic.command import upgrade
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from living_kb.config import settings

SessionFactory = async_sessionmaker[AsyncSession]
ZERO_VECTOR = "[" + ",".join("0" for _ in range(settings.embedding_dimensions)) + "]"
ALEMBIC_INI = Path(__file__).resolve().parents[1] / "alembic.ini"
TEST_DATABASE_SUFFIX = "_test"


def derive_test_database_url(database_url: str) -> str:
    """Point at a dedicated database on the same server as the configured one."""
    url = make_url(database_url)
    if url.database is None:
        raise ValueError("DATABASE_URL must name a database")
    test_database = f"{url.database}{TEST_DATABASE_SUFFIX}"
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", test_database) is None:
        raise ValueError(f"Cannot derive a safe test database name from {url.database!r}")
    return url.set(database=test_database).render_as_string(hide_password=False)


async def create_test_database(database_url: str, test_database_url: str) -> None:
    """Create the test database on the configured server if it does not exist."""
    name = cast(str, make_url(test_database_url).database)
    engine = create_async_engine(database_url, isolation_level="AUTOCOMMIT", poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            exists = await connection.scalar(
                text("SELECT 1 FROM pg_database WHERE datname = :name"), {"name": name}
            )
            if exists is None:
                await connection.execute(text(f'CREATE DATABASE "{name}"'))
    finally:
        await engine.dispose()


def migrate_test_database(test_database_url: str) -> None:
    """Apply every migration to the test database, not the configured one."""
    config = Config(str(ALEMBIC_INI))
    config.set_main_option("script_location", str(ALEMBIC_INI.parent / "living_kb" / "migrations"))
    config.attributes["database_url"] = test_database_url
    upgrade(config, "head")


async def reset_database(test_database_url: str) -> None:
    """Remove all rows from the test database only."""
    engine = create_async_engine(test_database_url, poolclass=NullPool)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "TRUNCATE TABLE costs, passages, jobs, articles, publications "
                    "RESTART IDENTITY CASCADE"
                )
            )
    finally:
        await engine.dispose()


@pytest.fixture(scope="session")
def test_database_url() -> str:
    return derive_test_database_url(settings.database_url)


@pytest.fixture(scope="session")
def prepared_test_database(test_database_url: str) -> str:
    asyncio.run(create_test_database(settings.database_url, test_database_url))
    migrate_test_database(test_database_url)
    asyncio.run(reset_database(test_database_url))
    return test_database_url


@pytest.fixture(scope="session")
def session_factory(prepared_test_database: str) -> Iterator[SessionFactory]:
    engine = create_async_engine(prepared_test_database, pool_pre_ping=True, poolclass=NullPool)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        asyncio.run(engine.dispose())


@pytest.fixture
def reset_test_db(session_factory: SessionFactory) -> Callable[[], Coroutine[Any, Any, None]]:
    async def reset() -> None:
        async with session_factory() as session:
            await session.execute(
                text(
                    "TRUNCATE TABLE costs, passages, jobs, articles, publications "
                    "RESTART IDENTITY CASCADE"
                )
            )
            await session.commit()

    return reset


@pytest.fixture
def clean_db(reset_test_db: Callable[[], Coroutine[Any, Any, None]]) -> None:
    asyncio.run(reset_test_db())


@pytest.fixture
def session_override(
    session_factory: SessionFactory,
) -> Callable[[], AsyncIterator[AsyncSession]]:
    async def override() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            yield session

    return override


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
