from pathlib import Path

import pytest

from living_kb.sources.publications import (
    NotAllowedError,
    Publication,
    assert_allowed,
    find_publication,
    load_publications,
)

REGISTRY = """
publications:
  - slug: example
    name: Example Newsletter
    feed_url: https://example.substack.com/feed
    homepage: https://example.substack.com
"""


def test_load_publications_reads_entries_from_yaml(tmp_path: Path) -> None:
    path = tmp_path / "publications.yaml"
    path.write_text(REGISTRY)

    (pub,) = load_publications(path)

    assert (pub.slug, pub.name, pub.feed_url, pub.homepage) == (
        "example",
        "Example Newsletter",
        "https://example.substack.com/feed",
        "https://example.substack.com",
    )


def test_load_publications_rejects_entry_missing_a_required_field(tmp_path: Path) -> None:
    path = tmp_path / "publications.yaml"
    path.write_text("publications:\n  - slug: example\n    name: Example Newsletter\n")

    with pytest.raises(ValueError):
        load_publications(path)


def test_load_publications_reads_optional_notes(tmp_path: Path) -> None:
    path = tmp_path / "publications.yaml"
    path.write_text(REGISTRY + "    notes: Weekly deep dives.\n")

    (pub,) = load_publications(path)

    assert pub.notes == "Weekly deep dives."


def test_load_publications_reads_optional_archive_urls(tmp_path: Path) -> None:
    path = tmp_path / "publications.yaml"
    path.write_text(REGISTRY + "    archive_urls:\n      - https://archive.example.org\n")

    (pub,) = load_publications(path)

    assert pub.archive_urls == ["https://archive.example.org"]


EXAMPLE = Publication(
    slug="example",
    name="Example Newsletter",
    feed_url="https://feeds.example.org/example",
    homepage="https://www.example.com",
    archive_urls=["https://archive.example.net"],
)


def test_find_publication_matches_article_on_homepage_host() -> None:
    assert find_publication("https://www.example.com/p/my-post", [EXAMPLE]) == EXAMPLE


def test_find_publication_matches_article_on_feed_host() -> None:
    assert find_publication("https://feeds.example.org/p/my-post", [EXAMPLE]) == EXAMPLE


def test_find_publication_matches_article_on_archive_host() -> None:
    assert find_publication("https://archive.example.net/p/my-post", [EXAMPLE]) == EXAMPLE


def test_find_publication_returns_none_for_unknown_host() -> None:
    assert find_publication("https://random-blog.example.io/p/my-post", [EXAMPLE]) is None


@pytest.mark.parametrize(
    "url",
    [
        "https://evil-www.example.com/p/my-post",
        "https://www.example.com.evil.io/p/my-post",
        "https://blog.www.example.com/p/my-post",
        "https://example.com/p/my-post",
    ],
)
def test_find_publication_rejects_lookalike_and_unlisted_hosts(url: str) -> None:
    assert find_publication(url, [EXAMPLE]) is None


def test_find_publication_ignores_host_case() -> None:
    assert find_publication("https://WWW.Example.COM/p/my-post", [EXAMPLE]) == EXAMPLE


def test_assert_allowed_returns_the_matching_publication() -> None:
    assert assert_allowed("https://www.example.com/p/my-post", [EXAMPLE]) == EXAMPLE


def test_assert_allowed_raises_not_allowed_error_for_unknown_host() -> None:
    with pytest.raises(NotAllowedError, match="random-blog.example.io"):
        assert_allowed("https://random-blog.example.io/p/my-post", [EXAMPLE])


SHIPPED_REGISTRY = Path(__file__).parents[2] / "publications.yaml"


def test_shipped_registry_has_unique_slugs_and_allows_its_own_homepages() -> None:
    pubs = load_publications(SHIPPED_REGISTRY)

    assert pubs
    assert len({pub.slug for pub in pubs}) == len(pubs)
    for pub in pubs:
        assert assert_allowed(pub.homepage, pubs) == pub
