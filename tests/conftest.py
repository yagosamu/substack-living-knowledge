from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable, Iterator

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from living_kb.config import settings

SessionFactory = async_sessionmaker[AsyncSession]


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
