from __future__ import annotations

import asyncio
from typing import cast

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


async def column_names(session: AsyncSession, table: str) -> set[str]:
    result = await session.execute(
        text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = :table"
        ),
        {"table": table},
    )
    return {cast(str, row[0]) for row in result.all()}


@pytest.mark.integration
def test_initial_schema(session_factory: async_sessionmaker[AsyncSession]) -> None:
    async def inspect() -> None:
        async with session_factory() as session:
            assert await column_names(session, "publications") == {
                "id",
                "slug",
                "name",
                "feed_url",
                "homepage",
                "active",
                "created_at",
            }
            assert await column_names(session, "articles") == {
                "id",
                "canonical_id",
                "publication_id",
                "url",
                "title",
                "author",
                "published_at",
                "content_hash",
                "text",
                "status",
                "created_at",
                "updated_at",
            }
            assert await column_names(session, "passages") == {
                "id",
                "article_id",
                "chunk_index",
                "text",
                "embedding",
                "search_vector",
            }
            assert await column_names(session, "jobs") == {
                "event_id",
                "kind",
                "status",
                "total",
                "done",
                "publication_id",
                "created_at",
            }
            assert await column_names(session, "costs") == {
                "id",
                "call_id",
                "article_id",
                "model",
                "input_tokens",
                "output_tokens",
                "estimated_usd",
                "created_at",
            }

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

            generation_expression = cast(
                str | None,
                await session.scalar(
                    text(
                        "SELECT generation_expression FROM information_schema.columns "
                        "WHERE table_schema = 'public' AND table_name = 'passages' "
                        "AND column_name = 'search_vector'"
                    )
                ),
            )
            assert generation_expression is not None
            assert "to_tsvector" in generation_expression
            assert "'english'" in generation_expression

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
            assert "uq_costs_call_id" in indexes
            assert "uq_costs_article_id" not in indexes

            unique_definition = cast(
                str | None,
                await session.scalar(
                    text(
                        "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                        "WHERE conname = 'uq_passages_article_chunk'"
                    )
                ),
            )
            assert unique_definition == "UNIQUE (article_id, chunk_index)"

    asyncio.run(inspect())
