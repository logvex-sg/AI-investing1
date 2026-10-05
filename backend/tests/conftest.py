"""Integration test fixtures.

Integration tests run against a real PostgreSQL database (`ecosystem_test`),
because the parts worth testing — transactions, append-only triggers, vector
search, foreign keys — only exist in the database. Each test runs inside a
transaction that is rolled back, so the suite is order-independent.
"""

from __future__ import annotations

import os
import uuid

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

os.environ.setdefault("ECOSYSTEM_ENVIRONMENT", "test")

from ecosystem.config import get_settings  # noqa: E402


def _test_database_url() -> str:
    base = get_settings().database_url
    if base.endswith("/ecosystem"):
        return base[: -len("/ecosystem")] + "/ecosystem_test"
    return base + "_test"


@pytest_asyncio.fixture
async def engine():
    # pytest-asyncio gives each test its own event loop and asyncpg connections
    # are loop-bound, so the engine must be per-test with no pooling.
    eng = create_async_engine(_test_database_url(), poolclass=NullPool)
    try:
        async with eng.connect() as conn:
            await conn.close()
    except Exception as exc:  # pragma: no cover - environment guard
        await eng.dispose()
        pytest.skip(f"test database unavailable: {exc}")
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture
async def session(engine):
    """A session wrapped in a transaction that is rolled back afterwards."""
    connection = await engine.connect()
    transaction = await connection.begin()
    maker = async_sessionmaker(bind=connection, expire_on_commit=False)
    async with maker() as sess:
        yield sess
    # A test may have rolled back already (for example when it deliberately
    # tripped an append-only trigger); tolerate that.
    try:
        await transaction.rollback()
    except Exception:
        pass
    await connection.close()


@pytest.fixture
def unique_name() -> str:
    return f"test-{uuid.uuid4().hex[:10]}"
