from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from typing import cast

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from living_kb.api import app, get_probe
from living_kb.db import get_session
from living_kb.health import Probe

SessionDependency = Callable[[], AsyncIterator[AsyncSession]]


class OkProbe:
    async def get(self, url: str) -> httpx.Response:
        assert url.endswith("/health")
        return httpx.Response(200, json={"data": {"status": "ok"}})


class FailingProbe:
    async def get(self, url: str) -> httpx.Response:
        raise httpx.ConnectError("inngest unreachable")


async def broken_session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(
        "postgresql+asyncpg://living_kb:living_kb_pw@127.0.0.1:1/living_kb",
        connect_args={"timeout": 2},
        poolclass=NullPool,
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            yield session
    finally:
        await engine.dispose()


def get_health(session_dependency: SessionDependency, probe: Probe) -> httpx.Response:
    app.dependency_overrides[get_session] = session_dependency
    app.dependency_overrides[get_probe] = lambda: probe
    try:
        with TestClient(app) as client:
            return cast(httpx.Response, client.get("/health"))
    finally:
        app.dependency_overrides.clear()


@pytest.mark.integration
@pytest.mark.usefixtures("clean_db")
def test_health_ok(session_override: SessionDependency) -> None:
    response = get_health(session_override, OkProbe())

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "database": "ok",
        "inngest": "ok",
        "articles": 0,
        "passages": 0,
    }


@pytest.mark.integration
@pytest.mark.usefixtures("clean_db")
def test_health_degraded_when_inngest_is_down(
    session_override: SessionDependency,
) -> None:
    response = get_health(session_override, FailingProbe())

    assert response.status_code == 200
    assert response.json() == {
        "status": "degraded",
        "database": "ok",
        "inngest": "unavailable",
        "articles": 0,
        "passages": 0,
    }


@pytest.mark.integration
def test_health_degraded_when_postgres_is_down() -> None:
    response = get_health(broken_session, OkProbe())

    assert response.status_code == 200
    assert response.json() == {
        "status": "degraded",
        "database": "unavailable",
        "inngest": "ok",
        "articles": 0,
        "passages": 0,
    }
