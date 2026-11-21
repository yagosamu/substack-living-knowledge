from living_kb.config import Settings


def test_spec_defaults() -> None:
    settings = Settings.model_validate({})

    assert settings.chunk_size == 1000
    assert settings.chunk_overlap == 150
    assert settings.embedding_model == "text-embedding-3-small"
    assert settings.embedding_dimensions == 1536
    assert settings.embedding_batch_size == 96
    assert settings.embedding_price_per_million_tokens == 0.02
    assert settings.discovery_limit == 5
    assert settings.request_timeout_seconds == 15
    assert settings.max_article_bytes == 5_242_880
    assert settings.user_agent.startswith("living-kb/")
    assert settings.ingest_concurrency == 3
    assert settings.step_retries == 3
    assert (
        settings.database_url
        == "postgresql+asyncpg://living_kb:living_kb_pw@localhost:5433/living_kb"
    )
    assert settings.inngest_base_url == "http://localhost:8288"
    assert settings.inngest_dev is True
    assert settings.api_port == 8000
    assert settings.publications_registry_path == "config/publications.yaml"
