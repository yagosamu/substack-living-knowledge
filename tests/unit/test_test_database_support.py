from __future__ import annotations

import asyncio

import conftest
import pytest
from conftest import derive_test_database_url, reset_database

from living_kb.config import settings


def test_derive_test_database_url() -> None:
    url = "postgresql+asyncpg://alice:secret@db.example.test:6543/dev_db"
    assert (
        derive_test_database_url(url)
        == "postgresql+asyncpg://alice:secret@db.example.test:6543/dev_db_test"
    )


def test_derive_test_database_url_rejects_missing_database() -> None:
    url = "postgresql+asyncpg://alice:secret@db.example.test:6543"
    with pytest.raises(ValueError, match="must name a database"):
        derive_test_database_url(url)


def test_derive_test_database_url_rejects_unsafe_name() -> None:
    url = "postgresql+asyncpg://alice:secret@db.example.test:6543/dev-db"
    with pytest.raises(ValueError, match="safe test database name"):
        derive_test_database_url(url)


def test_derive_test_database_url_rejects_dev_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(conftest, "TEST_DATABASE_SUFFIX", "")
    url = "postgresql+asyncpg://alice:secret@db.example.test:6543/dev_db"
    with pytest.raises(ValueError, match="must differ"):
        derive_test_database_url(url)


def test_reset_database_refuses_dev_database() -> None:
    with pytest.raises(RuntimeError, match="Refusing to use development database"):
        asyncio.run(reset_database(settings.database_url))
