# AGENTS.md

`living-kb` turns AI newsletters into a durable, queryable knowledge base. It runs RSS discovery, then ingestion (fetch, parse, chunk, embed), then hybrid retrieval. Later layers add attributed claims in a graph, an MCP server, and evals. The Python package is `living_kb`.

Every change ships as one **task**. A task is a GitHub issue labelled `ready-for-agent`, and it gets its own branch and one PR. The issue is the contract. When it is ambiguous, ask in a comment on the issue before you guess.

## Implementing a task

1. Read the task with `gh issue view <n> --comments`. Also read its parent spec issue and every ADR it links (`docs/adr/`).
2. Work on branch `task/<n>-<slug>` and keep the diff inside the task's scope. Any extra change goes in the PR description under **Follow-ups**.
3. Make the task's contract tests pass **without editing them**. They are the contract and the human wrote them. If a test looks wrong, say so in the PR and leave it as is. Add any extra tests you need beside them.
4. Run `uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest`. The task is done when all four are green locally.
5. Open the PR against `main`. Title it `task #<n>: <summary>`, and start the body with `Closes #<n>`. Then list every acceptance-checklist item from the issue and how it is met.

Merges into `main` happen through a reviewed PR with green CI. The human merges.

## Reviewing a task

Follow [`docs/review.md`](docs/review.md).

## Conventions

- **Language:** code, comments, commits, specs and ADRs are in English. `docs/learning/` is the human's study notes, in Portuguese. Leave that folder untouched.
- **Config:** every tunable number lives in `living_kb/config.py` (pydantic-settings, read from `.env`). Code reads `settings.<name>` rather than inline literals.
- **Logging:** JSON lines. Each line emitted inside an Inngest function carries that run's `run_id`, so one ID links logs, steps and cost rows.
- **Tests:**
  - Unit tests are pure.
  - Integration tests (`@pytest.mark.integration`) run against the real Postgres/pgvector from `infra/docker-compose.yml` locally, and against the service container in CI.
  - OpenAI and Anthropic are always faked in tests, using recorded responses or stub clients. A test run costs zero API spend.
- **Secrets** live only in `.env`, which git ignores. `.env.example` lists every variable with a safe placeholder.
- **Decisions:** a choice that a future reader would ask "why?" about gets an ADR in `docs/adr/NNNN-<slug>.md` (Context, Decision, Consequences).
