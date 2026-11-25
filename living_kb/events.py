"""Typed event contracts shared by producers and consumers."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, HttpUrl

PUBLICATION_ADDED = "kb/publication.added"
ARTICLE_DISCOVERED = "kb/article.discovered"
ARTICLE_INGESTED = "kb/article.ingested"

DiscoveryVia = Literal["rss", "manual"]


class PublicationAdded(BaseModel):
    """A reviewed addition to the publications allowlist."""

    slug: str = Field(min_length=1)
    name: str = Field(min_length=1)
    feed_url: HttpUrl
    homepage: HttpUrl


class ArticleDiscovered(BaseModel):
    """One article announced by discovery; the ingest consumer validates it first."""

    canonical_id: str = Field(min_length=1)
    publication_id: int = Field(gt=0)
    url: HttpUrl
    title: str = Field(min_length=1)
    published_at: datetime
    via: DiscoveryVia


class ArticleIngested(BaseModel):
    """The announcement that one article and its passages are durable."""

    canonical_id: str = Field(min_length=1)
    article_id: int = Field(gt=0)
    content_hash: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    passage_count: int = Field(ge=0)
