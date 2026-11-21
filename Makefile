COMPOSE = docker compose --project-directory . -f infra/docker-compose.yml
API_PORT ?= 8000

.PHONY: start stop reset lint test check

start:
	$(COMPOSE) up -d --wait
	uv run alembic upgrade head
	uv run uvicorn living_kb.api:app --reload --host 0.0.0.0 --port $(API_PORT)

stop:
	$(COMPOSE) down

reset:
	$(COMPOSE) down -v --remove-orphans

lint:
	uv run ruff check .
	uv run ruff format --check .
	uv run mypy

test:
	uv run pytest

check: lint test
