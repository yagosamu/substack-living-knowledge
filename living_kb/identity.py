from urllib.parse import urlsplit, urlunsplit


def normalize_url(url: str) -> str:
    parts = urlsplit(url.strip())
    host = parts.hostname
    if not host:
        raise ValueError(f"URL has no host: {url!r}")
    if ":" in host:
        host = f"[{host}]"
    path = parts.path.rstrip("/")
    if path and not path.startswith("/"):
        path = f"/{path}"
    return urlunsplit(("https", host, path, "", ""))


def canonical_id(publication_slug: str, url: str) -> str:
    path = urlsplit(normalize_url(url)).path
    post_slug = path.rsplit("/", 1)[-1] if path else ""
    if not post_slug:
        raise ValueError(f"URL has no post slug: {url!r}")
    return f"pub:{publication_slug}:{post_slug}"
