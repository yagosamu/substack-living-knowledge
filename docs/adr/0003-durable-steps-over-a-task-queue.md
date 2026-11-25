# 0003 — Durable steps over a task queue

- **Status:** Accepted
- **Date:** 2026-11-21

## Context

Ingesting one article is a sequence of steps with different costs and failure modes:

`check-allowlist → fetch-html → parse → chunk → embed → persist → emit`

(Rule 4 later merged `fetch-html` and `parse` into one step, `fetch-article`.)

- `fetch-html` hits a publisher's site.
- `embed` is a paid OpenAI call with rate limits.
- `persist` writes to Postgres.
- `emit` notifies the rest of the system.

Discovery fans out one run per article, and a backfill means thousands of runs. The process running them **will** die mid-run at some point: a deploy, an OOM, a crash, a reboot.

Written as one plain function, a crash after `embed` and during `persist` means:

- **Repeated work.** A re-run repeats every step, so the article is fetched again (an extra hit on the publisher) and embedded again (paid twice). Across a backfill this multiplies.
- **Partial state.** Without a transaction, the article row can exist with only some of its passages. Nothing reports an error, and search silently sees a fraction of the article.
- **The dual-write gap.** `persist` (Postgres) and `emit` (the event bus) are two systems no single transaction covers. Dying between them leaves an article stored but never announced, or announced but never stored.
- **No record of progress.** Nothing says which articles finished, which are partial, and at which step a failure happened.

Fixing this by hand means writing a progress table and checking it before every step. That grows into a home-made workflow engine:

- re-triggering failed runs;
- retries with backoff, telling permanent failures (robots.txt disallow) from transient ones (a 429);
- locking, so two workers never take the same article;
- per-publication concurrency limits;
- a UI to see what failed and where;
- cleanup of old progress.

None of that is the product.

The options considered:

| | Unit of retry | Who records progress | Retries, backoff, concurrency, visibility |
|---|---|---|---|
| Cron + plain script | the whole script | nobody | us |
| Task queue (Celery, RQ) | the whole task | nobody. Splitting into chained tasks moves intermediate results through the broker, which is the progress table again | partly the queue, partly us |
| **Durable execution (Inngest)** | **each step** | **the engine, in a step journal** | the engine |

## Decision

Pipelines run as **durable functions on Inngest**, each side effect inside a named step.

- **Replay with memoization.** On a retry the engine re-invokes the function from the top. Each `step.run(name, fn)` first looks the name up in the run's journal. A completed step returns its recorded result without executing, and the first missing step executes. A failure in `embed` resumes at `embed`, so the article is never re-fetched and paid embeddings are never re-bought.
- **Pipeline code depends on a step-runner seam, not on Inngest.** Pipelines are plain async functions that receive a runner with one operation, `run(name, fn)`, plus their ports (HTTP, embedder, event sender, database). A thin adapter maps the runner onto Inngest's `ctx.step.run` and registers triggers, concurrency and retries. In tests a journaling fake runner can fail a named step and replay. Permanent failures raise a dedicated `PermanentError`. Registered functions enter through `run_durable(ctx, pipeline)`, which translates a `PermanentError` raised **anywhere** in the function, inside a step or not, to Inngest's non-retriable error. *Amended after the PR #28 review: translation used to live only inside steps, so a malformed event, rejected before the first step, was retried three times in production.*

## Rules this imposes

1. **Steps are idempotent.** Durable execution is *at-least-once* per step, not exactly-once. A step can complete its side effect, then die before the engine records it, so it runs again. Writes are upserts keyed by natural identity (`canonical_id`, `(article, chunk_index)`), never blind inserts. `persist` writes the article and its passages in **one transaction**.
2. **Code outside steps is deterministic.** Everything between steps re-executes on every replay, so it must take the same decisions every time. Anything that can differ between runs goes inside a step, so its result is journaled: the clock, randomness, UUIDs, network, database or file reads. Otherwise a replay can take a different branch, run steps the first attempt never ran, and mix two attempts into one result.
3. **Step names are stable and unique.** They are the journal's keys. Renaming a step makes in-flight runs treat it as new. Uniqueness is enforced by our runners, not by the engine. The Inngest SDK accepts a reused id and silently re-keys it by call order (`embed`, `embed:1`), so a journal key would depend on the order of calls. If that order changes between attempts, which is the determinism hazard of rule 2, a replay would hand a step another step's result. Both `JournalRunner` and `InngestRunner` raise on a name reused within one execution, so the fake and production behave the same.
4. **Step outputs are small and JSON-serializable.** The journal stores and returns them verbatim, and the engine caps their size. The rule is enforced in code: both runners reject an output larger than `max_step_output_bytes` (default 3 MB) as a permanent failure.

   The rule shapes step boundaries. Journal what is expensive to recompute, and nothing that is cheap to recompute.
   - **Embeddings are journaled.** A 60-passage article is about 1.3 MB. That is the price of never paying for them twice.
   - **Raw HTML is not.** Fetching and parsing share one step, `fetch-article`, which journals only the extracted article. A parse failure re-fetches the page, which costs one polite request, not money. Parsing is deterministic and cheap.

   *Amended after the PR #28 review: a separate `fetch-html` step journaled pages of up to 5 MB.*
5. **The emit step closes the dual-write gap by retry.** If a run dies after `persist` commits and before `emit`, the retry replays `persist` from the journal and re-attempts `emit`. Consumers of `kb/article.ingested` must therefore tolerate duplicates, which follows from rule 1.

## Consequences

- **Gains.** Paid and polite-sensitive work runs once per success. Each article run is independent, so one bad article cannot block the others. Retries, backoff, per-publication concurrency and a run/step UI come from the engine and are not ours to build.
- **One more component:** the Inngest dev server locally, and a hosted or self-run Inngest in production.
- **A new class of bug:** non-determinism outside steps, which ordinary unit tests do not catch.
- **Mitigated by the seam:**
  - Vendor dependency shrinks to one adapter. Swapping engines (e.g. Temporal) or going in-process changes only that adapter.
  - Resume and replay are tested in CI without the engine. The journaling fake proves that a failure at step *k* re-executes the earlier steps zero times and step *k* exactly once.
  - It rejects non-serializable and over-budget step outputs at test time.
- **Not covered by automated tests:** behaviour against the real engine. That is verified by the manual kill-and-resume demo (#13).
