from pathlib import Path

import pytest

from living_kb.identity import canonical_id, normalize_url
from living_kb.sources.chunking import chunk_text
from living_kb.sources.publications import load_publications


def test_load_publications_reads_optional_notes(tmp_path: Path) -> None:
    path = tmp_path / "publications.yaml"
    path.write_text(
        "publications:\n"
        "  - slug: example\n"
        "    name: Example Newsletter\n"
        "    feed_url: https://example.substack.com/feed\n"
        "    homepage: https://example.substack.com\n"
        "    notes: recorded fixture\n"
    )

    (publication,) = load_publications(path)

    assert publication.notes == "recorded fixture"


def test_load_publications_rejects_a_registry_without_publications(tmp_path: Path) -> None:
    path = tmp_path / "publications.yaml"
    path.write_text("something_else: true\n")

    with pytest.raises(ValueError, match="publications"):
        load_publications(path)


def test_normalize_url_rejects_unsupported_schemes() -> None:
    with pytest.raises(ValueError, match="http or https"):
        normalize_url("ftp://example.com/feed")


def test_canonical_id_accepts_encoded_post_slugs() -> None:
    assert canonical_id("example", "https://example.com/p/my%20post") == "pub:example:my%20post"


@pytest.mark.parametrize(
    ("chunk_size", "chunk_overlap"),
    [(0, 0), (-1, 0), (100, -1)],
)
def test_chunking_rejects_invalid_sizes(chunk_size: int, chunk_overlap: int) -> None:
    with pytest.raises(ValueError):
        chunk_text("some text", chunk_size=chunk_size, chunk_overlap=chunk_overlap)
