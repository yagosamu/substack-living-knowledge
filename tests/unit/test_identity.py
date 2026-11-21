import pytest

from living_kb.identity import canonical_id, normalize_url


def test_normalize_url_drops_query_string() -> None:
    assert (
        normalize_url("https://example.substack.com/p/my-post?utm_source=x")
        == "https://example.substack.com/p/my-post"
    )


def test_normalize_url_drops_fragment() -> None:
    assert (
        normalize_url("https://example.substack.com/p/my-post#comments")
        == "https://example.substack.com/p/my-post"
    )


def test_normalize_url_upgrades_http_to_https() -> None:
    assert (
        normalize_url("http://example.substack.com/p/my-post")
        == "https://example.substack.com/p/my-post"
    )


def test_normalize_url_lowercases_host() -> None:
    assert (
        normalize_url("https://Example.Substack.COM/p/my-post")
        == "https://example.substack.com/p/my-post"
    )


def test_normalize_url_drops_port() -> None:
    assert (
        normalize_url("https://example.substack.com:443/p/my-post")
        == "https://example.substack.com/p/my-post"
    )


def test_normalize_url_strips_trailing_slash() -> None:
    assert (
        normalize_url("https://example.substack.com/p/my-post/")
        == "https://example.substack.com/p/my-post"
    )


def test_canonical_id_combines_publication_slug_and_post_slug() -> None:
    assert (
        canonical_id("example", "https://example.substack.com/p/my-post") == "pub:example:my-post"
    )


def test_canonical_id_is_identical_for_variants_of_the_same_post() -> None:
    variants = [
        "https://example.substack.com/p/my-post",
        "http://Example.substack.com/p/my-post/?utm_source=x#top",
        "https://example.substack.com:443/p/my-post?ref=share",
    ]
    assert {canonical_id("example", url) for url in variants} == {"pub:example:my-post"}


@pytest.mark.parametrize("url", ["https://example.substack.com", "https://example.substack.com/"])
def test_canonical_id_rejects_url_without_post_slug(url: str) -> None:
    with pytest.raises(ValueError):
        canonical_id("example", url)
