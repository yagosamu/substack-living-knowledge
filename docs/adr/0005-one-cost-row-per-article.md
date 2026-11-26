# 0005 — One cost row per article

- **Status:** Accepted
- **Date:** 2026-11-26

## Context

Ingestion journals the `embed` step's output in Inngest (ADR 0003), and `persist` writes the
usage as a cost row. Durable execution is at-least-once: two different events can deliver the
same article, and a single run can replay `persist` after its commit was lost from the journal.
A blind `INSERT` would create one row per attempt, so the operator could not tell the article's
cost from retry noise.

The schema already has a nullable `costs.article_id`, and other producers may later write cost
rows without an article. The article-linked rows therefore need uniqueness that leaves those
unlinked rows alone.

## Decision

There is at most one article-linked cost row per article. A partial unique index on
`costs(article_id)` enforces it, and `persist` inserts with `ON CONFLICT ... DO NOTHING` inside
the same transaction as the article and its passages. The row records the model, prompt tokens
and estimated USD of the first successful embedding for that article.

A **replay** of a completed step returns the journaled `persist` result without writing again.
A **re-delivered event** is a fresh run and re-executes `persist`, but the conflict clause keeps
the single row. Re-ingesting changed content does not replace that row; accumulating a per-call
ledger would need a different key and is out of scope for #8.

The usage estimate is computed where the provider response is available: the embedder sums
`prompt_tokens` across every batch and applies `settings.embedding_price_per_million_tokens`,
rounded to six decimals. The pipeline copies that small record verbatim instead of recomputing
a price from configuration during replay.

## Consequences

- Cost rows are stable under retries and re-deliveries, and a failed cost insert rolls back the
  article and its passages.
- A re-ingest with genuinely different content keeps the original estimate. A future ticket can
  change the key to `(article, content_hash)` if a historical ledger is wanted.
- Unlinked cost rows remain unrestricted because the index is partial.
- The pricing formula lives in the embedder and is covered by unit tests; the wiring test proves
  the production adapter passes the configured price through.
