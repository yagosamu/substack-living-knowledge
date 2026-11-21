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

`make start` brings up Postgres, Adminer and the Inngest dev server, applies migrations and runs the API with reload on port 8000. Use `make stop` for the containers, `make reset` to drop the database volume, and `make check` for lint, formatting, types and tests.
