# substack-living-knowledge

A durable, queryable knowledge base built from AI newsletters.

The system discovers articles from RSS feeds and ingests them through checkpointed, resumable pipelines (fetch → parse → chunk → embed). It stores passages in Postgres with pgvector and answers questions with hybrid retrieval and citations. It is built in layers, and the later ones add:

- attributed claims (who said what, when, with the exact quote) in a temporal knowledge graph
- an MCP server so coding agents can query the knowledge base
- an evaluation suite grounded in the system's own failures

## Status

Early construction. Progress is tracked in [GitHub Issues](https://github.com/yagosamu/substack-living-knowledge/issues), and architectural decisions live in [`docs/adr/`](docs/adr/).

## How it's built

Every change starts as a written spec with contract tests. Coding agents implement it, a reviewer agent from a different model family checks it, CI gates it, and a human merges it. See [ADR 0001](docs/adr/0001-development-workflow-and-environment.md).

## Development

Requires Python 3.12, [`uv`](https://docs.astral.sh/uv/) and Docker.

```bash
uv sync
cp .env.example .env
make start
```

`make start` brings up Postgres, Adminer and the Inngest dev server, applies migrations and runs the API with reload. The main checkout keeps ports 5433/8081/8288/8000 and the Compose project `infra`. Each linked worktree gets its own Compose project (`living-kb-<worktree>-<hash>`) and a stable, free port block; `make stack-ports` prints them. The first start in a worktree derives the block and saves it in the gitignored `.dev-stack.local.json`; `.dev-stack.env` carries the derived ports, `DATABASE_URL` and `INNGEST_BASE_URL` to the app and tests. `.env.example` keeps those variables commented out: copying it does not pin the main-checkout ports. Set `COMPOSE_PROJECT_NAME`, `POSTGRES_PORT`, `ADMINER_PORT`, `INNGEST_PORT`, `API_PORT`, `DATABASE_URL` or `INNGEST_BASE_URL` in the shell or `.env` to override any derived value. Use `make stop` for the containers, `make reset` to drop this worktree's database volume, and `make check` for lint, formatting, types and tests.

### Tests

Run the full suite with `make test` (or `uv run pytest`) while the Postgres service from `make start` is running. Integration tests use a dedicated database on the same Postgres server: the database in `DATABASE_URL` has `_test` appended (`living_kb` → `living_kb_test`). The suite creates that database when missing, applies all migrations to it before the first integration test, and truncates only it between tests. FastAPI requests in the suite use the same test database, so no test writes to development data.
