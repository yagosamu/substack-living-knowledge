from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable, Coroutine
from typing import Any, cast

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from living_kb.config import settings


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
