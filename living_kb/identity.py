from urllib.parse import urlsplit, urlunsplit


def normalize_url(url: str) -> str:
    parts = urlsplit(url.strip())
    host = parts.hostname
    if not host:
        raise ValueError(f"URL has no host: {url!r}")
    netloc = f"[{host}]" if ":" in host else host
    return urlunsplit(("https", netloc, parts.path.rstrip("/"), "", ""))


def canonical_id(publication_slug: str, url: str) -> str:
    post_slug = urlsplit(normalize_url(url)).path.rsplit("/", 1)[-1]
    if not post_slug:
        raise ValueError(f"URL has no post slug: {url!r}")
    return f"pub:{publication_slug}:{post_slug}"
