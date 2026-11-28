"""Record one cost row per paid embedding call.

Revision ID: 0003_costs_ledger
Revises: 0002_one_cost_per_article
Create Date: 2026-11-28
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0003_costs_ledger"
down_revision: str | Sequence[str] | None = "0002_one_cost_per_article"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_costs_article_id")
    op.execute("ALTER TABLE costs ADD COLUMN call_id TEXT")
    op.execute("CREATE UNIQUE INDEX uq_costs_call_id ON costs (call_id) WHERE call_id IS NOT NULL")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_costs_call_id")
    op.execute("ALTER TABLE costs DROP COLUMN IF EXISTS call_id")
    op.execute(
        "CREATE UNIQUE INDEX uq_costs_article_id ON costs (article_id) WHERE article_id IS NOT NULL"
    )
