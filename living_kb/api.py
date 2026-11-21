"""FastAPI application: health and the Inngest serve endpoint."""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import inngest.fast_api
from fastapi import Depends, FastAPI
from sqlalchemy.ext.asyncio import AsyncSession

from living_kb.config import settings
from living_kb.db import get_session
from living_kb.health import HealthResponse, Probe, build_health
from living_kb.inngest_app import functions as inngest_functions
from living_kb.inngest_app import inngest_client

app = FastAPI(title="living-kb", version="0.1.0")

inngest.fast_api.serve(app, inngest_client, inngest_functions)


async def get_probe() -> AsyncIterator[Probe]:
    """Yield an HTTP client for the Inngest reachability check."""
    async with httpx.AsyncClient(timeout=settings.health_check_timeout_seconds) as client:
        yield client


@app.get("/health", response_model=HealthResponse)
async def health(
    session: AsyncSession = Depends(get_session),  # noqa: B008
    probe: Probe = Depends(get_probe),  # noqa: B008
) -> HealthResponse:
    """Report dependencies and table counts without raising on outages."""
    return await build_health(session, probe)
