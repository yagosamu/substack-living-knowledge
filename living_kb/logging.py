"""JSON-line logging with an explicit durable-run context."""

from __future__ import annotations

import contextlib
import json
import logging
import sys
from contextvars import ContextVar, Token
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any
from urllib.parse import unquote, urlsplit

from living_kb.config import settings

_CONTEXT_FIELDS = ("run_id", "function", "step")
_RESERVED_FIELDS = frozenset(
    {"timestamp", "level", "logger", "message", "exception", "stack", "color_message"}
) | frozenset(_CONTEXT_FIELDS)
_STANDARD_RECORD_FIELDS = frozenset(logging.LogRecord("", 0, "", 0, "", (), None).__dict__)
_REDACTED = "[REDACTED]"


@dataclass(frozen=True)
class LogContext:
    """The fields a log line gains while a durable run or step is active."""

    run_id: str | None = None
    function: str | None = None
    step: str | None = None

    def as_fields(self) -> dict[str, str]:
        return {
            name: value for name in _CONTEXT_FIELDS if (value := getattr(self, name)) is not None
        }


_current: ContextVar[LogContext | None] = ContextVar("living_kb_log_context", default=None)


def _context() -> LogContext:
    return _current.get() or LogContext()


def bind_run(*, run_id: str | None = None, function: str | None = None) -> Token[LogContext | None]:
    """Correlate every log line in this execution context with one durable run."""
    return _current.set(LogContext(run_id=run_id, function=function))


def bind_step(name: str) -> Token[LogContext | None]:
    """Add the current step name without losing the enclosing run's fields."""
    return _current.set(replace(_context(), step=name))


def reset(token: Token[LogContext | None]) -> None:
    """Restore the context that was active before a run or step began."""
    _current.reset(token)


def _secret_values() -> tuple[str, ...]:
    password: str | None = None
    with contextlib.suppress(ValueError):
        password = urlsplit(settings.database_url).password
    values = (
        settings.openai_api_key,
        settings.inngest_event_key,
        settings.inngest_signing_key,
        settings.postgres_password,
        settings.database_url,
        unquote(password) if password else None,
    )
    return tuple(value for value in values if value)


def _redact(line: str) -> str:
    for secret in _secret_values():
        line = line.replace(json.dumps(secret, ensure_ascii=False)[1:-1], _REDACTED)
        line = line.replace(secret, _REDACTED)
    return line


class JsonFormatter(logging.Formatter):
    """Render one log record as one JSON object on one line."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        extras = {
            name: value
            for name, value in record.__dict__.items()
            if name not in _STANDARD_RECORD_FIELDS and name not in _RESERVED_FIELDS
        }
        payload.update(extras)
        payload.update(_context().as_fields())
        if record.exc_info is not None:
            payload["exception"] = record.exc_text or self.formatException(record.exc_info)
        if record.stack_info:
            payload["stack"] = record.stack_info
        return _redact(json.dumps(payload, ensure_ascii=False, default=str))


def configure_logging() -> None:
    """Send application and ASGI logs through the JSON formatter exactly once."""
    formatter = JsonFormatter()
    root = logging.getLogger()
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    for name in ("uvicorn", "uvicorn.access", "uvicorn.error", "inngest", "living_kb"):
        named = logging.getLogger(name)
        named.handlers.clear()
        named.propagate = True
