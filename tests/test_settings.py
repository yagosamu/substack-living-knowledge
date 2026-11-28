from pathlib import Path

import pytest

from living_kb.config import Settings


def test_spec_defaults(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    for field in Settings.model_fields:
        monkeypatch.delenv(field.upper(), raising=False)
    monkeypatch.chdir(tmp_path)
    config = Settings()

    assert config.chunk_size == 1000
    assert config.chunk_overlap == 150
    assert config.embedding_model == "text-embedding-3-small"
    assert config.embedding_dimensions == 1536
    assert config.embedding_batch_size == 96
    assert config.embedding_price_per_million_tokens == 0.02
    assert config.discovery_limit == 5
    assert config.request_timeout_seconds == 15
    assert config.max_redirects == 5
    assert config.max_article_bytes == 5_242_880
    assert config.user_agent.startswith("living-kb/")
    assert config.ingest_concurrency == 3
    assert config.step_retries == 3
    assert (
        config.database_url
        == "postgresql+asyncpg://living_kb:living_kb_pw@localhost:5433/living_kb"
    )
    assert config.inngest_base_url == "http://localhost:8288"
    assert config.inngest_dev is True
    assert config.inngest_event_key is None
    assert config.inngest_signing_key is None
    assert config.api_port == 8000
    assert config.publications_registry_path == "publications.yaml"
    assert config.health_check_timeout_seconds == 2.0
