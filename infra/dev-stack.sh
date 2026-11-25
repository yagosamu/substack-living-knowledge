#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root"
env_file="$root/.dev-stack.env"
uv run python -m living_kb.dev_stack --write-env "$env_file"
set -a
. "$env_file"
set +a

compose=(docker compose --project-directory "$root" -f "$root/infra/docker-compose.yml" -p "$COMPOSE_PROJECT_NAME")

case "${1:-}" in
  start)
    "${compose[@]}" up -d --wait
    uv run alembic upgrade head
    exec uv run uvicorn living_kb.api:app --reload --host 0.0.0.0 --port "$API_PORT"
    ;;
  stop)
    "${compose[@]}" down
    ;;
  reset)
    "${compose[@]}" down -v --remove-orphans
    ;;
  ports)
    tr '\n' ' ' < "$env_file"
    printf '\n'
    ;;
  env)
    cat "$env_file"
    ;;
  test)
    exec uv run pytest
    ;;
  *)
    printf 'usage: infra/dev-stack.sh start|stop|reset|ports|env|test\n' >&2
    exit 2
    ;;
esac
