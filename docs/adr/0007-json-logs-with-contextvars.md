# 0007 — JSON logs correlated through context variables

- **Status:** Accepted
- **Date:** 2026-11-26

## Context

One ingested article produces logs from HTTP, parsing, embeddings, database writes and the event sender, often across several replay attempts. Plain text lines cannot be correlated with the Inngest run, step journal or cost rows. A log field must be present for every line inside a run and absent outside one, including uvicorn's startup and access lines. Pipelines must not be changed to carry IDs: they are plain functions written against the step-runner seam (ADR 0003).

## Decision

Application logs are JSON lines rendered by a standard-library `logging.Formatter` subclass. A `contextvars.ContextVar` holds an immutable snapshot with optional `run_id`, `function` and `step` fields; the formatter includes only the fields that are set.

`run_durable` binds `run_id` and the function name once, around the whole pipeline. Both `JournalRunner.run` and `InngestRunner.run` add the step name around the step call, and reset it afterwards. `JournalRunner.execute` can bind a run for tests. Registered functions pass their own name explicitly because the Inngest SDK context exposes `run_id` but not a function ID. Importing the API installs the formatter on the root logger and routes uvicorn, Inngest and `living_kb` loggers through it.

## Consequences

- Any logger used inside a run is correlated without changes to pipelines or their ports.
- Async tasks started inside a run inherit its context; a task that outlives its step keeps the step name it was created with.
- The steps and the entry point must both be exercised by tests: binding only in the fake would hide a production gap.
- No new logging dependency; structured extras and exceptions are flattened into the same JSON object.
