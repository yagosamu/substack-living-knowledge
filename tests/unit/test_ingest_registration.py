from typing import Any

from living_kb.config import Settings, settings
from living_kb.inngest_app import functions
from living_kb.sources.publications import load_publications


def _config(local_id: str) -> dict[str, Any]:
    (fn,) = [fn for fn in functions if fn.local_id == local_id]
    config: dict[str, Any] = fn.get_config("http://app/api/inngest").main.model_dump(
        exclude_none=True
    )
    return config


def test_ingest_article_is_registered_on_article_discovered_with_retries() -> None:
    config = _config("ingest-article")

    assert config["triggers"] == [{"event": "kb/article.discovered"}]
    (step,) = config["steps"].values()
    assert step["retries"] == {"attempts": settings.step_retries}


def test_ingest_article_limits_concurrency_per_publication() -> None:
    (limit,) = _config("ingest-article")["concurrency"]

    assert limit["key"] == "event.data.publication_id"
    assert limit["limit"] == settings.ingest_concurrency


def test_the_default_publications_registry_path_points_at_the_shipped_registry() -> None:
    # The default must resolve from the repo root without any .env override.
    default_path = Settings(_env_file=None).publications_registry_path

    slugs = [publication.slug for publication in load_publications(default_path)]

    assert slugs == ["decoding-ai", "hands-on-ai-engineering", "the-neural-maze"]
