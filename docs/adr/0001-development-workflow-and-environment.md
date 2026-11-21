# 0001 — Development workflow and environment

- **Status:** Accepted
- **Date:** 2026-11-21

## Context

This system is built from scratch, one task at a time. Most implementation is delegated to coding agents. The human writes the specs, the tests behind each contract, and the conceptual core, and makes every merge decision. That setup needs a mechanical quality gate that holds no matter which agent touched the code. It also needs an environment that matches the Linux production target.

## Decision

- **Environment:** WSL2 (Ubuntu) on the Linux filesystem, with Docker Desktop's WSL integration for infrastructure containers. Python 3.12, managed by `uv`.
- **Unit of work:** one task = one spec in `docs/tasks/` = one branch = one PR.
  - The spec contains context, contract, tests, out-of-scope and an acceptance checklist.
  - The human commits the contract tests before the task is delegated.
- **Roles:**
  - Implementer agents make the contract tests pass.
  - A reviewer agent from a different model family checks the acceptance checklist and runs mutation checks against the tests (`docs/review.md`).
  - The human merges.
- **Gate:**
  - GitHub Actions runs ruff, mypy (strict) and pytest, with a Postgres/pgvector service container, on every PR.
  - `main` is protected: changes arrive through a PR with green CI.
- **Testing:**
  - Integration tests run against real Postgres/pgvector.
  - Paid model APIs (OpenAI, Anthropic) are always faked in tests.
- **Observability:**
  - Structured JSON logs carry the Inngest `run_id`.
  - LLM tracing (Langfuse) is re-evaluated when LLM calls and evals arrive.
- **Language:** English for code and project docs. `docs/learning/` holds personal study notes in Portuguese.

## Consequences

- Specs must be precise enough for a weaker model to implement correctly. Writing them is deliberate practice in specification.
- CI catches lint, type and test failures before review. The reviewer spends its effort on spec conformance, test strength and design.
- Branch protection removes any reliance on agents choosing not to push to `main`.
- Real-database tests are slower than mocks. They test the SQL, indexes and idempotency that the system's value depends on.
- Faked model APIs keep test runs free and deterministic. Real model behaviour is covered separately by evals.
