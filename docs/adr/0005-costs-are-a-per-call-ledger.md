# 0005 — Costs are a per-call ledger

- **Status:** Accepted
- **Date:** 2026-11-28

## Context

Ingestion journals the `embed` step's output in Inngest (ADR 0003), and `persist` writes the usage as a cost row. The table's purpose is to tell the operator what ingestion **really costs**, not merely what an article looked like the first time it was seen. A durable run can replay a completed step, and a separate event can deliver the same article again; those two cases are different.

The first design keyed the row by `article_id` with `ON CONFLICT DO NOTHING`. A probe during the PR #35 review sent one article through three fresh runs — two identical, one with changed HTML. The fake provider was paid three times (3,000 tokens), but the table held one row (1,000 tokens). Two thirds of the spend was invisible, and a changed article kept a stale estimate. The original issue criterion, "re-running the event after success writes none", was corrected after the review because it encoded that wrong purpose.

## Decision

Every **paid embedding call** gets its own cost row. The `embed` step generates a `call_id` (`uuid4().hex`) inside the step, so the identifier is journaled with the vectors and usage as required by ADR 0003 rule 2. `persist` inserts the row with that `call_id`, the article id, the model, prompt tokens, output tokens and estimated USD, in the same transaction as the article and its passages.

The unique key is the call, not the article. A unique index on `costs(call_id)` (for non-null values) plus `ON CONFLICT (call_id) ... DO NOTHING` makes writes idempotent per paid call:

- A **journal replay** returns the journaled `embed` result — including the same `call_id` — and does not pay the provider again. If `persist` committed but its journal entry was lost, the retry executes `persist` a second time with the same `call_id`; the conflict clause leaves one row.
- A **fresh re-delivery** is a new run with a new journal. `embed` runs again and pays again, so it generates a new `call_id` and writes a second row, even when the article content is unchanged.

The old partial unique index on `costs(article_id)` is removed. `article_id` stays nullable for cost rows from other producers; those rows are unrestricted by the call-id index when `call_id` is null.

The USD estimate is still computed where the provider response is available: the embedder sums `prompt_tokens` across every batch and applies `settings.embedding_price_per_million_tokens`, rounded to six decimals. The pipeline copies that record verbatim instead of recomputing a price during replay.

## Consequences

- The sum of `costs.input_tokens` and `costs.estimated_usd` reconciles with the number of paid calls, including re-deliveries and changed content.
- Retry noise is still excluded: re-running `persist` for a call already recorded cannot add a second row, and a failed cost insert rolls back the article and its passages.
- A re-delivery adds a row even when it produces no new article version. Operators can see that spend, and a future report can group by article or canonical id.
- Call ids are generated inside a step, so a replay can never invent a new one. This is covered by the `persist`-after-commit test, which would fail if randomness escaped the step.
- Unlinked cost rows without a `call_id` remain unrestricted, because the unique index ignores nulls.
- The pricing formula lives in the embedder and is covered by a six-decimal test; wiring tests prove the production adapter passes a non-default model and the configured price through.
- **Accepted gap: paid calls that never reach `persist` are not recorded.** Cost rows are written in `persist`, so two narrow cases book less than was paid. In the first, the provider is paid but the process dies before the engine journals the `embed` result: the retry pays again under a new `call_id`, and only that second call is recorded. In the second, `persist` fails permanently after a paid `embed`, and nothing is recorded. Closing the gap would mean writing the cost row inside the `embed` step itself, and linking it to the article later. That gives `embed` a database side effect and leaves cost rows briefly unlinked. Both cases need a failure inside a millisecond-scale window, and each costs cents, so the gap was accepted deliberately (decision of 2026-11-28, after the PR #35 review) rather than fixed.
