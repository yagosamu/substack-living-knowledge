from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable, Coroutine
from typing import Any, cast

import httpx
import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from conftest import (
    ALEMBIC_INI,
    ZERO_VECTOR,
    create_test_database,
    database_exists,
    database_name,
    drop_database,
    migrate_test_database,
)
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from living_kb.api import app, get_probe
from living_kb.config import settings
from living_kb.db import get_session


class OkProbe:
    async def get(self, url: str) -> httpx.Response:
        return httpx.Response(200)


def scratch_database_url(dev_url: str, label: str) -> str:
    name = f"{database_name(dev_url)}_{label}_{uuid.uuid4().hex[:8]}"
    return make_url(dev_url).set(database=name).render_as_string(hide_password=False)


async def seed_articles(url: str, prefix: str, count: int) -> tuple[int, int]:
    engine = create_async_engine(url, poolclass=NullPool)
    try:
        async with engine.begin() as connection:
            for index in range(count):
                canonical_id = f"{prefix}-{index}"
                article_id = cast(
                    int,
                    await connection.scalar(
                        text(
                            "INSERT INTO articles (canonical_id, url) "
                            "VALUES (:canonical_id, :url) RETURNING id"
                        ),
                        {"canonical_id": canonical_id, "url": f"https://{prefix}.test/{index}"},
                    ),
                )
                for chunk_index in (0, 1):
                    await connection.execute(
                        text(
                            "INSERT INTO passages (article_id, chunk_index, text, embedding) "
                            "VALUES (:article_id, :chunk_index, 'probe text', "
                            "CAST(:embedding AS vector))"
                        ),
                        {
                            "article_id": article_id,
                            "chunk_index": chunk_index,
                            "embedding": ZERO_VECTOR,
                        },
                    )
        async with engine.connect() as connection:
            articles = cast(int, await connection.scalar(text("SELECT count(*) FROM articles")))
            passages = cast(int, await connection.scalar(text("SELECT count(*) FROM passages")))
        return articles, passages
    finally:
        await engine.dispose()


@pytest.mark.integration
def test_reset_leaves_dev_database_row_untouched(
    test_database_url: str,
    reset_test_db: Callable[[], Coroutine[Any, Any, None]],
) -> None:
    assert test_database_url != settings.database_url

    async def scenario() -> None:
        engine = create_async_engine(settings.database_url, poolclass=NullPool)
        slug = f"dev-survivor-{uuid.uuid4().hex}"
        query = text("SELECT count(*) FROM publications WHERE slug = :slug")
        try:
            async with engine.begin() as connection:
                await connection.execute(
                    text(
                        "INSERT INTO publications (slug, name, feed_url, homepage) "
                        "VALUES (:slug, 'Dev survivor', 'https://dev.test/feed', "
                        "'https://dev.test')"
                    ),
                    {"slug": slug},
                )

            async with engine.connect() as connection:
                before = cast(int, await connection.scalar(query, {"slug": slug}))
            assert before == 1

            await reset_test_db()

            async with engine.connect() as connection:
                after = cast(int, await connection.scalar(query, {"slug": slug}))
            assert after == 1
        finally:
            async with engine.begin() as connection:
                await connection.execute(
                    text("DELETE FROM publications WHERE slug = :slug"), {"slug": slug}
                )
            await engine.dispose()

    asyncio.run(scenario())


@pytest.mark.integration
def test_creates_and_migrates_missing_database() -> None:
    scratch_url = scratch_database_url(settings.database_url, "scratch")
    name = database_name(scratch_url)

    async def create() -> None:
        assert await database_exists(settings.database_url, name) is False
        await create_test_database(settings.database_url, scratch_url)
        assert await database_exists(settings.database_url, name) is True

    async def inspect() -> tuple[str, set[str]]:
        engine = create_async_engine(scratch_url, poolclass=NullPool)
        try:
            async with engine.connect() as connection:
                version = cast(
                    str,
                    await connection.scalar(text("SELECT version_num FROM alembic_version")),
                )
                result = await connection.execute(
                    text(
                        "SELECT table_name FROM information_schema.tables "
                        "WHERE table_schema = 'public'"
                    )
                )
                tables = {cast(str, row[0]) for row in result.all()}
            return version, tables
        finally:
            await engine.dispose()

    async def cleanup() -> None:
        await drop_database(settings.database_url, scratch_url)
        assert await database_exists(settings.database_url, name) is False

    asyncio.run(create())
    try:
        migrate_test_database(scratch_url)
        version, tables = asyncio.run(inspect())
        config = Config(str(ALEMBIC_INI))
        config.set_main_option(
            "script_location", str(ALEMBIC_INI.parent / "living_kb" / "migrations")
        )
        assert version == ScriptDirectory.from_config(config).get_current_head()
        assert {"publications", "articles", "passages", "jobs", "costs"} <= tables
    finally:
        asyncio.run(cleanup())


@pytest.mark.integration
def test_api_requests_read_test_database(
    clean_db: None,
    test_database_url: str,
) -> None:
    dev_prefix = f"api-dev-{uuid.uuid4().hex[:8]}"

    async def scenario() -> None:
        dev_engine = create_async_engine(settings.database_url, poolclass=NullPool)
        try:
            test_counts = await seed_articles(test_database_url, "api-test", 2)
            dev_counts = await seed_articles(settings.database_url, dev_prefix, 3)
            assert dev_counts[0] > test_counts[0]

            assert get_session not in app.dependency_overrides
            app.dependency_overrides[get_probe] = lambda: OkProbe()
            try:
                with TestClient(app) as client:
                    response = client.get("/health")
            finally:
                app.dependency_overrides.pop(get_probe, None)

            assert response.status_code == 200
            body = response.json()
            assert (body["articles"], body["passages"]) == test_counts
            assert (body["articles"], body["passages"]) != dev_counts
        finally:
            async with dev_engine.begin() as connection:
                await connection.execute(
                    text("DELETE FROM articles WHERE canonical_id LIKE :prefix"),
                    {"prefix": f"{dev_prefix}-%"},
                )
            await dev_engine.dispose()

    asyncio.run(scenario())
