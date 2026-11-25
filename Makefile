STACK = bash infra/dev-stack.sh

.PHONY: start stop reset stack-ports stack-env lint test check

start:
	$(STACK) start

stop:
	$(STACK) stop

reset:
	$(STACK) reset

stack-ports:
	$(STACK) ports

stack-env:
	$(STACK) env

lint:
	uv run ruff check .
	uv run ruff format --check .
	uv run mypy

test:
	$(STACK) test

check: lint test
