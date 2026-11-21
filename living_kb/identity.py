from urllib.parse import urlsplit, urlunsplit

_SCHEMES = frozenset({"http", "https"})


def normalize_url(url: str) -> str:
    parts = urlsplit(url.strip())
    if parts.scheme.lower() not in _SCHEMES:
        raise ValueError(f"URL must be http or https: {url!r}")

    host = parts.hostname
    if not host:
        raise ValueError(f"URL has no host: {url!r}")
    if ":" in host:
        host = f"[{host}]"

    return urlunsplit(("https", host, parts.path.rstrip("/"), "", ""))


def canonical_id(publication_slug: str, url: str) -> str:
    path = urlsplit(normalize_url(url)).path.rstrip("/")
    post_slug = path.rsplit("/", 1)[-1]
    if not post_slug:
        raise ValueError(f"URL has no post slug: {url!r}")
    return f"pub:{publication_slug}:{post_slug}"
