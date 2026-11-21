"""Dependency-isolated health checks."""

from __future__ import annotations

from typing import Literal, Protocol

import httpx
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from living_kb.config import settings

DependencyState = Literal["ok", "unavailable"]


class HealthResponse(BaseModel):
    """The exact shape of GET /health."""

    status: Literal["ok", "degraded"]
    database: DependencyState
    inngest: DependencyState
    articles: int
    passages: int


class Probe(Protocol):
    """The small part of an HTTP client that health checks need."""

    async def get(self, url: str) -> httpx.Response: ...


async def check_database(session: AsyncSession) -> tuple[bool, int, int]:
    """Return reachability and row counts; never raise on a failed dependency."""
    try:
        articles = await session.scalar(text("SELECT count(*) FROM articles"))
        passages = await session.scalar(text("SELECT count(*) FROM passages"))
    except Exception:
        return False, 0, 0
    return True, int(articles or 0), int(passages or 0)


async def check_inngest(probe: Probe) -> bool:
    """Probe the Inngest dev server's health endpoint."""
    url = f"{settings.inngest_base_url.rstrip('/')}/health"
    try:
        response = await probe.get(url)
    except Exception:
        return False
    return response.status_code < 400


async def build_health(session: AsyncSession, probe: Probe) -> HealthResponse:
    """Combine independent checks into one response."""
    database_ok, articles, passages = await check_database(session)
    inngest_ok = await check_inngest(probe)
    return HealthResponse(
        status="ok" if database_ok and inngest_ok else "degraded",
        database="ok" if database_ok else "unavailable",
        inngest="ok" if inngest_ok else "unavailable",
        articles=articles,
        passages=passages,
    )
