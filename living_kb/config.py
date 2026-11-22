"""Typed configuration for the whole application."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any, cast

from pydantic_settings import BaseSettings, SettingsConfigDict

_UNSET = object()


class Settings(BaseSettings):
    """Every tunable value, read from the environment or a local .env file."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    def __init__(self, *, _env_file: object = _UNSET, **values: Any) -> None:
        """Expose pydantic-settings' `_env_file` override to static type checkers."""
        if _env_file is _UNSET:
            super().__init__(**values)
        else:
            super().__init__(_env_file=cast("str | Path | None", _env_file), **values)

    chunk_size: int = 1000
    chunk_overlap: int = 150

    embedding_model: str = "text-embedding-3-small"
    embedding_dimensions: int = 1536
    embedding_batch_size: int = 96
    embedding_price_per_million_tokens: float = 0.02
    openai_api_key: str | None = None

    discovery_limit: int = 5
    request_timeout_seconds: int = 15
    max_article_bytes: int = 5_242_880
    min_article_chars: int = 100
    user_agent: str = "living-kb/0.1 (+https://github.com/yagosamu/substack-living-knowledge)"

    ingest_concurrency: int = 3
    step_retries: int = 3

    postgres_user: str = "living_kb"
    postgres_password: str = "living_kb_pw"
    postgres_db: str = "living_kb"
    postgres_port: int = 5433
    database_url: str = "postgresql+asyncpg://living_kb:living_kb_pw@localhost:5433/living_kb"

    inngest_app_id: str = "living-kb"
    inngest_base_url: str = "http://localhost:8288"
    inngest_dev: bool = True
    inngest_event_key: str | None = None
    inngest_signing_key: str | None = None
    inngest_port: int = 8288

    adminer_port: int = 8081
    api_port: int = 8000
    publications_registry_path: str = "publications.yaml"

    health_check_timeout_seconds: float = 2.0


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings instance."""
    return Settings()


settings = get_settings()
