"""Create the initial five-table schema.

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-11-21
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0001_initial_schema"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.execute(
        """
        CREATE TABLE publications (
            id BIGSERIAL PRIMARY KEY,
            slug TEXT NOT NULL,
            name TEXT NOT NULL,
            feed_url TEXT NOT NULL,
            homepage TEXT NOT NULL,
            active BOOLEAN NOT NULL DEFAULT TRUE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_publications_slug UNIQUE (slug)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE articles (
            id BIGSERIAL PRIMARY KEY,
            canonical_id TEXT NOT NULL,
            publication_id BIGINT REFERENCES publications (id),
            url TEXT NOT NULL,
            title TEXT,
            author TEXT,
            published_at TIMESTAMPTZ,
            content_hash TEXT,
            text TEXT,
            status TEXT NOT NULL DEFAULT 'discovered',
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_articles_canonical_id UNIQUE (canonical_id)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE passages (
            id BIGSERIAL PRIMARY KEY,
            article_id BIGINT NOT NULL REFERENCES articles (id) ON DELETE CASCADE,
            chunk_index INTEGER NOT NULL,
            text TEXT NOT NULL,
            embedding vector(1536) NOT NULL,
            search_vector tsvector GENERATED ALWAYS AS (
                to_tsvector('english', "text")
            ) STORED,
            CONSTRAINT uq_passages_article_chunk UNIQUE (article_id, chunk_index)
        )
        """
    )
    op.execute(
        """
        CREATE INDEX ix_passages_search_vector_gin
        ON passages USING gin (search_vector)
        """
    )
    op.execute(
        """
        CREATE INDEX ix_passages_embedding_hnsw
        ON passages USING hnsw (embedding vector_cosine_ops)
        """
    )
    op.execute(
        """
        CREATE TABLE jobs (
            event_id TEXT PRIMARY KEY,
            kind TEXT NOT NULL,
            status TEXT NOT NULL,
            total INTEGER NOT NULL DEFAULT 0,
            done INTEGER NOT NULL DEFAULT 0,
            publication_id BIGINT REFERENCES publications (id),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        CREATE TABLE costs (
            id BIGSERIAL PRIMARY KEY,
            article_id BIGINT REFERENCES articles (id) ON DELETE CASCADE,
            model TEXT NOT NULL,
            input_tokens INTEGER NOT NULL DEFAULT 0,
            output_tokens INTEGER NOT NULL DEFAULT 0,
            estimated_usd NUMERIC(12, 6) NOT NULL DEFAULT 0,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS costs")
    op.execute("DROP TABLE IF EXISTS jobs")
    op.execute("DROP TABLE IF EXISTS passages")
    op.execute("DROP TABLE IF EXISTS articles")
    op.execute("DROP TABLE IF EXISTS publications")
