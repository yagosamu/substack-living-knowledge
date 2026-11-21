from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


@pytest.mark.integration
def test_initial_schema(session_factory: async_sessionmaker[AsyncSession]) -> None:
    async def inspect() -> None:
        async with session_factory() as session:
            tables = {
                row[0]
                for row in (
                    await session.execute(
                        text(
                            "SELECT table_name FROM information_schema.tables "
                            "WHERE table_schema = 'public'"
                        )
                    )
                ).all()
            }
            assert {
                "publications",
                "articles",
                "passages",
                "jobs",
                "costs",
            } <= tables

            assert (
                await session.scalar(text("SELECT 1 FROM pg_extension WHERE extname = 'vector'"))
                == 1
            )

            columns = {
                row[0]: (row[1], row[2])
                for row in (
                    await session.execute(
                        text(
                            "SELECT a.attname, format_type(a.atttypid, a.atttypmod), "
                            "a.attgenerated FROM pg_attribute a "
                            "JOIN pg_class c ON c.oid = a.attrelid "
                            "JOIN pg_namespace n ON n.oid = c.relnamespace "
                            "WHERE n.nspname = 'public' AND c.relname = 'passages' "
                            "AND a.attnum > 0 AND NOT a.attisdropped"
                        )
                    )
                ).all()
            }
            assert columns["embedding"][0] == "vector(1536)"
            assert columns["search_vector"][0] == "tsvector"
            assert columns["search_vector"][1] in {"s", b"s"}

            indexes = {
                row[0]: row[1]
                for row in (
                    await session.execute(
                        text(
                            "SELECT indexname, indexdef FROM pg_indexes WHERE schemaname = 'public'"
                        )
                    )
                ).all()
            }
            assert any(
                "USING gin" in definition and "search_vector" in definition
                for definition in indexes.values()
            )
            assert any(
                "USING hnsw" in definition and "vector_cosine_ops" in definition
                for definition in indexes.values()
            )
            assert "uq_passages_article_chunk" in indexes

            unique_constraints = {
                row[0]
                for row in (
                    await session.execute(
                        text("SELECT conname FROM pg_constraint WHERE contype = 'u'")
                    )
                ).all()
            }
            assert {
                "uq_publications_slug",
                "uq_articles_canonical_id",
                "uq_passages_article_chunk",
            } <= unique_constraints

    asyncio.run(inspect())
