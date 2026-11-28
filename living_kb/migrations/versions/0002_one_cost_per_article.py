"""Keep one cost row per article.

Revision ID: 0002_one_cost_per_article
Revises: 0001_initial_schema
Create Date: 2026-11-26
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0002_one_cost_per_article"
down_revision: str | Sequence[str] | None = "0001_initial_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "CREATE UNIQUE INDEX uq_costs_article_id ON costs (article_id) WHERE article_id IS NOT NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_costs_article_id")
