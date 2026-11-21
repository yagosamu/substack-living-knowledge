from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from typing import cast

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from living_kb.api import app, get_probe
from living_kb.config import settings
from living_kb.db import get_session
from living_kb.health import Probe

SessionDependency = Callable[[], AsyncIterator[AsyncSession]]


class OkProbe:
    async def get(self, url: str) -> httpx.Response:
        assert url.endswith("/health")
        return httpx.Response(200, json={"data": {"status": "ok"}})


class StatusProbe:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code

    async def get(self, url: str) -> httpx.Response:
        assert url.endswith("/health")
        return httpx.Response(self.status_code)


class FailingProbe:
    async def get(self, url: str) -> httpx.Response:
        raise httpx.ConnectError("inngest unreachable")


class TimeoutProbe:
    async def get(self, url: str) -> httpx.Response:
        raise httpx.TimeoutException("inngest timed out")


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


def assert_health(
    response: httpx.Response,
    status: str,
    database: str,
    inngest: str,
    articles: int,
    passages: int,
) -> None:
    assert response.status_code == 200
    assert response.json() == {
        "status": status,
        "database": database,
        "inngest": inngest,
        "articles": articles,
        "passages": passages,
    }


def test_get_probe_uses_configured_timeout() -> None:
    async def inspect() -> None:
        probe: Probe | None = None
        async for candidate in get_probe():
            probe = candidate
        assert isinstance(probe, httpx.AsyncClient)
        assert probe.timeout == httpx.Timeout(settings.health_check_timeout_seconds)

    asyncio.run(inspect())


@pytest.mark.integration
@pytest.mark.usefixtures("populated_db")
def test_health_ok(session_override: SessionDependency) -> None:
    assert_health(get_health(session_override, OkProbe()), "ok", "ok", "ok", 1, 2)


@pytest.mark.integration
@pytest.mark.usefixtures("populated_db")
def test_health_degraded_when_inngest_is_down(
    session_override: SessionDependency,
) -> None:
    assert_health(
        get_health(session_override, FailingProbe()),
        "degraded",
        "ok",
        "unavailable",
        1,
        2,
    )


@pytest.mark.integration
@pytest.mark.usefixtures("populated_db")
@pytest.mark.parametrize("status_code", [400, 500])
def test_health_degraded_when_inngest_returns_error(
    session_override: SessionDependency, status_code: int
) -> None:
    assert_health(
        get_health(session_override, StatusProbe(status_code)),
        "degraded",
        "ok",
        "unavailable",
        1,
        2,
    )


@pytest.mark.integration
@pytest.mark.usefixtures("populated_db")
def test_health_degraded_when_inngest_times_out(
    session_override: SessionDependency,
) -> None:
    assert_health(
        get_health(session_override, TimeoutProbe()),
        "degraded",
        "ok",
        "unavailable",
        1,
        2,
    )


@pytest.mark.integration
def test_health_degraded_when_postgres_is_down() -> None:
    assert_health(
        get_health(broken_session, OkProbe()),
        "degraded",
        "unavailable",
        "ok",
        0,
        0,
    )


@pytest.mark.integration
def test_health_degraded_when_both_dependencies_are_down() -> None:
    assert_health(
        get_health(broken_session, FailingProbe()),
        "degraded",
        "unavailable",
        "unavailable",
        0,
        0,
    )
